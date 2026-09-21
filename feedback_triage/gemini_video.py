import time
from collections import Counter
from collections.abc import Callable, Iterable
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any, Protocol, cast

from feedback_triage.models import AnalysisWireResult, VerifiedAnalysis, parse_wire_analysis
from feedback_triage.timing import TimingCallback, report_timing


MODEL = "gemini-3.5-flash-lite"
PROMPT = """Inspect the Feedback Recording's speech and visuals. Return only one valid JSON object with exactly this shape and no Markdown or commentary:
{
  "schema_version": "1.0",
  "video_summary": "non-empty summary",
  "observations": [{
    "observation_id": "obs_001",
    "topic_key": "lowercase-kebab-case",
    "type": "bug|change_request|feature_request|question|decision|reaction|commentary",
    "intent": "explicit_change|explicit_problem|ambiguous_reaction|question|decision|none",
    "title": "non-empty title",
    "component": null,
    "summary": "non-empty summary",
    "requested_outcome": null,
    "acceptance_criteria": [],
    "clarification_question": null,
    "confidence": "high|medium|low",
    "evidence": [{"start_timecode": "00:00:00.000", "end_timecode": "00:00:01.000", "keyframe_timecode": null, "client_quote": null, "visual_observation": "what was seen"}],
    "rationale": "non-empty rationale"
  }]
}
Use null for unknown nullable fields. Each evidence item must have client_quote or visual_observation. Return timestamp-grounded Observations only. Every evidence timestamp must be a colon-delimited timecode string in HH:MM:SS[.fraction] form, with two-digit minutes and seconds; use as many hour digits as needed. Never return numeric elapsed seconds. Convert clock positions exactly: 1:26 becomes "00:01:26.000", 1:40 becomes "00:01:40.000", and 2:10 becomes "00:02:10.000", never "126", "140", or "210". Preserve ambiguity, do not invent requested outcomes or acceptance criteria, and use one topic_key for repeated mentions. Cover every distinct feedback mention that is supported by speech or visible UI. Treat a clearly visible authored anomaly without an explicit client problem statement as a possible bug with intent none, medium confidence, and visually grounded evidence; reserve low confidence for cases where the evidence itself is unclear.

Apply these field rules:
- explicit_change: type MUST be change_request or feature_request. requested_outcome states the client's requested change. acceptance_criteria contains only a separate test condition the client explicitly states; merely restating the change is not a criterion.
- explicit_problem: type MUST be bug and requested_outcome states only the direct resolution inherent in the stated problem (for example, an overlap must no longer occur). acceptance_criteria contains only a separate test condition the client explicitly states; merely negating the problem is not a criterion.
- ambiguous_reaction: type MUST be reaction and never commentary. requested_outcome is null, acceptance_criteria is empty, and clarification_question asks what specific change the client wants. Use this intent whenever the client voices an unresolved concern without naming a change.
- question, decision, and none intents: type MUST be question for the question intent and decision for the decision intent; the none intent uses commentary, or bug for a visually observed possible bug. requested_outcome MUST be null, acceptance_criteria MUST be empty, and clarification_question MUST be null. A visual-only anomaly does not authorize you to infer a desired fix or test criterion.

For repeated observations with the same topic_key, type, component, intent, and requested_outcome MUST be exactly identical. Only the mention-specific title, summary, evidence, rationale, and directly stated acceptance criteria may differ.

Before returning JSON, perform a consistency pass over repeated topic_key values: copy the first observation's type, component, intent, and requested_outcome into every later observation with that topic_key verbatim. Do not add page names or other qualifiers to those copied fields."""


class UntrustedInteraction(RuntimeError):
    """A remote result failed the completed-analysis trust seam."""

    code = "interaction_terminal"


class UploadFailed(UntrustedInteraction):
    code = "upload_failed"


class InteractionUnrecoverable(UntrustedInteraction):
    code = "interaction_unrecoverable"


class InteractionTimeout(UntrustedInteraction):
    code = "interaction_timeout"


class ProcessingUnverified(UntrustedInteraction):
    code = "processing_unverified"


class InteractionAPI(Protocol):
    def create(self, **kwargs: Any) -> Iterable[Any]: ...
    def get(self, *, id: str) -> Any: ...


class FilesAPI(Protocol):
    def upload(self, *, file: str) -> Any: ...
    def get(self, *, name: str) -> Any: ...


class GeminiClient(Protocol):
    interactions: InteractionAPI
    files: FilesAPI


def compose_prompt(prompt: str, project_context: str) -> str:
    context = project_context.strip()
    if not context:
        return prompt
    return (
        f"{prompt.rstrip()}\n\n"
        "Project context is background only. Do not invent feedback, outcomes, acceptance criteria, or evidence from it:\n"
        f"{context}"
    )


def _value(item: Any, name: str) -> Any:
    return item.get(name) if isinstance(item, dict) else getattr(item, name, None)


def _usage(interaction: Any) -> dict[str, int] | None:
    metadata = _value(interaction, "usage_metadata") or _value(interaction, "usage")
    if metadata is None:
        return None
    usage: dict[str, int] = {}
    for name in (
        "prompt_token_count",
        "candidates_token_count",
        "total_token_count",
        "cached_content_token_count",
        "thoughts_token_count",
        "input_tokens",
        "output_tokens",
        "total_tokens",
    ):
        value = _value(metadata, name)
        if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
            usage[name] = value
    return usage or None


def _retry_after(error: Exception) -> float | None:
    response = getattr(error, "response", None)
    headers = getattr(response, "headers", None) or getattr(error, "headers", None)
    if headers is None:
        return None
    value = headers.get("Retry-After")
    try:
        return max(0.0, float(value))
    except (TypeError, ValueError):
        if not isinstance(value, str):
            return None
        try:
            retry_at = parsedate_to_datetime(value)
            if retry_at.tzinfo is None:
                retry_at = retry_at.replace(tzinfo=UTC)
            return max(0.0, (retry_at - datetime.now(UTC)).total_seconds())
        except (TypeError, ValueError, OverflowError):
            return None


def _is_retryable(error: Exception) -> bool:
    status = getattr(error, "status_code", None)
    if status is None:
        response = getattr(error, "response", None)
        status = getattr(response, "status_code", None)
    return isinstance(error, (ConnectionError, TimeoutError)) or status in {408, 429, 500, 502, 503, 504}


def _retry_call(call: Callable[[], Any], *, deadline: float, attempts: int = 3) -> Any:
    for number in range(attempts):
        if time.monotonic() >= deadline:
            raise TimeoutError("retry deadline exhausted")
        try:
            return call()
        except Exception as error:
            if number == attempts - 1 or not _is_retryable(error):
                raise
            delay = _retry_after(error)
            delay = delay if delay is not None else min(2**number, 4)
            remaining = deadline - time.monotonic()
            if remaining <= 0 or delay > remaining:
                raise TimeoutError("retry deadline exhausted") from error
            time.sleep(delay)
    raise AssertionError("bounded retry loop exhausted")


def verify_completed_interaction(interaction: Any) -> VerifiedAnalysis:
    status = _value(interaction, "status")
    if status != "completed":
        raise UntrustedInteraction(f"retrieved interaction status is {status}")
    steps = _value(interaction, "steps") or []
    calls = [_value(step, "id") for step in steps if _value(step, "type") == "processing_call"]
    results = [_value(step, "call_id") for step in steps if _value(step, "type") == "processing_result"]
    if (
        not calls
        or any(not isinstance(step_id, str) or not step_id for step_id in calls + results)
        or Counter(calls) != Counter(results)
        or any(count != 1 for count in Counter(calls).values())
    ):
        raise ProcessingUnverified("processing steps are not fully matched one-to-one")
    output_text = _value(interaction, "output_text")
    if not isinstance(output_text, str):
        raise UntrustedInteraction("completed interaction has no text output")
    analysis = parse_wire_analysis(output_text)
    return VerifiedAnalysis(analysis=analysis, processing_pair_count=len(calls), gemini_usage=_usage(interaction))


def retrieve_verified_interaction(
    client: GeminiClient,
    interaction_id: str,
    *,
    deadline_seconds: float = 300,
    on_stage: Callable[[str], None] | None = None,
) -> VerifiedAnalysis:
    if on_stage is not None:
        on_stage("interaction_verification")
    deadline = time.monotonic() + deadline_seconds
    try:
        retrieved = _retry_call(lambda: client.interactions.get(id=interaction_id), deadline=deadline)
        while _value(retrieved, "status") == "in_progress" and time.monotonic() < deadline:
            time.sleep(min(2, deadline - time.monotonic()))
            retrieved = _retry_call(lambda: client.interactions.get(id=interaction_id), deadline=deadline)
    except TimeoutError as error:
        raise InteractionTimeout(str(error)) from error
    except Exception as error:
        raise InteractionUnrecoverable(str(error)) from error
    if _value(retrieved, "status") == "in_progress":
        raise InteractionTimeout("interaction remained in_progress until the retrieval deadline")
    return verify_completed_interaction(retrieved)


def run_stored_stream(
    client: GeminiClient,
    video: Path,
    *,
    on_created: Callable[[str], None],
    on_diagnostic: Callable[[str], None],
    upload_deadline_seconds: float = 300,
    prompt: str = PROMPT,
    project_context: str = "",
    on_timing: TimingCallback | None = None,
    on_stage: Callable[[str], None] | None = None,
) -> VerifiedAnalysis:
    if on_stage is not None:
        on_stage("upload")
    upload_started = time.monotonic()
    try:
        uploaded = client.files.upload(file=str(video))
        state = _value(_value(uploaded, "state"), "name")
        deadline = time.monotonic() + upload_deadline_seconds
        while state == "PROCESSING" and time.monotonic() < deadline:
            time.sleep(min(2, deadline - time.monotonic()))
            uploaded = _retry_call(
                lambda: client.files.get(name=_value(uploaded, "name")),
                deadline=deadline,
            )
            state = _value(_value(uploaded, "state"), "name")
    except Exception as error:
        raise UploadFailed(str(error)) from error
    finally:
        report_timing(on_timing, "upload_seconds", time.monotonic() - upload_started)
    if state != "ACTIVE":
        raise UploadFailed(f"uploaded file is not ACTIVE: {state}")
    if on_stage is not None:
        on_stage("gemini_processing")
    analysis_started = time.monotonic()
    try:
        try:
            stream = client.interactions.create(
                model=MODEL,
                input=[
                    {
                        "type": "video",
                        "uri": _value(uploaded, "uri"),
                        "mime_type": _value(uploaded, "mime_type"),
                        "processing": "agentic",
                    },
                    {"type": "text", "text": compose_prompt(prompt, project_context)},
                ],
                stream=True,
                store=True,
                response_format={
                    "text": {
                        "mime_type": "application/json",
                        "schema": AnalysisWireResult.model_json_schema(),
                    }
                },
            )
        except Exception as error:
            raise InteractionUnrecoverable(str(error)) from error
        interaction_id: str | None = None
        completed = False
        for event in stream:
            event_type = _value(event, "event_type") or _value(event, "type")
            if event_type == "interaction.created":
                interaction = _value(event, "interaction")
                candidate = _value(interaction, "id") or _value(event, "interaction_id")
                if not isinstance(candidate, str) or not candidate:
                    raise UntrustedInteraction("interaction.created has no interaction ID")
                on_created(candidate)
                interaction_id = candidate
            else:
                if interaction_id is None:
                    raise UntrustedInteraction("stream event arrived before interaction ID was persisted")
                on_diagnostic(str(event_type))
            if event_type == "error":
                event_error = _value(event, "error")
                code = _value(event_error, "code")
                message = _value(event_error, "message")
                raise UntrustedInteraction(f"stream error {code}: {message}")
            if event_type == "interaction.completed":
                completed = True
        if interaction_id is None:
            raise UntrustedInteraction("stream ended without an interaction ID")
        if not completed:
            raise InteractionTimeout("stream ended without interaction.completed")
        return retrieve_verified_interaction(client, interaction_id, on_stage=on_stage)
    finally:
        report_timing(on_timing, "analysis_seconds", time.monotonic() - analysis_started)


def create_client() -> GeminiClient:
    from google import genai

    return cast(GeminiClient, genai.Client())
