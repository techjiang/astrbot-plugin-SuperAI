/**
 * SuperAI Studio —— 插件 WebUI 页面
 * 通过 AstrBot 的 window.AstrBotPluginPage bridge 调用插件后端 API。
 */
const bridge = window.AstrBotPluginPage;

const $ = (id) => document.getElementById(id);
const state = { session: "" };

function t(key, fallback) {
  return bridge?.t ? bridge.t(key, fallback) : fallback;
}

function renderLabels() {
  $("heading").textContent = t("pages.studio.heading", "SuperAI Studio");
  $("refresh").textContent = t("pages.studio.refresh", "刷新");
  $("status-title").textContent = t("pages.studio.status", "运行状态");
  $("stats-title").textContent = t("pages.studio.stats", "用量统计");
  $("tools-title").textContent = t("pages.studio.tools", "已注册工具");
  $("memory-title").textContent = t("pages.studio.memory", "记忆查询");
  $("search").textContent = t("pages.studio.search", "检索");
  $("clear").textContent = t("pages.studio.clear", "清空记忆");
  $("session").placeholder = t("pages.studio.session", "会话 UMO");
}

function showError(message) {
  $("error").textContent = message ? `⚠️ ${message}` : "";
}

function renderStatus(data) {
  $("version").textContent = `v${data.version} · ${data.providers.length} providers · ${data.tools.length} tools`;

  const features = {
    router: "路由",
    memory: "记忆",
    web: "联网",
    "knowledge_base": "知识库",
    agent: "Agent",
    workflow: "工作流",
    metrics: "统计",
  };
  $("features").innerHTML = Object.entries(features)
    .map(([key, label]) => {
      const on = Boolean(data.features[key]);
      return `<span class="chip${on ? "" : " off"}">${label}${on ? " ✅" : " ❌"}</span>`;
    })
    .join("");

  const tiers = data.route_tiers || {};
  $("route-tiers").innerHTML = Object.entries(tiers)
    .map(([tier, provider]) => `<dt>${tier}</dt><dd>${provider || "（未配置）"}</dd>`)
    .join("");
}

function metric(label, value) {
  return `<div class="metric"><div class="num">${value}</div><div class="lbl">${label}</div></div>`;
}

function renderBars(container, data) {
  const entries = Object.entries(data || {});
  if (!entries.length) {
    container.innerHTML = "";
    return;
  }
  const max = Math.max(...entries.map(([, value]) => value), 1);
  container.innerHTML = entries
    .sort((a, b) => b[1] - a[1])
    .slice(0, 8)
    .map(
      ([name, value]) => `
      <div class="bar-row">
        <span class="name" title="${name}">${name || "default"}</span>
        <span class="track"><span class="fill" style="width:${Math.round((value / max) * 100)}%"></span></span>
        <span class="val">${value}</span>
      </div>`,
    )
    .join("");
}

function renderStats(data) {
  $("stats").innerHTML = [
    metric(t("pages.studio.requests", "请求数"), `${data.requests}（失败 ${data.failures}）`),
    metric(t("pages.studio.tokens", "Token"), data.total_tokens),
    metric(t("pages.studio.latency", "平均耗时"), `${data.avg_latency_ms} ms`),
  ].join("");
  renderBars($("provider-bars"), data.providers);
  renderBars($("route-bars"), data.routes);
}

function renderTools(data) {
  const tools = data.tools || [];
  $("tools").innerHTML = tools.length
    ? tools.map((name) => `<li>${name}</li>`).join("")
    : "<li>当前未注册任何工具</li>";
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
    const [status, stats, tools] = await Promise.all([
      bridge.apiGet("status"),
      bridge.apiGet("stats", { days: 7 }),
      bridge.apiGet("tools"),
    ]);
    renderStatus(status);
    renderStats(stats);
    renderTools(tools);
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
  } catch (error) {
    showError(error?.message || String(error));
  }
}

async function boot() {
  if (!bridge) {
    showError("未检测到 AstrBot 插件桥接，请从 AstrBot WebUI 打开本页面。");
    return;
  }
  const context = await bridge.ready();
  state.session = state.session || "";
  renderLabels();
  bridge.onContext(renderLabels);
  await loadAll();

  $("refresh").addEventListener("click", loadAll);
  $("search").addEventListener("click", searchMemory);
  $("clear").addEventListener("click", clearMemory);
  void context;
}

boot();
