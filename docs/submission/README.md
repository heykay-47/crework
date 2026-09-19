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
- `proof-bundle/media/third-run.gif` — validated 24.01-second GIF.
- `proof-bundle/media/third-run-timeline.json` — four ordered third-run events
  and removed waits.
- `proof-bundle/incidents/` — rejected background, incomplete-analysis and
  uncertain-write zero-write incidents, with sanitized evidence metadata.
- `third-run.gif` — copy of the bundle GIF for direct embedding in the
  reviewer-facing write-up.

The bundle is intentionally labeled deterministic acceptance-harness evidence:
the Issue Records target `demo/feedback` with `example.test` URLs and are not
live external writes.

## Rebuilding the checked-in package

The package was generated from the owned synthetic fixture by the read-only
proof builder using:

```bash
.venv/bin/python scripts/build-public-proof.py
```

The helper creates presentation cards and real fixture frames, then calls the
same `build_proof_package()` contract used by the CLI. It never calls Gemini or
GitHub. Rebuilding replaces the checked-in bundle, so review the resulting
diff before committing regenerated assets.
