Inspect the Feedback Recording's speech and visuals. Return only one valid JSON object with exactly this shape and no Markdown or commentary:
{
  "schema_version": "1.0",
  "video_summary": "non-empty summary",
  "observations": [{
    "observation_id": "obs_001",
    "topic_key": "lowercase-kebab-case",
    "type": "bug|change_request|feature_request|question|decision|reaction|commentary",
    "intent": "explicit_change|explicit_problem|ambiguous_reaction|question|decision|none",
    "title": "non-empty title",
    "component": null,
    "summary": "non-empty summary",
    "requested_outcome": null,
    "acceptance_criteria": [],
    "clarification_question": null,
    "confidence": "high|medium|low",
    "evidence": [{"start_seconds": 0.0, "end_seconds": 1.0, "keyframe_seconds": null, "client_quote": null, "visual_observation": "what was seen"}],
    "rationale": "non-empty rationale"
  }]
}
Use null for unknown nullable fields. Each evidence item must have client_quote or visual_observation. Return timestamp-grounded Observations only. Preserve ambiguity, do not invent requested outcomes or acceptance criteria, and use one topic_key for repeated mentions. Cover every distinct feedback mention that is supported by speech or visible UI. Treat a clearly visible authored anomaly without an explicit client problem statement as a possible bug with intent none, medium confidence, and visually grounded evidence; reserve low confidence for cases where the evidence itself is unclear.

Apply these field rules:
- explicit_change: requested_outcome states the client's requested change. acceptance_criteria contains only a separate test condition the client explicitly states; merely restating the change is not a criterion.
- explicit_problem: type is bug and requested_outcome states only the direct resolution inherent in the stated problem (for example, an overlap must no longer occur). acceptance_criteria contains only a separate test condition the client explicitly states; merely negating the problem is not a criterion.
- ambiguous_reaction: requested_outcome is null, acceptance_criteria is empty, and clarification_question asks what specific change the client wants.
- question, decision, and none intents: requested_outcome MUST be null, acceptance_criteria MUST be empty, and clarification_question MUST be null. A visual-only anomaly does not authorize you to infer a desired fix or test criterion.

For repeated observations with the same topic_key, type, component, intent, and requested_outcome MUST be exactly identical. Only the mention-specific title, summary, evidence, rationale, and directly stated acceptance criteria may differ.

Before returning JSON, perform a consistency pass over repeated topic_key values: copy the first observation's type, component, intent, and requested_outcome into every later observation with that topic_key verbatim. Do not add page names or other qualifiers to those copied fields.
