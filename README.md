# Client Feedback Triage

The tracer analyzes one owned synthetic **Feedback Recording** through Gemini agentic video, verifies the stored `AnalysisResult`, and routes its Observations through deterministic policy. It does not grant Approval or call GitHub.

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

The per-source Run Ledger is written atomically to `output/<source-sha256>/ledger.json`. Any failed trust gate records a stable failure and returns no Observation to policy or external integrations.

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

The scorer validates source identity and duration; exact semantic route, type, intent, reason, and Candidate identity; required and forbidden authored outcomes and text anchors; required client/visual evidence anchors; Evidence Span midpoint coverage within the result's own authored topic windows; required Evidence Frames; the shared navbar identity across both mention windows; hallucinated evidence; missing cases; and unexpected actionable results. Any permitted extra Withheld Result must be declared in the manifest with its reason and authored window. The command exits nonzero if the semantic score fails.

The Run Ledger stores the fingerprint and component digests for every behavior-affecting source, model, prompt, schema, context, policy, dependency/container, fixture version, ground-truth, and implementation input. A changed fingerprint cannot reuse the old ledger.

After verification, deterministic policy validates cross-field semantics and timestamps against the FFprobe duration. Ends up to 0.5 seconds beyond the duration are normalized to the duration; other timestamp violations fail the whole analysis. Observations with the same topic key merge into one result with sorted evidence and a stable `cand_` identity derived from the schema version, source hash, and topic key.

Policy routes each merged result in this order:

1. questions, decisions, and non-actionable commentary become a **Withheld Result**;
2. any low-confidence group becomes a **Withheld Result**;
3. ambiguous reactions become a **Clarification Request**;
4. visually inferred possible bugs and other medium-confidence actionable groups require **Manual Review**;
5. high-confidence explicit changes and problems become Approval-eligible **Candidates**.

Approval eligibility is only a route property; it is not Approval. Clarification Requests, Manual Review results, and Withheld Results cannot reach Approval in this run. Every route, reason code, normalized evidence span, and selected Evidence Frame timestamp is printed by the command and stored under `policy_result` in the Run Ledger. A policy contradiction records `policy_failed` and emits no routes; malformed model output records `output_invalid` before policy.

Normal reruns never create a replacement interaction. They reuse an already verified result or retrieve the exact persisted interaction ID until it is reconciled. An attempt without a persisted interaction ID blocks further work. Use `--reanalyze` only when an intentionally fresh attempt is required; it appends to the Run Ledger and preserves every earlier attempt.

## Development checks

The container is the supported runtime. If Docker is unavailable but Python 3.14 and FFmpeg already exist, the same checks can be run without installing system packages:

```bash
uv sync
uv run python -m mypy feedback_triage main.py tests
uv run python -m pytest
```
