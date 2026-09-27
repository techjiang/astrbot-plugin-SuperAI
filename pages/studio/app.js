/**
 * SuperAI Studio —— 插件 WebUI 页面
 * 通过 AstrBot 的 window.AstrBotPluginPage bridge 调用插件后端 API。
 */
const bridge = window.AstrBotPluginPage;

const $ = (id) => document.getElementById(id);
const state = { session: "" };

function t(key, fallback) {
  try {
    return bridge?.t ? bridge.t(key, fallback) : fallback;
  } catch {
    return fallback;
  }
}

/** 转义后再插入 innerHTML，避免会话 / 模型名里的尖括号破坏页面。 */
function esc(value) {
  return String(value ?? "").replace(
    /[&<>"']/g,
    (ch) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[ch],
  );
}

function renderLabels() {
  $("heading").textContent = t("pages.studio.heading", "SuperAI Studio");
  $("refresh").textContent = t("pages.studio.refresh", "刷新");
  $("status-title").textContent = t("pages.studio.status", "运行状态");
  $("health-title").textContent = t("pages.studio.health", "模型健康度");
  $("stats-title").textContent = t("pages.studio.stats", "用量统计");
  $("tools-title").textContent = t("pages.studio.tools", "已注册工具");
  $("sessions-title").textContent = t("pages.studio.sessions", "会话列表");
  $("memory-title").textContent = t("pages.studio.memory", "记忆查询");
  $("search").textContent = t("pages.studio.search", "检索");
  $("clear").textContent = t("pages.studio.clear", "清空记忆");
  $("session").placeholder = t("pages.studio.session", "会话 UMO");
}

function showError(message) {
  $("error").textContent = message ? `⚠️ ${message}` : "";
}

function renderStatus(data) {
  $("version").textContent =
    `v${data.version} · ${data.providers.length} providers · ${data.tools.length} tools` +
    (data.hooks_ready ? "" : " · 钩子未就绪");

  const features = {
    router: "路由",
    memory: "记忆",
    web: "联网",
    knowledge_base: "知识库",
    agent: "Agent",
    workflow: "工作流",
    metrics: "统计",
  };
  $("features").innerHTML = Object.entries(features)
    .map(([key, label]) => {
      const on = Boolean(data.features[key]);
      return `<span class="chip${on ? "" : " off"}">${esc(label)}${on ? " ✅" : " ❌"}</span>`;
    })
    .join("");

  const tiers = data.route_tiers || {};
  $("route-tiers").innerHTML = Object.entries(tiers)
    .map(([tier, provider]) => `<dt>${esc(tier)}</dt><dd>${esc(provider || "（未配置）")}</dd>`)
    .join("");
}

function renderHealth(data) {
  const rows = data.health_detail || [];
  if (!rows.length) {
    $("health").innerHTML = `<li>${esc(t("pages.studio.noData", "暂无数据"))}</li>`;
    return;
  }
  $("health").innerHTML = rows
    .map((row) => {
      const badge = row.healthy ? "✅" : "⚠️";
      const detail = row.failures ? `（连续失败 ${esc(row.failures)} 次）` : "";
      const last = row.last_failure ? ` 最近失败：${esc(row.last_failure)}` : "";
      return `<li>${badge} ${esc(row.provider_id)}${detail}${last}</li>`;
    })
    .join("");
}

function metric(label, value) {
  return `<div class="metric"><div class="num">${esc(value)}</div><div class="lbl">${esc(label)}</div></div>`;
}

function renderBars(container, data) {
  const entries = Object.entries(data || {});
  if (!entries.length) {
    container.innerHTML = "";
    return;
  }
  const max = Math.max(...entries.map(([, value]) => Number(value) || 0), 1);
  container.innerHTML = entries
    .sort((a, b) => b[1] - a[1])
    .slice(0, 8)
    .map(
      ([name, value]) => `
      <div class="bar-row">
        <span class="name" title="${esc(name)}">${esc(name || "default")}</span>
        <span class="track"><span class="fill" style="width:${Math.round(((Number(value) || 0) / max) * 100)}%"></span></span>
        <span class="val">${esc(value)}</span>
      </div>`,
    )
    .join("");
}

function renderStats(data) {
  $("stats").innerHTML = [
    metric(
      t("pages.studio.requests", "请求数"),
      `${esc(data.requests ?? 0)}（失败 ${esc(data.failures ?? 0)}）`,
    ),
    metric(t("pages.studio.tokens", "Token"), esc(data.total_tokens ?? 0)),
    metric(t("pages.studio.latency", "平均耗时"), `${esc(data.avg_latency_ms ?? 0)} ms`),
  ].join("");
  renderBars($("provider-bars"), data.providers);
  renderBars($("route-bars"), data.routes);
  renderBars(
    $("daily-bars"),
    Object.fromEntries((data.days || []).map((day) => [day.date, day.requests])),
  );
}

function renderTools(data) {
  const tools = data.tools || [];
  $("tools").innerHTML = tools.length
    ? tools.map((name) => `<li>${esc(name)}</li>`).join("")
    : `<li>${esc(t("pages.studio.noData", "当前未注册任何工具"))}</li>`;
}

function renderSessions(data) {
  const rows = data.sessions || [];
  if (!rows.length) {
    $("sessions").innerHTML = `<li>${esc(t("pages.studio.noData", "暂无数据"))}</li>`;
    return;
  }
  $("sessions").innerHTML = rows
    .map((row) => {
      const summary = row.summary_chars ? `，摘要 ${esc(row.summary_chars)} 字` : "";
      return `<li><button class="link" data-session="${esc(row.session)}">${esc(
        row.session,
      )}</button> — 记忆 ${esc(row.memories)} 条${esc(summary)}</li>`;
    })
    .join("");
  $("sessions")
    .querySelectorAll("button[data-session]")
    .forEach((button) => {
      button.addEventListener("click", () => {
        $("session").value = button.dataset.session;
        void searchMemory();
      });
    });
}

function renderMemory(data) {
  const lines = [
    `会话：${data.session}`,
    `记忆总数：${data.total}`,
    data.summary ? `当前摘要：${data.summary}` : "当前摘要：（无）",
    "—— 记忆条目 ——",
  ];
  (data.entries || []).forEach((entry) => {
    lines.push(`[${entry.kind}] ${entry.content}`);
  });
  $("memory-output").textContent = lines.join("\n");
}

async function loadAll() {
  showError("");
  try {
    const [status, stats, tools, sessions] = await Promise.all([
      bridge.apiGet("status"),
      bridge.apiGet("stats", { days: 7 }),
      bridge.apiGet("tools"),
      bridge.apiGet("sessions"),
    ]);
    renderStatus(status);
    renderHealth(status);
    renderStats(stats);
    renderTools(tools);
    renderSessions(sessions);
  } catch (error) {
    showError(error?.message || String(error));
  }
}

async function searchMemory() {
  showError("");
  const session = $("session").value.trim();
  if (!session) {
    showError("请先填写会话 UMO");
    return;
  }
  state.session = session;
  const query = $("query").value.trim();
  try {
    const data = await bridge.apiGet("memory", { session, q: query, limit: 20 });
    renderMemory(data);
  } catch (error) {
    showError(error?.message || String(error));
  }
}

async function clearMemory() {
  showError("");
  const session = $("session").value.trim() || state.session;
  if (!session) {
    showError("请先填写会话 UMO");
    return;
  }
  if (!window.confirm(`确认清空会话 ${session} 的记忆与摘要？`)) return;
  try {
    const result = await bridge.apiPost("memory/clear", { session });
    $("memory-output").textContent = `已清空 ${result.removed} 条记忆。`;
    await loadAll();
  } catch (error) {
    showError(error?.message || String(error));
  }
}

async function boot() {
  if (!bridge) {
    showError("未检测到 AstrBot 插件桥接，请从 AstrBot WebUI 打开本页面。");
    return;
  }
  renderLabels();
  $("refresh").addEventListener("click", loadAll);
  $("search").addEventListener("click", searchMemory);
  $("clear").addEventListener("click", clearMemory);
  $("session").addEventListener("keydown", (event) => {
    if (event.key === "Enter") void searchMemory();
  });
  try {
    await bridge.ready();
  } catch (error) {
    showError(error?.message || String(error));
    return;
  }
  bridge.onContext(renderLabels);
  await loadAll();
}

void boot();
