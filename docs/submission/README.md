# Public submission evidence

The parent [SUBMISSION.md](../../SUBMISSION.md) is the reviewer-facing
write-up. This directory contains the immutable, sanitized proof package and
the separately embeddable GIF referenced there.

## Artifact map

- `proof-bundle/manifest.json` — bundle inventory, source identity and attempt
  references.
- `proof-bundle/claim-index.json` — executable check, expected value, observed
  value and artifact links for every public claim.
- `proof-bundle/deterministic-report.json` — six-case semantic score.
- `proof-bundle/ledger.json` — allowlisted Run Ledger view with no raw API
  bodies, credentials, private paths or interaction IDs.
- `proof-bundle/images/` — the fixed nine-image sequence.
- `proof-bundle/evidence-frames/` — final run frames extracted from the owned
  synthetic recording.
- `proof-bundle/media/third-run.gif` — validated 26.16-second edited recording
  with the command and removed-wait labels visible in the media itself.
- `proof-bundle/media/third-run-timeline.json` — four ordered third-run events
  and removed waits.
- `proof-bundle/incidents/` — real rejected-background and controlled
  incomplete-analysis zero-write incidents, with sanitized evidence metadata.
- `third-run.gif` — copy of the bundle GIF for direct embedding in the
  reviewer-facing write-up.

The bundle contains three live Gemini analyses and three verified public demo
Issue Records for `heykay-47/crework-feedback-demo`. The deterministic report
scores the persisted live result against authored fixture ground truth.

## Rebuilding the checked-in package

The package was generated from the owned synthetic fixture by the read-only
proof builder using:

```bash
.venv/bin/python scripts/build-public-proof.py
```

The helper derives presentation images from persisted live measurements,
copies the captured GitHub page, renders the authentic asciinema recording,
and calls the same `build_proof_package()` contract used by the CLI. It never
calls Gemini or GitHub. Rebuilding replaces the checked-in bundle, so review
the resulting diff before committing regenerated assets.

Before rebuilding, the real background-rejection epoch can be captured once.
The command defaults to a local request-shape dry run; `--execute-background`
performs the documented live probe and requires `GEMINI_API_KEY`:

```bash
.venv/bin/python scripts/capture-proof-incidents.py
.venv/bin/python scripts/capture-proof-incidents.py --execute-background
```
