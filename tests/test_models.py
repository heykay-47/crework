import pytest
from pydantic import ValidationError

from feedback_triage.models import AnalysisWireResult, seconds_to_timecode, timecode_to_seconds


def test_timecodes_convert_without_mmss_digit_ambiguity() -> None:
    assert timecode_to_seconds("00:01:26.000") == 86.0
    assert timecode_to_seconds("00:01:40.000") == 100.0
    assert timecode_to_seconds("00:02:10.000") == 130.0
    assert timecode_to_seconds("100:00:00.5") == 360000.5


def test_seconds_to_timecode_round_trips_fractional_long_video_position() -> None:
    timecode = seconds_to_timecode(3726.125)

    assert timecode == "01:02:06.125000"
    assert timecode_to_seconds(timecode) == 3726.125


@pytest.mark.parametrize("value", ["126", "00:61:00", "00:00:60", "00:00:01.1234567"])
def test_invalid_timecodes_are_rejected(value: str) -> None:
    with pytest.raises(ValueError, match="timecode"):
        timecode_to_seconds(value)


def test_wire_schema_declares_timecode_pattern_and_rejects_numeric_digits() -> None:
    schema = AnalysisWireResult.model_json_schema()
    evidence = schema["$defs"]["EvidenceWireSpan"]

    assert evidence["properties"]["start_timecode"]["description"] == "Elapsed position as HH:MM:SS[.fraction]."
    with pytest.raises(ValidationError):
        AnalysisWireResult.model_validate(
            {
                "schema_version": "1.0",
                "video_summary": "summary",
                "observations": [
                    {
                        "observation_id": "obs_001",
                        "topic_key": "topic",
                        "type": "change_request",
                        "intent": "explicit_change",
                        "title": "Change",
                        "component": None,
                        "summary": "Summary",
                        "requested_outcome": "Outcome",
                        "acceptance_criteria": [],
                        "clarification_question": None,
                        "confidence": "high",
                        "evidence": [
                            {
                                "start_timecode": "126",
                                "end_timecode": "00:02:00.000",
                                "keyframe_timecode": None,
                                "client_quote": "Change it",
                                "visual_observation": None,
                            }
                        ],
                        "rationale": "Explicit request.",
                    }
                ],
            }
        )
