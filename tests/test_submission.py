from __future__ import annotations

import json
from pathlib import Path

from feedback_triage.proof import IMAGE_SEQUENCE, probe_media_duration


ROOT = Path(__file__).parents[1]
SUBMISSION = ROOT / "SUBMISSION.md"
BUNDLE = ROOT / "docs" / "submission" / "proof-bundle"


def test_submission_is_self_contained_and_links_visible_proof() -> None:
    write_up = SUBMISSION.read_text(encoding="utf-8")
    required_phrases = (
        "2026-09-01",
        "operational pain",
        "spoken and visual",
        "Perception",
        "Interpretation",
        "Execution",
        "Stored-stream fallback",
        "synthetic fixture",
        "no-code",
        "production extensions",
        "Final reviewer pass",
    )
    assert all(phrase.casefold() in write_up.casefold() for phrase in required_phrases)
    assert all(f"docs/submission/proof-bundle/images/{index:02d}-" in write_up for index in range(1, 10))
    assert "docs/submission/third-run.gif" in write_up

    manifest = json.loads((BUNDLE / "manifest.json").read_text(encoding="utf-8"))
    claims = json.loads((BUNDLE / "claim-index.json").read_text(encoding="utf-8"))
    report = json.loads((BUNDLE / "deterministic-report.json").read_text(encoding="utf-8"))
    ledger_text = (BUNDLE / "ledger.json").read_text(encoding="utf-8")

    assert manifest["source_sha256"] == "f0b72e2f45e33166616e18293b1326be5fd8d1d8635a4e6a3770b516e9773fdc"
    assert tuple(slot["label"] for slot in manifest["image_sequence"]) == IMAGE_SEQUENCE
    assert all(claim["status"] == "passed" for claim in claims)
    assert any(claim["claim_id"] == "claim_request_trust" for claim in claims)
    assert any(claim["claim_id"] == "claim_zero_unsafe_writes" for claim in claims)
    assert (BUNDLE / "incidents" / "uncertain-write.json").is_file()
    assert report["passed"] is True
    assert report["matched_case_ids"] == ["A", "B", "C", "D", "E", "F"]
    assert "private-interaction" not in ledger_text
    assert "interaction_id" not in ledger_text
    assert "/tmp/" not in ledger_text
    assert "stream=True; store=True" in ledger_text
    assert json.loads(ledger_text)["acceptance"]["manual_baseline"]["watch_seconds"] == 360.0

    for artifact in manifest["artifacts"]:
        assert (BUNDLE / artifact["path"]).is_file()
    assert 20 <= probe_media_duration(BUNDLE / "media" / "third-run.gif") <= 30
