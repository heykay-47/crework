# Client Feedback Triage

The tracer analyzes one owned synthetic **Feedback Recording** through Gemini agentic video, verifies the stored `AnalysisResult`, and routes its Observations through deterministic policy. Analysis is separate from the explicit human review and GitHub Issue publishing step.

## Build and run

Create the short development input if needed:

```bash
./scripts/make-synthetic-recording.sh
```

Build the Python 3.14 image with FFmpeg and the fixture-only speech synthesizer:

```bash
docker build -t client-feedback-triage .
```

Run it as your host user. Credentials are injected at runtime; input, project context, and output are mounted rather than copied into the image.

```bash
mkdir -p output
docker run --rm --user "$(id -u):$(id -g)" \
  --env GEMINI_API_KEY \
  --mount type=bind,src="$PWD/inbox",dst=/input,readonly \
  --mount type=bind,src="$PWD/CONTEXT.md",dst=/context/CONTEXT.md,readonly \
  --mount type=bind,src="$PWD/output",dst=/output \
  client-feedback-triage analyze /input/synthetic-feedback.mp4 --output /output
```

The command FFprobes the input before creating a Gemini client. It then uploads the recording, uses `gemini-3.5-flash-lite` with `stream=True, store=True`, atomically records the interaction ID from `interaction.created`, and treats later stream events only as diagnostics. It retrieves the stored interaction and requires:

1. `status == "completed"`;
2. every `processing_call.id` to match exactly one observed `processing_result.call_id`, with no orphans;
3. `output_text` to pass the v1 Gemini wire schema, after which its timecodes are parsed into the persisted domain schema.

Gemini evidence timestamps use strict `HH:MM:SS[.fraction]` strings at the API boundary, not numeric seconds. This avoids the long-video ambiguity where a model could turn `1:26` into `126`. The trust boundary converts those strings deterministically to numeric elapsed seconds (`1:26` becomes `86.0`) before policy validation and Run Ledger persistence. Hours may contain more than two digits, so recordings longer than 24 hours remain representable. Malformed timecodes and impossible post-conversion spans fail closed as `output_invalid` or `policy_failed`; they are never heuristically clamped.

The per-source Run Ledger is written atomically to `output/<source-sha256>/ledger.json`. Any failed trust gate records a stable failure and returns no Observation to policy or external integrations. In the normal full triage pipeline, actionable `candidate` and `manual_review` routes also receive a local Evidence Frame under `output/<source-sha256>/evidence-frames/<attempt-id>/<candidate-id>.png`; the first two acceptance commands intentionally defer frame extraction until `run`.

## Frozen acceptance cycle

The canonical fixture has an opt-in, source-scoped acceptance record at `output/<source-sha256>/acceptance.json`. Run the exact consecutive sequence with the same output directory and frozen fixture:

```bash
mkdir -p output
docker run --rm -it --user "$(id -u):$(id -g)" \
  --env GEMINI_API_KEY \
  --mount type=bind,src="$PWD/fixtures/canonical",dst=/input,readonly \
  --mount type=bind,src="$PWD/fixtures/canonical",dst=/context,readonly \
  --mount type=bind,src="$PWD/fixtures",dst=/app/fixtures,readonly \
  --mount type=bind,src="$PWD/output",dst=/output \
  client-feedback-triage analyze /app/fixtures/canonical/feedback-recording.mp4 --output /output
docker run --rm -it --user "$(id -u):$(id -g)" \
  --env GEMINI_API_KEY \
  --mount type=bind,src="$PWD/fixtures/canonical",dst=/input,readonly \
  --mount type=bind,src="$PWD/fixtures/canonical",dst=/context,readonly \
  --mount type=bind,src="$PWD/fixtures",dst=/app/fixtures,readonly \
  --mount type=bind,src="$PWD/output",dst=/output \
  client-feedback-triage analyze /app/fixtures/canonical/feedback-recording.mp4 --output /output --reanalyze
docker run --rm -it --user "$(id -u):$(id -g)" \
  --env GEMINI_API_KEY --env GITHUB_TOKEN \
  --mount type=bind,src="$PWD/fixtures/canonical",dst=/input,readonly \
  --mount type=bind,src="$PWD/fixtures/canonical",dst=/context,readonly \
  --mount type=bind,src="$PWD/fixtures",dst=/app/fixtures,readonly \
  --mount type=bind,src="$PWD/manual-baseline.json",dst=/manual-baseline.json,readonly \
  --mount type=bind,src="$PWD/output",dst=/output \
  client-feedback-triage run /app/fixtures/canonical/feedback-recording.mp4 --output /output --reanalyze \
  --repository owner/repository --manual-baseline /manual-baseline.json
```

The first two commands require fresh, completed, semantically passing analyses and do not extract Evidence Frames. The third command is the only step that extracts frames, asks for Approval, and can create Issues. A command-order change, nonqualifying attempt, or behavior fingerprint change resets the consecutive requirement. `run` first searches open and closed Issues for every exact final-source marker; it performs no cleanup mutation and stops if one is present. GitHub writes remain interactive and require `GITHUB_TOKEN`.

The manual baseline is a measured JSON artifact for the same previously unseen recording. It must contain `source_sha256`, `recording_label`, `equivalent_issue_count`, matching positive `equivalent_issue_numbers` for the manually closed demo Issues, positive `watch_seconds`, `issue_writing_seconds`, and `active_human_seconds`, plus timezone-qualified `measured_at`. The canonical cycle expects three equivalent Issues. Before analysis, `run` reads those Issue numbers and refuses to continue unless every one is closed; it never closes or otherwise mutates them. Successful acceptance records upload, Gemini analysis/usage, Evidence Frame, active-review, write, wall-clock, and active-human measurements without extrapolating a manual baseline.

## Freeze a public proof bundle

After the acceptance cycle passes, use the read-only `package` command (also available as `proof`) to copy reviewer-supplied screenshots, the edited third-run GIF, and sanitized incident evidence into a new immutable bundle. It loads the persisted Acceptance Record and Run Ledger, validates current and archived fingerprint epochs, recomputes the deterministic score, and writes `manifest.json`, `claim-index.json`, `deterministic-report.json`, `ledger.json`, the nine-image sequence, Evidence Frames, GIF timeline, and incident artifacts. It does not analyze, call Gemini, prompt for Approval, or write to GitHub.

The command requires one image for each fixed label, a real 20-30 second GIF, a timeline JSON describing the four third-run events and measured removed waits, and incident JSON for both the rejected background interaction and an incomplete or uncertain zero-write case:

```bash
docker run --rm --user "$(id -u):$(id -g)" \
  --mount type=bind,src="$PWD/output",dst=/output \
  --mount type=bind,src="$PWD/proof-inputs",dst=/proof-inputs,readonly \
  client-feedback-triage package /output/<source-sha256>/ledger.json \
  --bundle /output/<source-sha256>/proof-bundle \
  --gif /proof-inputs/third-run.gif \
  --gif-timeline /proof-inputs/third-run-timeline.json \
  --image capability-date=/proof-inputs/01-capability-date.png \
  --image fixture-manifest=/proof-inputs/02-fixture-manifest.png \
  --image request-trust=/proof-inputs/03-request-trust.png \
  --image three-run-summary=/proof-inputs/04-three-run-summary.png \
  --image six-routes=/proof-inputs/05-six-routes.png \
  --image evidence-frames=/proof-inputs/06-evidence-frames.png \
  --image manual-review-approval=/proof-inputs/07-manual-review-approval.png \
  --image final-issues=/proof-inputs/08-final-issues.png \
  --image sanitized-run-ledger=/proof-inputs/09-sanitized-run-ledger.png \
  --incident /proof-inputs/background-rejection.json \
  --incident /proof-inputs/zero-write-case.json
```

The bundle is rejected if an artifact contains credentials, authorization tokens, private filesystem paths, or email-like personal data. Source paths remain command inputs only; the manifest and sanitized ledger contain bundle-relative artifact paths and allowlisted metadata.

## Review and publish GitHub Issues

After a verified analysis, use the terminal `publish` command. It prompts as needed for each policy-admitted Candidate and Manual Review result; unchanged persisted Approvals resume without another prompt. Clarification Requests and Withheld Results are never offered for Approval. A Manual Review must be confirmed or edited before it can be approved. The GitHub token is read from `GITHUB_TOKEN` and is never written to the Run Ledger or printed:

```bash
docker run --rm -it --user "$(id -u):$(id -g)" \
  --env GITHUB_TOKEN \
  --mount type=bind,src="$PWD/inbox",dst=/input,readonly \
  --mount type=bind,src="$PWD/output",dst=/output \
  client-feedback-triage publish /input/synthetic-feedback.mp4 \
  --output /output \
  --repository demo/feedback \
  --operator-label "demo review"
```

Each Approval is an immutable snapshot of the source SHA-256, stable Candidate ID, destination, complete rendered payload, payload hash, exact baseline Candidate snapshot hash, reviewed Candidate prose baseline, approval timestamp, and optional operator label. The Issue body contains exactly one marker:

```text
<!-- crework:v1 source_sha256=<64-lowercase-hex> candidate_id=<cand_...> -->
```

The command holds a source-scoped lock, reconciles pending writes and existing Issue Records before asking for new decisions, searches open and closed Issues for the exact marker, persists `approved` and then `write_pending` before a POST, and submits approved Candidates sequentially. An existing exact marker is adopted without POST. If multiple exact matches are found, publishing stops; provide one or more explicit `--canonical-selection CANDIDATE_ID=ISSUE_NUMBER` values on a later run to record the human canonical choice and adopt that verified Issue without mutation. A create response is accepted only after destination, Issue number, and marker verification; a destination-scoped Issue Record is persisted immediately. Issue creation is never automatically retried. Use `--retry-failed` only for a definitive rejection, or use both `--retry-uncertain --confirm-no-issue` after an explicit human certification. A lost response or zero-match reconciliation remains fail-closed. Changed `--reanalyze` snapshots and destinations require fresh Approval, while an existing verified Issue remains skip-only; no rerun updates, reopens, overwrites, or recreates it.

## Local browser review application

The `web` command serves a local, single-user review workspace at `http://127.0.0.1:8000`. It uses the same analysis, policy, Run Ledger, Evidence Frame, Approval, reconciliation, and write-coordination services as the CLI; it does not expose credentials or raw Gemini diagnostics to the browser. Uploads are staged beneath `/output/web/uploads`, and completed web recordings can be reopened from their persisted metadata without rerunning Gemini. Completed CLI ledgers are also opened from `/output/<source_sha256>/ledger.json` when their matching recording is present under `/input`; choose them from the persisted-recording picker.

```bash
mkdir -p output
docker run --rm --user "$(id -u):$(id -g)" \
  --env GEMINI_API_KEY --env GITHUB_TOKEN --env GITHUB_REPOSITORY \
  --publish 127.0.0.1:8000:8000 \
  --mount type=bind,src="$PWD/inbox",dst=/input,readonly \
  --mount type=bind,src="$PWD/CONTEXT.md",dst=/context/CONTEXT.md,readonly \
  --mount type=bind,src="$PWD/output",dst=/output \
  client-feedback-triage web --host 0.0.0.0 --port 8000
```

The interface has one explicit Analyze action, coarse stage announcements without invented percentages, a keyboard-operable timeline, route-labelled review groups, immutable evidence/provenance fields, editable Approval prose only, a collapsed trust panel, and separate Approval and publish actions. `Clarification Request` and `Withheld Result` routes have no Approval action. Publishing is fail-closed through the same marker reconciliation and Issue Record rules as `publish`; definitive failures can be retried, uncertain writes require the visible no-existing-Issue certification, and canonical conflicts require an explicit Candidate-to-Issue selection before the publish control is enabled.

## Canonical semantic evaluation

`fixtures/canonical/` contains the owned six-minute Feedback Recording, frozen project context and prompt, and an independent ground-truth manifest for the six authored cases. Rebuild the recording in the project container when intentionally creating a new fixture version:

```bash
docker run --rm --user "$(id -u):$(id -g)" --entrypoint /bin/bash \
  --mount type=bind,src="$PWD",dst=/workspace \
  client-feedback-triage \
  /workspace/scripts/make-canonical-recording.sh \
  /workspace/fixtures/canonical/feedback-recording.mp4
```

Changing the recording requires updating its manifest source hash, duration, version, and source-derived Candidate IDs. Run a fresh live analysis and semantic score with:

```bash
mkdir -p output
docker run --rm --user "$(id -u):$(id -g)" \
  --env GEMINI_API_KEY \
  --mount type=bind,src="$PWD/fixtures/canonical",dst=/fixture,readonly \
  --mount type=bind,src="$PWD/output",dst=/output \
  client-feedback-triage analyze /fixture/feedback-recording.mp4 \
  --output /output \
  --prompt /fixture/prompt.md \
  --context /fixture/project-context.md \
  --ground-truth /fixture/ground-truth.json \
  --reanalyze
```

The scorer validates source identity and duration; exact semantic route, type, intent, reason, and Candidate identity; required and forbidden authored outcomes and text anchors; required client/visual evidence anchors; Evidence Span midpoint coverage within the result's own authored topic windows; required Evidence Frames; the shared navbar identity across both mention windows; hallucinated evidence; missing cases; and unexpected actionable results. Any permitted extra Withheld Result must be declared in the manifest with its reason and authored window. The command exits nonzero if the semantic score fails. Inspect the printed `evidence_frames` array and the attempt-specific directory to verify extracted PNGs for the required visual cases.

The Run Ledger stores the fingerprint and component digests for every behavior-affecting source, model, prompt, schema, context, policy, dependency/container, fixture version, ground-truth, and implementation input. A changed fingerprint cannot reuse the old ledger.

After verification, deterministic policy validates cross-field semantics and timestamps against the FFprobe duration. Ends up to 0.5 seconds beyond the duration are normalized to the duration; other timestamp violations fail the whole analysis. Observations with the same topic key merge into one result with sorted evidence and a stable `cand_` identity derived from the schema version, source hash, and topic key.

Policy routes each merged result in this order:

1. questions, decisions, and non-actionable commentary become a **Withheld Result**;
2. any low-confidence group becomes a **Withheld Result**;
3. ambiguous reactions become a **Clarification Request**;
4. visually inferred possible bugs and other medium-confidence actionable groups require **Manual Review**;
5. high-confidence explicit changes and problems become Approval-eligible **Candidates**.

Approval eligibility is only a route property; it is not Approval. Candidates may be approved explicitly, and Manual Review results require either the explicit confirmation control or an edit whose rendered Issue payload differs from the persisted result. Retyping the same value, adding only surrounding whitespace, or submitting any other no-op edit does not confirm Manual Review. Clarification Requests and Withheld Results cannot be approved. Every route, reason code, normalized evidence span, and selected Evidence Frame timestamp is printed by the command and stored under `policy_result` in the Run Ledger. Frame selection uses the earliest visual keyframe, then the earliest visual-span midpoint, then the earliest supplied keyframe, and finally the earliest normalized-span midpoint. FFmpeg is invoked without a shell; a nonzero exit, missing executable, timeout, or missing/empty output records an `EvidenceFrameRecord` with `status: "failed"` and an error. That failure is visible in the top-level `evidence_frames` output but leaves the Candidate route, verified attempt, and policy result intact. A policy contradiction records `policy_failed` and emits no routes; malformed model output records `output_invalid` before policy.

Normal reruns never create a replacement interaction. They reuse an already verified result or retrieve the exact persisted interaction ID until it is reconciled. An attempt without a persisted interaction ID blocks further work. Use `--reanalyze` only when an intentionally fresh attempt is required; it appends to the Run Ledger and preserves every earlier attempt.

## Development checks

The container is the supported runtime. If Docker is unavailable but Python 3.14 and FFmpeg already exist, the same checks can be run without installing system packages:

```bash
uv sync
uv run python -m mypy feedback_triage main.py tests
uv run python -m pytest
```

The browser workflow uses the Playwright Python package in the development dependency group and injects fake Gemini/GitHub services, so it does not need credentials:

```bash
uv run pytest tests/test_browser.py -q
```

If Playwright or its Chromium executable is unavailable, the browser test reports an explicit skip. Install the executable when browser coverage is needed with `uv run playwright install chromium`.

The container smoke test builds the supported image, runs it as the current host UID/GID with read-only `/input` and `/context` mounts and a writable `/output` mount, then checks `/api/health` and `/`:

```bash
uv run pytest tests/test_container.py -q
```

It reports an explicit skip when the Docker CLI or daemon is unavailable.
