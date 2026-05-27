// ── EVALUATION UI ─────────────────────────────────────────
// LLM-as-a-Judge evaluation pipeline — wizard flow
// Upload → Column Map → Progress → Results

const evalState = {
  sessionId: null,
  columns: [],
  preview: [],
  rowCount: 0,
  inputColumn: null,
  evalColumns: [],
  selectedIndices: [],
  previewAllRows: false,
  currentRunId: null,
  results: null,
  pollTimer: null,
  filter: "all",
};

const E = (id) => document.getElementById(id);

function esc(v) {
  return String(v ?? "")
    .replace(/&/g, "&amp;").replace(/</g, "&lt;")
    .replace(/>/g, "&gt;").replace(/"/g, "&quot;");
}

function renderMarkdown(text) {
  if (!text) return "—";
  
  // If the text has "Step 1: ... Step 2: ..." without newlines, add them
  let processedText = text;
  if (!text.includes('\n') && /Step \d+:/.test(text)) {
    processedText = text.replace(/(Step \d+:)/g, '\n$1').trim();
  }

  if (window.marked && window.marked.parse) {
    return marked.parse(processedText, { breaks: true });
  }
  return `<p>${esc(processedText).replace(/\n/g, '<br>')}</p>`;
}

// ── Navigation ────────────────────────────────────────────

function switchToEval(e) {
  if (e) e.preventDefault();
  E("main").style.display = "none";
  E("eval-main").style.display = "";
  E("nav-tracer").classList.remove("nav-item-active");
  E("nav-eval").classList.add("nav-item-active");
  evalLoadHistory();
}

function switchToTracer(e) {
  if (e) e.preventDefault();
  E("eval-main").style.display = "none";
  E("main").style.display = "";
  E("nav-eval").classList.remove("nav-item-active");
  E("nav-tracer").classList.add("nav-item-active");
}

// ── Step visibility ───────────────────────────────────────

function evalShowStep(step) {
  ["eval-step-upload", "eval-step-columns", "eval-step-progress", "eval-step-results"].forEach(id => {
    E(id).style.display = "none";
  });
  E(`eval-step-${step}`).style.display = "";
}

// ── Upload ────────────────────────────────────────────────

function evalSetupDragDrop() {
  const zone = E("eval-upload-zone");
  if (!zone) return;

  ["dragenter", "dragover"].forEach(evt => {
    zone.addEventListener(evt, (e) => {
      e.preventDefault();
      zone.classList.add("eval-upload-zone-active");
    });
  });

  ["dragleave", "drop"].forEach(evt => {
    zone.addEventListener(evt, (e) => {
      e.preventDefault();
      zone.classList.remove("eval-upload-zone-active");
    });
  });

  zone.addEventListener("drop", (e) => {
    const file = e.dataTransfer.files[0];
    if (file) evalUploadFile(file);
  });

  zone.addEventListener("click", () => {
    E("eval-file-input").click();
  });

  E("eval-file-input").addEventListener("change", (e) => {
    if (e.target.files[0]) evalUploadFile(e.target.files[0]);
  });
}

async function evalUploadFile(file) {
  if (!file.name.toLowerCase().endsWith(".csv")) {
    alert("Please upload a .csv file");
    return;
  }

  const zone = E("eval-upload-zone");
  const content = zone.querySelector(".eval-upload-content");
  if (content) {
    content.innerHTML = `
      <div class="eval-upload-spinner"></div>
      <p class="eval-upload-title">Uploading & parsing…</p>
    `;
  }

  try {
    const form = new FormData();
    form.append("file", file);
    const res = await fetch("/api/eval/upload", { method: "POST", body: form });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail || `Upload failed: ${res.status}`);
    }
    const data = await res.json();
    evalState.sessionId = data.session_id;
    evalState.columns = data.columns;
    evalState.preview = data.preview;
    evalState.rowCount = data.row_count;
    evalState.selectedIndices = Array.from({ length: data.row_count }, (_, i) => i);
    evalState.previewAllRows = false;

    // Show file info
    E("eval-upload-zone").style.display = "none";
    E("eval-file-info").style.display = "flex";
    E("eval-file-name").textContent = file.name;
    E("eval-file-rows").textContent = `${data.row_count} rows · ${data.columns.length} columns`;

    // Render column mapping
    evalRenderColumnMapper();
    E("eval-step-columns").style.display = "";

  } catch (err) {
    const content = zone.querySelector(".eval-upload-content");
    if (content) {
      content.innerHTML = `
        <span class="material-symbols-outlined eval-upload-icon" style="color:#dc2626">error</span>
        <p class="eval-upload-title" style="color:#dc2626">${esc(err.message)}</p>
        <p class="eval-upload-sub"><a href="#" onclick="evalClearFile();return false" class="eval-upload-link">Try again</a></p>
      `;
    }
  }
}

function evalClearFile() {
  evalState.sessionId = null;
  evalState.columns = [];
  evalState.preview = [];
  evalState.inputColumn = null;
  evalState.evalColumns = [];
  evalState.selectedIndices = [];

  const zone = E("eval-upload-zone");
  zone.style.display = "";
  // Reset the content without removing the hidden file input
  const content = zone.querySelector(".eval-upload-content");
  if (content) {
    content.innerHTML = `
      <span class="material-symbols-outlined eval-upload-icon">cloud_upload</span>
      <p class="eval-upload-title">Drop your CSV file here</p>
      <p class="eval-upload-sub">or <a href="#" onclick="E('eval-file-input').click();return false" class="eval-upload-link">browse files</a></p>
      <p class="eval-upload-hint">Supports .csv files with headers</p>
    `;
  }
  
  E("eval-file-info").style.display = "none";
  E("eval-step-columns").style.display = "none";
  const fileInput = E("eval-file-input");
  if (fileInput) fileInput.value = "";

  evalSetupDragDrop();
}

function evalBackToMapper() {
  if (!evalState.sessionId) {
    evalReset();
    return;
  }
  evalState.currentRunId = null;
  evalState.results = null;
  _loggedInstances.clear();
  if (evalState.pollTimer) clearInterval(evalState.pollTimer);
  evalShowStep("columns");
}

// ── Column Mapper ─────────────────────────────────────────

function evalToggleRowSelection(idx) {
  const i = evalState.selectedIndices.indexOf(idx);
  if (i > -1) {
    evalState.selectedIndices.splice(i, 1);
  } else {
    evalState.selectedIndices.push(idx);
  }
  evalUpdateSelectionCount();
}

function evalToggleAllSelection(checked) {
  if (checked) {
    evalState.selectedIndices = Array.from({ length: evalState.rowCount }, (_, i) => i);
  } else {
    evalState.selectedIndices = [];
  }
  evalRenderPreviewTable();
  evalUpdateSelectionCount();
}

function evalUpdateSelectionCount() {
  const count = evalState.selectedIndices.length;
  const el = E("eval-selection-count");
  if (el) {
    el.textContent = `${count} rows selected`;
    el.style.color = count === 0 ? "#dc2626" : "#16a34a";
  }
  const btn = E("eval-start-btn");
  if (btn) {
    btn.disabled = !evalState.inputColumn || evalState.evalColumns.length === 0 || count === 0;
  }
}

function evalRenderColumnMapper() {
  const inputContainer = E("eval-input-cols");
  const checkContainer = E("eval-check-cols");

  inputContainer.innerHTML = evalState.columns.map(col => `
    <label class="eval-col-item">
      <input type="radio" name="eval-input-col" value="${esc(col)}" onchange="evalOnColumnChange()" />
      <span class="eval-col-label">${esc(col)}</span>
      <span class="eval-col-sample">${esc(getSample(col))}</span>
    </label>
  `).join("");

  checkContainer.innerHTML = evalState.columns.map(col => `
    <label class="eval-col-item">
      <input type="checkbox" name="eval-check-col" value="${esc(col)}" onchange="evalOnColumnChange()" />
      <span class="eval-col-label">${esc(col)}</span>
      <span class="eval-col-sample">${esc(getSample(col))}</span>
    </label>
  `).join("");

  evalRenderPreviewTable();
}

function getSample(col) {
  if (!evalState.preview.length) return "";
  const val = evalState.preview[0][col] || "";
  return val.length > 50 ? val.substring(0, 50) + "…" : val;
}

function evalOnColumnChange() {
  const inputRadio = document.querySelector('input[name="eval-input-col"]:checked');
  evalState.inputColumn = inputRadio ? inputRadio.value : null;

  evalState.evalColumns = Array.from(document.querySelectorAll('input[name="eval-check-col"]:checked'))
    .map(cb => cb.value);

  // Update start button
  const btn = E("eval-start-btn");
  btn.disabled = !evalState.inputColumn || evalState.evalColumns.length === 0;

  // Highlight columns in preview
  evalRenderPreviewTable();
  evalUpdateSelectionCount();
}

function evalShowAllPreview() {
  evalState.previewAllRows = true;
  evalRenderPreviewTable();
}

function evalRenderPreviewTable() {
  const table = E("eval-preview-table");
  if (!evalState.preview.length) {
    table.innerHTML = '<tr><td class="eval-preview-empty">No data to preview</td></tr>';
    return;
  }

  const cols = evalState.columns;
  const isInput = (c) => c === evalState.inputColumn;
  const isEval = (c) => evalState.evalColumns.includes(c);

  const allSelected = evalState.selectedIndices.length === evalState.rowCount;

  let html = "<thead><tr>";
  html += `<th style="width:40px;text-align:center"><input type="checkbox" ${allSelected ? 'checked' : ''} onchange="evalToggleAllSelection(this.checked)" /></th>`;
  html += "<th>#</th>";
  html += cols.map(c => {
    let cls = "";
    if (isInput(c)) cls = "eval-col-highlight-input";
    else if (isEval(c)) cls = "eval-col-highlight-eval";
    return `<th class="${cls}">${esc(c)}</th>`;
  }).join("");
  html += "</tr></thead><tbody>";

  const rowsToDisplay = evalState.previewAllRows ? evalState.preview : evalState.preview.slice(0, 10);

  rowsToDisplay.forEach((row, displayIdx) => {
    // Calculate original absolute index
    const absoluteIdx = displayIdx; 
    // Since rowsToDisplay is either the whole array or the first 10, 
    // displayIdx matches absoluteIdx for the first 10.
    
    const isRowSelected = evalState.selectedIndices.includes(absoluteIdx);
    html += `<tr style="${isRowSelected ? '' : 'opacity:0.5;background:#f9fafb'}">`;
    html += `<td style="text-align:center"><input type="checkbox" ${isRowSelected ? 'checked' : ''} onchange="evalToggleRowSelection(${absoluteIdx})" /></td>`;
    html += `<td style="color:#9ba0ad;font-family:'JetBrains Mono';font-size:11px">${absoluteIdx + 1}</td>`;
    html += cols.map(c => {
      let cls = "";
      if (isInput(c)) cls = "eval-col-highlight-input";
      else if (isEval(c)) cls = "eval-col-highlight-eval";
      const val = row[c] || "";
      return `<td class="${cls}">${esc(val.length > 80 ? val.substring(0, 80) + "…" : val)}</td>`;
    }).join("");
    html += "</tr>";
  });

  if (!evalState.previewAllRows && evalState.rowCount > 10) {
    const hiddenCount = evalState.rowCount - rowsToDisplay.length;
    html += `<tr><td colspan="${cols.length + 2}" style="text-align:center;padding:12px;background:#f8f9fa">
      <div style="display:flex;flex-direction:column;gap:4px;align-items:center">
        <span style="color:#727785;font-size:11px">Showing first ${rowsToDisplay.length} rows. ${hiddenCount} more rows hidden.</span>
        <button class="eval-filter-btn" style="background:#0058be;color:#fff;border-color:#0058be" onclick="evalShowAllPreview()">Show All Rows</button>
      </div>
    </td></tr>`;
  }

  html += "</tbody>";
  table.innerHTML = html;
}

// ── Evaluation Run ────────────────────────────────────────

async function evalStartRun() {
  if (!evalState.inputColumn || evalState.evalColumns.length === 0) return;

  evalShowStep("progress");
  E("eval-progress-fill").style.width = "0%";
  E("eval-progress-text").textContent = "Starting evaluation…";
  E("eval-progress-pct").textContent = "0%";
  E("eval-progress-log").innerHTML = "";

  try {
    const res = await fetch("/api/eval/run", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({
        session_id: evalState.sessionId,
        input_column: evalState.inputColumn,
        eval_columns: evalState.evalColumns,
        selected_indices: evalState.selectedIndices,
        dataset_name: E("eval-dataset-name").value || "Evaluation Run",
      }),
    });

    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail || `Failed: ${res.status}`);
    }

    const data = await res.json();
    evalState.currentRunId = data.run_id;
    evalPollStatus();

  } catch (err) {
    E("eval-progress-text").textContent = `Error: ${err.message}`;
    E("eval-progress-fill").classList.add("eval-progress-error");
  }
}

function evalPollStatus() {
  if (evalState.pollTimer) clearInterval(evalState.pollTimer);
  evalState.pollTimer = setInterval(async () => {
    try {
      const res = await fetch(`/api/eval/status/${evalState.currentRunId}`);
      const data = await res.json();

      E("eval-progress-fill").style.width = `${data.progress.percent}%`;
      E("eval-progress-pct").textContent = `${Math.round(data.progress.percent)}%`;

      if (data.current_instance) {
        E("eval-progress-text").textContent = `Evaluating ${data.progress.current} of ${data.progress.total}`;
        evalAddProgressLog(data.current_instance);
      }

      if (data.status === "completed") {
        clearInterval(evalState.pollTimer);
        E("eval-progress-text").textContent = "Evaluation complete!";
        E("eval-progress-fill").style.width = "100%";
        setTimeout(() => evalLoadResults(evalState.currentRunId), 600);
      } else if (data.status === "failed") {
        clearInterval(evalState.pollTimer);
        E("eval-progress-text").textContent = `Failed: ${data.error || "Unknown error"}`;
        E("eval-progress-fill").classList.add("eval-progress-error");
        E("eval-progress-actions").style.display = "block";
      }
    } catch (err) {
      // Silent retry on network error
    }
  }, 2000);
}

const _loggedInstances = new Set();
function evalAddProgressLog(text) {
  if (_loggedInstances.has(text)) return;
  _loggedInstances.add(text);
  const log = E("eval-progress-log");
  const entry = document.createElement("div");
  entry.className = "eval-log-entry eval-log-fadein";
  entry.innerHTML = `<span class="material-symbols-outlined" style="font-size:14px;color:#16a34a">check</span> ${esc(text)}`;
  log.prepend(entry);
  if (log.children.length > 10) log.removeChild(log.lastChild);
}

// ── Results ───────────────────────────────────────────────

async function evalLoadResults(runId) {
  try {
    const res = await fetch(`/api/eval/results/${runId}`);
    const data = await res.json();
    evalState.results = data;
    evalRenderResults(data);
    evalShowStep("results");
    evalLoadHistory();
  } catch (err) {
    E("eval-progress-text").textContent = `Failed to load results: ${err.message}`;
  }
}

function evalRenderResults(data) {
  evalRenderMetricCards(data.statistics);
  evalRenderDistribution(data.statistics);
  evalRenderPerColumn(data.statistics, data.eval_columns);
  evalRenderInstanceTable(data.instances, data.eval_columns);
}

// ── Metric Cards ──────────────────────────────────────────

function evalRenderMetricCards(stats) {
  const avgScore = stats.overall_avg_score || 0;
  const passRate = stats.overall_pass_rate || 0;
  const total = stats.total_instances || 0;
  const latency = stats.total_latency_s || 0;

  const scoreColor = avgScore >= 4 ? "#16a34a" : avgScore >= 3 ? "#d97706" : "#dc2626";
  const passColor = passRate >= 0.8 ? "#16a34a" : passRate >= 0.5 ? "#d97706" : "#dc2626";

  let html = `
    <div class="eval-metric-card">
      <div class="eval-metric-icon-wrap" style="background:#dce8ff">
        <span class="material-symbols-outlined" style="color:#0058be;font-variation-settings:'FILL' 1">stars</span>
      </div>
      <div class="eval-metric-body">
        <p class="eval-metric-label">OVERALL SCORE</p>
        <p class="eval-metric-value" style="color:${scoreColor}">${avgScore.toFixed(2)}<span class="eval-metric-unit"> / 5</span></p>
      </div>
    </div>
    <div class="eval-metric-card">
      <div class="eval-metric-icon-wrap" style="background:#dcfce7">
        <span class="material-symbols-outlined" style="color:#16a34a;font-variation-settings:'FILL' 1">verified</span>
      </div>
      <div class="eval-metric-body">
        <p class="eval-metric-label">PASS RATE</p>
        <p class="eval-metric-value" style="color:${passColor}">${(passRate * 100).toFixed(1)}<span class="eval-metric-unit">%</span></p>
      </div>
    </div>`;

  if (stats.execution_accuracy !== null && stats.execution_accuracy !== undefined) {
    const exRate = stats.execution_accuracy;
    const exColor = exRate >= 0.8 ? "#16a34a" : exRate >= 0.5 ? "#d97706" : "#dc2626";
    html += `
    <div class="eval-metric-card">
      <div class="eval-metric-icon-wrap" style="background:#fae8ff">
        <span class="material-symbols-outlined" style="color:#a21caf;font-variation-settings:'FILL' 1">code</span>
      </div>
      <div class="eval-metric-body">
        <p class="eval-metric-label">EXECUTION ACCURACY</p>
        <p class="eval-metric-value" style="color:${exColor}">${(exRate * 100).toFixed(1)}<span class="eval-metric-unit">%</span></p>
      </div>
    </div>`;
  }

  html += `
    <div class="eval-metric-card">
      <div class="eval-metric-icon-wrap" style="background:#f0f1f3">
        <span class="material-symbols-outlined" style="color:#505f76;font-variation-settings:'FILL' 1">dataset</span>
      </div>
      <div class="eval-metric-body">
        <p class="eval-metric-label">INSTANCES</p>
        <p class="eval-metric-value">${total}</p>
      </div>
    </div>
    <div class="eval-metric-card">
      <div class="eval-metric-icon-wrap" style="background:#fef3c7">
        <span class="material-symbols-outlined" style="color:#d97706;font-variation-settings:'FILL' 1">timer</span>
      </div>
      <div class="eval-metric-body">
        <p class="eval-metric-label">EVAL TIME</p>
        <p class="eval-metric-value">${latency < 60 ? latency.toFixed(1) + "s" : (latency / 60).toFixed(1) + "m"}</p>
      </div>
    </div>
  `;

  E("eval-metrics").innerHTML = html;
}

// ── Score Distribution ────────────────────────────────────

const SCORE_COLORS = {
  EXCELLENT: { bg: "#dce8ff", fg: "#0058be" },
  GOOD:      { bg: "#dcfce7", fg: "#16a34a" },
  ACCEPTABLE:{ bg: "#fef3c7", fg: "#d97706" },
  POOR:      { bg: "#fecaca", fg: "#dc2626" },
  FAIL:      { bg: "#fee2e2", fg: "#7f1d1d" },
};

function evalRenderDistribution(stats) {
  const dist = stats.score_distribution || {};
  const total = Object.values(dist).reduce((a, b) => a + b, 0) || 1;

  E("eval-distribution").innerHTML = ["EXCELLENT", "GOOD", "ACCEPTABLE", "POOR", "FAIL"].map(label => {
    const count = dist[label] || 0;
    const pct = (count / total * 100).toFixed(1);
    const c = SCORE_COLORS[label];
    return `
      <div class="eval-dist-row">
        <span class="eval-dist-label">${label}</span>
        <div class="eval-dist-bar-track">
          <div class="eval-dist-bar-fill" style="width:${pct}%;background:${c.fg}"></div>
        </div>
        <span class="eval-dist-count">${count}</span>
      </div>`;
  }).join("");
}

// ── Per-Column Breakdown ──────────────────────────────────

function evalRenderPerColumn(stats, evalCols) {
  const avgMap = stats.per_column_avg || {};
  const passMap = stats.per_column_pass_rate || {};

  E("eval-per-column").innerHTML = (evalCols || []).map(col => {
    const avg = avgMap[col] || 0;
    const pass = passMap[col] || 0;
    const barPct = (avg / 5 * 100).toFixed(0);
    const color = avg >= 4 ? "#16a34a" : avg >= 3 ? "#d97706" : "#dc2626";
    return `
      <div class="eval-col-stat-card">
        <div class="eval-col-stat-header">
          <span class="eval-col-stat-name">${esc(col)}</span>
          <span class="eval-col-stat-score" style="color:${color}">${avg.toFixed(2)}</span>
        </div>
        <div class="eval-col-stat-bar-track">
          <div class="eval-col-stat-bar-fill" style="width:${barPct}%;background:${color}"></div>
        </div>
        <span class="eval-col-stat-pass">Pass rate: ${(pass * 100).toFixed(1)}%</span>
      </div>`;
  }).join("");
}

// ── Instance Table ────────────────────────────────────────

function evalRenderInstanceTable(instances, evalCols) {
  if (!instances || !instances.length) {
    E("eval-instance-table").innerHTML = '<tr><td class="eval-preview-empty">No instances</td></tr>';
    return;
  }

  const hasEx = instances.some(i => i.execution_accuracy !== null && i.execution_accuracy !== undefined);

  let html = "<thead><tr><th>#</th><th>Input</th>";
  html += (evalCols || []).map(c => `<th>${esc(c)}</th>`).join("");
  if (hasEx) {
    html += "<th>EX</th>";
  }
  html += "<th>Avg</th><th>Status</th><th></th></tr></thead><tbody>";

  instances.forEach((inst, idx) => {
    const filtered = evalState.filter;
    if (filtered === "pass" && !inst.passed) return;
    if (filtered === "fail" && inst.passed) return;

    const inputShort = inst.input_text.length > 60 ? inst.input_text.substring(0, 60) + "…" : inst.input_text;
    html += `<tr class="eval-instance-row" onclick="evalToggleDetail(${idx})">`;
    html += `<td class="eval-td-num">${inst.row_index}</td>`;
    html += `<td class="eval-td-input">${esc(inputShort)}</td>`;

    (evalCols || []).forEach(col => {
      const cs = inst.scores[col];
      if (cs) {
        html += `<td>${evalScoreBadge(cs.score_label, cs.score)}</td>`;
      } else {
        html += '<td><span class="eval-score-badge eval-score-na">N/A</span></td>';
      }
    });

    if (hasEx) {
      const ex = inst.execution_accuracy;
      html += `<td>${ex === null || ex === undefined ? '—' : ex ? '<span style="color:#16a34a;font-weight:bold">✓</span>' : '<span style="color:#dc2626;font-weight:bold">✗</span>'}</td>`;
    }

    const avgColor = inst.avg_score >= 4 ? "#16a34a" : inst.avg_score >= 3 ? "#d97706" : "#dc2626";
    html += `<td><strong style="color:${avgColor}">${inst.avg_score.toFixed(1)}</strong></td>`;
    html += `<td>${inst.passed
      ? '<span class="eval-pass-badge">PASS</span>'
      : '<span class="eval-fail-badge">FAIL</span>'}</td>`;
    html += `<td><span class="material-symbols-outlined eval-expand-icon" id="eval-expand-${idx}">expand_more</span></td>`;
    html += "</tr>";

    // Detail row (hidden by default)
    html += `<tr class="eval-detail-row" id="eval-detail-${idx}" style="display:none"><td colspan="${(evalCols || []).length + 5 + (hasEx ? 1 : 0)}">`;
    html += evalRenderInstanceDetail(inst, evalCols);
    html += "</td></tr>";
  });

  html += "</tbody>";
  E("eval-instance-table").innerHTML = html;
}

function evalScoreBadge(label, score) {
  const c = SCORE_COLORS[label] || { bg: "#f0f1f3", fg: "#505f76" };
  return `<span class="eval-score-badge" style="background:${c.bg};color:${c.fg}">${label}</span>`;
}

function evalRenderInstanceDetail(inst, evalCols) {
  let html = '<div class="eval-detail-content">';

  // Execution Accuracy
  if (inst.execution_accuracy !== null && inst.execution_accuracy !== undefined) {
    const ex = inst.execution_accuracy;
    html += `<div class="eval-detail-section" style="border-left:4px solid ${ex ? '#16a34a' : '#dc2626'};padding-left:12px">
      <h5 class="eval-detail-title" style="display:flex;align-items:center;gap:6px">
        <span class="material-symbols-outlined" style="color:${ex ? '#16a34a' : '#dc2626'}">${ex ? 'task_alt' : 'cancel'}</span>
        Execution Accuracy: ${ex ? '<span style="color:#16a34a">MATCH</span>' : '<span style="color:#dc2626">MISMATCH</span>'}
      </h5>
      <p class="eval-detail-text" style="font-size:12px;color:#505f76">The SQL query generated by the agent was executed against the database and compared directly with the expected SQL results.</p>
    </div>`;
  }

  // Agent output
  html += `<div class="eval-detail-section">
    <h5 class="eval-detail-title"><span class="material-symbols-outlined" style="font-size:16px">smart_toy</span> Agent Output</h5>
    <div class="eval-markdown-body">${renderMarkdown(inst.agent_answer || "—")}</div>
    ${inst.agent_sql ? `<pre class="eval-detail-sql">${esc(inst.agent_sql)}</pre>` : ""}
  </div>`;

  // Per-column judge results
  (evalCols || []).forEach(col => {
    const cs = inst.scores[col];
    if (!cs) return;
    html += `<div class="eval-detail-section">
      <h5 class="eval-detail-title">
        <span class="material-symbols-outlined" style="font-size:16px">gavel</span>
        ${esc(col)} — ${evalScoreBadge(cs.score_label, cs.score)}
        <span class="eval-detail-confidence">Confidence: ${(cs.confidence * 100).toFixed(0)}%</span>
      </h5>
      <div class="eval-detail-compare">
        <div class="eval-detail-expected">
          <span class="eval-detail-compare-label">Expected</span>
          <p>${esc(cs.expected || "—")}</p>
        </div>
        <div class="eval-detail-actual">
          <span class="eval-detail-compare-label">Actual</span>
          <p>${esc(cs.actual || "—")}</p>
        </div>
      </div>
      <div class="eval-detail-reasoning">
        <span class="eval-detail-compare-label">Judge Reasoning</span>
        <div class="eval-markdown-body">${renderMarkdown(cs.reasoning || "—")}</div>
      </div>
    </div>`;
  });

  // Execution accuracy side-by-side data comparison
  if (inst.execution_accuracy !== null && inst.execution_accuracy !== undefined) {
    const isOk = inst.execution_accuracy;
    const badge = isOk 
      ? '<span class="eval-pass-badge">✓ MATCH</span>' 
      : '<span class="eval-fail-badge">✗ MISMATCH</span>';
    
    html += `<div class="eval-detail-section">
      <h5 class="eval-detail-title">
        <span class="material-symbols-outlined" style="font-size:16px">database</span>
        Execution Accuracy — ${badge}
      </h5>`;

    const diff = inst.execution_accuracy_diff;
    if (diff && !isOk) {
      html += `<div class="eval-diff-summary" style="margin-bottom:12px;padding:10px;background:#fff5f5;border:1px solid #feb2b2;border-radius:6px">
        <p style="font-size:13px;font-weight:700;color:#c53030;margin-bottom:4px">Mismatch Detail: ${esc(diff.message)}</p>
        ${diff.missing_rows && diff.missing_rows.length ? `
          <div style="margin-top:8px">
            <span class="eval-detail-compare-label" style="color:#c53030">Rows missing in actual (showing first ${diff.missing_rows.length}):</span>
            ${renderMiniTable(diff.missing_rows)}
          </div>
        ` : ''}
        ${diff.extra_rows && diff.extra_rows.length ? `
          <div style="margin-top:8px">
            <span class="eval-detail-compare-label" style="color:#c53030">Extra rows in actual (showing first ${diff.extra_rows.length}):</span>
            ${renderMiniTable(diff.extra_rows)}
          </div>
        ` : ''}
      </div>`;
    }

    html += `<div class="eval-data-compare-grid">
        <div class="eval-data-pane">
          <span class="eval-detail-compare-label">Expected Data (First 5 rows)</span>
          ${renderMiniTable(inst.expected_data_preview)}
        </div>
        <div class="eval-data-pane">
          <span class="eval-detail-compare-label">Actual Data (First 5 rows)</span>
          ${renderMiniTable(inst.actual_data_preview)}
        </div>
      </div>
    </div>`;
  }

  html += "</div>";
  return html;
}

function renderMiniTable(rows) {
  if (!rows || !rows.length) return '<div class="eval-data-empty">No results or empty set</div>';
  const keys = Object.keys(rows[0]);
  let h = '<div class="eval-mini-table-wrap"><table class="eval-mini-table"><thead><tr>';
  h += keys.map(k => `<th>${esc(k)}</th>`).join("");
  h += "</tr></thead><tbody>";
  rows.forEach(row => {
    h += "<tr>" + keys.map(k => `<td>${esc(row[k])}</td>`).join("") + "</tr>";
  });
  h += "</tbody></table></div>";
  return h;
}

function evalToggleDetail(idx) {
  const row = E(`eval-detail-${idx}`);
  const icon = E(`eval-expand-${idx}`);
  if (!row) return;
  const hidden = row.style.display === "none";
  row.style.display = hidden ? "" : "none";
  if (icon) icon.textContent = hidden ? "expand_less" : "expand_more";
}

// ── Filtering ─────────────────────────────────────────────

function evalFilterTable(filter, btn) {
  evalState.filter = filter;
  document.querySelectorAll(".eval-filter-btn").forEach(b => b.classList.remove("eval-filter-active"));
  if (btn) btn.classList.add("eval-filter-active");

  if (evalState.results) {
    evalRenderInstanceTable(evalState.results.instances, evalState.results.eval_columns);
  }
}

function toggleMetricsGuide() {
  const content = E("metrics-guide-content");
  const chev = E("metrics-guide-chevron");
  const isHidden = content.style.display === "none";
  content.style.display = isHidden ? "" : "none";
  chev.textContent = isHidden ? "expand_less" : "expand_more";
}

// ── Run History ───────────────────────────────────────────

async function evalLoadHistory() {
  try {
    const res = await fetch("/api/eval/runs");
    const data = await res.json();
    const runs = data.runs || [];

    if (runs.length === 0) {
      E("eval-history-bar").style.display = "none";
      return;
    }

    E("eval-history-bar").style.display = "";
    E("eval-history-list").innerHTML = runs.slice(0, 5).map(run => {
      const isRunning = run.status === "running";
      const scoreColor = run.overall_avg_score >= 4 ? "#16a34a" : run.overall_avg_score >= 3 ? "#d97706" : "#dc2626";
      const date = new Date(run.created_at).toLocaleDateString();
      
      let scoreHtml = `<div class="eval-history-score" style="color:${scoreColor}">${run.overall_avg_score.toFixed(1)}</div>`;
      if (isRunning) {
        scoreHtml = `<div class="eval-history-running" title="Evaluation in progress"><span class="eval-spin material-symbols-outlined" style="font-size:18px;color:#0058be">hourglass_top</span></div>`;
      }

      return `
        <div class="eval-history-item" onclick="evalLoadResults('${run.run_id}')">
          <div class="eval-history-info">
            <span class="eval-history-name">${esc(run.dataset_name)}</span>
            <span class="eval-history-date">${date} · ${run.total_instances} instances ${isRunning ? '<span style="color:#0058be;margin-left:4px">(Running...)</span>' : ""}</span>
          </div>
          ${scoreHtml}
        </div>`;
    }).join("");
  } catch {
    // Silently ignore history load failures
  }
}

// ── Reset ─────────────────────────────────────────────────

function evalReset() {
  evalState.currentRunId = null;
  evalState.results = null;
  evalState.filter = "all";
  _loggedInstances.clear();
  if (evalState.pollTimer) clearInterval(evalState.pollTimer);
  evalClearFile();
  evalShowStep("upload");
}

// ── Boot ──────────────────────────────────────────────────

document.addEventListener("DOMContentLoaded", () => {
  evalSetupDragDrop();
});

// Also run setup immediately in case DOMContentLoaded already fired
if (document.readyState !== "loading") {
  evalSetupDragDrop();
}
