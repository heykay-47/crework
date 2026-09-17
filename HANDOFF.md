# HANDOFF: Crework Labs AI Engineer Intern Buildathon — Agentic Video Client Feedback Triage

## Session Metadata

- **Created/revised:** 2026-09-16 (Asia/Kolkata); revised after third-party review and first-party documentation cross-check
- **Purpose:** Transfer the complete reasoning, decisions, requirements, implementation plan, testing plan, demo plan, and submission strategy from the brainstorming session into a fresh coding/AI-agent session with minimal ambiguity.
- **Current phase:** **Research and design revised on 2026-09-16; implementation has NOT started yet.**
- **Project/repository:** Not created/selected yet.
- **Git branch:** N/A.
- **Primary assignment:** Crework Labs — AI Engineer Intern technical buildathon.
- **Recommended working title:** **Client Feedback Triage Agent**
- **Core concept:** Turn client screen-recording feedback into evidence-grounded, review-ready project tickets using Gemini's newly released Agentic Video Understanding capability.
- **Handoff format reference:** `softaworks/agent-toolkit` → `skills/session-handoff`. The handoff intentionally prioritizes current state, important context, decisions and rationale, immediate next steps, assumptions, gotchas, environment state, and related resources.
- **Security note:** No API keys, tokens, passwords, or other secrets are included in this handoff. Do not add secrets to this file.
- **For a new agent:** jump first to [Operational fast path](#operational-fast-path-for-a-new-coding-agent-read-this-before-the-rest), then [Immediate Next Steps](#immediate-next-steps--do-these-in-order); treat the rest as reference material.

---

## Current State Summary

The user is applying for the **AI Engineer Intern (Technical)** role at **Crework Labs**. Crework's assignment tests the exact loop used in the role: **track what newly shipped in frontier AI → identify a real operational pain for Crework's ICP → build the smallest real workflow that uses that new capability → document and prove the workflow clearly.**

After comparing multiple concepts, the selected direction is:

> **A one-shot CLI that receives a client screen recording, uses Gemini Agentic Video Understanding to inspect visual/spoken feedback, returns completed timestamped observations, validates and deduplicates them, gates ambiguity, extracts one useful FFmpeg evidence frame, requests approval, writes approved GitHub Issues and saves a local run log.**

This is intentionally **not** a hosted SaaS app. There should be **no custom frontend, no visual automation builder, no unnecessary backend, no database unless a genuine need appears, and no multi-agent framework for its own sake.** The working surface should be a code editor/terminal/API plus the existing issue tracker.

The project must be built and tested with the user's own/synthetic data. **The assignment explicitly says the repository/source code will not be opened:** the write-up, screenshots and GIF must independently prove the build. Therefore the workflow must be visually easy to prove and the implementation should prioritize a clean end-to-end result over breadth.

No code has been created yet. The next agent should start from **Immediate Next Steps** below.

---

# 1. What Crework Is Actually Testing

## 1.1 Role interpretation

Crework does **not** appear to be hiring an intern whose main value is wiring together Zapier/Make/n8n or building generic chatbot demos. The role description repeatedly emphasizes:

- Full-stack engineering fundamentals plus strong awareness of new AI capabilities.
- Tracking newly released models/features/connectors/agent products.
- Backtracking from the new capability into a real operational use case.
- Building **workflows, not applications**.
- Directly using frontier-model capabilities and APIs.
- Building around how a business actually works, rather than installing generic templates.
- Ownership, speed, independent thinking and execution.
- Eventual growth toward **Forward Deployed Engineer** work on real client systems.

The assignment is therefore better understood as an audition for this reasoning loop:

```text
NEW CAPABILITY
      ↓
What does this newly make practical?
      ↓
REAL OPERATIONAL PAIN
      ↓
What is the smallest useful workflow?
      ↓
DIRECT API / CUSTOM ENGINEERING
      ↓
GUARDRAILS + TESTING
      ↓
BUSINESS ACTION
      ↓
PROOF + DOCUMENTATION
```

## 1.2 What the reviewer should conclude about the candidate

The submission should make a reviewer think:

> "Give this person a new model release and an underspecified operational problem, and they can independently identify a useful workflow, scope it correctly, engineer it directly, handle model uncertainty, test it realistically, and explain exactly why the system exists."

The strongest signals to communicate are:

1. **Frontier awareness:** the candidate noticed a capability released within the required time window.
2. **Business judgment:** the candidate did not choose a novelty demo; they matched the capability to a believable pain felt by 50–300 person businesses.
3. **Technical execution:** the workflow uses model/API capabilities directly and includes real integration code.
4. **Reliability thinking:** probabilistic reasoning is surrounded by deterministic validation/policy.
5. **Scope control:** the candidate resisted unnecessary UI, infrastructure and frameworks.
6. **Ownership:** choices are made and justified rather than waiting for instructions.
7. **Testing discipline:** the candidate intentionally tests ambiguous, duplicate and failure cases rather than showing only the happy path.
8. **Communication:** the write-up explains the business problem, architecture, decisions, caveats and measured result without hype.

---

# 2. Assignment Constraints That Must Not Be Violated

The **Hard constraints** below are grounded in the assignment and JD supplied verbatim by the user. The subsequent scope-drift list is **our inferred implementation guidance**, not additional Crework rules. This distinction matters when making scope trade-offs.

## Hard constraints

- Pick a capability that shipped in the **last few weeks**.
- Pair it with an operational pain relevant to **50–300 person service/product businesses in North America or MENA**.
- Build the **smallest real version** in roughly **1–3 days**.
- Build a **workflow**, not a new SaaS product.
- Use existing tools/APIs as the surfaces where work happens.
- **No visual/node-based workflow builders:** the assignment explicitly bans Make.com, n8n and any equivalent, including builders that export code; the JD additionally names Zapier. A coded clone of a trivial no-code flow does not satisfy the JD's separate technical-differentiation test.
- Screenshots should show real working surfaces: code editor, terminal, API playground/chat interface, existing destination tool, etc.
- Do not require a paid tool for the assignment.
- Do not use techniques likely to flag/ban an account.
- Use the user's own accounts/data or synthetic data.
- **Do not rely on repository review:** the assignment says the repository/source is not required and **"we will not open one if you do."** Proof must be present in the submitted write-up/screenshots/GIF.
- Submission must include:
  - capability spotted,
  - caveat designed around,
  - pain point and why pairing makes sense,
  - step-by-step workflow documentation,
  - screenshots throughout,
  - screenshot of final result,
  - separate GIF,
  - other capability-to-pain pairings considered.
- Optional bonus: short video walkthrough.

## Inferred scope-drift signals (engineering heuristics, NOT quoted assignment requirements)

The assignment requires newsletter-sized workflow scope but does not explicitly prohibit every item below. If the build starts requiring any of these **before the core path works**, reconsider whether they are necessary:

- custom web UI,
- login/auth system,
- database,
- hosted backend,
- Docker/Kubernetes for demo purposes,
- Slack + Gmail + Notion + Linear + CRM all at once,
- vector database without a proven need,
- multi-agent orchestration purely for optics,
- complex RAG pipeline,
- production observability stack,
- cloud deployment merely to say it is deployed.

Core first. Stretch features only after the complete loop works. **Separate no-code test from tool choice:** the JD says, "If a non technical person could build it in a no code tool in an afternoon, it is not what this role is for." Demonstrate the substantive custom engineering and the measured visual-grounding problem being solved, not just avoidance of named platforms.

**Newsletter size/shape calibration:** one manually supplied MP4 -> one agentic analysis -> one policy/review gate -> GitHub Issues. A watcher, screenshot hosting, cross-video dedupe, Slack, and dashboards are outside the default MVP. Preserve the necessary reliability boundaries without turning each into a microservice or a file. The build must be simple enough to explain in a short newsletter article.

---

# 3. Selected Capability — Verified Current Facts

## Capability

**Google Gemini Agentic Video Understanding**.

## Why it qualifies

Google announced **Agentic Video Understanding on September 1, 2026**, which is within the assignment's "last few weeks" window as of this handoff date (September 16, 2026).

Official release article:

- https://blog.google/innovation-and-ai/models-and-research/gemini-models/introducing-agentic-video-in-gemini/

Official developer documentation:

- https://ai.google.dev/gemini-api/docs/video-understanding

Official Gemini API pricing:

- https://ai.google.dev/gemini-api/docs/pricing

## What is new about it

The relevant distinction is **not** simply "Gemini understands videos."

Static video processing traditionally samples the stream at a fixed rate. Google's new **agentic** processing allows supported Gemini Flash models to dynamically navigate the timeline and request the information needed for the prompt — such as relevant transcript, frames and/or audio — rather than treating every part of the video uniformly.

Google describes dynamic inspection and reports **up to 88% fewer tokens, up to 66% lower cost, and up to 7% higher quality** in the comparisons in its September 1 launch article. These are **Google-reported benchmark maxima, not measured results for this project or promises for 5–7 minute videos**. The current Interactions API exposes agentic video using `processing: "agentic"` inside the video input. As of this revision the video docs also list **Gemini 3.8 Flash** as supported; the **September 1 launch lineup** was 3.7 Flash, 3.6 Flash and 3.5 Flash-Lite. Use the release date of the *capability*, not a later model launch, as the assignment hook.

This is the technical thesis for the assignment:

> **Previously, client screen recordings were awkward operational inputs because a transcript loses visual evidence and naive fixed-frame video processing can miss brief UI states. Agentic video understanding makes it substantially more practical for a model to locate and inspect the exact moments relevant to a task.**

Do not reduce the capability explanation to "new Gemini model is smarter."

## Model choice

Recommended implementation model: **`gemini-3.5-flash-lite`**. Google currently lists Gemini 3.5 Flash-Lite as supporting Agentic Video Understanding via `processing: "agentic"`, and its standard Developer API input/output pricing is free on the Free Tier. It is also explicitly positioned as a low-latency, cost-efficient multimodal model for high-volume agentic workflows.

The user reported **500 requests/day (RPD)** for 3.5 Flash-Lite. This has **not** been independently checked in their AI Studio project. Treat 500 RPD as a user-reported account/project quota, not a universal contractual limit: Google's public rate-limit documentation says active limits vary by model/tier/account and should be checked in Google AI Studio.

The assignment's story should remain centered on the **September 1 agentic-video capability**, not on using the newest model number. Google describes **3.7 Flash** as being on the accuracy/cost frontier in its launch comparisons. We are deliberately testing **3.5 Flash-Lite first** because its standard free tier satisfies the no-paid-tool assignment; do **not** claim it is accurate enough until the evaluation confirms it. If it fails, improve the prompt/guardrails or evaluate another **free and available** supported model, with any resulting choice documented. No paid-model escalation is required or assumed.

The current Gemini API docs identify **Interactions API** as the preferred API for new builds; `generateContent` remains supported but is classified as legacy. This project will use Interactions, not claim that GenerateContent is unusable. The exact current smoke-test call is specified below; the next agent must confirm SDK/model availability against official docs and a live test without switching API families accidentally.

## Verified API shape and reliability design (implementation reference; NOT tested in user's project)

Google's current video guide shows `client.files.upload`, polling until file state is `ACTIVE`, then `client.interactions.create` with a video input whose `processing` is `"agentic"`. The docs recommend `stream=True` **or** `background=True` for long/complex requests to avoid non-streaming 401/timeouts. We choose **background + polling** for the MVP because it provides a recoverable interaction ID and a single completed final JSON to validate. Background support for the **exact model/SDK/project combination must still be smoke-tested**; if it fails, use documented `stream=True` and only accept a completed result. Official sources:

- https://ai.google.dev/gemini-api/docs/video-understanding
- https://ai.google.dev/gemini-api/docs/background-execution
- https://ai.google.dev/gemini-api/docs/streaming
- https://ai.google.dev/gemini-api/docs/structured-output
- https://ai.google.dev/gemini-api/docs/interactions-breaking-changes-may-2026

Minimal **illustrative** capability smoke test (install a current `google-genai`; must supply a synthetic local MP4 and `GEMINI_API_KEY`; do not execute writes to GitHub at this stage):

```python
import time
from google import genai

client = genai.Client()
video = client.files.upload(file="path/to/synthetic.mp4")
for _ in range(150):
    if video.state and video.state.name == "ACTIVE":
        break
    if video.state and video.state.name == "FAILED":
        raise RuntimeError("Video processing failed")
    time.sleep(2)
    video = client.files.get(name=video.name)
else:
    raise TimeoutError("Video upload not ACTIVE within polling budget")

interaction = client.interactions.create(
    model="gemini-3.5-flash-lite",
    input=[
        {
            "type": "video",
            "uri": video.uri,
            "mime_type": video.mime_type,
            "processing": "agentic",
        },
        {"type": "text", "text": "Describe one visually grounded observation with a MM:SS timestamp."},
    ],
    background=True,
)
from pathlib import Path
Path("output").mkdir(exist_ok=True)
Path("output/interaction-id.txt").write_text(interaction.id)
print("Interaction ID:", interaction.id)  # Saved locally for recovery; output/ is gitignored.
for _ in range(180):
    if interaction.status != "in_progress":
        break
    time.sleep(5)
    interaction = client.interactions.get(id=interaction.id)
else:
    raise TimeoutError("Interaction still running; persist ID and resume polling")

if interaction.status != "completed":
    raise RuntimeError(f"Analysis not completed: {interaction.status}")
print(interaction.output_text)
```

After the smoke test, request structured output using the **current** Interactions `response_format={"type":"text", "mime_type":"application/json", "schema": AnalysisResult.model_json_schema()}` as documented; locally run `AnalysisResult.model_validate_json(...)`. The exact combination of **agentic video + background + schema + 3.5 Flash-Lite** is a live integration test, not something this planning session executed. Google has changed earlier Interactions syntax, so **do not use the old top-level `response_mime_type` or pass the JSON schema directly as `response_format`**. If the combination is rejected by API/SDK, simplify to an agentic video request with JSON prompt + local Pydantic validation; do not pretend native schema constraints worked.

**Failure contract:** save run/video hash and interaction ID before/after starting work as appropriate. On polling/network interruption, retrieve that **same** interaction ID first; do not blindly start another billable/quota-consuming analysis. If its status is `failed`, `cancelled` or `incomplete`, or an ordinary stream ends without `interaction.completed`, mark run `analysis_incomplete`, preserve progress/event logs as **diagnostic only**, and create **zero** GitHub tickets. Never turn mid-stream text fragments into issue candidates or combine fragments from separate attempts. A bounded retry may reanalyze the same video, but its entire final output must pass schema/timestamp/policy validation before review or issue writes. If streaming is chosen instead, handle SSE completion explicitly; resumption via `last_event_id` is documented for background interactions. When a request fails after unknown execution status, query the saved interaction ID before retrying. Testing must inject failure and prove no writes occur.

## Caveat to acknowledge

Agentic video improves **perception/retrieval**, but it does not guarantee correct interpretation of **client intent**.

A model might correctly see what happened yet still be uncertain whether:

- the client is explicitly requesting a change,
- the client is merely thinking aloud,
- a visual anomaly is actually a bug,
- two comments refer to the same underlying issue,
- enough information exists to write acceptance criteria.

Therefore the workflow must deliberately separate:

```text
PERCEPTION
What happened / what was shown?
        ↓
INTERPRETATION
What does the client appear to mean or request?
        ↓
EXECUTION POLICY
Is this safe/clear enough to create as real work?
```

**Model reasoning handles perception/interpretation. Deterministic code controls execution.**

That sentence captures one of the most important engineering ideas in the project.

---

# 4. Selected Business Pain

## Target ICP slice

The workflow is designed for a believable subset of Crework's ICP:

- software development agencies,
- design/product agencies,
- outsourced development teams,
- SaaS/product teams,
- other service businesses where clients review digital deliverables asynchronously.

Company size assumption: **50–300 employees**.

## Operational pain

Clients frequently send Loom-like screen recordings containing mixed feedback:

- bugs,
- copy/design changes,
- feature requests,
- questions,
- decisions,
- vague reactions,
- repeated mentions of the same issue,
- visual behavior they point at without explaining precisely.

An account manager, PM, designer or engineer then needs to:

1. watch the entire recording,
2. understand both speech and on-screen behavior,
3. separate actionable requests from commentary,
4. find the relevant timestamps,
5. avoid creating duplicate tasks,
6. infer which project/component each item belongs to,
7. write clean tickets,
8. ask clarification where intent is unclear.

That translation step is repetitive operational work and delays the feedback-to-execution loop.

## Why the capability/pain match is strong

A transcript-only system can capture what the client said but may miss **what they pointed at, clicked, hovered over, opened, or demonstrated visually**.

The new capability is therefore directly useful rather than decorative:

```text
Client video = audio + transcript + visual UI state
                         ↓
Agentic video can inspect the relevant moments
                         ↓
Mixed feedback becomes structured work candidates
```

The workflow does not merely summarize content. It turns unstructured multimodal client input into operational artifacts.

---

# 5. Product Definition

## Recommended name

Use a descriptive name, not an overbranded startup name.

Preferred:

**Client Feedback Triage Agent**

Alternative article-style title:

**"Clients Send 10-Minute Screen Recordings. This Workflow Turns Them Into Review-Ready Tickets."**

## One-sentence product definition

> A one-shot Python workflow takes a local client screen recording, uses Gemini Agentic Video Understanding to extract timestamp-grounded work candidates, validates and deduplicates them, captures one local evidence frame, routes uncertainty for review, creates approved GitHub Issues, and logs what it changed.

## Explicit non-goals

The MVP is **not**:

- a generic meeting summarizer,
- a video Q&A chatbot,
- a project-management platform,
- a Loom clone,
- a hosted multi-tenant service,
- an autonomous PM replacement,
- a broad "AI agency operations" platform.

---

# 6. Architecture Overview

## End-to-end flow

```text
┌──────────────────────────────┐
│  client-feedback-001.mp4     │
│  passed to one-shot CLI      │
└──────────────┬───────────────┘
               │
               ▼
┌──────────────────────────────┐
│ Input / one-shot CLI         │
│ - validates file type        │
│ - creates run ID             │
│ - calculates source hash     │
└──────────────┬───────────────┘
               │
               ▼
┌──────────────────────────────┐
│ Gemini Agentic Video         │
│ Understanding               │
│ Interactions API, agentic    │
│ background + poll/recover    │
│ + project context + rules    │
└──────────────┬───────────────┘
               │
               ▼
┌──────────────────────────────┐
│ Completed validated result   │
│ no incomplete/partial writes │
│ bugs / changes / questions   │
│ timestamps / evidence        │
└──────────────┬───────────────┘
               │
               ▼
┌──────────────────────────────┐
│ Deterministic policy layer   │
│ - Pydantic validation        │
│ - timestamp checks           │
│ - dedupe rules               │
│ - ambiguity gating           │
│ - execution eligibility      │
└──────────────┬───────────────┘
               │
               ▼
┌──────────────────────────────┐
│ FFmpeg evidence extraction   │
│ frame/thumbnail around       │
│ relevant timestamp           │
└──────────────┬───────────────┘
               │
               ▼
┌──────────────────────────────┐
│ Terminal review              │
│ proposed actions displayed   │
│ approve selected writes      │
└──────────────┬───────────────┘
               │
               ▼
┌──────────────────────────────┐
│ GitHub Issues REST API       │
│ create approved tickets      │
└──────────────┬───────────────┘
               │
               ▼
┌──────────────────────────────┐
│ local run JSON / audit log   │
│ what model proposed +        │
│ what human approved + IDs    │
└──────────────────────────────┘
```

## Why this is custom engineering and not a no-code automation

The trigger (one CLI command on an MP4) is not the differentiator. The substantive custom engineering is in:

- direct use of a newly released multimodal model capability,
- structured schema enforcement,
- prompt/context design,
- evidence grounding to timestamps,
- classification of intent,
- duplicate handling,
- policy separation between model judgment and system execution,
- FFmpeg evidence extraction,
- safe external API writes,
- idempotency/auditability.

The JD's actual bar is whether a nontechnical person could recreate the **substance** in a no-code tool in an afternoon. Simply putting an LLM call inside Python would fail that bar. A visual builder *can* call a video model or write a timestamp; do not claim categorical impossibility. Demonstrate the integrated, tested behavior: separate observations from client requests, reject incomplete outputs, check timestamps against actual video duration, collapse duplicates deterministically, gate ambiguity and review, make repeat runs safe, and surface visual evidence. Keep this focused on one video and one destination. A reviewer should not see a generic "trigger → LLM → create issue" flow.

---

# 7. Proposed Codebase

Keep the codebase small and understandable.

```text
client-feedback-triage/
├── HANDOFF.md                   # this planning reference (copy here if useful)
├── README.md                    # short setup + reproducible run commands
├── .env.example                 # variable names only
├── .gitignore
├── requirements.txt
├── config.yaml                  # tiny project-specific rules; YAML optional
├── main.py                      # one-shot CLI, context loading, terminal approval, audit/run persistence
├── gemini_video.py              # upload, agentic Interactions call, poll/resume, final output
├── models.py                    # Pydantic data contracts
├── policy.py                    # validation, dedupe, ambiguity and idempotency rules
├── integrations.py              # minimal FFmpeg frame extraction + GitHub Issues API functions
├── project-context/
│   └── brief.md                 # one realistic demo project; no RAG
├── inbox/                       # owned/synthetic local recording
└── output/                      # run JSON and local evidence frame; gitignored
```


This is a **maximum suggested starting structure, not a required number of files**. Start with `main.py` + `gemini_video.py` and split out only the tiny modules needed to test the core safely. Do **not** create `watcher.py`, `context.py`, `dedupe.py`, `review.py`, or `audit.py` as separate components by default. The demo is a one-shot command, not a persistent background server. No Docker, DB or hosted UI is needed.

## Recommended minimal dependencies

Prefer a small dependency footprint, approximately:

- `google-genai` — Gemini API/Files/Interactions.
- `pydantic` — structured validation.
- `python-dotenv` — environment variables.
- `httpx` or `requests` — GitHub REST calls.
- `PyYAML` — config loading if YAML is used.

System dependency:

- `ffmpeg`

Avoid LangChain/CrewAI/AutoGen unless a concrete need emerges. None is currently justified.

---

# 8. Planned Data Model

The exact schema can evolve, but preserve the distinction between model observations and executable tickets.

## Suggested Pydantic models

```python
class Evidence(BaseModel):
    start_seconds: float
    end_seconds: float
    client_quote: str | None = None
    visual_observation: str | None = None

class IssueCandidate(BaseModel):
    type: Literal[
        "bug",
        "change_request",
        "feature_request",
        "question",
        "decision",
        "clarification"
    ]
    title: str
    component: str | None = None
    summary: str
    requested_outcome: str | None = None
    acceptance_criteria: list[str]
    evidence: Evidence
    confidence: Literal["high", "medium", "low"]
    needs_clarification: bool
    visually_inferred: bool
    duplicate_group: str | None = None
    rationale: str

class AnalysisResult(BaseModel):
    video_summary: str
    candidates: list[IssueCandidate]
```

Do **not** treat model-provided confidence as a calibrated statistical probability. Prefer categorical confidence and deterministic policy.

## Important semantic distinction

The model may return candidates that should never become tickets automatically, e.g.:

```json
{
  "type": "clarification",
  "title": "Pricing section feedback is underspecified",
  "needs_clarification": true,
  "confidence": "high"
}
```

High confidence can mean "the model is confident the request is ambiguous," not "safe to execute."

---

# 9. Project Context Design

The workflow should demonstrate that it understands a specific business/project process instead of behaving like a generic summarizer.

Use lightweight local context rather than overbuilding RAG.

## `config.yaml` concept

```yaml
project:
  name: Demo Website Redesign

# Observation categories emitted by the model, not all GitHub ticket types.
# `decision` and `question` are recorded but excluded from implementation-ticket creation by policy.
observation_types:
  - bug
  - change_request
  - feature_request
  - question
  - decision
  - clarification

components:
  - hero
  - navbar
  - pricing
  - contact
  - footer
  - mobile

execution_policy:
  require_human_approval: true
  never_create_when_needs_clarification: true
  require_review_when_visually_inferred: true

rules:
  - Do not turn general commentary into a task.
  - Do not invent requirements that were not stated or demonstrated.
  - Merge repeated mentions of the same underlying issue.
  - Keep questions separate from implementation tasks.
  - Ground every candidate to a timestamp range.
```

## `project-context/brief.md`

Keep this short: one page describing what the demo project is, major screens/components and relevant requirements.

## `project-context/project-rules.md`

Put business/process conventions here, e.g.:

- what counts as bug vs change request,
- naming conventions,
- when clarification is required,
- what acceptance criteria are allowed to infer.

Do not build embeddings/vector search for two markdown files.

---

# 10. Prompt / Analysis Requirements

The prompt is a major piece of the engineering. It should explicitly prevent common failure modes.

## Model responsibilities

Ask the model to:

1. inspect both audio/transcript and visual state,
2. identify only actionable or operationally relevant observations,
3. classify each observation,
4. ground every item to start/end timestamps,
5. preserve a short client quote when useful,
6. describe visual evidence separately from spoken intent,
7. distinguish explicit requests from inferred problems,
8. merge repeated mentions of the same underlying issue,
9. identify ambiguity instead of guessing,
10. avoid inventing acceptance criteria or expected outcomes.

## Prompt rules worth making explicit

- General commentary is **not** automatically a task.
- "I don't like this" without a specified desired outcome should become a clarification, not a design task.
- A question is not an implementation request.
- If the client repeats the same bug later, represent it once and preserve multiple evidence moments if useful.
- If a visual malfunction is observed but the client never explicitly calls it a bug, mark `visually_inferred=true`.
- If there is no grounded timestamp, the item is invalid.
- Acceptance criteria may be written only from stated/demonstrated facts. Do not introduce arbitrary product decisions.

## Structured output

Use Interactions `response_format={"type":"text", "mime_type":"application/json", "schema": AnalysisResult.model_json_schema()}` as the documented structured-output shape and **verify its compatibility in a real call** with agentic video, `background=True`, and Gemini 3.5 Flash-Lite. Regardless of server-side schema behavior, validate `interaction.output_text` with `AnalysisResult.model_validate_json(...)` only after `status == "completed"`. If native schema is rejected, request JSON in the prompt and validate locally; do not use stale `response_mime_type` syntax. Preserve the failed API response (sanitized) for the write-up.

The next agent must consult current docs rather than assume old SDK method names.

---

# 11. Deterministic Policy Layer

This layer is a key selection signal. Do not let the model directly create issues.

Possible policy logic:

```python
if candidate.needs_clarification:
    action = "clarification_only"

elif candidate.type in {"question", "decision"}:
    action = "do_not_create_implementation_ticket"

elif candidate.visually_inferred:
    action = "manual_review_required"

elif candidate.confidence != "high":
    action = "manual_review_required"

else:
    action = "eligible_for_approval"
```

For the buildathon MVP, **all external GitHub writes can still require final human approval**, even when an item is eligible. This is defensible because the workflow is being tested on ambiguous client intent and avoids accidental commitments.

In the write-up explain that a production deployment could later auto-execute carefully evaluated high-confidence categories while retaining review for ambiguous cases.

## Timestamp validation

At minimum:

- `start_seconds >= 0`
- `end_seconds >= start_seconds`
- `end_seconds <= video_duration + small_tolerance`

Get duration with `ffprobe` rather than trusting the model.

## Idempotency

Avoid creating duplicate GitHub Issues if the same video is processed twice.

Simplest acceptable strategy:

- Compute SHA-256 of the source video and persist a run record (`output/<hash>.json` is fine).
- Persist `interaction_id` and status so API failures can resume the same server-side run.
- Persist successful GitHub issue IDs **immediately after each confirmed write**. Before retrying an uncertain GitHub write, search for an issue carrying a stable source-hash + candidate/group marker, then reconcile; avoid duplicating tickets after client-side timeouts.
- On rerun, skip already successful candidates by default. `--force` must not bypass duplicate protection silently.

For a 1–3 day demo, sequential writes plus a local ledger and a stable marker in issue bodies are enough; a database/queue is unnecessary.

---

# 12. Duplicate Handling

Do not overengineer semantic dedupe.

Recommended MVP approach:

1. Ask the model to group repeated mentions inside the same video.
2. Validate group IDs and collapse them deterministically.
3. Preserve multiple evidence timestamps if available.

Optional stretch:

- compare candidate title/component against existing open GitHub Issues and warn about possible duplicates.

Do **not** make cross-project semantic search a core requirement.

---

# 13. FFmpeg Evidence Extraction

Evidence extraction is one of the highest-value additions because it makes the final output visibly grounded.

Given a candidate range such as `48.2–63.1s`, choose a sensible timestamp near the visually relevant moment (initially midpoint is acceptable; model can optionally provide `keyframe_seconds`).

Example command concept:

```bash
ffmpeg -y -ss 52.0 -i client-feedback.mp4 -frames:v 1 output/evidence/navbar-bug.png
```

Use `ffprobe` to inspect video duration.

## Core vs stretch

**Core:** extract **one demonstrably useful frame** locally and reference the timestamp in the GitHub Issue. A GitHub-hosted screenshot attachment is not required for the minimal first version; show the local image separately in screenshots/GIF.

**Stretch only if core is stable:** upload the image into a demo repository path through the GitHub Contents API, then embed the repository-hosted image in the issue body. GitHub issue attachments do not have a trivial generic REST upload endpoint, so do not burn hours trying to mimic browser attachment uploads.

The assignment can still demonstrate evidence grounding even if the issue contains the timestamp and the screenshot is shown separately in the write-up/GIF.

---

# 14. GitHub Issues Integration

Use GitHub Issues as the execution target because it is free, easy to demonstrate, and has a straightforward REST API.

This is a **demo issue tracker adapter**, not a claim that Crework clients universally use GitHub.

In the write-up, mention that the final adapter can be swapped for Linear/Jira/ClickUp in production.

## Issue body structure

Recommended output:

```markdown
## Summary
The mobile navigation drawer overlaps the company logo at the mobile breakpoint.

## Type
Bug

## Component
Navbar

## Source evidence
- Video: `client-feedback-001.mp4`
- Timestamp: `00:48–01:03`
- Client wording: "This happens on my phone."

## Observed behavior
The open navigation drawer obscures part of the logo.

## Requested / expected outcome
Keep the logo unobstructed while the mobile navigation is open.

## Acceptance criteria
- Logo remains unobstructed at the demonstrated mobile viewport.
- Navigation remains usable.

## AI handling
- Confidence: High
- Visually inferred: No
- Human approved: Yes
```

Do not fabricate acceptance criteria beyond the recording/project rules.

## Labels

Keep it small, e.g.:

- `ai-triaged`
- `bug`
- `change-request`
- `needs-clarification`

Labels are optional if creating them becomes distracting.

---

# 15. Terminal Review Experience

The terminal output is part of the product for the assignment because it will appear in screenshots/GIFs.

Aim for something clear, not elaborate:

```text
CLIENT FEEDBACK ANALYSIS
────────────────────────────────────────
Source: client-feedback-001.mp4

4 observations found

[1] BUG · HIGH
    Mobile navigation overlaps logo
    Evidence: 00:48–01:03
    Component: navbar
    Action: eligible for approval

[2] CHANGE REQUEST · HIGH
    Hero CTA → "Schedule a Call"
    Evidence: 00:18–00:29
    Component: hero
    Action: eligible for approval

[3] BUG · MEDIUM · VISUALLY INFERRED
    Contact button appears unresponsive
    Evidence: 04:02–04:13
    Action: manual review required

[4] CLARIFICATION
    Pricing section feedback lacks desired outcome
    Evidence: 02:31–02:45
    Action: do not create implementation ticket

Create eligible GitHub issues? [y/N]
```

Do not spend significant time styling the terminal.

---

# 16. Audit Log

Crework's positioning emphasizes controlled access and logged actions. A simple local audit log is enough for the assignment.

Suggested run record:

```json
{
  "run_id": "2026-09-16T12-30-02Z_client-feedback-001",
  "source_video": "client-feedback-001.mp4",
  "source_sha256": "...",
  "model": "gemini-3.5-flash-lite",
  "processing": "agentic",
  "candidates_found": 4,
  "eligible_for_approval": 2,
  "manual_review_required": 1,
  "clarifications": 1,
  "human_approved": 2,
  "github_issue_numbers": [12, 13]
}
```

Never store API keys/tokens in logs.

---

# 17. Demo Recording Design

Do not test against a random video. Create a deliberately designed synthetic client recording that tests the claims.

## Recommended length

Approximately **5–7 minutes**.

Reasoning:

- long enough to feel like real asynchronous client feedback,
- long enough for selective timeline navigation to make conceptual sense,
- still quick to record and rerun during development.

## Suggested source

Use a demo website or the user's portfolio. Do not use confidential third-party client material.

## Include these cases intentionally

### Case A — explicit change request

At ~`00:30`:

> "Can we change this button from 'Get Started' to 'Schedule a Call'?"

Expected:

- type: `change_request`
- high confidence
- explicit request
- hero/CTA component
- ticket eligible

### Case B — clear visual bug + spoken confirmation

At ~`01:20`:

Switch to mobile/responsive view and demonstrate navigation covering the logo.

Say something like:

> "On my phone the menu covers the logo like this."

Expected:

- type: `bug`
- visual + verbal evidence
- timestamp
- ticket eligible

### Case C — vague reaction that must NOT become a fabricated task

At ~`02:30`:

> "I'm not really sure about this pricing section. Something just feels off. Maybe we should revisit it."

Expected:

- clarification required
- no invented redesign instruction
- no implementation ticket

### Case D — duplicate mention

At ~`03:30`:

Mention the navbar issue again on another page.

Expected:

- merge/group with Case B rather than blindly create a second ticket
- preserve additional evidence if useful

### Case E — question, not work request

At ~`04:10`:

> "Do we know whether analytics is already tracking this button?"

Expected:

- `question`
- not converted into a feature request unless explicit project rules say otherwise

### Case F — visual-only anomaly

At ~`05:00`:

Click a button that visibly does nothing or demonstrate an obvious UI glitch without explicitly calling it a bug.

Expected:

- possible bug candidate
- `visually_inferred=true`
- manual review required

This case is especially useful for demonstrating why video understanding adds value beyond transcription.

---

# 18. Test Plan

Testing must be deliberate and documented. Do not rely only on "it ran once."

## Functional test matrix

| Case | Expected system behavior |
|---|---|
| Explicit spoken change | Extract as change request with timestamp |
| Visual + spoken bug | Extract bug and describe visual state |
| Vague dislike | Flag clarification; do not invent task |
| Duplicate mention | Merge/group, not duplicate issue |
| Question | Keep as question, not implementation work |
| Visual-only anomaly | Mark visually inferred and require review |
| Invalid/missing timestamp | Reject or route to review |
| Malformed model JSON | Validation failure; no GitHub write; bounded retry if useful |
| Agentic request times out / disconnects during processing | Save/retrieve interaction ID if available; resume polling, otherwise mark incomplete; no GitHub writes |
| Stream fails mid-output / no `interaction.completed` | Preserve event log only for diagnosis; never use partial JSON as candidates; mark incomplete and safely retry whole analysis if appropriate |
| Interaction finishes `failed`/`cancelled`/`incomplete` | Report failure, no tickets, do not mistake status for success |
| GitHub write response lost after request may have succeeded | Reconcile by stable issue marker/ledger before retry; no duplicate issue |
| Project RPM/TPM/RPD limit hit (429) | Respect retry guidance/backoff; stop safely and preserve progress; never switch to paid tier silently |
| GitHub API failure | Do not falsely mark action successful |
| Same video processed twice | Skip/warn due to idempotency |
| FFmpeg extraction failure | Candidate remains, evidence failure logged |

## Evaluation against ground truth

Before running the workflow, create a tiny ground-truth file describing what the test video actually contains.

Example:

```yaml
expected:
  actionable:
    - hero CTA copy change
    - mobile navbar overlap
  clarification:
    - pricing section vague feedback
  question:
    - analytics tracking question
  duplicate:
    - second navbar mention
  visual_inference:
    - unresponsive contact button
```

Then compare actual extraction against expected.

Do not claim formal benchmark accuracy from one synthetic video. Report it transparently as a **demo test**.

## Time measurement

Manually process the same video once and record:

- time to watch,
- time to write tickets.

Then measure workflow:

- model processing time,
- human review time,
- issue-creation time.

Report the real numbers even if the savings are modest.

Never invent ROI/time-saving statistics.

---

# 19. Screenshot Plan

Capture screenshots while building. Do not rely on recreating all proof at the end.

Recommended evidence set:

1. **New capability proof** — official Google announcement/docs showing agentic video capability and date.
2. **Input** — `inbox/` containing the synthetic client MP4.
3. **Code/API configuration** — a concise editor screenshot showing `processing="agentic"` and the structured analysis call.
4. **Structured result** — terminal or saved JSON showing candidates with timestamps/classification.
5. **Guardrail** — terminal showing vague feedback routed to clarification and visually inferred issue routed to review.
6. **Evidence extraction** — relevant screenshot/frame produced by FFmpeg.
7. **Approval** — terminal approval step.
8. **Final result** — GitHub Issues list showing newly created issues.
9. **One issue opened** — clean ticket with source timestamp and AI-handling metadata.
10. **Audit proof** — run log with actual created issue IDs.
11. **Evaluation result** — simple comparison of expected vs detected items/time if useful.

Avoid screenshots containing API keys, tokens, email addresses or other sensitive data.

---

# 20. GIF Plan

The GIF should prove the loop in roughly 15–30 seconds, not serve as a tutorial.

Ideal sequence:

```text
Terminal already running
        ↓
Drop client-feedback-001.mp4 into inbox/
        ↓
Detected → uploading → analyzing
        ↓
Candidate summary appears
        ↓
Ambiguous item visibly held back
        ↓
Approve eligible items
        ↓
GitHub issue creation succeeds
        ↓
Refresh GitHub Issues page
        ↓
New issues visible
```

If analysis takes too long for a compelling GIF, record the real process and edit dead waiting time out while clearly noting that the GIF is shortened. Do not fake results.

---

# 21. Submission Write-up Strategy

The assignment is not asking for source code. The write-up must carry the proof and reasoning.

Recommended narrative:

## 21.1 Opening

Start with the operational problem rather than a generic AI introduction.

Concept:

> A five-minute client screen recording can contain a bug, two requested changes, a question and several minutes of commentary. The recording is useful context, but somebody still has to turn it into work. I wanted to test whether a video capability released this month could remove most of that translation step without letting ambiguous AI interpretation silently enter the backlog.

## 21.2 Capability spotted

Explain:

- release date,
- what agentic video does differently from static processing,
- what specifically changed in feasibility,
- official source.

Use **at most one sentence** on Google's reported benchmark results: *up to 88% token reduction, 66% cost reduction, 7% quality lift* across its comparisons, citing the September 1 launch article; do not imply these apply to our synthetic 5–7 minute recording. Explain why active moment inspection matters more than benchmark marketing.

Model rationale: Google highlighted 3.7 Flash at its accuracy/cost frontier. **We chose 3.5 Flash-Lite first because its standard free tier fits the assignment and it supports the same new capability. Whether it is sufficiently accurate for our test is an evaluation question, not a claim made in advance.** If evaluation fails, document the failure and choose a feasible free alternative or simplify scope; do not silently use a paid model.

## 21.3 Pain point

Describe who feels it and what the manual process looks like.

Be concrete:

> PM/account manager watches client recordings and manually translates visual/spoken feedback into tickets.

Avoid generic statements like "businesses waste time managing data."

## 21.4 Why the pairing makes sense

The key sentence:

> The model must understand both what the client says and what they are showing; transcript-only automation loses the visual state, which is often the most important part of bug/design feedback.

## 21.5 Architecture

Show **one compact diagram** with the actual workflow: manually supplied MP4 -> agentic analysis (recoverable execution) -> validated observations/policy -> one evidence frame -> manual approval -> GitHub Issues. Explicitly state the one-shot CLI and minimal project context are deliberate, **newsletter-sized** scope choices. The video-specific evidence and safe-write checks add code because they solve real failure modes, not because the system is an app. Do not call the number of Python modules itself a business benefit.

## 21.6 Step-by-step build

Explain each stage in the same spirit as Crework's newsletter:

1. realistic input,
2. project context,
3. agentic video call,
4. structured candidate extraction,
5. deterministic validation/policy,
6. evidence extraction,
7. review,
8. GitHub action,
9. audit log.

Include real screenshots at the relevant point. In a short paragraph, answer the JD's literal differentiation test: "If a non technical person could build it in a no code tool in an afternoon, it is not what this role is for." Explain **specific demonstrated behavior**, not that no-code tools are universally incapable: visual-versus-spoken evidence separation, video-duration/timestamp checking, duplicate collapse, incomplete-response gating, replay-safe GitHub writes, and review for ambiguous client intent. If those pieces are not truly implemented/tested, do not claim them; trim the description to what worked.

## 21.7 Caveat designed around

Use this explicitly:

> Agentic video improves the model's ability to find and inspect relevant moments, but better perception does not eliminate ambiguity about client intent. I therefore separated model interpretation from execution: Gemini proposes grounded work candidates; deterministic policy and human review decide whether anything enters the backlog.

## 21.8 Testing / what failed

Mention real mistakes if they occurred.

Examples:

- approximate timestamp was off by several seconds,
- model initially turned vague feedback into a task,
- duplicate grouping failed until prompt was tightened,
- evidence midpoint selected an unhelpful frame,
- schema output occasionally needed retry.

A real failure plus a sensible fix is useful evidence, but do not fabricate one. Include any actual interrupted/failed interaction and recovery behavior **only if tested**; do not copy a community anecdote as though it happened locally.

## 21.9 Measured result

Use actual manual vs workflow timing and ground-truth comparison.

Do not extrapolate one demo into yearly dollar savings unless the assumptions are clearly labeled and useful.

## 21.10 What would change in production

Short, pragmatic list only:

- ingest from Drive/Loom/approved source rather than local folder,
- adapt output to Jira/Linear/ClickUp,
- evaluate on many real anonymized videos,
- tune policies per client,
- secure credential storage,
- controlled auto-execution for proven categories,
- production audit/observability.

Do not imply the buildathon MVP is production-ready.

---

# 22. Other Capability-to-Pain Pairings to Mention

The assignment explicitly asks for alternatives considered. Keep this section short in the final submission; it proves breadth without distracting from the chosen build.

## Alternative A — Screen recording → SOP

- **Capability:** Agentic video understanding.
- **Pain:** tribal knowledge/process demonstrations never become documentation.
- **Workflow:** employee records procedure once → model identifies steps/warnings/decision points → generates timestamped SOP/checklist in existing docs system.
- **Why not selected:** useful, but ends mainly in documentation; selected concept drives a clearer operational action (backlog creation) and better demonstrates execution policy.

## Alternative B — Field/site walkthrough → scope draft

- **Capability:** Agentic video understanding.
- **Pain:** home-services/field-service teams manually translate walkthrough videos into job scopes/estimator notes.
- **Why interesting:** very strong service-delivery relevance.
- **Why not selected:** domain mistakes can have higher consequences; obtaining a convincing test video and domain ground truth in 1–3 days is harder.

## Alternative C — RFP/proposal orchestration with new agent tooling

- **Capability:** a newly released agent runtime/API could coordinate research, scope extraction and drafting.
- **Pain:** proposals take days after a good sales conversation.
- **Why not selected:** easier to drift into generic "agent framework demo" and larger orchestration; the capability-to-pain link is less visually obvious than new video perception → operational input.

## Alternative D — Real-time voice → CRM/follow-up

- **Capability:** newly improved live audio/voice agents.
- **Pain:** sales reps delay CRM updates and follow-ups.
- **Why not selected:** telephony/realtime integration raises demo complexity and cost/account-risk constraints.

Do not include unverified model/product release claims in the final submission. Re-check all alternative capability dates before naming specific products.

---

# 23. Decisions Made

| Decision | Alternatives considered | Rationale |
|---|---|---|
| Build client video → tickets | SOP generator, site walkthrough → scope, RFP agent, voice CRM agent | Best combination of newly unlocked capability, clear ICP pain, engineering depth, low demo risk and strong GIF/screenshots |
| Use Gemini Agentic Video Understanding | Generic video model/static sampling/transcript-only | Released Sep 1; directly addresses combined visual/audio feedback and selective moment inspection |
| Use Python | Full-stack web app/JS service | Fastest direct workflow implementation, good API/FFmpeg ecosystem, no need for frontend |
| Use GitHub Issues | Jira/Linear/custom dashboard | Free, easy API, easy visual proof; adapter can be swapped later |
| Use local folder input first | Loom API/webhook/Drive integration | Keeps scope on new capability; avoids spending assignment time on ingestion plumbing |
| Use Pydantic validation | Trust raw model JSON | Shows deterministic control around probabilistic output |
| Require approval before external writes in MVP | Fully autonomous writes | Client intent can be ambiguous; protects against turning uncertain interpretation into business commitments |
| Extract evidence with FFmpeg | Text-only tickets | Makes grounding visible and proves timestamps are actionable |
| Store JSON/JSONL audit locally | Database/observability stack | Enough to prove traceability without building infrastructure |
| Lightweight context files | RAG/vector DB | Tiny project context does not justify retrieval infrastructure |
| Avoid multi-agent framework | CrewAI/LangChain/complex orchestration | No current need; would add complexity without increasing business value |

---

# 24. Work Completed in This Session

## Completed reasoning/research

- [x] Parsed the buildathon assignment requirements.
- [x] Parsed the AI Engineer Intern JD and the role's intended working style.
- [x] Interpreted the evaluation as capability awareness + business pain + engineering + ownership + scope + reliability.
- [x] Compared several candidate workflow concepts.
- [x] Selected **Client Feedback Triage Agent** as the primary concept.
- [x] Verified Google's September 1, 2026 Agentic Video Understanding announcement against official Google sources.
- [x] Verified current Gemini video-understanding documentation and agentic processing behavior.
- [x] Verified that Gemini 3.5 Flash-Lite supports Agentic Video Understanding and currently has free-tier standard input/output pricing.
- [x] User selected Gemini 3.5 Flash-Lite and reports 500 RPD; actual quota has **not** been verified in their Google AI Studio project. Public rate limits vary and must be checked before implementation.
- [x] Verified Crework's public positioning around agentic systems operating on existing tools, workflow redesign, human steering and logged actions.
- [x] Defined the high-level architecture and deterministic guardrails.
- [x] Designed the synthetic test-video cases.
- [x] Defined screenshot/GIF strategy.
- [x] Defined write-up narrative and what the reviewer should infer.
- [x] Reviewed the `softaworks/agent-toolkit` session-handoff guidance and used its recommended handoff structure/priorities.

## Files modified/created

- `HANDOFF.md` — this file only.

No project code exists yet.

---

# 25. Pending Work

## Immediate Next Steps — Do These in Order

### 1. Create the project locally

Create a new folder/repository, for example:

```bash
mkdir client-feedback-triage
cd client-feedback-triage
git init
python -m venv .venv
```

Use the user's normal Python environment conventions if already established.

### 2. Verify prerequisites

Check:

```bash
python --version
ffmpeg -version
ffprobe -version
```

If FFmpeg is missing, install it using the platform-appropriate package manager.

### 3. Create `.gitignore` and `.env.example`

`.env.example` should contain names only:

```text
GEMINI_API_KEY=
GITHUB_TOKEN=
GITHUB_REPO=
```

Never commit real values.

`.gitignore` should include at least:

```text
.env
.venv/
__pycache__/
inbox/*.mp4
processed/*.mp4
output/
```

If demo evidence needs to be preserved for submission, selectively keep sanitized assets outside ignored paths.

### 4. Prove the Gemini API capability in isolation

Start from the **Interactions API smoke test in section 3** (background+poll; persist ID). Do not mix legacy GenerateContent request parameters into it. Before writing the whole architecture:

- record or use a tiny test MP4,
- upload it using the current official Gemini SDK docs,
- invoke agentic processing,
- ask for one timestamp-grounded observation,
- print the completed raw result and inspect `interaction.steps` for `processing_call` / `processing_result` to establish that agentic mode actually ran. If the API returns no such evidence, do not assume enabling a flag proves agentic behavior.

**Do not build watcher/GitHub integration until this works.**

### 5. Add structured analysis

Implement Pydantic models and get one **completed** video interaction to return valid structured candidates. Use current `response_format` if compatible; always validate locally. Save failures for debugging, never for writes.

### 6. Add the synthetic ground-truth recording

Record the designed 5–7 minute feedback video containing the six intentional cases.

### 7. Tighten prompt against failures

Run the synthetic video and iterate until the workflow reliably distinguishes:

- action,
- ambiguity,
- question,
- duplicate,
- visual inference.

Document failures rather than hiding them.

### 8. Add deterministic policy and idempotency

Only after extraction works.

### 9. Add FFmpeg evidence extraction

Generate at least one convincing visual evidence frame.

### 10. Add GitHub issue creation

Use a demo repository and a least-privilege token where possible.

### 11. Add terminal approval and audit log

Now the full loop should work.

### 12. Test the complete workflow from a clean run

Delete/reset demo issues if necessary and run from input to final output while recording timings.

### 13. Capture screenshots and GIF

Use sanitized demo data and ensure no secrets are visible.

### 14. Write the submission

Use actual observations/timings/failures from the finished build. Do not pre-write fake results.

---

# 26. Suggested 1–3 Day Execution Plan

## Day 1 — Capability + core intelligence

Goal: prove the important part works.

- project setup,
- current Gemini SDK/API verification,
- video upload,
- `processing="agentic"` on the **Interactions video content block** (not a universal SDK argument),
- structured output,
- project context,
- first Pydantic validation,
- record synthetic video,
- iterate prompt against cases.

**End-of-day success condition:** one video produces mostly correct structured candidates with timestamps.

## Day 2 — Reliable workflow + action

- deterministic policy,
- duplicate merge,
- duration/timestamp validation,
- FFmpeg evidence,
- GitHub Issues adapter,
- approval flow,
- idempotency,
- audit log,
- error handling.

**End-of-day success condition:** clean end-to-end run creates correct approved issues and withholds ambiguous items.

## Day 3 — Proof + documentation (if available)

- rerun from clean state,
- manual timing comparison,
- expected-vs-actual evaluation,
- screenshot capture,
- GIF recording/editing,
- assignment write-up,
- optional short walkthrough video,
- final security/privacy check.

Do not spend Day 3 adding random features.

---

# 27. Blockers / Open Questions

These do not need user clarification before beginning; use reasonable defaults and adjust if implementation reveals a real constraint.

- [ ] **GitHub destination repo:** choose/create a demo repository for issues.
- [ ] **Exact current Gemini SDK call shape:** must be taken from current official docs at implementation time.
- [ ] **Live integration test not yet executed:** verify the specific combination (`gemini-3.5-flash-lite` + agentic video + background polling + schema); official docs document each feature, not proof of this exact combination in the user's project. Use `stream=True` fallback if background is unsupported. Do not interpret partial output as final.
- [ ] **Evidence hosting:** local screenshot is core; embedding image in GitHub Issue is optional stretch.
- [ ] **Watcher vs one-shot CLI:** build one-shot CLI first; add folder watcher only after complete core works.
- [x] **Model choice:** use `gemini-3.5-flash-lite` as the default. It supports `processing: "agentic"`, is free-tier friendly, and the user currently sees 500 RPD for their project/account. Only move to a larger Flash model if measured extraction quality is insufficient; document any change.

---

# 28. Deferred Items

Do not implement these unless the core submission is already finished and tested.

- Real Loom ingestion.
- Google Drive watcher.
- Jira/Linear/ClickUp adapters.
- Slack notifications.
- Email notifications.
- Frontend/dashboard.
- Database.
- OAuth/multi-user auth.
- Cloud deployment.
- Multi-tenant architecture.
- Cross-video long-term memory.
- Vector DB/RAG.
- Automatic creation without human review.
- Automatic screenshot hosting/attachments.
- Existing-backlog semantic duplicate search.
- Cost dashboard.
- Complex retry queue.

These can be mentioned under "production next steps" rather than built.

---

# 29. Important Context for the Resuming Agent

This is the most important section to read before coding.

1. **Do not re-open ideation unless the selected capability becomes technically unavailable.** The session already compared alternatives. The priority is shipping.
2. **No code exists yet.** Everything architectural in this handoff is a plan, not an implementation claim.
3. **The selected project's real differentiator is not video summarization.** It is turning multimodal client feedback into **grounded operational work candidates** while preserving uncertainty and preventing unsafe execution.
4. **The new capability must remain essential to the story.** The demo should include at least one visually meaningful case that transcript-only processing would handle poorly.
5. **The assignment is evaluated via proof.** Optimize terminal output, final GitHub Issues, screenshots, GIF and clear write-up.
6. **Do not build a UI.** That would directly fight the assignment's workflow-not-application framing.
7. **Do not use no-code/node automation tools.** Rejection risk is explicit.
8. **Keep the model/policy boundary visible.** Model proposes; deterministic code validates/routes; human approves MVP external writes.
9. **Do not present AI confidence as mathematically calibrated probability.** Use categorical confidence plus concrete rules.
10. **Do not fabricate business impact.** Measure the demo and label the scope of the measurement.
11. **Use synthetic/owned data.** Do not send confidential client recordings through a free API tier.
12. **Keep implementation direct.** Plain Python + official API + FFmpeg + GitHub REST is a stronger signal for this role than unnecessary agent frameworks.
13. **If something fails during testing, preserve the learning.** The final write-up should show at least one meaningful failure/iteration if one occurs.
14. **The final submission should demonstrate scope judgment.** "I deliberately did not build X" can be a positive point when X was unnecessary.

---

# 30. Assumptions Made

- The user can obtain a Gemini Developer API key and use the free tier for the demo.
- The user has or can create a GitHub repository where Issues can be created.
- The user can create a fine-grained GitHub token with only required permissions rather than exposing a broad personal token.
- Python is acceptable for the implementation.
- FFmpeg can be installed locally.
- Synthetic screen-recorded client feedback is acceptable under the assignment's instruction to use one's own accounts/data.
- A terminal approval step is acceptable because Crework's public positioning explicitly includes human steering and workflow design around where humans remain in the loop.
- The submission does not require live cloud hosting.
- The exact SDK syntax and model availability may change; official docs at build time are authoritative.

---

# 31. Potential Gotchas

## 31.1 Short videos vs agentic-processing advantage

Google's docs indicate agentic processing is particularly beneficial for long-form video and may add some time-to-first-token overhead for very short clips. Use a realistic 5–7 minute recording so the demo does not look like the capability was chosen for a 20-second clip.

## 31.2 Timestamp accuracy

Model timestamps may be approximate. Validate range bounds and test whether extracted evidence frames actually show the stated behavior. If midpoint extraction is poor, let the model return a `keyframe_seconds` field or sample several frames in the interval.

## 31.3 Model over-action

The most likely failure is turning vague commentary into a concrete task. The prompt and policy must penalize this behavior. It is a feature when the system says "needs clarification."

## 31.4 Visual inference vs explicit intent

A model may detect a malfunction the client did not mention. Do not silently turn that into a contractual/requested change. Mark it clearly as visually inferred and require review.

## 31.5 Duplicate mentions

Do not assume the model will always merge duplicates perfectly. Preserve group IDs and enforce a deterministic collapse where possible.

## 31.6 GitHub token permissions

Use least privilege. Never show token values in terminal screenshots. Do not commit `.env`.

## 31.7 GitHub issue image attachments

Avoid spending time trying to use an undocumented attachment-upload trick. Local evidence + timestamp is sufficient for core. Repository-hosted evidence image is optional.

## 31.8 Free-tier data handling

Use synthetic/owned data only. For any future real-client deployment, review current Gemini data-usage/privacy terms and select the appropriate paid/enterprise setup before sending confidential material.

## 31.9 API drift and interrupted execution

Google's current docs recommend streaming or background execution for long/complex agentic requests. Synchronous non-streaming processing may hit authentication/timeouts during backend retries. The default is **Interactions background + polling**, with saved interaction ID; `stream=True` is a fallback if this exact model/API combination rejects background. For a stream, an `interaction.completed` event (and validated final output) is required; mid-stream fragments are diagnostic only. Fail closed on `failed`, `cancelled`, `incomplete`, 401, timeout or abrupt stream termination; reconcile existing ID before issuing a new request. A `processing_call` / `processing_result` in completed interaction steps verifies agentic exploration. See sections 3 and 18.

Free-tier use can fail on **RPM or TPM even before 500 daily requests**. User-reported 500 RPD is not guaranteed; quotas are per project, and **RPD resets at midnight Pacific time**, not midnight Asia/Kolkata. Check current limits in AI Studio; cache successful completed analyses by input hash during prompt iteration, throttle retries, and never silently switch to billing. Google's privacy terms mean all demo video content must be owned/synthetic.

Gemini is moving quickly. Verify current official documentation for:

- supported model,
- file upload method,
- Interactions API call,
- `processing="agentic"` on the **Interactions video content block** (not a universal SDK argument),
- structured output support,
- polling/stream/background behavior.

Do not blindly copy code snippets from this handoff if docs differ.

## 31.10 Over-polishing

The assignment explicitly values thought, prioritization and a working build over polish. Do not burn hours on terminal colors, diagrams, branding or a fancy README before the workflow works. This 1,700+ line handoff is a **reference**, not a task list that must be implemented in full; the next agent should first follow the operational quick start directly below.

---

# 32. Environment State

No project environment has been created yet. **No Gemini API call, model output, retry, or GitHub issue creation has been executed in this handoff-review session.** All snippets are reviewed against docs, not live-tested with user credentials.

## Required environment variable names

```text
GEMINI_API_KEY
GITHUB_TOKEN
GITHUB_REPO
```

Possible optional names:

```text
GITHUB_API_URL=https://api.github.com
GEMINI_MODEL=gemini-3.5-flash-lite
```

Again: names only in committed files; values stay in `.env` or secure local environment.

## External tools/services

- Gemini Developer API / Google AI Studio.
- GitHub Issues REST API.
- FFmpeg/ffprobe.
- Local filesystem.

## Active processes

None.

---

# 33. Recommended Commands / Development Sequence

These are illustrative; adjust for OS/environment.

```bash
# Setup
python -m venv .venv
source .venv/bin/activate      # Windows PowerShell differs
pip install google-genai pydantic python-dotenv httpx pyyaml

# Verify FFmpeg
ffmpeg -version
ffprobe -version

# Early test
python main.py analyze path/to/short-test.mp4

# Later full workflow
python main.py run inbox/client-feedback-001.mp4
```

A watcher is a **deferred, optional extension**, not the default implementation or a required artifact:

```bash
python main.py watch
```

Do not make a watcher a prerequisite for proving the intelligence layer.

---

# 34. Definition of Done for the Build

The build is complete enough to submit when ALL of these are true:

- [ ] The capability is verified and clearly cited to an official September 1 source.
- [ ] A synthetic 5–7 minute feedback video exists.
- [ ] Gemini agentic video processing analyzes the recording.
- [ ] Output is parsed into validated structured candidates.
- [ ] At least one visual + spoken bug is correctly captured.
- [ ] At least one vague comment is **not** converted into an implementation ticket.
- [ ] Duplicate feedback is merged/grouped or visibly handled.
- [ ] At least one visual-only/inferred observation is routed to review.
- [ ] Timestamps are validated.
- [ ] FFmpeg extracts useful evidence from at least one candidate.
- [ ] Human review occurs before external write in the MVP.
- [ ] Approved candidates create real GitHub Issues.
- [ ] Rerunning the same source cannot silently duplicate all issues.
- [ ] Audit log records the run and created issue IDs.
- [ ] The complete loop has been tested from a clean state.
- [ ] Manual processing time and workflow time have been measured.
- [ ] Screenshots show each important stage.
- [ ] A separate GIF proves the end-to-end flow.
- [ ] The write-up includes the caveat and the design response.
- [ ] The write-up includes other pairings considered.
- [ ] No secrets appear in code, screenshots, GIF or submitted material.
- [ ] No no-code workflow builder was used.
- [ ] No unnecessary custom frontend/backend was built.

---

# 35. Definition of a Strong Submission

A merely working submission says:

> "I uploaded a video to Gemini and created GitHub issues."

A strong submission demonstrates:

> "I noticed that agentic video changed how reliably a model can inspect long multimodal recordings. I mapped that to a real translation bottleneck in client-service delivery. I built a small direct-API workflow that preserves evidence and distinguishes observation from intent. I then placed deterministic policy around the model so ambiguity cannot silently become committed work, tested it against intentionally difficult cases, measured the workflow, and documented exactly where it still fails."

That is the standard to optimize for.

---

# 36. Final Submission Checklist

Before sending the assignment, perform one final pass:

## Capability

- [ ] Release date is accurate.
- [ ] Official source linked.
- [ ] Explanation focuses on the capability, not model hype.
- [ ] Caveat is explicit.

## Business relevance

- [ ] Target team/business is named.
- [ ] Manual pain is concrete.
- [ ] New capability materially improves this workflow.

## Engineering

- [ ] Direct APIs used.
- [ ] No node/visual automation tool used.
- [ ] Schema validation exists.
- [ ] Policy/guardrails exist.
- [ ] Errors do not falsely appear as success.
- [ ] Secrets are protected.

## Proof

- [ ] Real test input.
- [ ] Real output.
- [ ] Final GitHub issues visible.
- [ ] Screenshots are readable.
- [ ] GIF is understandable without narration.
- [ ] At least one non-happy-path behavior is visible.

## Communication

- [ ] Write-up starts from the problem/capability connection.
- [ ] Architecture is understandable quickly.
- [ ] Important choices explain **why**.
- [ ] Limitations are concrete.
- [ ] Measurements are real and scoped honestly.
- [ ] Production extensions are clearly separated from MVP.

---

# 37. Related Resources

## Crework

- Crework Labs: https://www.creworklabs.com/
- Agentic AI systems: https://www.creworklabs.com/agentic-ai-systems
- Newsletter/publication: https://shikshita.substack.com/

Assignment examples to review for tone/shape:

- Client onboarding: https://shikshita.substack.com/p/i-automated-everything-that-happens
- Lead follow-up: https://shikshita.substack.com/p/how-to-stop-losing-warm-leads-to
- Morning briefing: https://shikshita.substack.com/p/i-wake-up-to-a-full-executive-briefing
- Meeting notes: https://shikshita.substack.com/p/i-stopped-paying-for-a-note-taker
- Website lead capture: https://shikshita.substack.com/p/turn-website-visitors-into-leads

## Gemini / Google

- Agentic Video announcement (Sep 1, 2026): https://blog.google/innovation-and-ai/models-and-research/gemini-models/introducing-agentic-video-in-gemini/
- Video understanding docs: https://ai.google.dev/gemini-api/docs/video-understanding
- Gemini API pricing: https://ai.google.dev/gemini-api/docs/pricing
- Gemini Interactions migration: https://ai.google.dev/gemini-api/docs/migrate-to-interactions
- Interactions breaking changes (May 2026): https://ai.google.dev/gemini-api/docs/interactions-breaking-changes-may-2026
- Background execution/recovery: https://ai.google.dev/gemini-api/docs/background-execution
- Streaming: https://ai.google.dev/gemini-api/docs/streaming
- Structured output: https://ai.google.dev/gemini-api/docs/structured-output
- Gemini project rate limits: https://ai.google.dev/gemini-api/docs/rate-limits
- Gemini API changelog: https://ai.google.dev/gemini-api/docs/changelog

## GitHub

- GitHub REST Issues docs: https://docs.github.com/en/rest/issues/issues
- Authentication guidance: https://docs.github.com/en/rest/authentication/authenticating-to-the-rest-api

## Handoff standard used

- Session-handoff skill folder: https://github.com/softaworks/agent-toolkit/tree/main/skills/session-handoff
- Skill instructions: https://github.com/softaworks/agent-toolkit/blob/main/skills/session-handoff/SKILL.md
- Handoff template: https://github.com/softaworks/agent-toolkit/blob/main/skills/session-handoff/references/handoff-template.md

---

# 37A. Verification of External Review and Scope Corrections (2026-09-16)

The user provided a detailed third-party review of the previous 1,714-line handoff. These verdicts distinguish **source-confirmed facts** from useful inference and advice requiring correction; this is not evidence that the application was executed.

| Review point | Verdict / implemented correction |
|---|---|
| JD/assignment ICP, workflow emphasis, restrictions, deliverables | Confirmed against the **actual assignment and JD the user supplied in this conversation**. Zapier is named in the JD; assignment bans visual/node builders generally. |
| Repo "apparently not reviewed" | Corrected: assignment explicitly says **"we will not open one if you do."** |
| No Docker/vector DB etc. listed as source requirements | Correct: reclassified as **inferred scope heuristics**, not employer rules. |
| No-code-in-an-afternoon test missing from submission narrative | Confirmed in JD and added, with precise engineering evidence rather than false claims about what no-code tools can never do. |
| Architecture too broad versus newsletter size | Valid risk. **One-shot CLI first**, five small Python files maximum in the suggested tree, one source video, one destination, one evidence frame; watcher, hosting, cross-video search deferred. Module count is not itself a grading rule. |
| Interactions API current call | Confirmed `client.interactions.create(...)` and video `processing="agentic"`; **correction:** GenerateContent remains supported as a legacy alternative, not technically broken. Interactions chosen for this project. |
| Streaming/background timeouts | Confirmed Google recommends `stream=True` **or** `background=True` for long/complex video. Default background+poll+persist ID; test exact model compatibility before relying on it. |
| "Preserve partially parsed candidates" after failed stream | **Rejected as a safe execution strategy.** Preserve event/progress logs for diagnostics; only a completed, schema-validated result can go to review/issue writes. Recover same background interaction when possible. |
| 3.7 Flash accuracy/cost claim and 3.5 Lite cost trade-off | Confirmed Google's **launch-post comparison**; choose free 3.5 first but do not say it passed until measured. Supported list now also includes 3.8 Flash, introduced after Sep 1 launch lineup. |
| Missing failure-injection test | Added explicit timeout, incomplete interaction, stream loss, 429 and uncertain GitHub write tests. These remain **planned**, not passed. |
| 500 RPD/RPM/TPM + reset | 500 RPD **user-reported, not account-verified**; Google says quotas vary, per-project RPD resets midnight Pacific. |
| `ticket_types` misses `decision` | Corrected to `observation_types` including `decision`; **not every observation is an implementation ticket**. |
| Benchmark numbers | Google reports **up to** 88% lower tokens, 66% lower cost and 7% quality improvement in launch comparisons. Optional one-sentence attributed write-up detail, not project ROI. |
| GitHub browser attachment automation | Core avoids it. Local evidence + timestamp; repository-hosted image optional only if demonstrably supported and worthwhile. |

Verification sources (first-party):
- Crework assignment + JD: verbatim text provided by user in this conversation; not claims about rendered Notion pages.
- https://blog.google/innovation-and-ai/models-and-research/gemini-models/introducing-agentic-video-in-gemini/
- https://ai.google.dev/gemini-api/docs/video-understanding
- https://ai.google.dev/gemini-api/docs/interactions-overview
- https://ai.google.dev/gemini-api/docs/migrate-to-interactions
- https://ai.google.dev/gemini-api/docs/background-execution
- https://ai.google.dev/gemini-api/docs/streaming
- https://ai.google.dev/gemini-api/docs/structured-output
- https://ai.google.dev/gemini-api/docs/rate-limits
- https://ai.google.dev/gemini-api/docs/pricing

## Operational fast path for a new coding agent (read this BEFORE the rest)

1. **State check:** as of this revision, no project exists and no integration test has been run. Verify local files/branch before assuming status changed. No prior handoff chain is known.
2. **Scope:** one-shot local MP4 -> Gemini 3.5 Flash-Lite agentic Interactions (background/poll if supported) -> complete schema-validated observations -> deterministic policy -> one local FFmpeg evidence frame -> review -> approved GitHub Issues -> simple ledger. Watcher is optional.
3. **First command objective:** install minimal SDK, verify `ffmpeg`/`ffprobe`, and run the isolated section 3 example with a synthetic video. Inspect `steps` for agentic processing. Do not touch GitHub yet.
4. **Next:** get one completed schema-validated analysis, inject one failure and prove no write, then make a single approved test GitHub issue. Persist IDs before expanding test coverage.
5. **Then:** record a 5–7-minute demo covering clear request, visual bug, vague commentary, duplicate, question, visually inferred anomaly; compare actual vs ground truth, measure real time, capture terminal/issue screenshots and a GIF.
6. **Submission:** rely on the write-up/screenshots/GIF (repo will not be opened). Include capability+caveat, pain+pairing, reproducible steps, final result, alternative pairings, actual limitations, and the JD's no-code differentiation test backed by genuine engineering evidence. Never invent metrics or tests.

---

# 38. First Instruction to the Next Coding Agent

Start with the **Operational fast path** in section 37A, then read this handoff's architecture, safety rules, and immediate next steps before coding. **Do not restart ideation.**

Start with the smallest technical uncertainty:

> **Build the one-shot CLI first. Use section 3's documented Interactions API shape and background/poll recovery to make a synthetic MP4 produce one timestamp-grounded observation; verify `processing_call`/`processing_result` in the completed interaction steps. Then validate the result before adding any GitHub writes.**

Only after that works should you introduce Pydantic schemas, the full synthetic test recording, policy rules, evidence extraction and GitHub writes.

The project's success depends more on a reliable capability → pain → guarded action loop than on the size of the codebase.
