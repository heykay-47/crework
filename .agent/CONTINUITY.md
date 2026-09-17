# Continuity

## [PLANS]

- 2026-09-17T14:22:24Z [USER] Implement GitHub issue #12 with TDD at the settled policy seam, run final checks and `/code-review`, then commit on the current branch.
- 2026-09-17T09:30:57Z [USER] Implement approved GitHub issues #10–#19 in dependency order; issue #10 is the first tracer bullet.

## [DECISIONS]

- 2026-09-17T14:35:10Z [CODE] Issue #12 keeps malformed shape/value failures at the verified-output boundary as `output_invalid`; deterministic cross-field, topic, duplicate-ID/group, and duration checks fail the whole analysis as `policy_failed`.
- 2026-09-17T14:35:10Z [CODE] Policy results are recomputed from verified analysis and FFprobe duration, then atomically stored on the same Run Ledger attempt under `policy_result`; policy failures remove routes and settle that attempt as failed.
- 2026-09-17T13:53:32Z [CODE] Issue #11 normal reruns reconcile the latest persisted attempt or reuse its verified result; only explicit `--reanalyze` may append after all prior attempts settle, and every attempt remains in ledger history.
- 2026-09-17T08:53:02Z [USER] [MILESTONE] Closed Wayfinder map #1 is the authoritative risk-first addendum and supersedes conflicting `HANDOFF.md` details.
- 2026-09-17T09:24:43Z [USER] Approved the ten-ticket implementation graph exactly as published in issues #10–#19.
- 2026-09-17T05:09:38Z [USER] Use `gemini-3.5-flash-lite` with `stream=True, store=True`; persist `interaction.created` before diagnostics and trust only a retrieved `completed` interaction with fully matched processing pairs and schema-valid output.
- 2026-09-17T05:09:38Z [USER] Use an atomic per-source Run Ledger and a minimal Python 3.14/FFmpeg container; stream deltas never produce policy results or writes.
- 2026-09-17T05:26:30Z [USER] Gemini emits Observations only; deterministic policy, Approval, and GitHub writes remain later implementation slices.
- 2026-09-17T06:17:35Z [USER] External writes require exact immutable Approval snapshots and workspace-local reconciliation; issue #10 performs no external writes.

## [PROGRESS]

- 2026-09-17T14:35:10Z [CODE] Implemented deterministic Observation validation, duplicate grouping, evidence normalization/frame selection, stable Candidate IDs, ordered routes, CLI/Run Ledger integration, and policy documentation for issue #12.
- 2026-09-17T14:35:10Z [TOOL] Focused policy, triage, and recovery tests pass (31 tests); strict mypy passes across source and tests. Final full suite, container build, review, and commit remain.
- 2026-09-17T13:53:32Z [CODE] Implemented issue #11 recovery: exact-ID retrieval, bounded idempotent retries with `Retry-After`, stable failure handling, independent replacement attempts, and CLI/docs/tests for `--reanalyze`.
- 2026-09-17T13:53:32Z [TOOL] Two-axis `/code-review` found stale-result admission, post-ID interruption, terminal replacement, and deadline bugs; all correctness findings were addressed and re-reviewed.
- 2026-09-17T09:30:57Z [TOOL] [MILESTONE] Published issues #10–#19 with verified blocking edges; #10 was the sole initial frontier.
- 2026-09-17T13:08:52Z [CODE] Implemented issue #10: CLI orchestration, FFprobe boundary, stored Gemini stream/retrieval, processing verification, strict Pydantic schema, atomic ledger, container, synthetic fixture script, and tests.
- 2026-09-17T13:08:52Z [TOOL] Ran two-axis `/code-review`; addressed missing-input persistence, evidence-span ordering, ledger interruption/fingerprint coverage, context-mount docs, and container guidance.

## [DISCOVERIES]

- 2026-09-17T14:42:25Z [TOOL] The first final-suite run exposed obsolete input-boundary tests that still expected timestamp semantics to be Pydantic failures; those cases now live at the policy seam so they classify as `policy_failed`.
- 2026-09-17T13:53:32Z [CODE] Stream exceptions after `interaction.created` must leave the attempt recoverable; only failures before an ID exists can be settled as creation-unrecoverable without exact-ID retrieval.
- 2026-09-17T04:36:36Z [TOOL] Gemini 3.5 Flash-Lite rejects background interactions but supports the stored-stream fallback with completed retrieval and processing proof.
- 2026-09-17T13:08:52Z [TOOL] `google-genai==2.24.0` requires nested `response_format.text`; agentic output still needed an explicit exact JSON shape in the prompt to reliably satisfy the schema.
- 2026-09-17T13:08:52Z [TOOL] A live owned synthetic Feedback Recording completed with two matched processing pairs and schema-valid output; earlier transient stream errors were recorded fail-closed.
- 2026-09-17T13:08:52Z [TOOL] Final verification: Docker image built, host-UID container invalid-input run persisted `invalid_input`, 19 tests passed, and strict mypy passed.

## [OUTCOMES]

- 2026-09-17T14:42:25Z [TOOL] Issue #12 implementation passes the final suite (51 tests), strict mypy (16 source files), Docker image build, and two-axis `/code-review`; both review axes report no remaining findings. Commit remains.
- 2026-09-17T13:53:32Z [TOOL] Issue #11 implementation passed Docker build, 28 tests, strict mypy, and two-axis review; committed as `aff206b` and ready for issue closure.
- 2026-09-17T08:53:02Z [TOOL] [MILESTONE] Wayfinding completed at issue #1; schema, policy, approval safety, evidence, and proof contracts are linked from the closed map.
- 2026-09-17T13:08:52Z [TOOL] Issue #10 is implemented in commit `f7a256d`, reviewed, verified, and closed; the next frontier should be recomputed from issues #11–#19.
