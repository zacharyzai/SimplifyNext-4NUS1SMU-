(function () {
  "use strict";

  const els = {
    threadId: document.getElementById("thread-id"),
    userId: document.getElementById("user-id"),
    btnRun: document.getElementById("btn-run"),
    btnSimulate: document.getElementById("btn-simulate"),
    btnReset: document.getElementById("btn-reset"),
    status: document.getElementById("status-line"),
    conversationBody: document.getElementById("conversation-body"),
    traceBody: document.getElementById("trace-body"),
    openQuestionsContainer: document.getElementById("open-questions-container"),
    openQuestionsList: document.getElementById("open-questions-list"),
    answerText: document.getElementById("answer-text"),
    btnSubmitAnswer: document.getElementById("btn-submit-answer"),
    approvalContainer: document.getElementById("approval-container"),
    btnApprove: document.getElementById("btn-approve"),
    btnReject: document.getElementById("btn-reject"),
  };

  // Heuristic for "checked" items that found nothing, per the spec's grey
  // styling rule. Trace records don't (yet) carry per-item found/not-found
  // flags, so this looks for the vocabulary a real module would actually
  // use to describe an empty result (UNKNOWN cells, missing data, etc).
  // Tighten this once modules/forecast.py and modules/ingestion.py exist
  // and emit real "checked" entries.
  const FOUND_NOTHING_PATTERN = /\b(unknown|insufficient|none|missing|no data|not seen|no earnings|absent)\b/i;

  function setStatus(text) {
    els.status.textContent = text;
  }

  function fmtCents(cents) {
    if (cents === null || cents === undefined) return "?";
    const sign = cents < 0 ? "-" : "";
    const abs = Math.abs(cents);
    const dollars = Math.floor(abs / 100);
    const rem = String(abs % 100).padStart(2, "0");
    return `${sign}$${dollars.toLocaleString()}.${rem}`;
  }

  async function postJSON(url, body) {
    const res = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body || {}),
    });
    return res.json();
  }

  async function getJSON(url) {
    const res = await fetch(url);
    return res.json();
  }

  function renderConversation(data) {
    const s = data.state_summary || {};
    let html = "";

    if (s.forecast) {
      html += `<p class="conclusion-line">Projected shortfall of `
        + `<strong>${fmtCents(s.forecast.shortfall_amount_cents)}</strong> on `
        + `<strong>${s.forecast.shortfall_date || "an unknown date"}</strong> `
        + `(confidence: ${s.forecast.confidence || "?"}).</p>`;
    }

    if (s.explanation) {
      html += `<div class="field-block"><h3>Explanation</h3><p>${escapeHtml(s.explanation)}</p></div>`;
    }

    if (s.chosen_plan) {
      html += `<div class="field-block"><h3>Chosen plan</h3>`
        + `<div class="plan-card"><div class="plan-name">${escapeHtml(s.chosen_plan.name || s.chosen_plan.id || "unnamed")}</div>`
        + `<div>Impact: ${fmtCents(s.chosen_plan.impact_cents)} &middot; Effort: ${s.chosen_plan.effort ?? "?"}</div>`
        + `</div></div>`;
    } else if (s.materiality_flag && s.materiality_flag.fire === false) {
      html += `<div class="field-block"><h3>Decision</h3>`
        + `<p>Staying silent — the situation does not clear the materiality threshold right now.</p></div>`;
    }

    if (s.user_constraints && s.user_constraints.length) {
      html += `<div class="field-block"><h3>Remembered constraints</h3><ul class="constraint-list">`
        + s.user_constraints.map((c) => `<li>${escapeHtml(c)}</li>`).join("")
        + `</ul></div>`;
    }

    if (s.rejected_plans && s.rejected_plans.length) {
      html += `<details class="rejected-plays"><summary>Rejected plays (${s.rejected_plans.length})</summary>`
        + `<ul class="rejected-list">`
        + s.rejected_plans.map((p) => `<li><strong>${escapeHtml(p.id)}</strong> — ${escapeHtml(p.reason)}</li>`).join("")
        + `</ul></details>`;
    }

    els.conversationBody.innerHTML = html || `<p class="empty-state">Run produced no output.</p>`;

    // Open questions box
    if (data.open_questions && data.open_questions.length) {
      els.openQuestionsList.innerHTML = data.open_questions.map((q) => `<li>${escapeHtml(q)}</li>`).join("");
      els.openQuestionsContainer.hidden = false;
    } else {
      els.openQuestionsContainer.hidden = true;
    }

    // Approval bar
    els.approvalContainer.hidden = !data.awaiting_approval;
  }

  function renderTrace(trace) {
    if (!trace || !trace.length) {
      els.traceBody.innerHTML = `<p class="empty-state">No trace yet for this thread.</p>`;
      return;
    }
    els.traceBody.innerHTML = trace.map(renderTraceCard).join("");
  }

  function renderTraceCard(record) {
    const degraded = !!record.degraded;
    const confidence = record.confidence || "MEDIUM";
    const checkedItems = (record.checked || []).map((item) => {
      const grey = FOUND_NOTHING_PATTERN.test(item) ? " found-nothing" : "";
      return `<li class="${grey}">${escapeHtml(item)}</li>`;
    }).join("");

    return `
      <div class="trace-card${degraded ? " degraded" : ""}">
        <div class="trace-card-header">
          <span class="trace-node-name">${escapeHtml(record.node || "unknown node")}
            <span class="confidence-chip confidence-${confidence}">${confidence}</span>
            ${degraded ? `<span class="degraded-label">degraded</span>` : ""}
          </span>
          <span class="trace-ts">${escapeHtml(record.ts || "")}</span>
        </div>
        ${checkedItems ? `<ul class="trace-checked">${checkedItems}</ul>` : ""}
        <p class="trace-concluded">${escapeHtml(record.concluded || "")}</p>
      </div>`;
  }

  function escapeHtml(str) {
    return String(str)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function currentThreadId() {
    return els.threadId.value.trim() || "bob-001";
  }

  function currentUserId() {
    return els.userId.value.trim() || "bob-001";
  }

  async function runAgent(label) {
    setStatus(`${label}…`);
    try {
      const data = await postJSON("/run", {
        user_id: currentUserId(),
        thread_id: currentThreadId(),
      });
      if (!data.ok) {
        setStatus(`Error: ${data.error}`);
        return;
      }
      renderConversation(data);
      renderTrace(data.trace);
      setStatus(`${label} — done.`);
    } catch (err) {
      setStatus(`Network error: ${err}`);
    }
  }

  async function refreshTrace() {
    const data = await getJSON(`/trace/${encodeURIComponent(currentThreadId())}`);
    if (data.ok) renderTrace(data.trace);
  }

  els.btnRun.addEventListener("click", () => runAgent("Run agent"));
  els.btnSimulate.addEventListener("click", () => runAgent("Simulate next Wednesday"));

  els.btnReset.addEventListener("click", async () => {
    setStatus("Resetting demo…");
    const data = await postJSON("/demo/reset", { thread_id: currentThreadId() });
    if (data.ok) {
      els.conversationBody.innerHTML = `<p class="empty-state">Thread reset. Click "Run agent" to start.</p>`;
      els.traceBody.innerHTML = `<p class="empty-state">No trace yet for this thread.</p>`;
      els.openQuestionsContainer.hidden = true;
      els.approvalContainer.hidden = true;
      setStatus("Demo reset.");
    } else {
      setStatus(`Error: ${data.error}`);
    }
  });

  els.btnApprove.addEventListener("click", async () => {
    setStatus("Sending approval…");
    const data = await postJSON("/approve", { thread_id: currentThreadId(), approved: true });
    if (data.ok) {
      renderConversation(data);
      renderTrace(data.trace);
      setStatus("Approved — action executed.");
    } else {
      setStatus(`Error: ${data.error}`);
    }
  });

  els.btnReject.addEventListener("click", async () => {
    setStatus("Sending rejection…");
    const data = await postJSON("/approve", { thread_id: currentThreadId(), approved: false });
    if (data.ok) {
      renderConversation(data);
      renderTrace(data.trace);
      setStatus("Rejected — constraint recorded.");
    } else {
      setStatus(`Error: ${data.error}`);
    }
  });

  els.btnSubmitAnswer.addEventListener("click", async () => {
    const text = els.answerText.value.trim();
    if (!text) return;
    setStatus("Sending answer…");
    const data = await postJSON("/answer", {
      thread_id: currentThreadId(),
      answers: { "response": text },
    });
    if (data.ok) {
      els.answerText.value = "";
      renderConversation(data);
      renderTrace(data.trace);
      setStatus("Answer sent.");
    } else {
      setStatus(`Error: ${data.error}`);
    }
  });

  setStatus("Ready.");
})();
