// ── TRACE HISTORY UI ─────────────────────────────────────────
// Handles loading, filtering, paging, and visualising previous queries.

const historyState = {
  page: 1,
  limit: 8,
  search: "",
  outcome: "",
  debounceTimer: null,
};

// ── Tab Navigation Overrides ─────────────────────────────────

function switchToTracer(e) {
  if (e) e.preventDefault();
  el("eval-main").style.display = "none";
  el("history-main").style.display = "none";
  el("main").style.display = "";
  
  el("nav-eval").classList.remove("nav-item-active");
  el("nav-history").classList.remove("nav-item-active");
  el("nav-tracer").classList.add("nav-item-active");
}

function switchToEval(e) {
  if (e) e.preventDefault();
  el("main").style.display = "none";
  el("history-main").style.display = "none";
  el("eval-main").style.display = "";
  
  el("nav-tracer").classList.remove("nav-item-active");
  el("nav-history").classList.remove("nav-item-active");
  el("nav-eval").classList.add("nav-item-active");
  
  if (typeof evalLoadHistory === "function") evalLoadHistory();
}

function switchToHistory(e) {
  if (e) e.preventDefault();
  el("main").style.display = "none";
  el("eval-main").style.display = "none";
  el("history-main").style.display = "";
  
  el("nav-tracer").classList.remove("nav-item-active");
  el("nav-eval").classList.remove("nav-item-active");
  el("nav-history").classList.add("nav-item-active");
  
  historyState.page = 1;
  loadHistory();
}

// ── Relative Time Helper ────────────────────────────────────

function getRelativeTime(timestampStr) {
  if (!timestampStr) return "—";
  
  // SQLite timestamps are stored in UTC format "YYYY-MM-DD HH:MM:SS" or ISO
  let date;
  if (timestampStr.includes("T")) {
    date = new Date(timestampStr);
  } else {
    // Append 'Z' to treat sqlite UTC correctly or fall back to native parsing
    date = new Date(timestampStr.replace(" ", "T") + "Z");
  }
  
  const now = new Date();
  const diffMs = now - date;
  
  // Handle local offset disparities or future dates from system timing
  if (diffMs < 0) return "just now";
  
  const diffSec = Math.floor(diffMs / 1000);
  const diffMin = Math.floor(diffSec / 60);
  const diffHour = Math.floor(diffMin / 60);
  const diffDay = Math.floor(diffHour / 24);
  
  if (diffSec < 15) return "just now";
  if (diffSec < 60) return `${diffSec} seconds ago`;
  if (diffMin < 60) return `${diffMin} min${diffMin !== 1 ? "s" : ""} ago`;
  if (diffHour < 24) return `${diffHour} hour${diffHour !== 1 ? "s" : ""} ago`;
  if (diffDay === 1) return "yesterday";
  if (diffDay < 7) return `${diffDay} days ago`;
  
  return date.toLocaleDateString(undefined, { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' });
}

// ── Search & Filter Callbacks ───────────────────────────────

function historyOnSearchInput(input) {
  clearTimeout(historyState.debounceTimer);
  historyState.debounceTimer = setTimeout(() => {
    historyState.search = input.value.trim();
    historyState.page = 1;
    loadHistory();
  }, 350);
}

function historyFilterOutcome(outcome, btn) {
  historyState.outcome = outcome === "all" ? "" : outcome;
  historyState.page = 1;
  
  // Update active state in filter group
  const group = el("history-filter-group");
  group.querySelectorAll("button").forEach(b => b.classList.remove("active"));
  btn.classList.add("active");
  
  loadHistory();
}

// ── Rendering Functions ─────────────────────────────────────

function getOutcomeBadge(outcome) {
  const meta = {
    SUCCESS:     { bg: "bg-emerald-50 text-emerald-700 border-emerald-200", dot: "bg-emerald-600" },
    SELF_HEALED: { bg: "bg-blue-50 text-blue-700 border-blue-200", dot: "bg-blue-600" },
    BLOCKED:     { bg: "bg-amber-50 text-amber-700 border-amber-200", dot: "bg-amber-600" },
    DECLINED:    { bg: "bg-slate-50 text-slate-700 border-slate-200", dot: "bg-slate-500" },
    ERROR:       { bg: "bg-red-50 text-red-700 border-red-200", dot: "bg-red-600" },
  }[outcome] || { bg: "bg-slate-50 text-slate-700 border-slate-200", dot: "bg-slate-500" };
  
  return `
    <span class="inline-flex items-center space-x-1.5 px-2.5 py-0.5 rounded-full border text-[10px] font-bold tracking-wider uppercase ${meta.bg}">
      <span class="w-1.5 h-1.5 rounded-full ${meta.dot}"></span>
      <span>${outcome}</span>
    </span>
  `;
}

async function loadHistory() {
  const tableBody = el("history-table-body");
  tableBody.innerHTML = `
    <tr>
      <td colspan="5" class="py-12 text-center">
        <div class="flex flex-col items-center justify-center space-y-2">
          <div class="w-6 h-6 border-2 border-slate-200 border-t-blue-600 rounded-full animate-spin"></div>
          <span class="text-xs text-slate-400">Loading audit history…</span>
        </div>
      </td>
    </tr>
  `;

  try {
    const url = new URL("/api/history", window.location.origin);
    url.searchParams.append("page", historyState.page);
    url.searchParams.append("limit", historyState.limit);
    if (historyState.search) url.searchParams.append("search", historyState.search);
    if (historyState.outcome) url.searchParams.append("outcome", historyState.outcome);

    const res = await fetch(url.toString());
    if (!res.ok) throw new Error(`HTTP Error: ${res.status}`);
    const data = await res.json();

    renderStats(data.stats);
    renderTable(data.traces, data.total_count);
    renderPagination(data.page, data.total_pages, data.total_count);

  } catch (err) {
    tableBody.innerHTML = `
      <tr>
        <td colspan="5" class="py-12 text-center">
          <span class="material-symbols-outlined text-red-500 text-3xl mb-1">error</span>
          <p class="text-xs font-semibold text-red-600">Failed to load history</p>
          <p class="text-[10px] text-slate-400 mt-1">${esc(err.message)}</p>
        </td>
      </tr>
    `;
  }
}

function renderStats(stats) {
  if (!stats) return;
  
  el("stat-total-queries").textContent = stats.total_queries.toLocaleString();
  el("stat-avg-latency").innerHTML = `${stats.avg_latency_s}<span class="text-xs font-medium text-slate-400 ml-1">s</span>`;
  el("stat-self-heal").textContent = `${stats.self_heal_rate_pct}%`;
  el("stat-error-rate").textContent = `${stats.error_rate_pct}%`;
  
  const healthText = el("history-health-text");
  healthText.textContent = `${stats.health_rate_pct}% Health`;
  
  const dot = el("history-health-dot");
  dot.className = "w-2 h-2 rounded-full";
  if (stats.health_rate_pct >= 95) {
    dot.classList.add("bg-emerald-600");
  } else if (stats.health_rate_pct >= 80) {
    dot.classList.add("bg-amber-500");
  } else {
    dot.classList.add("bg-red-600");
  }
}

function renderTable(traces, totalCount) {
  const tableBody = el("history-table-body");
  
  if (!traces || traces.length === 0) {
    tableBody.innerHTML = `
      <tr>
        <td colspan="5" class="py-16 text-center">
          <span class="material-symbols-outlined text-slate-300 text-4xl mb-2">find_in_page</span>
          <p class="text-xs font-medium text-slate-400">No traces found matching your filters</p>
        </td>
      </tr>
    `;
    return;
  }

  tableBody.innerHTML = traces.map((trace, idx) => {
    const isEven = idx % 2 === 0;
    const bgClass = isEven ? "bg-white" : "bg-slate-50/30";
    const latencySec = (trace.latency_ms / 1000).toFixed(1);
    
    return `
      <tr class="trace-row group hover:bg-slate-50 transition-colors ${bgClass}">
        <td class="px-6 py-4 text-xs text-slate-500 whitespace-nowrap">${esc(getRelativeTime(trace.timestamp))}</td>
        <td class="px-6 py-4 text-xs font-medium text-slate-800 truncate max-w-lg" title="${esc(trace.question)}">${esc(trace.question)}</td>
        <td class="px-6 py-4 whitespace-nowrap">${getOutcomeBadge(trace.outcome)}</td>
        <td class="px-6 py-4 text-xs font-mono text-slate-500 whitespace-nowrap">${latencySec}s</td>
        <td class="px-6 py-4 text-right whitespace-nowrap">
          <div class="flex items-center justify-end space-x-2 opacity-0 group-hover:opacity-100 transition-opacity duration-200">
            <button class="px-3 py-1 font-semibold text-[10px] bg-white border border-slate-200 text-slate-700 hover:border-blue-500 hover:text-blue-600 transition-all rounded shadow-sm" onclick="historyViewTrace(${trace.id})">
              View Trace
            </button>
            <button class="p-1 text-slate-400 hover:text-blue-600 transition-all rounded hover:bg-slate-100 flex" onclick="historyRerunTrace(${trace.id}, '${esc(trace.question.replace(/'/g, "\\'"))}')" title="Rerun Query">
              <span class="material-symbols-outlined text-[16px]">refresh</span>
            </button>
          </div>
        </td>
      </tr>
    `;
  }).join("");
}

function renderPagination(currentPage, totalPages, totalCount) {
  const footer = el("history-pagination-footer");
  if (totalCount === 0) {
    footer.innerHTML = "";
    return;
  }

  const startIdx = (currentPage - 1) * historyState.limit + 1;
  const endIdx = Math.min(currentPage * historyState.limit, totalCount);

  // Generate pagination buttons
  let buttonsHTML = "";
  
  // Left arrow
  buttonsHTML += `
    <button class="p-1.5 border border-slate-200 rounded-lg bg-white hover:bg-slate-50 disabled:opacity-30 transition-colors" ${currentPage === 1 ? "disabled" : ""} onclick="historyGoToPage(${currentPage - 1})">
      <span class="material-symbols-outlined text-[16px] flex">chevron_left</span>
    </button>
  `;

  // Always show first page, last page, and pages around current page
  const range = [];
  const delta = 1;
  for (let i = 1; i <= totalPages; i++) {
    if (i === 1 || i === totalPages || (i >= currentPage - delta && i <= currentPage + delta)) {
      range.push(i);
    }
  }

  let l;
  for (const i of range) {
    if (l) {
      if (i - l === 2) {
        buttonsHTML += `<button class="w-8 h-8 flex items-center justify-center bg-white border border-slate-200 hover:bg-slate-50 rounded-lg text-xs font-semibold text-slate-700" onclick="historyGoToPage(${l + 1})">${l + 1}</button>`;
      } else if (i - l > 2) {
        buttonsHTML += `<span class="px-1 text-slate-400 text-xs select-none">...</span>`;
      }
    }
    
    if (i === currentPage) {
      buttonsHTML += `
        <button class="w-8 h-8 flex items-center justify-center bg-blue-600 text-white rounded-lg text-xs font-bold shadow-sm shadow-blue-500/20">${i}</button>
      `;
    } else {
      buttonsHTML += `
        <button class="w-8 h-8 flex items-center justify-center bg-white border border-slate-200 hover:bg-slate-50 rounded-lg text-xs font-semibold text-slate-700 transition-colors" onclick="historyGoToPage(${i})">${i}</button>
      `;
    }
    l = i;
  }

  // Right arrow
  buttonsHTML += `
    <button class="p-1.5 border border-slate-200 rounded-lg bg-white hover:bg-slate-50 disabled:opacity-30 transition-colors" ${currentPage === totalPages ? "disabled" : ""} onclick="historyGoToPage(${currentPage + 1})">
      <span class="material-symbols-outlined text-[16px] flex">chevron_right</span>
    </button>
  `;

  footer.innerHTML = `
    <span class="text-slate-400 text-xs font-medium">Showing ${startIdx}-${endIdx} of ${totalCount.toLocaleString()} traces</span>
    <div class="flex items-center space-x-1">
      ${buttonsHTML}
    </div>
  `;
}

function historyGoToPage(p) {
  historyState.page = p;
  loadHistory();
}

// ── Interactive Logic Links ─────────────────────────────────

async function historyViewTrace(id) {
  try {
    const res = await fetch(`/api/history/${id}`);
    if (!res.ok) throw new Error(`Failed: ${res.status}`);
    const traceData = await res.json();
    
    // Switch to Tracer screen
    switchToTracer(null);
    
    // Render this trace on the tracer dashboard
    if (typeof window.renderTrace === "function") {
      window.renderTrace(traceData);
      
      // Auto populate question
      el("question-input").value = traceData.question;
    }
  } catch (err) {
    alert(`Could not open trace view: ${err.message}`);
  }
}

function historyRerunTrace(id, question) {
  // Switch to Tracer screen
  switchToTracer(null);
  
  // Populate the text input
  el("question-input").value = question;
  
  // Trigger form submit in app.js
  const form = el("ask-form");
  if (form) {
    form.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true }));
  }
}
