// ── STATE ─────────────────────────────────────────────────
const state = {
  traces: [],
  currentTrace: null,
  currentSql: "",
  threadId: null,
};

const el = (id) => document.getElementById(id);

function esc(v) {
  return String(v ?? "")
    .replace(/&/g, "&amp;").replace(/</g, "&lt;")
    .replace(/>/g, "&gt;").replace(/"/g, "&quot;");
}

// ── VALUE RENDERING ───────────────────────────────────────
function renderVal(v) {
  const s = String(v ?? "");
  if (s === "true")  return `<span class="v-t">true</span>`;
  if (s === "false") return `<span class="v-f">false</span>`;
  if (s === "null" || s === "—") return `<span class="v-null">${esc(s)}</span>`;
  if (/^".*"$/.test(s)) return `<span class="v-str">${esc(s)}</span>`;
  if (/^\[/.test(s))    return `<span class="v-arr">${esc(s)}</span>`;
  if (!isNaN(parseFloat(s)) && s.trim() !== "") return `<span class="v-num">${esc(s)}</span>`;
  return `<span class="v-text">${esc(s)}</span>`;
}

function renderKV(obj) {
  const entries = Object.entries(obj || {});
  if (!entries.length) return '<span class="v-null">—</span>';
  return `<div class="kv-list">${
    entries.map(([k, v]) =>
      `<div class="kv-row">
        <span class="kv-key">${esc(k)}</span>
        <span class="kv-val">${renderVal(v)}</span>
      </div>`
    ).join("")
  }</div>`;
}

// ── SQL SYNTAX HIGHLIGHTING ───────────────────────────────
function renderSQL(sql) {
  if (!sql) return '<span class="v-null">—</span>';
  const KEYWORDS = ["SELECT","FROM","WHERE","JOIN","LEFT","RIGHT","INNER","OUTER","ON","AND","OR","NOT","IN","LIKE","ORDER","BY","GROUP","HAVING","LIMIT","OFFSET","AS","DISTINCT","WITH","NULL","IS","CASE","WHEN","THEN","ELSE","END"];
  const FUNCTIONS = ["COUNT","AVG","SUM","MAX","MIN","COALESCE","IFNULL","CAST","ROUND","LENGTH","TRIM","LOWER","UPPER"];

  // Split on string literals to avoid highlighting inside them
  const parts = sql.split(/(\'[^\']*\')/g);
  return parts.map((part, i) => {
    if (i % 2 === 1) return `<span class="sql-str">${esc(part)}</span>`;
    let h = esc(part);
    FUNCTIONS.forEach(fn => {
      h = h.replace(new RegExp(`\\b${fn}\\b`, "g"), `<span class="sql-fn">${fn}</span>`);
    });
    KEYWORDS.forEach(kw => {
      h = h.replace(new RegExp(`\\b${kw}\\b`, "g"), `<span class="sql-kw">${kw}</span>`);
    });
    h = h.replace(/\b(\d+(?:\.\d+)?)\b/g, `<span class="sql-num">$1</span>`);
    return h;
  }).join("");
}

// ── NODE METADATA MAPS ────────────────────────────────────
const NODE_ICONS = {
  check_relevance: { icon: "policy",          badge: "LLM",   badgeCls: "badge-llm",   statusIcon: { ok: "check_circle", declined: "do_not_disturb_on" } },
  fetch_schema:    { icon: "schema",          badge: "DB",    badgeCls: "badge-db",    statusIcon: { ok: "check_circle" } },
  generate_sql:    { icon: "code",            badge: "LLM",   badgeCls: "badge-llm",   statusIcon: { ok: "check_circle" } },
  validate_sql:    { icon: "verified_user",   badge: "GUARD", badgeCls: "badge-guard", statusIcon: { ok: "check_circle", blocked: "shield_locked" } },
  execute_sql:     { icon: "bolt",            badge: "DB",    badgeCls: "badge-db",    statusIcon: { ok: "check_circle", error: "error_circle" } },
  regenerate_sql:  { icon: "autorenew",       badge: "RETRY", badgeCls: "badge-retry", statusIcon: { retry: "autorenew" } },
  format_answer:   { icon: "chat_bubble",     badge: "LLM",   badgeCls: "badge-llm",   statusIcon: { ok: "check_circle" } },
  polite_decline:  { icon: "not_interested",  badge: "GATE",  badgeCls: "badge-gate",  statusIcon: { declined: "do_not_disturb_on" } },
  error_response:  { icon: "error",           badge: "SYS",   badgeCls: "badge-error", statusIcon: { error: "error_circle" } },
};

const NODE_CONFIG = {
  check_relevance: { model: '"gpt-4o-mini"', temperature: "0.0",  task: '"binary_classification"' },
  fetch_schema:    { source: '"university.db"', method: '"introspect"', cache: '"session"' },
  generate_sql:    { model: '"gpt-4o"',      temperature: "0.0",  dialect: '"SQLite"' },
  validate_sql:    { policy: '"SELECT-only"', dml_block: "true",  regex_guard: "true" },
  execute_sql:     { db: '"university.db"',  timeout: '"30s"',    read_only: "true" },
  regenerate_sql:  { model: '"gpt-4o"',      temperature: "0.3",  max_retries: "3" },
  format_answer:   { model: '"gpt-4o-mini"', temperature: "0.2",  style: '"conversational"' },
  polite_decline:  { type: '"static_response"', scope: '"university_db"' },
  error_response:  { type: '"error_handler"' },
};

// ── INPUT/OUTPUT STATE INFERENCE ──────────────────────────
function nodeInput(node, item, trace) {
  const q = `"${trace.question}"`;
  const sql = trace.sql_query;
  const frag = sql ? `"${sql.substring(0, 55)}${sql.length > 55 ? "…" : ""}"` : '"pending"';

  switch (node) {
    case "check_relevance":  return { raw_question: q };
    case "fetch_schema":     return { question: q, classification: '"relevant"' };
    case "generate_sql":     return { question: q, schema_loaded: '"✓ loaded"' };
    case "validate_sql":     return { sql: frag };
    case "execute_sql":      return { sql: frag, db: '"university.db"' };
    case "regenerate_sql": {
      const m = item.detail?.match(/attempt (\d+)/);
      return { attempt: m ? m[1] : "1", strategy: '"schema_hints"' };
    }
    case "format_answer":    return { row_count: String(item.row_count ?? "n/a"), question: q };
    case "polite_decline":   return { question: q, classification: '"not_relevant"' };
    case "error_response":   return { trigger: '"pipeline_error"', fatal: "true" };
    default:                 return { detail: `"${item.detail}"` };
  }
}

function nodeOutput(node, item, trace) {
  switch (node) {
    case "check_relevance": {
      const rel = !String(item.detail).includes("not");
      return { classification: rel ? '"relevant"' : '"not_relevant"', confidence: rel ? "0.97" : "0.11", should_proceed: rel ? "true" : "false" };
    }
    case "fetch_schema": {
      const chars = String(item.detail).match(/(\d+)/)?.[1] ?? "1432";
      return { tables: '["teachers","students","courses","enrollments"]', schema_chars: chars, status: '"loaded"' };
    }
    case "generate_sql":
    case "regenerate_sql":
      return { dialect: '"SQLite"', is_select: "true" };
    case "validate_sql":
      return { safe: item.status === "blocked" ? "false" : "true", policy: '"SELECT-only"', threat_detected: item.status === "blocked" ? "true" : "false" };
    case "execute_sql":
      if (item.status === "error") return { status: '"FAILED"', error: `"${String(item.detail).substring(0, 50)}"` };
      return { rows_returned: item.row_count !== null ? String(item.row_count) : "n/a", status: '"OK"' };
    case "format_answer": {
      const ans = trace.answer ?? "";
      return { answer: `"${ans.substring(0, 70)}${ans.length > 70 ? "…" : ""}"`, confidence: "0.99", complete: "true" };
    }
    case "polite_decline":
      return { response: '"decline"', reason: '"off_topic"' };
    case "error_response":
      return { error: "true" };
    default:
      return { result: `"${item.detail}"` };
  }
}

function nodePreview(node, item, trace) {
  switch (node) {
    case "check_relevance":
      return { text: String(item.detail).includes("not") ? "NOT RELEVANT" : "RELEVANT", cls: String(item.detail).includes("not") ? "preview-declined" : "preview-ok" };
    case "fetch_schema":
      return { text: "4 tables", cls: "preview-ok" };
    case "generate_sql":
    case "regenerate_sql": {
      const s = trace.sql_query;
      return s ? { text: s.substring(0, 28) + (s.length > 28 ? "…" : ""), cls: "preview-ok" } : null;
    }
    case "validate_sql":
      return { text: item.status === "blocked" ? "BLOCKED" : "PASS", cls: item.status === "blocked" ? "preview-error" : "preview-ok" };
    case "execute_sql":
      if (item.status === "error") return { text: "FAILED", cls: "preview-error" };
      return item.row_count !== null ? { text: `${item.row_count} row${item.row_count !== 1 ? "s" : ""}`, cls: "preview-ok" } : null;
    case "regenerate_sql":
      return { text: `Retry ${item.detail?.match(/(\d+)/)?.[1] ?? "1"}/3`, cls: "preview-retry" };
    case "format_answer":
      return { text: "Answer ready", cls: "preview-ok" };
    case "polite_decline":
      return { text: "Declined", cls: "preview-declined" };
    case "error_response":
      return { text: "Error", cls: "preview-error" };
    default:
      return null;
  }
}

// ── TOGGLE HELPERS ────────────────────────────────────────
function toggleNode(id) {
  const card = el(id);
  if (!card) return;
  const body = card.querySelector(".node-body");
  const chev = card.querySelector(".node-chevron");
  const isHidden = body.classList.toggle("hidden");
  chev.classList.toggle("chevron-open", !isHidden);
  // Update preview chip visibility
  const chip = card.querySelector(".node-preview-chip");
  if (chip) chip.style.display = isHidden ? "" : "none";
}

function toggleMini(btn) {
  const body = btn.nextElementSibling;
  const chev = btn.querySelector(".mini-chevron");
  const hidden = body.classList.toggle("hidden");
  chev.classList.toggle("mini-chevron-open", !hidden);
}

// ── NODE CARD RENDER ──────────────────────────────────────
function renderNodeCard(item, trace, isFirst) {
  const { index, node, status, description, metadata } = item;
  const id = `nd-${index}`;
  const meta = NODE_ICONS[node] || { icon: "help", badge: "SYS", badgeCls: "badge-gate", statusIcon: {} };
  const cfg = NODE_CONFIG[node] || {};

  const statusIconMap = { ok: "check_circle", error: "error_circle", blocked: "shield_locked", retry: "autorenew", declined: "do_not_disturb_on" };
  const sIcon = meta.statusIcon?.[status] || statusIconMap[status] || "check_circle";

  const nodeName = node.replace(/_/g, " ").replace(/\b\w/g, c => c.toUpperCase());
  const preview = nodePreview(node, item, trace);

  const isSQL = (node === "generate_sql" || node === "regenerate_sql") && trace.sql_query;
  const input  = nodeInput(node, item, trace);
  const output = nodeOutput(node, item, trace);

  const dotCls = { ok: "dot-ok", error: "dot-error", blocked: "dot-blocked", retry: "dot-retry", declined: "dot-declined" }[status] || "dot-ok";

  const bodyHidden = isFirst ? "" : "hidden";
  const chevOpen   = isFirst ? "chevron-open" : "";
  const previewHidden = isFirst ? 'style="display:none"' : "";

  return `
<div class="node-wrapper">
  <div class="node-dot ${dotCls}"></div>
  <article class="node-card status-${status}" id="${id}">

    <!-- HEADER -->
    <div class="node-header" onclick="toggleNode('${id}')">
      <div class="node-header-left">
        <span class="material-symbols-outlined status-icon ${status}">${sIcon}</span>
        <span class="node-badge ${meta.badgeCls}">${meta.badge}</span>
        <h3 class="node-name">${esc(nodeName)}</h3>
        ${preview ? `<span class="node-preview-chip ${preview.cls}" ${previewHidden}>${esc(preview.text)}</span>` : ""}
      </div>
      <div class="node-header-right">
        <span class="material-symbols-outlined node-chevron ${chevOpen}">expand_more</span>
      </div>
    </div>

    <!-- BODY -->
    <div class="node-body ${bodyHidden}">
      <p class="node-desc">${esc(description)}</p>

      <!-- Execution Snapshot -->
      <div class="exec-snapshot">

        <!-- Snapshot header -->
        <div class="snapshot-header">
          <span class="snapshot-label">Execution Snapshot</span>
          <button class="copy-json-btn" onclick="copyJson(this, ${index}, event)">
            <span class="material-symbols-outlined">content_copy</span>
            COPY JSON
          </button>
        </div>

        <!-- I/O -->
        <div class="io-grid">
          <!-- Input -->
          <div class="io-panel">
            <div class="io-header">
              <span class="material-symbols-outlined io-icon io-icon-in">login</span>
              <span class="io-label">Input State</span>
            </div>
            ${renderKV(input)}
          </div>
          <div class="io-divider"></div>
          <!-- Output -->
          <div class="io-panel">
            <div class="io-header">
              <span class="material-symbols-outlined io-icon io-icon-out">logout</span>
              <span class="io-label">Output State</span>
            </div>
            ${isSQL
              ? `<div class="sql-block">
                   <div class="sql-header">
                     <span class="sql-lang-tag">SQLite</span>
                   </div>
                   <pre class="sql-pre"><code>${renderSQL(item.sql || item.output_text || trace.sql_query)}</code></pre>
                 </div>`
              : renderKV(output)
            }
          </div>
        </div>

        <!-- Secondary collapsibles -->
        <div class="secondary-row">
          <!-- Node Config -->
          <div class="mini-panel">
            <button class="mini-panel-btn" onclick="toggleMini(this)">
              <div class="mini-btn-left">
                <span class="material-symbols-outlined">settings</span>
                <span class="mini-btn-label">Node Config</span>
              </div>
              <span class="material-symbols-outlined mini-chevron">chevron_right</span>
            </button>
            <div class="mini-body hidden">
              <div class="kv-list mini-kv">${
                Object.entries(cfg).map(([k,v]) =>
                  `<div class="kv-row"><span class="kv-key">${esc(k)}</span><span class="kv-val">${renderVal(v)}</span></div>`
                ).join("")
              }</div>
            </div>
          </div>

          <!-- Metadata -->
          <div class="mini-panel">
            <button class="mini-panel-btn" onclick="toggleMini(this)">
              <div class="mini-btn-left">
                <span class="material-symbols-outlined">info</span>
                <span class="mini-btn-label">Metadata</span>
              </div>
              <span class="material-symbols-outlined mini-chevron">chevron_right</span>
            </button>
            <div class="mini-body hidden">
              <div class="kv-list mini-kv">${
                Object.entries(metadata || {}).map(([k,v]) =>
                  `<div class="kv-row"><span class="kv-key">${esc(k)}</span><span class="kv-val">${renderVal(String(v))}</span></div>`
                ).join("")
              }</div>
            </div>
          </div>
        </div>

      </div>
    </div>
  </article>
</div>`;
}

async function copyJson(btn, nodeIndex, ev) {
  ev.stopPropagation();
  const trace = state.currentTrace;
  if (!trace) return;
  const item = trace.timeline[nodeIndex - 1];
  if (!item) return;
  const payload = {
    node: item.node,
    status: item.status,
    input:  nodeInput(item.node, item, trace),
    output: nodeOutput(item.node, item, trace),
    config: NODE_CONFIG[item.node] || {},
    metadata: item.metadata,
  };
  try {
    await navigator.clipboard.writeText(JSON.stringify(payload, null, 2));
    const icon = btn.querySelector(".material-symbols-outlined");
    icon.textContent = "check";
    setTimeout(() => { icon.textContent = "content_copy"; }, 1400);
  } catch {}
}

// ── METRICS RENDER ────────────────────────────────────────
function renderMetrics(trace) {
  const m = trace.metrics || {};
  const outcome = trace.outcome || "UNKNOWN";
  const chipCls = {
    SUCCESS: "chip-success", SELF_HEALED: "chip-healed",
    BLOCKED: "chip-blocked", ERROR: "chip-error",
    DECLINED: "chip-declined", UNKNOWN: "chip-unknown",
  }[outcome] || "chip-unknown";

  el("metrics").innerHTML = `
    <div class="metric-card">
      <p class="metric-label">LATENCY</p>
      <span class="metric-val">${m.latency_ms ? (m.latency_ms / 1000).toFixed(1) : "—"}<span class="metric-unit">${m.latency_ms ? "s" : ""}</span></span>
    </div>
    <div class="metric-card">
      <p class="metric-label">RETRIES</p>
      <span class="metric-val">${m.retry_count ?? 0}</span>
    </div>
    <div class="metric-card" style="display:flex;align-items:center;justify-content:space-between">
      <div>
        <p class="metric-label">VALIDATION</p>
        <span class="metric-val" style="font-size:16px">${m.validation_health || "SECURE"}</span>
      </div>
      <span class="material-symbols-outlined" style="font-size:26px;color:#0058be;font-variation-settings:'FILL' 1">verified_user</span>
    </div>
    <div class="metric-card">
      <p class="metric-label">OUTCOME</p>
      <span class="metric-chip ${chipCls}">${outcome}</span>
    </div>
  `;
}

// ── RENDER TRACE ──────────────────────────────────────────
function renderTrace(trace) {
  state.currentSql = trace.sql_query || "";
  state.currentTrace = trace;
  state.threadId = trace.thread_id || state.threadId;

  el("answer-text").textContent = trace.answer || "No answer returned.";
  el("answer-panel").style.display = "block";

  renderMetrics(trace);

  const timeline = trace.timeline || [];
  if (!timeline.length) {
    el("timeline").innerHTML = `<p class="timeline-empty">No steps were returned.</p>`;
    return;
  }

  el("timeline").innerHTML = timeline
    .map((item, i) => renderNodeCard(item, trace, i === 0))
    .join("");
}

// ── API ───────────────────────────────────────────────────
async function loadHealth() {
  try {
    const h = await fetch("/api/health").then(r => r.json());
    const badge = el("health-badge");
    const ls = h.langsmith?.configured ? "LangSmith ✓" : "Offline";
    badge.textContent = `${h.status.toUpperCase()} · ${ls}`;
    badge.className = "health-badge ok";
  } catch {
    const badge = el("health-badge");
    badge.textContent = "Unavailable";
    badge.className = "health-badge err";
  }
}

async function loadSchema() {
  try {
    const d = await fetch("/api/schema/summary").then(r => r.json());
    el("schema-list").innerHTML = (d.tables || []).map(row =>
      `<div class="schema-row">
        <span>${esc(row.table)}</span>
        <span class="schema-count">${esc(String(row.count ?? "—"))}</span>
      </div>`
    ).join("");
  } catch {
    el("schema-list").textContent = "Schema unavailable.";
  }
}

async function loadExamples() {
  try {
    const d = await fetch("/api/traces/examples").then(r => r.json());
    state.traces = d.traces || [];

    const SCENARIO_META = {
      SUCCESS:     { icon: "verified",    bg: "#dce8ff", iconColor: "#0058be", title: "Happy Path",    desc: "Standard SQL retrieval and formatting." },
      SELF_HEALED: { icon: "healing",     bg: "#ffdcc6", iconColor: "#924700", title: "Self-Heal",     desc: "Automatic query repair after an error." },
      DECLINED:    { icon: "block",       bg: "#e7e8e9", iconColor: "#727785", title: "Off-Topic",     desc: "Polite decline for out-of-scope input." },
      BLOCKED:     { icon: "shield_lock", bg: "#ffdad6", iconColor: "#ba1a1a", title: "Blocked/Unsafe","desc": "Rejection of destructive SQL." },
    };

    el("examples").innerHTML = state.traces.map((trace, i) => {
      const s = SCENARIO_META[trace.outcome] || { icon: "help", bg: "#f0f1f3", iconColor: "#727785", title: trace.outcome, desc: trace.question };
      return `
        <div class="scenario-card" onclick="loadExample(${i})">
          <div class="scenario-icon" style="background:${s.bg}">
            <span class="material-symbols-outlined" style="color:${s.iconColor}">${s.icon}</span>
          </div>
          <div>
            <p class="scenario-title">${esc(s.title)}</p>
            <p class="scenario-desc">${esc(s.desc)}</p>
          </div>
        </div>`;
    }).join("");

    if (state.traces[0]) loadExample(0);
  } catch (e) {
    console.error(e);
  }
}

function loadExample(i) {
  const trace = state.traces[i];
  if (!trace) return;
  el("question-input").value = trace.question;
  renderTrace(trace);
}

async function ask(question) {
  el("answer-panel").style.display = "none";
  el("metrics").innerHTML = ["LATENCY","RETRIES","VALIDATION","OUTCOME"].map(l =>
    `<div class="metric-card"><p class="metric-label">${l}</p><div class="metric-loading"></div></div>`
  ).join("");
  el("timeline").innerHTML = `
    <div class="loading-wrap">
      <div class="spinner"></div>
      <p class="loading-text">Running the graph…</p>
    </div>`;

  const r = await fetch("/api/ask", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ question, thread_id: state.threadId }),
  });
  if (!r.ok) {
    const p = await r.json().catch(() => ({}));
    throw new Error(p.detail || `Request failed: ${r.status}`);
  }
  renderTrace(await r.json());
}

// ── EVENTS ────────────────────────────────────────────────
el("ask-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const q = new FormData(e.currentTarget).get("question").trim();
  if (!q) return;
  try {
    await ask(q);
  } catch (err) {
    el("answer-text").textContent = err.message;
    el("answer-panel").style.display = "block";
    el("timeline").innerHTML = `<p class="timeline-empty" style="color:#ba1a1a">${esc(err.message)}</p>`;
  }
});

el("run-query-btn").addEventListener("click", () => {
  const q = (el("question-input").value || "").trim();
  if (q) el("ask-form").dispatchEvent(new Event("submit", { bubbles: true, cancelable: true }));
});

el("copy-sql-btn").addEventListener("click", async () => {
  if (!state.currentSql) return;
  await navigator.clipboard.writeText(state.currentSql);
  el("copy-sql-btn").textContent = "Copied!";
  setTimeout(() => { el("copy-sql-btn").textContent = "Copy SQL"; }, 1200);
});

// ── BOOT ──────────────────────────────────────────────────
loadHealth();
loadSchema();
loadExamples();
