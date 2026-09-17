# Client Feedback Triage

The first tracer bullet analyzes one owned synthetic **Feedback Recording** through Gemini agentic video and stops at a verified `AnalysisResult`. It does not evaluate policy, request Approval, or call GitHub.

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

## Development checks

The container is the supported runtime. If Docker is unavailable but Python 3.14 and FFmpeg already exist, the same checks can be run without installing system packages:

```bash
uv sync
uv run python -m mypy feedback_triage main.py tests
uv run python -m pytest
```
