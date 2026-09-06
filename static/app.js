(function () {
  "use strict";

  const els = {
    threadId: document.getElementById("thread-id"),
    userId: document.getElementById("user-id"),
    btnRun: document.getElementById("btn-run"),
    btnSimulate: document.getElementById("btn-simulate"),
    btnReset: document.getElementById("btn-reset"),
    btnTheme: document.getElementById("btn-theme"),
    status: document.getElementById("status-line"),
    conversationBody: document.getElementById("conversation-body"),
    traceBody: document.getElementById("trace-body"),
    traceCount: document.getElementById("trace-count"),
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
  // Tighten this once modules/gate.py and modules/planner.py exist and
  // emit real "checked" entries.
  const FOUND_NOTHING_PATTERN = /\b(unknown|insufficient|none|missing|no data|not seen|no earnings|absent)\b/i;

  // --- Theme -----------------------------------------------------------

  const THEME_KEY = "cashflow-copilot-theme";

  function applyTheme(theme) {
    if (theme === "light" || theme === "dark") {
      document.documentElement.setAttribute("data-theme", theme);
    } else {
      document.documentElement.removeAttribute("data-theme");
    }
  }

  function initTheme() {
    const saved = localStorage.getItem(THEME_KEY);
    applyTheme(saved);
  }

  function toggleTheme() {
    const prefersDark = window.matchMedia("(prefers-color-scheme: dark)").matches;
    const current = document.documentElement.getAttribute("data-theme") || (prefersDark ? "dark" : "light");
    const next = current === "dark" ? "light" : "dark";
    localStorage.setItem(THEME_KEY, next);
    applyTheme(next);
  }

  initTheme();
  els.btnTheme.addEventListener("click", toggleTheme);

  // --- Helpers -----------------------------------------------------------

  function setStatus(text, isError) {
    els.status.textContent = text;
    els.status.classList.toggle("is-error", !!isError);
  }

  function fmtCents(cents) {
    if (cents === null || cents === undefined) return "unknown";
    const sign = cents < 0 ? "-" : "";
    const abs = Math.abs(cents);
    const dollars = Math.floor(abs / 100);
    const rem = String(abs % 100).padStart(2, "0");
    return `${sign}$${dollars.toLocaleString()}.${rem}`;
  }

  function setLoading(button, loading) {
    button.classList.toggle("is-loading", loading);
    button.disabled = loading;
  }

  async function withLoading(button, label, fn) {
    setLoading(button, true);
    setStatus(`${label}…`);
    try {
      await fn();
    } finally {
      setLoading(button, false);
    }
  }

  async function postJSON(url, body) {
    const res = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body || {}),
    });
    if (!res.ok) {
      throw new Error(`HTTP ${res.status}`);
    }
    return res.json();
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

  const ICON_CHECK = `<svg class="icon" viewBox="0 0 24 24" aria-hidden="true"><path d="M4 12.5 9 17l11-11"/></svg>`;
  const ICON_CONSTRAINT = `<svg class="icon" viewBox="0 0 24 24" aria-hidden="true"><path d="M12 3 3 7.5V12c0 5 3.8 8.7 9 9 5.2-.3 9-4 9-9V7.5L12 3Z"/></svg>`;
  const ICON_REJECTED = `<svg class="icon" viewBox="0 0 24 24" aria-hidden="true"><path d="M6 6l12 12M18 6 6 18"/></svg>`;
  const ICON_QUIET = `<svg class="icon" viewBox="0 0 24 24" aria-hidden="true"><path d="M12 3v18M8 7v10M4 10v4M16 7v10M20 10v4"/></svg>`;

  // --- Rendering -----------------------------------------------------------

  function renderConversation(data) {
    const s = data.state_summary || {};
    let html = "";

    if (s.forecast) {
      const confidence = s.forecast.confidence || "UNKNOWN";
      html += `<p class="conclusion-line">Projected shortfall of `
        + `<strong>${fmtCents(s.forecast.shortfall_amount_cents)}</strong> on `
        + `<strong>${escapeHtml(s.forecast.shortfall_date || "an unknown date")}</strong> `
        + `<span class="chip confidence-${escapeHtml(confidence)}">${escapeHtml(confidence)}</span></p>`;
    }

    if (s.explanation) {
      html += `<div class="field-block"><h3>Explanation</h3><p>${escapeHtml(s.explanation)}</p></div>`;
    }

    if (s.chosen_plan) {
      html += `<div class="field-block"><h3>Chosen plan</h3>`
        + `<div class="plan-card"><div class="plan-name">${escapeHtml(s.chosen_plan.name || s.chosen_plan.id || "unnamed")}</div>`
        + `<div class="plan-meta"><span>Impact: <strong>${fmtCents(s.chosen_plan.impact_cents)}</strong></span>`
        + `<span>Effort: <strong>${escapeHtml(s.chosen_plan.effort ?? "unknown")}</strong></span></div>`
        + `</div></div>`;
    } else if (s.materiality_flag && s.materiality_flag.fire === false) {
      html += `<div class="field-block"><h3>Decision</h3>`
        + `<div class="silent-note">${ICON_QUIET}<p>Staying silent — the situation does not clear the materiality threshold right now.</p></div></div>`;
    }

    if (s.user_constraints && s.user_constraints.length) {
      html += `<div class="field-block"><h3>Remembered constraints</h3><ul class="constraint-list">`
        + s.user_constraints.map((c) => `<li>${ICON_CONSTRAINT}<span>${escapeHtml(c)}</span></li>`).join("")
        + `</ul></div>`;
    }

    if (s.rejected_plans && s.rejected_plans.length) {
      html += `<details class="rejected-plays"><summary>Rejected plays (${s.rejected_plans.length})</summary>`
        + `<ul class="rejected-list">`
        + s.rejected_plans.map((p) => `<li>${ICON_REJECTED}<span><strong>${escapeHtml(p.id)}</strong> — ${escapeHtml(p.reason)}</span></li>`).join("")
        + `</ul></details>`;
    }

    els.conversationBody.innerHTML = html || `<div class="empty-state"><p>Run produced no output.</p></div>`;

    if (data.open_questions && data.open_questions.length) {
      els.openQuestionsList.innerHTML = data.open_questions.map((q) => `<li>${escapeHtml(q)}</li>`).join("");
      els.openQuestionsContainer.hidden = false;
    } else {
      els.openQuestionsContainer.hidden = true;
    }

    els.approvalContainer.hidden = !data.awaiting_approval;
  }

  function renderTrace(trace) {
    if (!trace || !trace.length) {
      els.traceBody.innerHTML = `<div class="empty-state"><svg class="icon" viewBox="0 0 24 24" aria-hidden="true"><path d="M3 12h4l2-7 4 14 2-7h6"/></svg><p>No trace yet for this thread.</p></div>`;
      els.traceCount.hidden = true;
      return;
    }
    els.traceBody.innerHTML = trace.map(renderTraceCard).join("");
    els.traceCount.hidden = false;
    els.traceCount.textContent = `${trace.length} step${trace.length === 1 ? "" : "s"}`;
  }

  function renderTraceCard(record) {
    const degraded = !!record.degraded;
    const confidence = record.confidence || "MEDIUM";
    const checkedItems = (record.checked || []).map((item) => {
      const grey = FOUND_NOTHING_PATTERN.test(item) ? " found-nothing" : "";
      return `<li class="${grey}">${escapeHtml(item)}</li>`;
    }).join("");

    return `
      <div class="trace-card confidence-${escapeHtml(confidence)}${degraded ? " degraded" : ""}">
        <div class="trace-card-header">
          <span class="trace-node-name">${escapeHtml(record.node || "unknown node")}</span>
          <span class="trace-ts">${escapeHtml(record.ts || "")}</span>
        </div>
        <div>
          <span class="chip confidence-${escapeHtml(confidence)}">${escapeHtml(confidence)}</span>
          ${degraded ? `<span class="chip degraded">Degraded</span>` : ""}
        </div>
        ${checkedItems ? `<ul class="trace-checked">${checkedItems}</ul>` : ""}
        <p class="trace-concluded">${escapeHtml(record.concluded || "")}</p>
      </div>`;
  }

  // --- Actions -----------------------------------------------------------

  async function runAgent(button, label) {
    await withLoading(button, label, async () => {
      try {
        const data = await postJSON("/run", {
          user_id: currentUserId(),
          thread_id: currentThreadId(),
        });
        if (!data.ok) {
          setStatus(`Error: ${data.error}`, true);
          return;
        }
        renderConversation(data);
        renderTrace(data.trace);
        setStatus(`${label} — done.`);
      } catch (err) {
        setStatus(`Network error: ${err.message || err}`, true);
      }
    });
  }

  els.btnRun.addEventListener("click", () => runAgent(els.btnRun, "Run agent"));
  els.btnSimulate.addEventListener("click", () => runAgent(els.btnSimulate, "Simulate next Wednesday"));

  els.btnReset.addEventListener("click", async () => {
    await withLoading(els.btnReset, "Resetting demo", async () => {
      try {
        const data = await postJSON("/demo/reset", { thread_id: currentThreadId() });
        if (data.ok) {
          renderConversation({ state_summary: {}, open_questions: [], awaiting_approval: false });
          renderTrace([]);
          setStatus("Demo reset.");
        } else {
          setStatus(`Error: ${data.error}`, true);
        }
      } catch (err) {
        setStatus(`Network error: ${err.message || err}`, true);
      }
    });
  });

  els.btnApprove.addEventListener("click", async () => {
    await withLoading(els.btnApprove, "Sending approval", async () => {
      try {
        const data = await postJSON("/approve", { thread_id: currentThreadId(), approved: true });
        if (data.ok) {
          renderConversation(data);
          renderTrace(data.trace);
          setStatus("Approved — action executed.");
        } else {
          setStatus(`Error: ${data.error}`, true);
        }
      } catch (err) {
        setStatus(`Network error: ${err.message || err}`, true);
      }
    });
  });

  els.btnReject.addEventListener("click", async () => {
    await withLoading(els.btnReject, "Sending rejection", async () => {
      try {
        const data = await postJSON("/approve", { thread_id: currentThreadId(), approved: false });
        if (data.ok) {
          renderConversation(data);
          renderTrace(data.trace);
          setStatus("Rejected — constraint recorded.");
        } else {
          setStatus(`Error: ${data.error}`, true);
        }
      } catch (err) {
        setStatus(`Network error: ${err.message || err}`, true);
      }
    });
  });

  els.btnSubmitAnswer.addEventListener("click", async () => {
    const text = els.answerText.value.trim();
    if (!text) return;
    await withLoading(els.btnSubmitAnswer, "Sending answer", async () => {
      try {
        const data = await postJSON("/answer", {
          thread_id: currentThreadId(),
          answers: { response: text },
        });
        if (data.ok) {
          els.answerText.value = "";
          renderConversation(data);
          renderTrace(data.trace);
          setStatus("Answer sent.");
        } else {
          setStatus(`Error: ${data.error}`, true);
        }
      } catch (err) {
        setStatus(`Network error: ${err.message || err}`, true);
      }
    });
  });

  setStatus("Ready.");
})();
