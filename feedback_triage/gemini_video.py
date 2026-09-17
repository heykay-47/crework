from collections import Counter
from collections.abc import Callable, Iterable
from pathlib import Path
import time
from typing import Any, Protocol, cast

from feedback_triage.models import AnalysisResult, VerifiedAnalysis


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
    "evidence": [{"start_seconds": 0.0, "end_seconds": 1.0, "keyframe_seconds": null, "client_quote": null, "visual_observation": "what was seen"}],
    "rationale": "non-empty rationale"
  }]
}
Use null for unknown nullable fields. Each evidence item must have client_quote or visual_observation. Return timestamp-grounded Observations only. Preserve ambiguity, do not invent requested outcomes or acceptance criteria, and use one topic_key for repeated mentions."""


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


def _value(item: Any, name: str) -> Any:
    return item.get(name) if isinstance(item, dict) else getattr(item, name, None)


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
    analysis = AnalysisResult.model_validate_json(output_text)
    return VerifiedAnalysis(analysis=analysis, processing_pair_count=len(calls))


def run_stored_stream(
    client: GeminiClient,
    video: Path,
    *,
    on_created: Callable[[str], None],
    on_diagnostic: Callable[[str], None],
    upload_deadline_seconds: float = 300,
) -> VerifiedAnalysis:
    try:
        uploaded = client.files.upload(file=str(video))
        state = _value(_value(uploaded, "state"), "name")
        deadline = time.monotonic() + upload_deadline_seconds
        while state == "PROCESSING" and time.monotonic() < deadline:
            time.sleep(2)
            uploaded = client.files.get(name=_value(uploaded, "name"))
            state = _value(_value(uploaded, "state"), "name")
    except Exception as error:
        raise UploadFailed(str(error)) from error
    if state != "ACTIVE":
        raise UploadFailed(f"uploaded file is not ACTIVE: {state}")
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
                {"type": "text", "text": PROMPT},
            ],
            stream=True,
            store=True,
            response_format={
                "text": {
                    "mime_type": "application/json",
                    "schema": AnalysisResult.model_json_schema(),
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
    try:
        retrieved = client.interactions.get(id=interaction_id)
    except Exception as error:
        raise InteractionUnrecoverable(str(error)) from error
    return verify_completed_interaction(retrieved)


def create_client() -> GeminiClient:
    from google import genai

    return cast(GeminiClient, genai.Client())
