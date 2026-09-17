# Gemini Agentic Video Contract

Research date: **2026-09-17**
Question: GitHub issue [#2, Verify the current Gemini agentic video contract](https://github.com/heykay-47/crework/issues/2)

## Decision

The proposed path is credible, but its full composition still needs one live smoke test.

- **Use** the GA Interactions API with stable model ID `gemini-3.5-flash-lite`.
- **Use** the official `google-genai` Python package at **2.21.0 or later**; prefer the current **2.24.0** for the smoke test. Interactions generally requires 2.3.0+, but 2.21.0 is the first release that explicitly added Interactions video understanding and exposed `processing_call` / `processing_result`.
- Upload a Feedback Recording with `client.files.upload`, wait for file state `ACTIVE`, then pass a video content block containing `uri`, `mime_type`, and `processing: "agentic"` before the text prompt.
- Prefer `background=True`, persist the returned interaction ID, and poll with `client.interactions.get(id=...)`. If that exact combination is rejected, retry as a new run with `stream=True` and accept output only after `interaction.completed`.
- Request JSON with the current nested `response_format` object and validate `interaction.output_text` locally with Pydantic after completion.
- Treat the run as demonstrably agentic only if the completed interaction's `steps` include both `processing_call` and `processing_result`.

Google documents every individual capability above. It does **not** publish an example proving the exact combination `gemini-3.5-flash-lite` + uploaded agentic video + background execution + structured schema. That combination remains the live test's central uncertainty.

## Support Matrix

| Combination or behavior | Status | Implementation consequence |
|---|---|---|
| `gemini-3.5-flash-lite` model ID | **Supported** | Stable GA model, released July 21, 2026; model page lists video input and structured outputs. |
| `gemini-3.5-flash-lite` + agentic video | **Supported** | The video guide and September 1 release notes explicitly include 3.5 Flash-Lite. |
| Interactions API for new projects | **Supported / preferred** | GA since June 2026. `generateContent` remains supported but is classified as legacy. |
| Files API upload -> `ACTIVE` -> video `uri` | **Supported** | Best conservative path for a local Feedback Recording. Fail if file processing enters `FAILED`; use a bounded poll. |
| Video block with `processing: "agentic"` | **Supported** | `processing` belongs inside the video content block, not at request top level. |
| Uploaded video + agentic mode + `background=True` | **Supported in principle; exact 3.5-Lite combination unverified** | The video guide recommends background execution for long/complex agentic video, and background execution supports standard Gemini models. No official example uses all three together on 3.5 Flash-Lite. |
| Background polling with `interactions.get(id=...)` | **Supported** | Persist the ID immediately. Poll through nonterminal states; consume output only at `completed`. |
| Agentic video + `stream=True` | **Supported in principle; exact 3.5-Lite combination unverified** | The video guide explicitly recommends streaming as the alternative for long requests. Require `interaction.completed`; fragments are not final output. |
| Structured JSON on 3.5 Flash-Lite | **Supported** | The model page lists structured outputs. Use nested `response_format` and local semantic validation. |
| `stream=True` + structured JSON | **Supported** | Google documents partial JSON text deltas that concatenate into the final object. Validate only after completion. |
| Agentic video + structured JSON | **Unverified as a combined path** | Both features are supported, but no first-party example combines them. Smoke-test before relying on server-side schema enforcement. |
| Agentic video + background + structured JSON on 3.5 Flash-Lite | **Unverified** | This is the required end-to-end smoke test. Fall back to prompt-requested JSON plus local validation if native schema is rejected. |
| Completed response exposes `processing_call` and `processing_result` | **Supported** | Their presence in `interaction.steps` is Google's documented proof that dynamic video navigation ran. A streamed completion event does not itself contain steps; retrieve the stored interaction after completion if needed. |
| `store=false` + background execution | **Unsupported** | Background execution requires stored interactions. Keep the default `store=true` or set it explicitly. |
| Legacy top-level `response_mime_type` or bare schema as `response_format` | **Deprecated / not the current contract** | Use `response_format={"type":"text","mime_type":"application/json","schema": ...}`. |
| Synchronous, non-streaming long agentic-video request | **Discouraged** | Google warns backend retries can outlive connection or authentication validity and surface as timeout or unexpected 401 errors. |
| Inline base64 video + agentic processing | **Unverified combination** | Interactions supports inline video and the SDK type allows `data` plus `processing`, but the agentic example uses a URI. Prefer Files API for the smoke test. |
| `generateContent` + background interaction polling | **Unsupported API mix** | Background resources and `interactions.get` belong to Interactions. GenerateContent has its own streaming path and different agentic-video field names. |

## Current Python Request Shape

This is the documented shape to test, not a record of a successful live call in this project:

```python
import time

from google import genai

client = genai.Client()
video = client.files.upload(file="path/to/feedback-recording.mp4")

while not video.state or video.state.name == "PROCESSING":
    time.sleep(2)
    video = client.files.get(name=video.name)

if not video.state or video.state.name != "ACTIVE":
    raise RuntimeError(f"Video processing ended in {video.state}")

interaction = client.interactions.create(
    model="gemini-3.5-flash-lite",
    input=[
        {
            "type": "video",
            "uri": video.uri,
            "mime_type": video.mime_type,
            "processing": "agentic",
        },
        {"type": "text", "text": "Extract timestamp-grounded observations."},
    ],
    background=True,
    response_format={
        "type": "text",
        "mime_type": "application/json",
        "schema": AnalysisResult.model_json_schema(),
    },
)

# Persist interaction.id before polling.
while interaction.status in {"queued", "in_progress"}:
    time.sleep(5)
    interaction = client.interactions.get(id=interaction.id)

if interaction.status != "completed":
    raise RuntimeError(f"Interaction ended in {interaction.status}")

step_types = {step.type for step in interaction.steps or []}
if not {"processing_call", "processing_result"}.issubset(step_types):
    raise RuntimeError("Completed interaction did not prove agentic processing")

result = AnalysisResult.model_validate_json(interaction.output_text)
```

The SDK also defines terminal or exceptional statuses including `requires_action`, `failed`, `cancelled`, `incomplete`, and `budget_exceeded`. Do not treat any of them as a completed analysis. The public polling example only loops over `in_progress`; checking `queued` as nonterminal is safer because the current SDK exposes that state.

## Video Input Contract

- The Files API flow is `client.files.upload(file=...)`, poll `client.files.get(name=...)`, then use the returned `uri` and `mime_type` in an Interactions video block.
- The guide's summary table says File API limits are 2 GB on free tier and 20 GB on paid tier.
- The same guide is internally inconsistent about inline size: its table says `<100 MB`, while prose says to use Files API when the total request exceeds 20 MB. Use Files API for this project rather than depending on either inline threshold.
- Place the text prompt after the single video block. Google recommends one video per prompt for best results.
- Agentic mode dynamically requests transcript, frame, and/or audio segments. Its navigation reasoning is billed/accounted as thought tokens and loaded media as tool-use tokens.

## Background, Polling, and Streaming

`background=True` returns an interaction ID immediately. Retrieval is:

```python
interaction = client.interactions.get(id=interaction_id)
```

Background execution requires storage; `store=true` is the default. Interactions are retained for one day on free tier and 55 days on paid tier unless a paid project configures a shorter supported window. Persist the ID locally because it is the recovery handle after a process or network interruption.

For the streaming fallback:

```python
stream = client.interactions.create(..., stream=True)
for event in stream:
    if event.event_type == "interaction.completed":
        completed = True
```

Text arrives as `step.delta` events. Do not parse or act on partial JSON. A normal stream ends with `interaction.completed` and then `[DONE]`; an `error` event or abrupt end is an incomplete analysis. Stored background interactions can also be streamed later with `interactions.get(id=..., stream=True, last_event_id=...)` and resumed from the last event ID.

## Structured Output Contract

The current Interactions form is:

```python
response_format={
    "type": "text",
    "mime_type": "application/json",
    "schema": AnalysisResult.model_json_schema(),
}
```

Gemini supports only a subset of JSON Schema, and very large or deeply nested schemas may be rejected. Structured output guarantees syntactically schema-shaped JSON, not correct business meaning. Continue to run `AnalysisResult.model_validate_json(...)` and deterministic policy checks locally.

The official structured-output page demonstrates schemas and streaming, but uses `gemini-3.8-flash`; the 3.5 Flash-Lite model page independently lists structured output support. There is no official example combining a schema with agentic video or background execution on 3.5 Flash-Lite.

## Proof of Agentic Processing

Google defines two Interactions step types:

- `processing_call`: a server request for a video segment or audio transcript, with an `id`.
- `processing_result`: the loaded result, linked by `call_id`.

They precede the final `model_output` and may be interleaved with `thought` steps. The Python SDK first exposed both types in `google-genai` 2.21.0. Merely sending `processing: "agentic"` is not sufficient evidence for the project write-up; inspect the completed interaction.

The streaming guide says the `interaction.completed` SSE payload does not include `steps`. For a streamed or background-streamed run, retrieve the stored interaction by ID after completion before checking the trace.

## Pricing and Quota Caveats

- Agentic video has no separate feature fee; Google says it uses standard Gemini API token pricing.
- For `gemini-3.5-flash-lite` standard inference, the official pricing page lists free-tier input and output as free of charge. Paid standard pricing is **$0.30 per 1M input tokens** for text/image/video/audio and **$2.50 per 1M output tokens**, including thinking tokens, as of this report.
- Free-tier content may be used to improve Google's products; paid-tier content is listed as not used for that purpose.
- Rate limits are per project, not per API key; they vary by model, tier, account status, and current capacity. RPM, TPM, and RPD can each bind. RPD resets at midnight Pacific.
- Public limits are not guaranteed. Check the project's live limits in Google AI Studio immediately before the smoke test. The previously reported `500 RPD` must remain an account-specific observation, not a universal contract.
- A failed/retried agentic analysis may still consume quota or billable tokens. Reconcile a persisted interaction ID before starting a replacement request.

## SDK Compatibility

| SDK fact | Established contract |
|---|---|
| Package | `google-genai`, imported with `from google import genai` |
| General Interactions minimum | Official overview says Python package **2.3.0+** |
| Minimum for this agentic-video shape | **2.21.0+**, released 2026-08-31, added Interactions video understanding and exposed processing steps |
| Current release on research date | **2.24.0**, published 2026-09-16 |
| Current Python support | 2.24.0 declares Python **>=3.10** and classifiers through Python 3.14 |
| Recommendation | Pin 2.24.0 for the smoke test; do not use the pre-May-2026 Interactions output/format syntax |

Context7 resolved the exact official library as [`/googleapis/python-genai`](https://github.com/googleapis/python-genai). The 2.24.0 generated types contain the video `processing` enum (`static` / `agentic`), `background`, `stream`, nested text response format, `steps`, `processing_call`, and `processing_result`.

## Required Live Smoke Test

The smoke test should record sanitized evidence for these questions:

1. Does the project's API entitlement accept `gemini-3.5-flash-lite` with an uploaded MP4 and `processing: "agentic"`?
2. Does adding `background=True` return a retrievable interaction ID and reach `completed`?
3. Does adding the nested JSON schema still complete and produce locally valid output?
4. Does the retrieved completed interaction contain at least one `processing_call` and matching `processing_result`?
5. If background fails, does a fresh `stream=True` run end with `interaction.completed`, produce valid final JSON, and expose the processing steps after retrieval?
6. What RPM/TPM/RPD limits does the actual AI Studio project show, and what usage does this representative Feedback Recording consume?

Until those pass, describe the architecture as **documented and integration-ready**, not empirically verified.

## Official Sources

| Source | Visible date | What it establishes |
|---|---|---|
| [Agentic video launch](https://blog.google/innovation-and-ai/models-and-research/gemini-models/introducing-agentic-video-in-gemini/) | Published 2026-09-01 | Launch models include 3.5 Flash-Lite; upload and YouTube availability; standard token pricing; benchmark maxima. |
| [Gemini API release notes](https://ai.google.dev/gemini-api/docs/changelog) | Entries dated 2026-07-21 and 2026-09-01 | 3.5 Flash-Lite GA date and agentic-video release across Interactions and GenerateContent. |
| [Video understanding](https://ai.google.dev/gemini-api/docs/video-understanding) | No update date visible in fetched content | Exact upload, video block, `processing: "agentic"`, long-request advice, and processing-step semantics. |
| [Gemini 3.5 Flash-Lite model page](https://ai.google.dev/gemini-api/docs/models/gemini-3.5-flash-lite) | Latest update July 2026 | Stable model ID, video input, structured outputs, limits and capabilities. |
| [Interactions API overview](https://ai.google.dev/gemini-api/docs/interactions-overview) | GA stated as June 2026 | Preferred API, supported models, storage contract, limitations, SDK minimum 2.3.0. |
| [Background execution](https://ai.google.dev/gemini-api/docs/background-execution) | No update date visible in fetched content | `background=True`, polling, resumable streaming, states, cancellation, and storage behavior. |
| [Streaming interactions](https://ai.google.dev/gemini-api/docs/streaming) | No update date visible in fetched content | SSE request and completion contract; completion events omit steps. |
| [Structured outputs](https://ai.google.dev/gemini-api/docs/structured-output) | Last updated 2026-09-02 UTC | Current nested `response_format`, schema subset, streaming structured JSON, and validation caveats. |
| [Pricing](https://ai.google.dev/gemini-api/docs/pricing) | No update date visible in fetched content | 3.5 Flash-Lite free and paid standard token pricing and data-use distinction. |
| [Rate limits](https://ai.google.dev/gemini-api/docs/rate-limits) | Last updated 2026-09-02 UTC | Per-project dynamic limits, RPM/TPM/RPD dimensions, reset time and no-guarantee caveat. |
| [`google-genai` 2.21.0 release](https://github.com/googleapis/python-genai/releases/tag/v2.21.0) | Published 2026-08-31 | First explicit Interactions video-understanding support and exposed processing-step types. |
| [`google-genai` 2.24.0 release](https://github.com/googleapis/python-genai/releases/tag/v2.24.0) | Published 2026-09-16 | Current official Python SDK release on the research date. |
| [2.24.0 request type](https://github.com/googleapis/python-genai/blob/v2.24.0/google/genai/_gaos/types/interactions/createmodelinteraction.py) | Tagged 2026-09-16 | SDK fields for background, streaming, response format, storage and model input. |
| [2.24.0 video type](https://github.com/googleapis/python-genai/blob/v2.24.0/google/genai/_gaos/types/interactions/videocontent.py) | Tagged 2026-09-16 | Video URI/data/mime fields and `static` / `agentic` processing values. |
| [2.24.0 interaction type](https://github.com/googleapis/python-genai/blob/v2.24.0/google/genai/_gaos/types/interactions/interaction.py) | Tagged 2026-09-16 | Status set, output helper, steps, usage and response fields. |

Google's performance figures are launch-post maxima, not promises for this workflow: up to 88% fewer tokens, up to 66% lower cost, and up to 7% higher quality in Google's comparisons. They should not be presented as measured Client Feedback Triage Agent results.
