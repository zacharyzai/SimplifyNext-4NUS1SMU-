(function () {
  "use strict";

  const els = {
    threadId: document.getElementById("thread-id"),
    userId: document.getElementById("user-id"),
    btnRun: document.getElementById("btn-run"),
    btnSimulate: document.getElementById("btn-simulate"),
    btnReset: document.getElementById("btn-reset"),
    rawText: document.getElementById("raw-text"),
    status: document.getElementById("status-line"),

    conclusionText: document.getElementById("conclusion-text"),
    explanationText: document.getElementById("explanation-text"),
    planCard: document.getElementById("plan-card"),
    planName: document.getElementById("plan-name"),
    planMeta: document.getElementById("plan-meta"),
    silentCard: document.getElementById("silent-card"),
    constraintsCard: document.getElementById("constraints-card"),
    constraintList: document.getElementById("constraint-list"),
    rejectedCard: document.getElementById("rejected-card"),
    rejectedSummary: document.getElementById("rejected-summary"),
    rejectedList: document.getElementById("rejected-list"),

    questionCard: document.getElementById("question-card"),
    openQuestionText: document.getElementById("open-question-text"),
    openQuestionProgress: document.getElementById("open-question-progress"),
    answerText: document.getElementById("answer-text"),
    btnSubmitAnswer: document.getElementById("btn-submit-answer"),

    liveDot: document.getElementById("live-dot"),
    liveText: document.getElementById("live-text"),
    approvalBar: document.getElementById("approval-bar"),
    approvalAction: document.getElementById("approval-action"),
    btnApprove: document.getElementById("btn-approve"),
    btnReject: document.getElementById("btn-reject"),

    traceMeta: document.getElementById("trace-meta"),
    traceEmpty: document.getElementById("trace-empty"),
    traceBody: document.getElementById("trace-body"),
  };

  // Heuristic for "checked" items that found nothing, per the spec's grey
  // styling rule. Trace records don't carry per-item found/not-found flags,
  // so this looks for the vocabulary a module would actually use to
  // describe an empty result (UNKNOWN cells, missing data, etc).
  const FOUND_NOTHING_PATTERN = /\b(unknown|insufficient|none|missing|no data|not seen|no earnings|absent)\b/i;

  // Plain-English gloss for each pipeline stage, keyed by a substring of
  // the trace record's raw node name (covers both the real module names --
  // "ingestion", "forecast_engine", "materiality_gate", "planner" -- and
  // the "(STUB)"-suffixed fallback names, so this doesn't need updating
  // again if a module regresses to its stub). Orchestration-only nodes
  // (clarify_node, execute_node, answer_endpoint, resume_after_approval)
  // intentionally have no entry -- they fall back to showing just the raw
  // node name, which is already plain enough.
  const NODE_INFO = [
    ["ingestion", "Reading your data", "Parses delivery logs, bank statements, and earnings messages into a clean record"],
    ["forecast", "Projecting cash flow", "Runs deterministic math over historical patterns — no LLM guessing"],
    ["gate", "Deciding whether to speak up", "Checks if the situation is material enough to bother the user"],
    ["planner", "Choosing a plan", "Ranks and picks an action, rejecting others with stated reasons"],
  ];

  function friendlyNodeInfo(nodeName) {
    const lower = (nodeName || "").toLowerCase();
    const match = NODE_INFO.find(([key]) => lower.includes(key));
    return match ? { label: match[1], description: match[2] } : null;
  }

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

  function setLive(state) {
    // state: "idle" | "running" | "approval" | "question"
    els.liveDot.classList.toggle("is-live", state === "running");
    els.liveText.textContent = {
      idle: "idle",
      running: "streaming",
      approval: "paused for approval",
      question: "paused for answer",
    }[state] || "idle";
  }

  // --- Rendering -----------------------------------------------------------

  function renderConversation(data) {
    const s = data.state_summary || {};

    if (s.forecast) {
      const confidence = s.forecast.confidence || "UNKNOWN";
      els.conclusionText.textContent =
        `Projected shortfall of ${fmtCents(s.forecast.shortfall_amount_cents)} on `
        + `${s.forecast.shortfall_date || "an unknown date"} (${confidence} confidence).`;
    } else {
      els.conclusionText.textContent = "No run yet.";
    }

    if (s.explanation) {
      // Dev-only markers ("STUB:") from not-yet-real modules must never
      // reach the user-facing explanation -- that's what the Trace panel
      // is for. Strip them here rather than in the stub data itself, so
      // the dev signal stays visible to the team while it's still useful.
      els.explanationText.textContent = s.explanation.replace(/^\s*STUB:\s*/i, "");
    } else {
      els.explanationText.textContent = "Press \"Run agent\" to start a thread. The right column fills in one step at a time, exactly as the agent completes them.";
    }

    if (s.chosen_plan) {
      els.planName.textContent = s.chosen_plan.name || s.chosen_plan.id || "unnamed";
      els.planMeta.innerHTML = `Impact: <strong>${fmtCents(s.chosen_plan.impact_cents)}</strong> &middot; Effort: <strong>${escapeHtml(s.chosen_plan.effort ?? "unknown")}</strong>`;
      els.planCard.hidden = false;
      els.silentCard.hidden = true;
    } else if (s.materiality_flag && s.materiality_flag.fire === false) {
      els.planCard.hidden = true;
      els.silentCard.hidden = false;
    } else {
      els.planCard.hidden = true;
      els.silentCard.hidden = true;
    }

    if (s.user_constraints && s.user_constraints.length) {
      els.constraintList.innerHTML = s.user_constraints.map((c) => `<li>${escapeHtml(c)}</li>`).join("");
      els.constraintsCard.hidden = false;
    } else {
      els.constraintsCard.hidden = true;
    }

    if (s.rejected_plans && s.rejected_plans.length) {
      els.rejectedSummary.textContent = `${s.rejected_plans.length} plan(s) rejected — show reasons`;
      els.rejectedList.innerHTML = s.rejected_plans.map((p) =>
        `<li><span class="rej-id">${escapeHtml(p.id)}</span><span class="rej-reason">${escapeHtml(p.reason)}</span></li>`
      ).join("");
      els.rejectedCard.hidden = false;
    } else {
      els.rejectedCard.hidden = true;
    }

    // Backend orders open_questions by whatever blocks forecast confidence
    // most (modules/forecast.py's find_data_gaps: soonest-first, then
    // fewest observations) -- so showing only the first one always means
    // showing the single most-blocking question, never a dump of the list.
    const hasQuestion = data.open_questions && data.open_questions.length;
    if (hasQuestion) {
      els.openQuestionText.textContent = data.open_questions[0];
      const remaining = data.open_questions.length - 1;
      if (remaining > 0) {
        els.openQuestionProgress.textContent = `(+${remaining} more after this)`;
        els.openQuestionProgress.hidden = false;
      } else {
        els.openQuestionProgress.hidden = true;
      }
      els.questionCard.hidden = false;
    } else {
      els.questionCard.hidden = true;
    }

    els.approvalBar.hidden = !data.awaiting_approval;
    if (data.awaiting_approval && s.chosen_plan) {
      els.approvalAction.textContent = s.chosen_plan.name || s.chosen_plan.id || "";
    }

    if (data.awaiting_approval) setLive("approval");
    else if (hasQuestion) setLive("question");
    else setLive("idle");
  }

  function renderTraceCard(record) {
    const degraded = !!record.degraded;
    const confidence = record.confidence || "MEDIUM";
    const rawNode = record.node || "unknown node";
    const info = friendlyNodeInfo(rawNode);
    const checkedItems = (record.checked || []).map((item, i) => {
      const grey = FOUND_NOTHING_PATTERN.test(item) ? " found-nothing" : "";
      return `<li class="${grey}" style="animation-delay:${i * 45}ms"><span class="mark">${grey ? "–" : "✓"}</span><span class="label">${escapeHtml(item)}</span></li>`;
    }).join("");

    const headerHtml = info
      ? `<span class="trace-node-name">${escapeHtml(info.label)}</span>
         <code class="trace-node-raw">${escapeHtml(rawNode)}</code>`
      : `<span class="trace-node-name">${escapeHtml(rawNode)}</span>`;

    return `
      <article class="trace-card${degraded ? " degraded" : ""}">
        <div class="trace-card-header">
          <span class="trace-node-heading">${headerHtml}</span>
          <span class="trace-ts">${escapeHtml(record.ts || "")}</span>
          ${degraded ? `<span class="chip degraded">degraded</span>` : ""}
          <span class="chip confidence-${escapeHtml(confidence)}">${escapeHtml(confidence)}</span>
        </div>
        ${info ? `<p class="trace-node-desc">${escapeHtml(info.description)}</p>` : ""}
        ${checkedItems ? `<span class="trace-concluded-label">Checked</span><ul class="trace-checked">${checkedItems}</ul>` : ""}
        <span class="trace-concluded-label">Concluded</span>
        <p class="trace-concluded">${escapeHtml(record.concluded || "")}</p>
      </article>`;
  }

  function refreshTraceMeta() {
    const count = els.traceBody.querySelectorAll(".trace-card").length;
    els.traceEmpty.hidden = count > 0;
    els.traceMeta.textContent = count > 0 ? `${count} step${count === 1 ? "" : "s"} · newest at the bottom` : "no thread yet";
  }

  function beginLiveTrace() {
    els.traceBody.innerHTML = "";
    refreshTraceMeta();
  }

  function appendLiveTraceCard(record) {
    els.traceBody.insertAdjacentHTML("beforeend", renderTraceCard(record));
    refreshTraceMeta();
  }

  // Used by the non-streamed endpoints (/approve, /answer) -- they return
  // the thread's full trace in one shot rather than as SSE events, so the
  // panel gets rebuilt wholesale instead of appended to.
  function renderFullTrace(trace) {
    els.traceBody.innerHTML = (trace || []).map(renderTraceCard).join("");
    refreshTraceMeta();
  }

  // --- Actions -----------------------------------------------------------

  // Streams /run/{run_id}/stream via EventSource instead of waiting on a
  // single blocking POST -- each "trace" event lands the moment that node
  // actually finishes (LLM calls and all), so the right panel builds up
  // live instead of appearing all at once after the whole run completes.
  function runAgent(button, label, rawText) {
    return withLoading(button, label, () => new Promise((resolve) => {
      beginLiveTrace();
      setLive("running");
      const params = new URLSearchParams({ user_id: currentUserId() });
      if (rawText) params.set("raw_text", rawText);
      const es = new EventSource(`/run/${encodeURIComponent(currentThreadId())}/stream?${params}`);

      es.addEventListener("trace", (e) => {
        const record = JSON.parse(e.data);
        appendLiveTraceCard(record);
        const info = friendlyNodeInfo(record.node);
        setStatus(`${info ? info.label : record.node}…`);
      });

      es.addEventListener("done", (e) => {
        const data = JSON.parse(e.data);
        es.close();
        if (!data.ok) {
          setStatus(`Error: ${data.error}`, true);
          setLive("idle");
        } else {
          renderConversation(data);
          setStatus(`${label} — done.`);
        }
        resolve();
      });

      es.addEventListener("error", (e) => {
        es.close();
        const message = e.data ? (JSON.parse(e.data).error || "stream error") : "connection lost";
        setStatus(`Error: ${message}`, true);
        setLive("idle");
        resolve();
      });
    }));
  }

  els.btnRun.addEventListener("click", () => {
    const rawText = els.rawText.value.trim();
    runAgent(els.btnRun, rawText ? "Transcribing and running agent" : "Run agent", rawText);
  });
  els.btnSimulate.addEventListener("click", () => runAgent(els.btnSimulate, "Simulate next Wednesday"));

  els.btnReset.addEventListener("click", async () => {
    await withLoading(els.btnReset, "Resetting demo", async () => {
      try {
        const data = await postJSON("/demo/reset", { thread_id: currentThreadId() });
        if (data.ok) {
          renderConversation({ state_summary: {}, open_questions: [], awaiting_approval: false });
          beginLiveTrace();
          els.rawText.value = "";
          setLive("idle");
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
          renderFullTrace(data.trace);
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
          renderFullTrace(data.trace);
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
          renderFullTrace(data.trace);
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
