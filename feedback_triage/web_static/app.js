(() => {
  "use strict";

  const stageOrder = [
    ["upload", "Upload validated"],
    ["gemini_processing", "Gemini processing"],
    ["interaction_verification", "Interaction verification"],
    ["policy_evaluation", "Policy evaluation"],
    ["evidence_frame_extraction", "Evidence Frame extraction"],
  ];
  const state = { file: null, recording: null, selectedId: null, objectUrl: null, polling: null, preview: null, recordings: [] };
  const $ = (id) => document.getElementById(id);

  function announce(message) { $("global-announcement").textContent = message; }
  function escapeHtml(value) {
    return String(value ?? "").replace(/[&<>'"]/g, (char) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;" })[char]);
  }
  function formatBytes(bytes) {
    if (bytes < 1024) return `${bytes} B`;
    if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
    if (bytes < 1024 * 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
    return `${(bytes / (1024 * 1024 * 1024)).toFixed(2)} GB`;
  }
  function timecode(seconds) {
    const total = Math.max(0, Number(seconds) || 0);
    const hours = Math.floor(total / 3600);
    const minutes = Math.floor((total % 3600) / 60);
    const secs = total % 60;
    return `${String(hours).padStart(2, "0")}:${String(minutes).padStart(2, "0")}:${secs.toFixed(3).padStart(6, "0")}`;
  }
  async function api(url, options = {}) {
    const response = await fetch(url, options);
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) {
      const detail = payload.detail || payload;
      const error = new Error(detail.message || "The local app returned an error.");
      error.code = detail.code || "request_failed";
      throw error;
    }
    return payload;
  }
  function showError(element, message) { element.textContent = message; element.hidden = false; }
  function hideError(element) { element.hidden = true; element.textContent = ""; }
  function resetUpload() {
    state.file = null;
    state.recording = null;
    state.selectedId = null;
    if (state.objectUrl) URL.revokeObjectURL(state.objectUrl);
    state.objectUrl = null;
    $("video-file").value = "";
    $("selected-file").hidden = true;
    $("recording-picker").value = "";
    $("upload-button").disabled = true;
    $("analysis-panel").hidden = true;
    $("review-panel").hidden = true;
    hideError($("upload-error"));
    announce("Ready for a new Feedback Recording.");
  }
  function selectFile(file) {
    hideError($("upload-error"));
    if (!file) return;
    const extension = file.name.slice(file.name.lastIndexOf(".")).toLowerCase();
    const allowed = [".mp4", ".mov", ".webm", ".mkv", ".m4v", ".avi"];
    if (!allowed.includes(extension) || (file.type && !file.type.startsWith("video/") && file.type !== "application/octet-stream")) {
      showError($("upload-error"), "Choose a supported video file before analysis.");
      return;
    }
    state.file = file;
    $("file-name").textContent = file.name;
    $("file-meta").textContent = `${file.type || "video"} | ${formatBytes(file.size)}`;
    $("selected-file").hidden = false;
    $("upload-button").disabled = false;
    announce(`Selected ${file.name}.`);
  }
  function renderStageList(recording) {
    $("stage-list").innerHTML = (recording.stages || stageOrder.map(([key]) => ({ key, state: recording.stage.key === key ? recording.stage.state : "pending" }))).map((stage) => {
      const item = stage.state;
      const label = stage.label || stage.key;
      const stateLabel = item.replaceAll("_", " ");
      return `<li class="stage-item ${item}" aria-label="${escapeHtml(label)}: ${escapeHtml(stateLabel)}"><span>${escapeHtml(label)}</span><span class="stage-state">${escapeHtml(stateLabel)}</span></li>`;
    }).join("");
  }
  function renderRecordingPicker(recordings) {
    state.recordings = recordings;
    const picker = $("recording-picker");
    picker.innerHTML = `<option value="">Choose a persisted recording...</option>${recordings.map((recording) => `<option value="${escapeHtml(recording.recording_id)}">${escapeHtml(recording.filename)} | ${escapeHtml(recording.status.replaceAll("_", " "))}</option>`).join("")}`;
    if (state.recording) picker.value = state.recording.recording_id;
  }
  function renderFailure(recording) {
    const box = $("analysis-error");
    if (!recording.failure) { hideError(box); return; }
    box.innerHTML = `<strong>${escapeHtml(recording.failure.code)}</strong><p>${escapeHtml(recording.failure.message)}</p><span class="muted">${recording.failure.retryable ? "This state can be retried after the cause is addressed." : "The app has not taken an unsafe next action."}</span>`;
    box.hidden = false;
  }
  function setRecording(recording) {
    state.recording = recording;
    $("recording-picker").value = recording.recording_id;
    $("analysis-panel").hidden = false;
    $("recording-status").textContent = recording.status.replaceAll("_", " ");
    $("recording-name").textContent = recording.filename;
    $("recording-media").textContent = recording.media_type;
    $("recording-size").textContent = formatBytes(recording.size_bytes);
    $("recording-duration").textContent = timecode(recording.duration_seconds);
    renderStageList(recording);
    renderFailure(recording);
    const ready = recording.status === "ready_for_review" || recording.status === "published";
    const fingerprintMismatch = recording.failure && recording.failure.code === "fingerprint_mismatch";
    const canReanalyze = ready || fingerprintMismatch;
    $("review-panel").hidden = !ready;
    $("analyze-button").disabled = recording.status === "analyzing" || recording.status === "publishing" || fingerprintMismatch;
    $("reanalyze-button").hidden = !canReanalyze;
    $("reanalyze-warning").hidden = !canReanalyze;
    if (ready) renderReview(recording);
    if (recording.status === "analyzing" || recording.status === "publishing") beginPolling();
    else stopPolling();
  }
  async function openRecording(recordingId) {
    try {
      const recording = await api(`/api/recordings/${recordingId}`);
      state.file = null;
      if (state.objectUrl) URL.revokeObjectURL(state.objectUrl);
      state.objectUrl = null;
      $("video-file").value = "";
      $("selected-file").hidden = true;
      $("upload-button").disabled = true;
      state.selectedId = null;
      $("preview-video").src = recording.media_url;
      $("review-video").src = recording.media_url;
      $("preview-placeholder").hidden = true;
      setRecording(recording);
      announce(`Opened ${recording.filename} from persisted local state.`);
    } catch (error) {
      showError($("upload-error"), error.message);
      announce(error.message);
    }
  }
  function renderConfig(config) {
    const badge = $("config-badge");
    badge.textContent = config.gemini_configured ? (config.destination_repository ? `Destination: ${config.destination_repository}` : "Gemini ready | publishing not configured") : "Gemini configuration required";
    if (!config.gemini_configured) badge.classList.add("warning");
  }
  function routedResults(group) { return group.routed_results || []; }
  function allRoutedResults(groups) { return groups.flatMap((group) => routedResults(group)); }
  function approvalEligible(candidate) { return candidate.approval_eligible === true; }
  function renderReview(recording) {
    const groups = recording.groups || [];
    const all = allRoutedResults(groups);
    $("video-summary").textContent = recording.video_summary || "Verified analysis is ready for review.";
    $("routed-result-count").textContent = `${all.length} routed result${all.length === 1 ? "" : "s"}`;
    $("timeline-duration").textContent = timecode(recording.duration_seconds);
    $("timeline-end").textContent = timecode(recording.duration_seconds);
    $("route-groups").innerHTML = groups.map((group) => `
      <section class="route-group" aria-labelledby="group-${escapeHtml(group.key)}">
        <h4 id="group-${escapeHtml(group.key)}">${escapeHtml(group.label)} <span>${routedResults(group).length}</span></h4>
        <p class="route-description">${escapeHtml(group.description)}</p>
        ${routedResults(group).length ? routedResults(group).map((candidate) => `
          <button class="queue-card ${escapeHtml(candidate.route)} ${candidate.candidate_id === state.selectedId ? "is-selected" : ""}" data-candidate-id="${escapeHtml(candidate.candidate_id)}" type="button">
            <span class="queue-card-title"><span>${escapeHtml(candidate.title)}</span><span>${escapeHtml(candidate.confidence)}</span></span>
            <span class="queue-card-meta">${escapeHtml(candidate.type)} | ${escapeHtml(candidate.route_label)}${candidate.component ? ` | ${escapeHtml(candidate.component)}` : ""}${candidate.reason_code ? ` | ${escapeHtml(candidate.reason_code)}` : ""}</span>
            <span class="queue-card-summary">${escapeHtml(candidate.summary)}</span>
            <span class="queue-card-evidence">${candidate.evidence_spans.length ? `Evidence: ${candidate.evidence_spans.map((span) => escapeHtml(span.start_timecode)).join(" | ")}` : "Evidence: none"}</span>
            ${candidate.decision !== "unreviewed" ? `<span class="decision-chip ${candidate.decision}">${escapeHtml(candidate.decision)}</span>` : ""}
          </button>`).join("") : `<p class="muted">No results in this group.</p>`}
      </section>`).join("");
    $("route-groups").querySelectorAll("[data-candidate-id]").forEach((button) => button.addEventListener("click", () => selectCandidate(button.dataset.candidateId)));
    renderTimeline(recording, all);
    const approved = all.filter((candidate) => candidate.decision === "approved" && approvalEligible(candidate));
    $("publish-button").hidden = approved.length === 0 || recording.status === "published";
    const hasFailedWrites = all.some((candidate) => candidate.write_state === "write_failed");
    const hasUncertainWrites = all.some((candidate) => candidate.write_state === "write_uncertain");
    const conflicts = all.filter((candidate) => candidate.write_state === "external_write_conflict");
    const hasConflicts = conflicts.length > 0;
    $("publish-options").hidden = approved.length === 0 || recording.status === "published" || (!hasFailedWrites && !hasUncertainWrites && !hasConflicts);
    $("retry-failed").disabled = !hasFailedWrites;
    $("retry-uncertain").disabled = !hasUncertainWrites;
    $("confirm-no-issue-option").hidden = !hasUncertainWrites;
    $("canonical-options").hidden = !hasConflicts;
    $("canonical-selections").innerHTML = conflicts.map((candidate) => `<label class="canonical-selection" for="canonical-${escapeHtml(candidate.candidate_id)}"><span>${escapeHtml(candidate.title)} (${escapeHtml(candidate.candidate_id)})</span><input id="canonical-${escapeHtml(candidate.candidate_id)}" data-canonical-candidate="${escapeHtml(candidate.candidate_id)}" type="number" min="1" step="1" inputmode="numeric" placeholder="Issue #" /></label>`).join("");
    $("canonical-selections").querySelectorAll("[data-canonical-candidate]").forEach((input) => input.addEventListener("input", () => updatePublishButton(all)));
    updatePublishButton(all);
    $("issue-count").textContent = hasConflicts ? "Resolve the recorded write conflict before publishing again." : (approved.length ? `${approved.length} approved Issue${approved.length === 1 ? "" : "s"} available` : "No approved Issues yet");
    renderTrust(recording.trust);
    if (state.selectedId) {
      const selected = all.find((candidate) => candidate.candidate_id === state.selectedId);
      if (selected) renderDetail(selected);
    }
  }
  function canonicalSelections(candidates) {
    return Object.fromEntries(candidates.filter((candidate) => candidate.write_state === "external_write_conflict").map((candidate) => {
      const value = Number($("canonical-" + candidate.candidate_id)?.value);
      return [candidate.candidate_id, Number.isInteger(value) && value > 0 ? value : null];
    }).filter((entry) => entry[1] !== null));
  }
  function updatePublishButton(candidates) {
    const conflicts = candidates.filter((candidate) => candidate.write_state === "external_write_conflict");
    const selections = canonicalSelections(candidates);
    $("publish-button").disabled = conflicts.some((candidate) => !selections[candidate.candidate_id]);
  }
  function renderTimeline(recording, routedResultsList) {
    const duration = Math.max(0.001, recording.duration_seconds);
    $("timeline-track").innerHTML = routedResultsList.flatMap((candidate) => {
      if (candidate.evidence_spans.length) {
        return candidate.evidence_spans.map((span, index) => {
          const position = Math.min(100, Math.max(0, (span.start_seconds / duration) * 100));
          return `<button class="timeline-marker ${escapeHtml(candidate.route)} ${candidate.candidate_id === state.selectedId ? "is-selected" : ""}" style="left:${position}%" data-candidate-id="${escapeHtml(candidate.candidate_id)}" data-seconds="${span.start_seconds}" type="button" aria-label="${escapeHtml(candidate.route_label)} ${escapeHtml(candidate.title)}, span ${index + 1} at ${escapeHtml(span.start_timecode)}"><span>${escapeHtml(span.start_timecode)}</span></button>`;
        });
      }
      const seconds = candidate.evidence_frame_seconds;
      const label = timecode(seconds);
      const position = Math.min(100, Math.max(0, (seconds / duration) * 100));
      return `<button class="timeline-marker ${escapeHtml(candidate.route)} frame-fallback ${candidate.candidate_id === state.selectedId ? "is-selected" : ""}" style="left:${position}%" data-candidate-id="${escapeHtml(candidate.candidate_id)}" data-seconds="${seconds}" type="button" aria-label="${escapeHtml(candidate.route_label)} ${escapeHtml(candidate.title)}, based on Evidence Frame at ${escapeHtml(label)}"><span>${escapeHtml(label)} · frame</span></button>`;
    }).join("");
    $("timeline-track").querySelectorAll("[data-candidate-id]").forEach((marker) => marker.addEventListener("click", () => {
      selectCandidate(marker.dataset.candidateId);
      seek(Number(marker.dataset.seconds));
    }));
  }
  function renderTrust(trust) {
    if (!trust) { $("trust-details").innerHTML = "<div><dt>Status</dt><dd>Not available yet</dd></div>"; return; }
    $("trust-details").innerHTML = [
      ["Source identity", trust.source_sha256], ["Duration", timecode(trust.duration_seconds)], ["Verified", trust.verified ? "Yes" : "No"],
      ["Processing pairs", trust.processing_pair_count ?? "Not available"], ["Model usage", Object.entries(trust.model_usage || {}).map(([key, value]) => `${key}: ${value}`).join(", ") || "Not reported"],
      ["Attempt", trust.attempt_id || "Not available"], ["Ledger", trust.ledger_location],
    ].map(([label, value]) => `<div><dt>${escapeHtml(label)}</dt><dd>${escapeHtml(value)}</dd></div>`).join("");
  }
  function selectCandidate(candidateId) {
    state.selectedId = candidateId;
    const candidate = allRoutedResults(state.recording.groups).find((item) => item.candidate_id === candidateId);
    if (candidate) { renderDetail(candidate); renderReview(state.recording); announce(`Selected ${candidate.route_label}: ${candidate.title}.`); }
  }
  function seek(seconds) {
    const video = $("review-video");
    if (Number.isFinite(seconds)) { video.currentTime = seconds; video.focus(); }
  }
  function renderDetail(candidate) {
    $("empty-detail").hidden = true;
    $("routed-result-detail").hidden = false;
    $("detail-route").textContent = candidate.route_label;
    const spans = candidate.evidence_spans.map((span) => `<button type="button" class="evidence-button" data-seconds="${span.start_seconds}"><span class="evidence-time">${escapeHtml(span.start_timecode)} to ${escapeHtml(span.end_timecode)}<br /><small>${span.start_seconds.toFixed(3)}s to ${span.end_seconds.toFixed(3)}s</small></span><span class="evidence-copy">${escapeHtml(span.client_quote || span.visual_observation || "Evidence span")}</span></button>`).join("");
    const editable = approvalEligible(candidate);
    $("routed-result-detail").innerHTML = `
      <h3 class="routed-result-title">${escapeHtml(candidate.title)}</h3>
       <div class="detail-meta"><span class="meta-chip">${escapeHtml(candidate.type)}</span><span class="meta-chip">${escapeHtml(candidate.intent)}</span><span class="meta-chip">${escapeHtml(candidate.confidence)} confidence</span><span class="meta-chip">${escapeHtml(candidate.candidate_id)}</span><span class="meta-chip">Topic: ${escapeHtml(candidate.topic_key)}</span></div>
      ${candidate.component ? `<div class="detail-section"><h4>Component</h4><p>${escapeHtml(candidate.component)}</p></div>` : ""}
       ${candidate.reason_code ? `<div class="detail-section"><h4>Policy reason</h4><p>${escapeHtml(candidate.reason_code)}</p></div>` : ""}
       <div class="detail-section"><h4>Visual inference</h4><p>${candidate.visually_inferred ? "Yes" : "No"} | Evidence Frame timestamp ${candidate.evidence_frame_seconds.toFixed(3)}s (${escapeHtml(timecode(candidate.evidence_frame_seconds))})</p></div>
      <div class="detail-section"><h4>Summary</h4><p>${escapeHtml(candidate.summary)}</p></div>
      ${candidate.client_quote ? `<div class="detail-section"><h4>Client quote</h4><p>${escapeHtml(candidate.client_quote)}</p></div>` : ""}
      ${candidate.visual_observation ? `<div class="detail-section"><h4>Visual observation</h4><p>${escapeHtml(candidate.visual_observation)}</p></div>` : ""}
      <div class="detail-section"><h4>Evidence spans</h4><div class="evidence-list">${spans || "<p class=\"muted\">No evidence spans available.</p>"}</div></div>
      <div class="detail-section"><h4>Rationale</h4><p>${escapeHtml(candidate.rationale)}</p></div>
      ${candidate.requested_outcome ? `<div class="detail-section"><h4>Requested outcome</h4><p>${escapeHtml(candidate.requested_outcome)}</p></div>` : ""}
      ${candidate.acceptance_criteria.length ? `<div class="detail-section"><h4>Acceptance criteria</h4><ul>${candidate.acceptance_criteria.map((item) => `<li>${escapeHtml(item)}</li>`).join("")}</ul></div>` : ""}
      ${candidate.clarification_question ? `<div class="detail-section"><h4>Clarification request</h4><p>${escapeHtml(candidate.clarification_question)}</p></div>` : ""}
       ${candidate.evidence_frame ? `<div class="detail-section"><h4>Evidence Frame</h4><p class="muted">Candidate ${escapeHtml(candidate.candidate_id)} | ${escapeHtml(candidate.evidence_frame.timestamp_timecode)} (${candidate.evidence_frame.timestamp_seconds.toFixed(3)}s) | ${escapeHtml(candidate.evidence_frame.status)}</p>${candidate.evidence_frame.url ? `<button class="evidence-frame-button" type="button" data-frame-seconds="${candidate.evidence_frame.timestamp_seconds}" aria-label="Seek to Evidence Frame at ${escapeHtml(candidate.evidence_frame.timestamp_timecode)}"><img class="evidence-image" src="${candidate.evidence_frame.url}" alt="Evidence Frame for ${escapeHtml(candidate.title)} at ${escapeHtml(candidate.evidence_frame.timestamp_timecode)}" /></button>` : `<p class="muted">${escapeHtml(candidate.evidence_frame.error || "Evidence Frame unavailable.")}</p>`}</div>` : ""}
      ${editable ? detailForm(candidate) : `<p class="immutable-note">This route is immutable here. Identity, route, evidence, provenance, and policy reasons cannot be edited or approved.</p>`}
      ${candidate.issue_url ? `<div class="detail-section"><h4>Issue Record</h4><a class="issue-link" href="${escapeHtml(candidate.issue_url)}" target="_blank" rel="noreferrer">Open linked Issue</a></div>` : ""}
    `;
    $("routed-result-detail").querySelectorAll(".evidence-button").forEach((button) => button.addEventListener("click", () => seek(Number(button.dataset.seconds))));
    $("routed-result-detail").querySelectorAll("[data-frame-seconds]").forEach((button) => button.addEventListener("click", () => seek(Number(button.dataset.frameSeconds))));
    $("routed-result-detail").querySelector("[data-action=preview]")?.addEventListener("click", () => openApprovalPreview(candidate));
    $("routed-result-detail").querySelector("[data-action=decline]")?.addEventListener("click", () => submitDecision(candidate, "decline"));
  }
  function detailForm(candidate) {
    const values = { title: candidate.title, summary: candidate.summary, requested_outcome: candidate.requested_outcome || "", component: candidate.component || "", acceptance_criteria: candidate.acceptance_criteria.join("\n") };
    const confirmation = candidate.route === "manual_review" ? `<label class="confirmation-row"><input id="manual-review-confirmed" type="checkbox" /> I explicitly confirm this Manual Review result.</label>` : "";
    return `<div class="detail-section"><h4>Approval prose</h4><form class="detail-form" id="detail-form"><label for="edit-title">Title</label><input id="edit-title" name="title" value="${escapeHtml(values.title)}" /><label for="edit-summary">Summary</label><textarea id="edit-summary" name="summary">${escapeHtml(values.summary)}</textarea><label for="edit-outcome">Requested outcome</label><textarea id="edit-outcome" name="requested_outcome">${escapeHtml(values.requested_outcome)}</textarea><label for="edit-component">Component</label><input id="edit-component" name="component" value="${escapeHtml(values.component)}" /><label for="edit-criteria">Acceptance criteria (one per line)</label><textarea class="criteria-input" id="edit-criteria" name="acceptance_criteria">${escapeHtml(values.acceptance_criteria)}</textarea>${confirmation}<div class="detail-actions"><button class="button button-primary" data-action="preview" type="button">Preview Approval</button><button class="button button-secondary" data-action="decline" type="button">Decline</button></div></form></div>`;
  }
  function formChanges(candidate) {
    const form = $("detail-form");
    if (!form) return {};
    const data = new FormData(form);
    const changes = {};
    const current = { title: candidate.title, summary: candidate.summary, requested_outcome: candidate.requested_outcome || "", component: candidate.component || "" };
    ["title", "summary", "requested_outcome", "component"].forEach((key) => { const value = String(data.get(key) || "").trim(); if (value !== current[key]) changes[key] = value; });
    const criteria = String(data.get("acceptance_criteria") || "").split("\n").map((item) => item.trim()).filter(Boolean);
    if (criteria.join("\n") !== candidate.acceptance_criteria.join("\n")) changes.acceptance_criteria = criteria;
    return changes;
  }
  function manualReviewConfirmed() { return Boolean($("manual-review-confirmed")?.checked); }
  async function openApprovalPreview(candidate) {
    try {
      const changes = formChanges(candidate);
      const payload = await api(`/api/recordings/${state.recording.recording_id}/routed-results/${candidate.candidate_id}/approval-preview`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ action: "approve", changes, manual_review_confirmed: manualReviewConfirmed() }) });
      state.preview = { candidate, changes, manual: candidate.route === "manual_review" };
      $("approval-destination").textContent = `Destination repository: ${payload.destination_repository}`;
      $("approval-title").value = payload.payload.title;
      $("approval-body").value = payload.payload.body;
      $("approval-dialog").showModal();
    } catch (error) { showError($("analysis-error"), error.message); announce(error.message); }
  }
  async function submitDecision(candidate, action) {
    const changes = action === "approve" ? formChanges(candidate) : {};
    try {
      const recording = await api(`/api/recordings/${state.recording.recording_id}/routed-results/${candidate.candidate_id}/review`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ action, changes, manual_review_confirmed: manualReviewConfirmed(), operator_label: null }) });
      setRecording(recording); announce(action === "approve" ? "Approval saved locally." : "Decline saved locally.");
    } catch (error) { showError($("analysis-error"), error.message); announce(error.message); }
  }
  async function publishApproved() {
    const approved = allRoutedResults(state.recording.groups).filter((candidate) => candidate.decision === "approved" && approvalEligible(candidate)).map((candidate) => candidate.candidate_id);
    if (!approved.length) return;
    if (!window.confirm(`Publish ${approved.length} approved Issue${approved.length === 1 ? "" : "s"} to ${state.recording.destination_repository || "the configured repository"}?`)) return;
    const retryFailed = Boolean($("retry-failed").checked);
    const retryUncertain = Boolean($("retry-uncertain").checked);
    const confirmNoIssue = Boolean($("confirm-no-issue").checked);
    if (retryUncertain && !confirmNoIssue) { announce("Confirm that no Issue exists for the exact marker before retrying an uncertain write."); return; }
    const selections = canonicalSelections(allRoutedResults(state.recording.groups));
    try { const recording = await api(`/api/recordings/${state.recording.recording_id}/publish`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ candidate_ids: approved, retry_failed: retryFailed, retry_uncertain: retryUncertain, confirm_no_issue: confirmNoIssue, canonical_issue_selections: selections }) }); setRecording(recording); announce("Publishing completed and Issue Records were persisted."); }
     catch (error) {
       showError($("analysis-error"), error.message);
       try { setRecording(await api(`/api/recordings/${state.recording.recording_id}`)); } catch (_) { /* Preserve the original visible failure. */ }
       announce(error.message);
     }
  }
  async function upload() {
    if (!state.file) return;
    const data = new FormData(); data.append("file", state.file);
    $("upload-button").disabled = true; $("upload-status").textContent = "Validating media...";
     try { const recording = await api("/api/recordings", { method: "POST", body: data }); state.objectUrl = URL.createObjectURL(state.file); $("preview-video").src = state.objectUrl; $("review-video").src = state.objectUrl; $("preview-placeholder").hidden = true; renderRecordingPicker([recording, ...state.recordings.filter((item) => item.recording_id !== recording.recording_id)]); setRecording(recording); $("upload-status").textContent = "Validated locally."; announce("Recording validated. Ready to analyze."); }
    catch (error) { $("upload-button").disabled = false; showError($("upload-error"), error.message); $("upload-status").textContent = ""; announce(error.message); }
  }
  async function analyze(reanalysis = false) {
    if (!state.recording) return;
    try { const recording = await api(`/api/recordings/${state.recording.recording_id}/${reanalysis ? "reanalyze" : "analyze"}`, { method: "POST" }); setRecording(recording); announce(reanalysis ? "Reanalysis started as a new attempt." : "Analysis started."); }
    catch (error) { showError($("analysis-error"), error.message); announce(error.message); }
  }
  function beginPolling() { if (state.polling) return; state.polling = window.setInterval(async () => { if (!state.recording) return; try { const recording = await api(`/api/recordings/${state.recording.recording_id}`); setRecording(recording); } catch (error) { announce(error.message); } }, 900); }
  function stopPolling() { if (state.polling) { clearInterval(state.polling); state.polling = null; } }
  async function boot() {
     try { renderConfig(await api("/api/config")); } catch (error) { showError($("upload-error"), error.message); }
     try { const recordings = await api("/api/recordings"); renderRecordingPicker(recordings); const latest = recordings.find((recording) => recording.status === "ready_for_review" || recording.status === "published") || recordings[0]; if (latest) await openRecording(latest.recording_id); } catch (_) { /* A new empty workspace is valid. */ }
   }

  $("video-file").addEventListener("change", (event) => selectFile(event.target.files[0]));
  $("drop-zone").addEventListener("dragover", (event) => { event.preventDefault(); $("drop-zone").classList.add("is-dragging"); });
  $("drop-zone").addEventListener("dragleave", () => $("drop-zone").classList.remove("is-dragging"));
  $("drop-zone").addEventListener("drop", (event) => { event.preventDefault(); $("drop-zone").classList.remove("is-dragging"); selectFile(event.dataTransfer.files[0]); });
   $("remove-file").addEventListener("click", resetUpload);
   $("new-recording").addEventListener("click", resetUpload);
   $("recording-picker").addEventListener("change", async (event) => { const recordingId = event.target.value; if (recordingId) await openRecording(recordingId); });
  $("upload-button").addEventListener("click", upload);
  $("analyze-button").addEventListener("click", () => analyze(false));
  $("reanalyze-button").addEventListener("click", () => analyze(true));
  $("publish-button").addEventListener("click", publishApproved);
  $("confirm-approval").addEventListener("click", async (event) => { event.preventDefault(); if (state.preview) { $("approval-dialog").close(); await submitDecision(state.preview.candidate, "approve"); state.preview = null; } });
  boot();
})();
