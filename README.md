# Client Feedback Triage

The tracer analyzes one owned synthetic **Feedback Recording** through Gemini agentic video, verifies the stored `AnalysisResult`, and routes its Observations through deterministic policy. It does not grant Approval or call GitHub.

## Build and run

Create an owned synthetic input if needed:

```bash
./scripts/make-synthetic-recording.sh
```

Build the pinned Python 3.14/FFmpeg image:

```bash
docker build -t client-feedback-triage .
```

Run it as your host user. Credentials are injected at runtime; input, project context, and output are mounted rather than copied into the image. This tracer bullet does not consume project context yet, but mounting it establishes the boundary used by later policy work.

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
