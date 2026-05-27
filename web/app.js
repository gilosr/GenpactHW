const state = {
  traces: [],
  currentSql: "",
  threadId: null,
};

const el = (id) => document.getElementById(id);

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

function metric(label, value) {
  return `<div class="metric"><span>${escapeHtml(label)}</span><strong>${escapeHtml(value)}</strong></div>`;
}

function renderMetadata(metadata) {
  const entries = Object.entries(metadata || {});
  if (!entries.length) return "";
  const rows = entries.map(([label, value]) => `
    <div class="metadata-row">
      <span class="metadata-label">${escapeHtml(label)}</span>
      <span class="metadata-value">${escapeHtml(value)}</span>
    </div>
  `).join("");
  return `
    <div class="metadata-box">
      <div class="metadata-title">Metadata</div>
      <div class="metadata-grid">${rows}</div>
    </div>`;
}

function renderTrace(trace) {
  state.currentSql = trace.sql_query || "";
  state.threadId = trace.thread_id || state.threadId;
  el("answer").textContent = trace.answer || "No answer returned.";
  el("metrics").innerHTML = [
    metric("Outcome", trace.outcome || "UNKNOWN"),
    metric("Latency", trace.metrics?.latency_ms ? `${trace.metrics.latency_ms} ms` : "Example"),
    metric("Retries", trace.metrics?.retry_count ?? 0),
    metric("Safety", trace.metrics?.validation_health || "SECURE"),
  ].join("");

  const timeline = trace.timeline || [];
  if (!timeline.length) {
    el("timeline").className = "timeline empty";
    el("timeline").textContent = "No trace steps were returned.";
    return;
  }

  el("timeline").className = "timeline";
  el("timeline").innerHTML = timeline.map((node) => {
    const outputClass = node.output_kind === "sql" ? "output-box code-output" : "output-box";
    const output = `
      <div class="${outputClass}">
        <div class="output-label">Output · ${escapeHtml(node.output_title || "Step output")}</div>
        <pre>${escapeHtml(node.output_text || node.detail || "No output recorded.")}</pre>
      </div>`;
    const metadata = renderMetadata(node.metadata);
    return `
      <article class="node">
        <div class="dot">${escapeHtml(node.index)}</div>
        <div class="node-card ${escapeHtml(node.status)}">
          <div class="node-head">
            <span class="node-name">${escapeHtml(node.node)}</span>
            <span class="status">${escapeHtml(node.status)}</span>
          </div>
          <p class="description">${escapeHtml(node.description)}</p>
          ${output}
          ${metadata}
        </div>
      </article>`;
  }).join("");
}

async function loadHealth() {
  try {
    const response = await fetch("/api/health");
    const health = await response.json();
    const langsmith = health.langsmith?.configured ? "LangSmith ready" : "Offline trace ready";
    el("health").textContent = `${health.status.toUpperCase()} · ${langsmith}`;
  } catch {
    el("health").textContent = "Service unavailable";
  }
}

async function loadSchema() {
  try {
    const response = await fetch("/api/schema/summary");
    const data = await response.json();
    el("schema").innerHTML = data.tables.map((row) => (
      `<div class="schema-row"><span>${escapeHtml(row.table)}</span><strong>${escapeHtml(row.count ?? "n/a")}</strong></div>`
    )).join("");
  } catch {
    el("schema").textContent = "Schema summary unavailable.";
  }
}

async function loadExamples() {
  const response = await fetch("/api/traces/examples");
  const data = await response.json();
  state.traces = data.traces || [];
  el("examples").innerHTML = state.traces.map((trace, index) => (
    `<button class="example-button" type="button" data-index="${index}">
      <strong>${escapeHtml(trace.outcome)}</strong><br>${escapeHtml(trace.question)}
    </button>`
  )).join("");
  el("examples").querySelectorAll("button").forEach((button) => {
    button.addEventListener("click", () => renderTrace(state.traces[Number(button.dataset.index)]));
  });
  if (state.traces[0]) renderTrace(state.traces[0]);
}

async function ask(question) {
  el("answer").innerHTML = '<span class="loading">Running the graph and building the trace...</span>';
  el("timeline").className = "timeline empty";
  el("timeline").textContent = "Waiting for agent response.";

  const response = await fetch("/api/ask", {
    method: "POST",
    headers: {"content-type": "application/json"},
    body: JSON.stringify({question, thread_id: state.threadId}),
  });
  if (!response.ok) {
    const payload = await response.json().catch(() => ({}));
    throw new Error(payload.detail || `Request failed with ${response.status}`);
  }
  renderTrace(await response.json());
}

el("ask-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const question = new FormData(event.currentTarget).get("question").trim();
  if (!question) return;
  try {
    await ask(question);
  } catch (error) {
    el("answer").textContent = error.message;
    el("timeline").className = "timeline empty";
    el("timeline").textContent = "The request did not complete.";
  }
});

el("copy-sql").addEventListener("click", async () => {
  if (!state.currentSql) return;
  await navigator.clipboard.writeText(state.currentSql);
  el("copy-sql").textContent = "Copied";
  setTimeout(() => { el("copy-sql").textContent = "Copy SQL"; }, 900);
});

loadHealth();
loadSchema();
loadExamples();
