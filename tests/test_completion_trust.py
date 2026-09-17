from types import SimpleNamespace
from collections.abc import Sequence

import pytest
from pydantic import ValidationError

from feedback_triage.gemini_video import UntrustedInteraction, verify_completed_interaction


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
    "evidence": [{"start_seconds": 1.0, "end_seconds": 2.0, "client_quote": "Use Start free trial", "visual_observation": null, "keyframe_seconds": null}],
    "rationale": "The request is explicit."
  }]
}"""


def interaction(*, status: str = "completed", steps: Sequence[object] | None = None, output: str = VALID_OUTPUT) -> object:
    return SimpleNamespace(status=status, steps=list(steps or []), output_text=output)


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
