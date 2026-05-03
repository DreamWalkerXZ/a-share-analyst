const form = document.querySelector("#runForm");
const runButton = document.querySelector("#runButton");
const companyInput = document.querySelector("#companyInput");
const yearInput = document.querySelector("#yearInput");
const noProxyInput = document.querySelector("#noProxyInput");
const traceInput = document.querySelector("#traceInput");
const stepsEl = document.querySelector("#steps");
const logStream = document.querySelector("#logStream");
const reportPreview = document.querySelector("#reportPreview");
const reportPath = document.querySelector("#reportPath");
const reportList = document.querySelector("#reportList");
const refreshReportsButton = document.querySelector("#refreshReportsButton");
const clearLogButton = document.querySelector("#clearLogButton");
const jobStatus = document.querySelector("#jobStatus");
const serverStatus = document.querySelector("#serverStatus");
const initialData = document.querySelector("#initialData");
const finalData = document.querySelector("#finalData");
const toolCalls = document.querySelector("#toolCalls");
const duration = document.querySelector("#duration");
const processPanel = document.querySelector(".process-panel");

let currentSource = null;
let currentJobId = "";
let latestState = null;
let timerInterval = null;

const idleSteps = [
  ["parse", "输入解析"],
  ["prefetch", "核心数据预取"],
  ["parse_data", "LLM 数据解析"],
  ["react", "ReAct 补充采集"],
  ["sections", "章节生成验证"],
  ["output", "Markdown 输出"],
].map(([id, label]) => ({ id, label, status: "idle", detail: "等待", progress: 0 }));

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

function inlineMarkdown(value) {
  return escapeHtml(value).replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>");
}

function markdownToHtml(markdown) {
  const lines = markdown.split(/\r?\n/);
  const html = [];
  let inList = false;

  const closeList = () => {
    if (inList) {
      html.push("</ul>");
      inList = false;
    }
  };

  for (const rawLine of lines) {
    const line = rawLine.trimEnd();
    const trimmed = line.trim();

    if (!trimmed) {
      closeList();
      continue;
    }

    if (/^-{3,}$/.test(trimmed)) {
      closeList();
      html.push("<hr />");
      continue;
    }

    if (trimmed.startsWith("### ")) {
      closeList();
      html.push(`<h3>${inlineMarkdown(trimmed.slice(4))}</h3>`);
      continue;
    }

    if (trimmed.startsWith("## ")) {
      closeList();
      html.push(`<h2>${inlineMarkdown(trimmed.slice(3))}</h2>`);
      continue;
    }

    if (trimmed.startsWith("# ")) {
      closeList();
      html.push(`<h1>${inlineMarkdown(trimmed.slice(2))}</h1>`);
      continue;
    }

    if (trimmed.startsWith("> ")) {
      closeList();
      html.push(`<blockquote>${inlineMarkdown(trimmed.slice(2))}</blockquote>`);
      continue;
    }

    if (trimmed.startsWith("- ")) {
      if (!inList) {
        html.push("<ul>");
        inList = true;
      }
      html.push(`<li>${inlineMarkdown(trimmed.slice(2))}</li>`);
      continue;
    }

    closeList();
    html.push(`<p>${inlineMarkdown(trimmed)}</p>`);
  }

  closeList();
  return html.join("\n");
}

function formatDuration(seconds) {
  const value = Number(seconds || 0);
  if (value < 60) return `${Math.round(value)}s`;
  const minutes = Math.floor(value / 60);
  const rest = Math.round(value % 60);
  return `${minutes}m ${rest}s`;
}

function formatDate(ts) {
  return new Date(ts * 1000).toLocaleString("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function formatSize(bytes) {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

function renderSteps(steps = idleSteps) {
  stepsEl.innerHTML = steps
    .map((step, index) => {
      const status = step.status || "idle";
      const progress = Number(step.progress || 0);
      return `
        <li class="step ${status}">
          <span class="step-index">${index + 1}</span>
          <strong>${escapeHtml(step.label)}</strong>
          <p>${escapeHtml(step.detail || "等待")}</p>
          <div class="progress-track">
            <div class="progress-fill" style="--progress: ${progress}%"></div>
          </div>
        </li>
      `;
    })
    .join("");
}

function setStatus(status) {
  const labels = {
    queued: "排队中",
    running: "运行中",
    completed: "已完成",
    failed: "失败",
  };
  jobStatus.textContent = labels[status] || "待启动";
  serverStatus.className = `status-dot ${status || "idle"}`;
  processPanel.classList.toggle("status-running", status === "running");
  runButton.disabled = status === "running" || status === "queued";
}

function updateDurationDisplay() {
  if (!latestState) {
    duration.textContent = "0s";
    return;
  }

  if (latestState.status === "running" || latestState.status === "queued") {
    const startedAt = Number(latestState.started_at || Date.now() / 1000);
    duration.textContent = formatDuration(Date.now() / 1000 - startedAt);
    return;
  }

  duration.textContent = formatDuration(latestState.duration);
}

function syncTimer() {
  if (latestState?.status === "running" || latestState?.status === "queued") {
    if (!timerInterval) {
      timerInterval = window.setInterval(updateDurationDisplay, 1000);
    }
    updateDurationDisplay();
    return;
  }

  if (timerInterval) {
    window.clearInterval(timerInterval);
    timerInterval = null;
  }
  updateDurationDisplay();
}

function renderState(state) {
  latestState = state;
  setStatus(state.status);
  renderSteps(state.steps);
  initialData.textContent = state.metrics?.initial_data ?? 0;
  finalData.textContent = state.metrics?.final_data ?? 0;
  toolCalls.textContent = state.metrics?.tool_calls ?? 0;
  syncTimer();
  if (state.output_path) {
    reportPath.textContent = state.output_path;
  }
}

function appendLog(line) {
  logStream.textContent += `${line}\n`;
  logStream.scrollTop = logStream.scrollHeight;
}

async function fetchJson(url, options = {}) {
  const response = await fetch(url, options);
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(payload.error || `HTTP ${response.status}`);
  }
  return payload;
}

async function loadReportByJob(jobId) {
  const payload = await fetchJson(`/api/jobs/${jobId}/report`);
  reportPath.textContent = payload.path;
  reportPreview.innerHTML = markdownToHtml(payload.markdown);
  await loadReports();
}

async function loadReportByName(name) {
  const response = await fetch(`/api/reports/${encodeURIComponent(name)}`);
  if (!response.ok) throw new Error(`HTTP ${response.status}`);
  const markdown = await response.text();
  reportPath.textContent = `output/${name}`;
  reportPreview.innerHTML = markdownToHtml(markdown);
}

async function loadReports() {
  const payload = await fetchJson("/api/reports");
  const reports = payload.reports || [];
  if (!reports.length) {
    reportList.innerHTML = `<p class="empty-state">暂无历史报告</p>`;
    return;
  }
  reportList.innerHTML = reports
    .map(
      (report) => `
        <button class="report-item" type="button" data-name="${escapeHtml(report.name)}">
          <strong>${escapeHtml(report.name)}</strong>
          <span>${formatDate(report.mtime)} · ${formatSize(report.size)}</span>
        </button>
      `,
    )
    .join("");
}

function connectEvents(jobId) {
  if (currentSource) {
    currentSource.close();
  }
  currentSource = new EventSource(`/api/jobs/${jobId}/events`);
  currentSource.addEventListener("state", async (event) => {
    const state = JSON.parse(event.data);
    renderState(state);
    if (state.status === "completed") {
      currentSource.close();
      await loadReportByJob(jobId);
    }
    if (state.status === "failed") {
      currentSource.close();
      appendLog(state.error || "流程失败");
    }
  });
  currentSource.addEventListener("log", (event) => {
    appendLog(JSON.parse(event.data).line);
  });
  currentSource.onerror = () => {
    if (currentSource && currentSource.readyState === EventSource.CLOSED) {
      currentSource.close();
    }
  };
}

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  latestState = null;
  syncTimer();
  logStream.textContent = "";
  reportPreview.innerHTML = `<p class="empty-state">报告生成中</p>`;
  reportPath.textContent = "等待输出路径";

  const quarter = new FormData(form).get("quarter");
  const payload = {
    company: companyInput.value,
    year: yearInput.value,
    quarter,
    no_proxy: noProxyInput.checked,
    disable_langsmith: traceInput.checked,
  };

  try {
    const job = await fetchJson("/api/jobs", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    currentJobId = job.id;
    renderState(job);
    connectEvents(job.id);
  } catch (error) {
    setStatus("failed");
    appendLog(error.message);
  }
});

clearLogButton.addEventListener("click", () => {
  logStream.textContent = "";
});

refreshReportsButton.addEventListener("click", loadReports);

reportList.addEventListener("click", async (event) => {
  const button = event.target.closest(".report-item");
  if (!button) return;
  await loadReportByName(button.dataset.name);
});

renderSteps();
setStatus("");
loadReports().catch((error) => appendLog(error.message));
