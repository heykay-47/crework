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
3. `output_text` to pass the v1 Pydantic schema.

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

Approval eligibility is only a route property; it is not Approval. Candidates may be approved explicitly, and Manual Review results require confirmation or editing first. Clarification Requests and Withheld Results cannot be approved. Every route, reason code, normalized evidence span, and selected Evidence Frame timestamp is printed by the command and stored under `policy_result` in the Run Ledger. Frame selection uses the earliest visual keyframe, then the earliest visual-span midpoint, then the earliest supplied keyframe, and finally the earliest normalized-span midpoint. FFmpeg is invoked without a shell; a nonzero exit, missing executable, timeout, or missing/empty output records an `EvidenceFrameRecord` with `status: "failed"` and an error. That failure is visible in the top-level `evidence_frames` output but leaves the Candidate route, verified attempt, and policy result intact. A policy contradiction records `policy_failed` and emits no routes; malformed model output records `output_invalid` before policy.

Normal reruns never create a replacement interaction. They reuse an already verified result or retrieve the exact persisted interaction ID until it is reconciled. An attempt without a persisted interaction ID blocks further work. Use `--reanalyze` only when an intentionally fresh attempt is required; it appends to the Run Ledger and preserves every earlier attempt.

## Development checks

The container is the supported runtime. If Docker is unavailable but Python 3.14 and FFmpeg already exist, the same checks can be run without installing system packages:

```bash
uv sync
uv run python -m mypy feedback_triage main.py tests
uv run python -m pytest
```
