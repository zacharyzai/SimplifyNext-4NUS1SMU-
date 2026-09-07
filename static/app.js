(function () {
  "use strict";

  const els = {
    threadId: document.getElementById("thread-id"),
    userId: document.getElementById("user-id"),
    btnRun: document.getElementById("btn-run"),
    btnReset: document.getElementById("btn-reset"),
    scenarioSelect: document.getElementById("scenario-select"),
    llmBadge: document.getElementById("llm-badge"),
    rawText: document.getElementById("raw-text"),
    btnSendRawText: document.getElementById("btn-send-raw-text"),
    chatLog: document.getElementById("chat-log"),
    chatEmpty: document.getElementById("chat-empty"),
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
    btnJumpLatest: document.getElementById("btn-jump-latest"),
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

  const SOURCE_LABELS = {
    partner_statement: "platform payout statement",
    self_reported_shift_log: "self-reported shift log",
    benchmark_prior: "industry benchmark (no personal data yet)",
    screenshot_ocr: "transcribed message/screenshot",
  };

  const TIER_LABELS = { 0: "Autonomous — acts and logs it", 1: "Notify — informs you, no action", 2: "Approval — halts and asks you first" };

  const REASON_LABELS = {
    not_applicable: "doesn't apply to this situation",
    insufficient_history: "not enough earnings history yet",
  };

  function prettyToken(s) {
    return (REASON_LABELS[s]) || s.replace(/_/g, " ");
  }

  // Judge-facing simplification of the raw "checked" strings each module
  // writes for its own trace record -- purely a display transform, so the
  // backend trace content (and any module self-test asserting on it
  // verbatim) is untouched. Falls through to the raw string unchanged if
  // nothing matches, so an unrecognised format never disappears.
  const CHECKED_HUMANIZERS = [
    [/^source: (.+)$/, (m) => `Data source: ${SOURCE_LABELS[m[1]] || m[1].replace(/_/g, " ")}`],
    [/^(\d+)d earnings history$/, (m) => `${m[1]} day${m[1] === "1" ? "" : "s"} of earnings history`],
    [/^permission tier: rule '([^']+)' -> tier (\d)$/, (m) => `Permission tier: ${TIER_LABELS[m[2]] || `tier ${m[2]}`}`],
    [/^materiality score \([^)]+\): (\d+)\/100, fire=(True|False)$/, (m) =>
      `Materiality score: ${m[1]}/100 — ${m[2] === "True" ? "urgent enough to speak up" : "not urgent enough to speak up"}`],
    [/^staleness score \([^)]+\): (\d+)\/100, fire=(True|False)$/, (m) =>
      `Staleness score: ${m[1]}/100 — ${m[2] === "True" ? "stale enough to check in" : "not stale enough to check in"}`],
    [/^(.+): rejected \(user_constraint: violates '(.+)'\)$/, (m) => `"${m[1]}" — rejected, conflicts with your rule "${m[2]}"`],
    [/^(.+): rejected \(user_constraint\)$/, (m) => `"${m[1]}" — rejected (conflicts with a stored constraint)`],
    [/^(.+): rejected \((.+)\)$/, (m) => `"${m[1]}" — rejected (${prettyToken(m[2])})`],
  ];

  function humanizeChecked(item) {
    for (const [pattern, fn] of CHECKED_HUMANIZERS) {
      const m = item.match(pattern);
      if (m) return fn(m);
    }
    return item;
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

  // The chat log exists purely so the user can see, in plain language,
  // what happened right after they hit Send -- it is a visual record of
  // "here's what you typed, here's what the agent concluded," not a
  // general-purpose free-form conversation. Every assistant bubble is built
  // from a deterministic template (server-side ack text, or a summary of
  // already-computed state_summary fields) -- never raw LLM prose -- so it
  // can't drift from what the trace panel and conclusion card show.
  function appendChatBubble(role, text) {
    els.chatEmpty.hidden = true;
    els.chatLog.insertAdjacentHTML(
      "beforeend",
      `<p class="chat-bubble ${role}">${escapeHtml(text)}</p>`
    );
    els.chatLog.scrollTop = els.chatLog.scrollHeight;
  }

  // Deterministic one-line summary of what the run concluded, for the chat
  // log's assistant bubble -- built from the same state_summary fields
  // renderConversation() already renders into the cards, so it can never
  // say something the rest of the UI disagrees with.
  function summarizeOutcome(data) {
    const s = data.state_summary || {};
    if (data.open_questions && data.open_questions.length) {
      return `I need more info: ${data.open_questions[0]}`;
    }
    if (s.chosen_plan) {
      return `Proposed a plan: ${s.chosen_plan.name || s.chosen_plan.id} `
        + (data.awaiting_approval ? "(needs your approval above)." : ".");
    }
    if (s.materiality_flag && s.materiality_flag.fire === false) {
      return "Updated the forecast — no material shortfall, so I'm staying silent.";
    }
    if (s.forecast) {
      return `Updated the forecast: shortfall of ${fmtCents(s.forecast.shortfall_amount_cents)} `
        + `on ${s.forecast.shortfall_date || "an unknown date"} (${s.forecast.confidence || "UNKNOWN"} confidence).`;
    }
    return "Run complete.";
  }

  // --- Rendering -----------------------------------------------------------

  function renderConversation(data) {
    const s = data.state_summary || {};

    if (s.forecast) {
      const f = s.forecast;
      const confidence = f.confidence || "UNKNOWN";
      const horizon = f.horizon_days || 14;
      if (f.shortfall_date) {
        els.conclusionText.textContent =
          `Projected shortfall of ${fmtCents(f.shortfall_amount_cents)} on `
          + `${f.shortfall_date} (${confidence} confidence).`;
      } else if (confidence === "UNKNOWN") {
        // Genuinely nothing to forecast from (no data yet), not "no
        // shortfall found" -- these read very differently to Bob.
        els.conclusionText.textContent =
          `Not enough earnings history yet to make a forecast (${confidence} confidence).`;
      } else {
        els.conclusionText.textContent =
          `No shortfall projected in the next ${horizon} days — you're on track (${confidence} confidence).`;
      }
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
      return `<li class="${grey}" style="animation-delay:${i * 45}ms"><span class="mark">${grey ? "–" : "✓"}</span><span class="label">${escapeHtml(humanizeChecked(item))}</span></li>`;
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

  // Trace panel is a bounded, scrollable box (static/style.css .trace-list)
  // once a run has more than a screenful of cards. "Near bottom" uses a
  // pixel threshold rather than exact equality since smooth-scroll and
  // sub-pixel layout rounding rarely land on scrollHeight exactly.
  const NEAR_BOTTOM_PX = 48;

  function isNearTraceBottom() {
    const el = els.traceBody;
    return el.scrollHeight - el.scrollTop - el.clientHeight < NEAR_BOTTOM_PX;
  }

  function scrollTraceToBottom() {
    els.traceBody.scrollTop = els.traceBody.scrollHeight;
    els.btnJumpLatest.hidden = true;
  }

  els.traceBody.addEventListener("scroll", () => {
    if (isNearTraceBottom()) els.btnJumpLatest.hidden = true;
  });
  els.btnJumpLatest.addEventListener("click", scrollTraceToBottom);

  function beginLiveTrace() {
    els.traceBody.innerHTML = "";
    refreshTraceMeta();
    els.btnJumpLatest.hidden = true;
  }

  function appendLiveTraceCard(record) {
    const wasNearBottom = isNearTraceBottom();
    els.traceBody.insertAdjacentHTML("beforeend", renderTraceCard(record));
    refreshTraceMeta();
    // Only yank the view down if the judge was already following along at
    // the bottom -- if they've scrolled up to reread an earlier step,
    // respect that and surface "jump to latest" instead of interrupting.
    if (wasNearBottom) scrollTraceToBottom();
    else els.btnJumpLatest.hidden = false;
  }

  // Used by the non-streamed endpoints (/approve, /answer) -- they return
  // the thread's full trace in one shot rather than as SSE events, so the
  // panel gets rebuilt wholesale instead of appended to.
  function renderFullTrace(trace) {
    els.traceBody.innerHTML = (trace || []).map(renderTraceCard).join("");
    refreshTraceMeta();
    scrollTraceToBottom();
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
      const scenario = els.scenarioSelect.value;
      if (scenario) params.set("scenario", scenario);
      const es = new EventSource(`/run/${encodeURIComponent(currentThreadId())}/stream?${params}`);

      es.addEventListener("chat", (e) => {
        const msg = JSON.parse(e.data);
        appendChatBubble("assistant", msg.text);
      });

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
          appendChatBubble("assistant", `Something went wrong: ${data.error}`);
        } else {
          renderConversation(data);
          // data.trace is the thread's FULL history (every run ever done
          // on it), not just what streamed live during this one -- redraw
          // from it so earlier steps (from a previous Run agent click, or
          // from /answer and /approve in between) don't visually vanish
          // just because beginLiveTrace() cleared the panel at the start
          // of this run. Without this, the panel only ever shows the most
          // recent run's steps even though nothing was actually lost.
          renderFullTrace(data.trace);
          if (rawText) appendChatBubble("assistant", summarizeOutcome(data));
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
    if (rawText) {
      appendChatBubble("user", rawText);
      els.rawText.value = "";
    }
    runAgent(els.btnRun, rawText ? "Transcribing and running agent" : "Run agent", rawText);
  });

  // A dedicated button right next to the earnings-message box, so typing
  // there and submitting doesn't require jumping to the unrelated "Run
  // agent" button at the top of the page -- same underlying action
  // (there's no lighter-weight "just add this record" endpoint; every
  // raw_text submission re-runs the full graph), just discoverable from
  // where you're actually typing.
  els.btnSubmitEarnings.addEventListener("click", () => {
    const rawText = els.rawText.value.trim();
    if (!rawText) {
      setStatus("Type an earnings message first.", true);
      return;
    }
    runAgent(els.btnSubmitEarnings, "Transcribing and running agent", rawText);
  });
  // Cmd/Ctrl+Enter submits from inside the textarea -- plain Enter still
  // inserts a newline, since a pasted screenshot transcript may be
  // multi-line.
  els.rawText.addEventListener("keydown", (e) => {
    if ((e.metaKey || e.ctrlKey) && e.key === "Enter") {
      e.preventDefault();
      els.btnSubmitEarnings.click();
    }
  });

  // The earnings-message box previously had no button of its own -- it only
  // ever did anything if the user separately clicked "Run agent" up top,
  // with no visual link between the two. This makes it self-sufficient.
  els.btnSendRawText.addEventListener("click", async () => {
    const rawText = els.rawText.value.trim();
    if (!rawText) return;
    appendChatBubble("user", rawText);
    els.rawText.value = "";
    await runAgent(els.btnSendRawText, "Transcribing and running agent", rawText);
  });

  els.btnReset.addEventListener("click", async () => {
    await withLoading(els.btnReset, "Resetting demo", async () => {
      try {
        const data = await postJSON("/demo/reset", { thread_id: currentThreadId() });
        if (data.ok) {
          renderConversation({ state_summary: {}, open_questions: [], awaiting_approval: false });
          beginLiveTrace();
          els.rawText.value = "";
          els.chatLog.querySelectorAll(".chat-bubble").forEach((el) => el.remove());
          els.chatEmpty.hidden = false;
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
    appendChatBubble("user", text);
    els.answerText.value = "";
    await withLoading(els.btnSubmitAnswer, "Sending answer", async () => {
      try {
        const data = await postJSON("/answer", {
          thread_id: currentThreadId(),
          answers: { response: text },
        });
        if (data.ok) {
          renderConversation(data);
          renderFullTrace(data.trace);
          appendChatBubble("assistant", summarizeOutcome(data));
          setStatus("Answer sent.");
        } else {
          setStatus(`Error: ${data.error}`, true);
          appendChatBubble("assistant", `Something went wrong: ${data.error}`);
        }
      } catch (err) {
        setStatus(`Network error: ${err.message || err}`, true);
      }
    });
  });

  // Populate the scenario selector from the backend rather than hardcoding
  // labels here, so data/demo_scenarios.py stays the single source of truth.
  (async () => {
    try {
      const res = await fetch("/scenarios");
      const data = await res.json();
      if (data.ok) {
        for (const s of data.scenarios) {
          const opt = document.createElement("option");
          opt.value = s.id;
          opt.textContent = s.label;
          opt.title = s.description;
          els.scenarioSelect.appendChild(opt);
        }
      }
    } catch (err) {
      // A missing scenario list must not block the rest of the UI.
    }
  })();

  // Surface whether the configured LLM provider is actually reachable up
  // front -- without this, a missing GEMINI_API_KEY / LLM_PROVIDER=none
  // degrades silently: the clarify loop asks its question, fails to
  // transcribe the answer, and exhausts MAX_REPLAN_LOOPS with no visible
  // signal that the LLM was never in the loop at all.
  (async () => {
    try {
      const res = await fetch("/health");
      const data = await res.json();
      const ready = !!data.llm_ready;
      els.llmBadge.textContent = ready
        ? `LLM ready (${data.llm_provider})`
        : `LLM unavailable (${data.llm_provider || "none"}) — transcription will fail`;
      els.llmBadge.classList.toggle("ready", ready);
      els.llmBadge.classList.toggle("not-ready", !ready);
    } catch (err) {
      els.llmBadge.textContent = "LLM status unknown";
    }
  })();

  setStatus("Ready.");
})();
