# Client Feedback Triage

This context turns recorded product feedback into reviewable work proposals while keeping issue creation under explicit human control.

## Language

**Feedback Recording**:
A local video containing spoken or visible product feedback that the system may inspect.
_Avoid_: Call, meeting, source video

**Observation**:
A timestamp-grounded statement returned from a completed model interaction about something said or seen in a Feedback Recording.
_Avoid_: Finding, raw feedback

**Candidate**:
A validated Observation presented for policy evaluation and possible human action.
_Avoid_: Task, ticket, issue

**Clarification Request**:
A Candidate whose intent or requested outcome is too ambiguous to propose as implementation work.
_Avoid_: Rejected task, low-confidence issue

**Manual Review**:
A Candidate that requires human interpretation, especially when it depends on visual inference rather than an explicit request.
_Avoid_: Automatic issue, visual bug

**Withheld Result**:
A valid Observation retained in the Run Ledger but excluded from Approval because it is non-actionable or has low confidence.
_Avoid_: Rejected Candidate, failed Observation

**Evidence Frame**:
A still image from the Feedback Recording that gives a human reviewer visual context for a Candidate.
_Avoid_: Screenshot, thumbnail

**Approval**:
An explicit human decision that authorizes one exact, reviewed form of a Candidate to become a GitHub Issue in one destination repository.
_Avoid_: Confirmation, acceptance

**Issue Record**:
The durable, destination-scoped link between an approved Candidate and its verified GitHub Issue, used to prevent repeated writes.
_Avoid_: Ticket mapping, write result

**Run Ledger**:
The local audit record of source identity, model interaction, Candidates, review decisions, write attempts, reconciliation decisions, and Issue Records for one or more runs.
_Avoid_: Database, cache
