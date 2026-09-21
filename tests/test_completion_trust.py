import json
from types import SimpleNamespace
from collections.abc import Sequence

import pytest
from pydantic import ValidationError

from feedback_triage.gemini_video import UntrustedInteraction, verify_completed_interaction
from feedback_triage.models import parse_wire_analysis


VALID_OUTPUT = """{
  "schema_version": "1.0",
  "video_summary": "The client requests a CTA label change.",
  "observations": [{
    "observation_id": "obs_001",
    "topic_key": "cta-label",
    "type": "change_request",
    "intent": "explicit_change",
    "title": "Change CTA label",
    "component": "hero",
    "summary": "The client asks for a clearer CTA label.",
    "requested_outcome": "Use Start free trial",
    "acceptance_criteria": ["CTA reads Start free trial"],
    "clarification_question": null,
    "confidence": "high",
    "evidence": [{"start_timecode": "00:00:01.000", "end_timecode": "00:00:02.000", "client_quote": "Use Start free trial", "visual_observation": null, "keyframe_timecode": null}],
    "rationale": "The request is explicit."
  }]
}"""

VALID_ANALYSIS_JSON = parse_wire_analysis(VALID_OUTPUT).model_dump_json()


def interaction(
    *,
    status: str = "completed",
    steps: Sequence[object] | None = None,
    output: str = VALID_OUTPUT,
    usage_metadata: object | None = None,
) -> object:
    return SimpleNamespace(
        status=status,
        steps=list(steps or []),
        output_text=output,
        usage_metadata=usage_metadata,
    )


def test_only_retrieved_completed_interaction_is_trusted() -> None:
    with pytest.raises(UntrustedInteraction, match="status is in_progress"):
        verify_completed_interaction(interaction(status="in_progress"))


@pytest.mark.parametrize(
    "steps",
    [
        [SimpleNamespace(type="processing_call", id="segment-1")],
        [SimpleNamespace(type="processing_result", call_id="segment-1")],
        [
            SimpleNamespace(type="processing_call", id="segment-1"),
            SimpleNamespace(type="processing_result", call_id="segment-2"),
        ],
        [
            SimpleNamespace(type="processing_call", id="segment-1"),
            SimpleNamespace(type="processing_call", id="segment-1"),
            SimpleNamespace(type="processing_result", call_id="segment-1"),
            SimpleNamespace(type="processing_result", call_id="segment-1"),
        ],
    ],
)
def test_every_processing_call_and_result_must_be_matched(steps: list[object]) -> None:
    with pytest.raises(UntrustedInteraction, match="processing steps are not fully matched"):
        verify_completed_interaction(interaction(steps=steps))


def test_schema_is_validated_after_completion_and_processing_proof() -> None:
    steps = [
        SimpleNamespace(type="processing_call", id="segment-1"),
        SimpleNamespace(type="processing_result", call_id="segment-1"),
    ]

    result = verify_completed_interaction(interaction(steps=steps))

    assert result.analysis.observations[0].observation_id == "obs_001"

    with pytest.raises(ValidationError):
        verify_completed_interaction(interaction(steps=steps, output='{"schema_version":"1.0"}'))


def test_long_video_timecodes_are_converted_to_elapsed_seconds() -> None:
    output = json.loads(VALID_OUTPUT)
    evidence = output["observations"][0]["evidence"][0]
    evidence.pop("start_seconds", None)
    evidence.pop("end_seconds", None)
    evidence.pop("keyframe_seconds", None)
    evidence.update(
        {
            "start_timecode": "00:01:26.000",
            "end_timecode": "00:01:40.000",
            "keyframe_timecode": "00:01:30.500",
        }
    )
    steps = [
        SimpleNamespace(type="processing_call", id="segment-1"),
        SimpleNamespace(type="processing_result", call_id="segment-1"),
    ]

    result = verify_completed_interaction(interaction(steps=steps, output=json.dumps(output)))
    span = result.analysis.observations[0].evidence[0]

    assert span.start_seconds == 86.0
    assert span.end_seconds == 100.0
    assert span.keyframe_seconds == 90.5


def test_completed_interaction_preserves_nonnegative_usage_metadata() -> None:
    steps = [
        SimpleNamespace(type="processing_call", id="segment-1"),
        SimpleNamespace(type="processing_result", call_id="segment-1"),
    ]

    result = verify_completed_interaction(
        interaction(
            steps=steps,
            usage_metadata=SimpleNamespace(
                prompt_token_count=11,
                candidates_token_count=7,
                total_token_count=18,
                thoughts_token_count=-1,
            ),
        )
    )

    assert result.gemini_usage == {
        "prompt_token_count": 11,
        "candidates_token_count": 7,
        "total_token_count": 18,
    }


@pytest.mark.parametrize(
    ("path", "value"),
    [
        (("observations", 0, "title"), "   "),
        (("observations", 0, "confidence"), "certain"),
        (("observations", 0, "evidence", 0, "start_timecode"), "126"),
    ],
)
def test_empty_required_values_invalid_enums_and_timecodes_are_output_invalid(
    path: tuple[str | int, ...], value: object
) -> None:
    output = json.loads(VALID_OUTPUT)
    target: object = output
    for part in path[:-1]:
        target = target[part]  # type: ignore[index]
    target[path[-1]] = value  # type: ignore[index]
    steps = [
        SimpleNamespace(type="processing_call", id="segment-1"),
        SimpleNamespace(type="processing_result", call_id="segment-1"),
    ]

    with pytest.raises(ValidationError):
        verify_completed_interaction(interaction(steps=steps, output=json.dumps(output)))
