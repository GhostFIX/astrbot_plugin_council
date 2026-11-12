const PLUGIN = "astrbot_plugin_council";
const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => Array.from(root.querySelectorAll(selector));

const state = {
  bridge: null,
  meta: { providers: [], personas: [], defaultSafetyPrompt: "" },
  settings: null,
  members: [],
  history: {
    page: 1,
    page_size: 20,
    total: 0,
    pages: 1,
    items: [],
  },
  activeTimer: null,
  currentDetail: null,
  activeDetailId: "",
  versionStatus: null,
};

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function esc(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

function safeUrl(value) {
  try {
    const url = new URL(String(value || ""));
    return url.protocol === "http:" || url.protocol === "https:"
      ? url.href
      : "";
  } catch {
    return "";
  }
}

function truncate(value, length = 80) {
  const text = String(value ?? "").replace(/\s+/g, " ").trim();
  return text.length > length ? text.slice(0, length) + "…" : text;
}

function formatDate(value) {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value);
  return date.toLocaleString("zh-CN", { hour12: false });
}

function formatDuration(ms) {
  const seconds = Math.max(0, Math.round(Number(ms || 0) / 1000));
  if (seconds < 60) return `${seconds} 秒`;
  const minutes = Math.floor(seconds / 60);
  const rest = seconds % 60;
  return `${minutes} 分 ${rest} 秒`;
}

function formatTokens(value) {
  const n = Number(value || 0);
  return n >= 1000 ? `${(n / 1000).toFixed(1)}k` : String(n);
}

function toast(message, type = "ok") {
  const node = $("#toast");
  node.textContent = message;
  node.classList.toggle("error", type === "error");
  node.classList.add("show");
  window.clearTimeout(toast._timer);
  toast._timer = window.setTimeout(() => node.classList.remove("show"), 3400);
}

async function getBridge() {
  if (state.bridge) return state.bridge;
  for (let i = 0; i < 30; i += 1) {
    const bridge = window.AstrBotPluginView || window.AstrBotPluginPage;
    if (bridge) {
      try {
        if (typeof bridge.ready === "function") await bridge.ready();
      } catch (error) {
        console.warn("bridge.ready failed", error);
      }
      state.bridge = bridge;
      return bridge;
    }
    await sleep(100);
  }
  return null;
}

function endpointUrl(endpoint, params = {}) {
  const url = new URL(
    `/api/v1/plugins/extensions/${encodeURIComponent(PLUGIN)}/${endpoint}`,
    window.location.origin,
  );
  for (const [key, value] of Object.entries(params)) {
    if (value === undefined || value === null || value === "") continue;
    url.searchParams.set(key, String(value));
  }
  return url;
}

function unwrap(payload) {
  if (payload && payload.success === false) {
    throw new Error(payload.error || "请求失败");
  }
  return payload;
}

async function apiGet(endpoint, params = {}) {
  const bridge = await getBridge();
  if (bridge && typeof bridge.apiGet === "function") {
    try {
      return unwrap(await bridge.apiGet(endpoint, params));
    } catch (error) {
      if (!String(error?.message || "").includes("404")) throw error;
    }
  }
  const response = await fetch(endpointUrl(endpoint, params), {
    method: "GET",
    credentials: "include",
    headers: { Accept: "application/json" },
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.error || `HTTP ${response.status}`);
  return unwrap(data);
}

async function apiPost(endpoint, body = {}) {
  const bridge = await getBridge();
  if (bridge && typeof bridge.apiPost === "function") {
    return unwrap(await bridge.apiPost(endpoint, body));
  }
  const response = await fetch(endpointUrl(endpoint), {
    method: "POST",
    credentials: "include",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.error || `HTTP ${response.status}`);
  return unwrap(data);
}

function selectOptions(select, items, selected, blankLabel) {
  select.innerHTML = "";
  if (blankLabel !== undefined) {
    const option = document.createElement("option");
    option.value = "";
    option.textContent = blankLabel;
    select.append(option);
  }
  for (const item of items) {
    const option = document.createElement("option");
    option.value = item.id;
    option.textContent = item.label || item.id;
    if (item.preview) option.title = item.preview;
    select.append(option);
  }
  select.value = selected || "";
}

function memberRow(member, index) {
  const row = document.createElement("div");
  row.className = "member-row";
  row.dataset.index = String(index);

  const top = document.createElement("div");
  top.className = "member-top";

  const nameLabel = document.createElement("label");
  nameLabel.innerHTML = `<span><span class="member-index">${index + 1}</span>显示名</span>`;
  const nameInput = document.createElement("input");
  nameInput.type = "text";
  nameInput.maxLength = 30;
  nameInput.value = member.display_name || "";
  nameInput.placeholder = "例如：审慎派";
  nameInput.addEventListener("input", () => {
    state.members[index].display_name = nameInput.value;
  });
  nameLabel.append(nameInput);

  const personaLabel = document.createElement("label");
  personaLabel.textContent = "人格";
  const personaSelect = document.createElement("select");
  selectOptions(
    personaSelect,
    state.meta.personas,
    member.persona_id || "default",
    undefined,
  );
  personaSelect.addEventListener("change", () => {
    state.members[index].persona_id = personaSelect.value;
  });
  personaLabel.append(personaSelect);

  const providerLabel = document.createElement("label");
  providerLabel.textContent = "Provider";
  const providerSelect = document.createElement("select");
  selectOptions(
    providerSelect,
    state.meta.providers,
    member.provider_id || "",
    "跟随当前会话模型",
  );
  providerSelect.addEventListener("change", () => {
    state.members[index].provider_id = providerSelect.value;
  });
  providerLabel.append(providerSelect);

  const remove = document.createElement("button");
  remove.type = "button";
  remove.className = "btn danger ghost";
  remove.textContent = "移除";
  remove.disabled = state.members.length <= 2;
  remove.addEventListener("click", () => {
    if (state.members.length <= 2) return;
    state.members.splice(index, 1);
    renderMembers();
  });

  top.append(nameLabel, personaLabel, providerLabel, remove);

  const extra = document.createElement("div");
  extra.className = "member-extra";

  const roleLabel = document.createElement("label");
  roleLabel.textContent = "关注角度 / 角色";
  const roleInput = document.createElement("input");
  roleInput.type = "text";
  roleInput.maxLength = 200;
  roleInput.value = member.role || "";
  roleInput.placeholder = "例如：从风险与反例角度审视";
  roleInput.addEventListener("input", () => {
    state.members[index].role = roleInput.value;
  });
  roleLabel.append(roleInput);

  const stanceLabel = document.createElement("label");
  stanceLabel.textContent = "初始倾向（可选）";
  const stanceInput = document.createElement("input");
  stanceInput.type = "text";
  stanceInput.maxLength = 100;
  stanceInput.value = member.stance || "";
  stanceInput.placeholder = "例如：倾向审慎，但可改变";
  stanceInput.addEventListener("input", () => {
    state.members[index].stance = stanceInput.value;
  });
  stanceLabel.append(stanceInput);

  extra.append(roleLabel, stanceLabel);
  row.append(top, extra);
  return row;
}

function renderMembers() {
  const container = $("#members");
  container.innerHTML = "";
  state.members.forEach((member, index) => {
    container.append(memberRow(member, index));
  });
  const add = $("#add-member");
  add.disabled = state.members.length >= 6;
}

function fillProviderSelect(select, selected, blankLabel = "跟随当前会话模型") {
  selectOptions(select, state.meta.providers, selected, blankLabel);
}

function renderSettings() {
  const settings = state.settings;
  if (!settings) return;
  state.members = settings.members.map((item) => ({ ...item }));
  renderMembers();

  const form = $("#settings-form");
  const setValue = (name, value) => {
    const input = form.elements.namedItem(name);
    if (!input) return;
    if (input.type === "checkbox") input.checked = Boolean(value);
    else input.value = value ?? "";
  };

  const discussion = settings.discussion || {};
  setValue("rounds", discussion.rounds);
  setValue("max_total_tokens", discussion.max_total_tokens);
  setValue(
    "max_tokens_per_speech",
    discussion.max_tokens_per_speech ?? 500,
  );
  setValue("max_context_chars", discussion.max_context_chars);
  setValue("request_timeout_sec", discussion.request_timeout_sec);
  setValue("max_duration_sec", discussion.max_duration_sec);
  setValue("cooldown_sec", discussion.cooldown_sec);
  setValue("summary_max_tokens", discussion.summary_max_tokens ?? 800);

  const output = settings.output || {};
  setValue("render_mode", output.render_mode);
  setValue("max_text_chars", output.max_text_chars);

  const search = settings.search || {};
  setValue("search_mode", search.mode);
  setValue("native_rounds", search.native_rounds);
  setValue("search_max_results", search.max_results);
  setValue("search_timeout_sec", search.timeout_sec);
  setValue("search_language", search.language);
  const keyInput = $("#anysearch-key");
  keyInput.value = "";
  keyInput.placeholder = search.has_anysearch_api_key
    ? "已配置，留空保持不变"
    : "留空使用匿名免费额度";
  $("#clear-anysearch-key").checked = false;

  const safety = settings.safety || {};
  setValue("safety_mode", safety.mode);
  setValue("safety_fail_mode", safety.fail_mode);
  setValue("store_refused_topic", safety.store_refused_topic);
  setValue("refuse_message", safety.refuse_message);
  setValue("safety_extra_rules", safety.extra_rules);
  setValue(
    "safety_system_prompt",
    safety.system_prompt || state.meta.defaultSafetyPrompt || "",
  );

  const chat = settings.chat_context || {};
  setValue("chat_context_mode", chat.mode || "persistent");
  setValue("chat_context_max_messages", chat.max_messages ?? 50);
  setValue("chat_context_max_chars", chat.max_chars ?? 3000);
  setValue("chat_context_vision_enabled", chat.vision_enabled ?? true);
  setValue("chat_context_stt_enabled", chat.stt_enabled ?? true);

  fillProviderSelect(
    $("#moderator-provider"),
    settings.moderator?.provider_id || "",
    "跟随当前会话模型",
  );
  fillProviderSelect(
    $("#safety-provider"),
    safety.provider_id || "",
    "跟随当前会话模型",
  );
  $("#save-state").textContent = "配置仅影响下一次讨论";
}

function numeric(form, name) {
  const value = Number(form.elements.namedItem(name).value);
  return Number.isFinite(value) ? value : 0;
}

function collectSettings() {
  const form = $("#settings-form");
  const base = JSON.parse(JSON.stringify(state.settings || {}));
  base.members = state.members.map((member) => ({
    display_name: String(member.display_name || "").trim(),
    persona_id: String(member.persona_id || "default"),
    provider_id: String(member.provider_id || ""),
    role: String(member.role || "").trim(),
    stance: String(member.stance || "").trim(),
  }));
  base.discussion = {
    rounds: numeric(form, "rounds"),
    max_total_tokens: numeric(form, "max_total_tokens"),
    max_tokens_per_speech: numeric(form, "max_tokens_per_speech"),
    max_context_chars: numeric(form, "max_context_chars"),
    request_timeout_sec: numeric(form, "request_timeout_sec"),
    max_duration_sec: numeric(form, "max_duration_sec"),
    cooldown_sec: numeric(form, "cooldown_sec"),
    summary_max_tokens: numeric(form, "summary_max_tokens"),
  };
  base.output = {
    render_mode: form.elements.namedItem("render_mode").value,
    max_text_chars: numeric(form, "max_text_chars"),
  };
  base.search = {
    mode: form.elements.namedItem("search_mode").value,
    native_rounds: Math.min(
      base.discussion.rounds,
      Math.max(1, numeric(form, "native_rounds")),
    ),
    max_results: numeric(form, "search_max_results"),
    timeout_sec: numeric(form, "search_timeout_sec"),
    language: form.elements.namedItem("search_language").value,
    anysearch_api_key: $("#anysearch-key").value.trim(),
  };
  base.safety = {
    mode: form.elements.namedItem("safety_mode").value,
    provider_id: $("#safety-provider").value,
    extra_rules: form.elements.namedItem("safety_extra_rules").value.trim(),
    fail_mode: form.elements.namedItem("safety_fail_mode").value,
    store_refused_topic: form.elements.namedItem("store_refused_topic").checked,
    refuse_message: form.elements.namedItem("refuse_message").value.trim(),
    system_prompt: form.elements
      .namedItem("safety_system_prompt")
      .value.trim(),
    max_tokens: base.safety?.max_tokens || 220,
  };
  base.chat_context = {
    mode: form.elements.namedItem("chat_context_mode").value,
    max_messages: numeric(form, "chat_context_max_messages"),
    max_chars: numeric(form, "chat_context_max_chars"),
    vision_enabled: form.elements.namedItem("chat_context_vision_enabled").checked,
    stt_enabled: form.elements.namedItem("chat_context_stt_enabled").checked,
    vision_max_tokens: base.chat_context?.vision_max_tokens || 200,
  };
  base.moderator = { provider_id: $("#moderator-provider").value };
  delete base.search.has_anysearch_api_key;
  return base;
}

async function saveSettings(event) {
  event.preventDefault();
  const button = $("#save-settings");
  button.disabled = true;
  try {
    const body = {
      settings: collectSettings(),
      clear_anysearch_key: $("#clear-anysearch-key").checked,
    };
    const data = await apiPost("settings/save", body);
    state.settings = data.settings;
    renderSettings();
    toast("配置已保存，下一次讨论生效");
  } catch (error) {
    toast(error.message || "保存失败", "error");
  } finally {
    button.disabled = false;
  }
}

async function resetSettings() {
  if (!window.confirm("恢复默认讨论配置？当前成员与搜索设置会被覆盖。")) return;
  try {
    const data = await apiPost("settings/reset", {});
    state.settings = data.settings;
    renderSettings();
    toast("已恢复默认配置");
  } catch (error) {
    toast(error.message || "恢复失败", "error");
  }
}

async function testSearch() {
  const result = $("#search-test-result");
  result.textContent = "测试中…";
  try {
    const data = await apiPost("search/test", {
      anysearch_api_key: $("#anysearch-key").value.trim(),
      query: "AstrBot 最新版本",
    });
    if (data.ok) {
      result.textContent = `成功：${data.count} 条结果${data.anonymous ? "（匿名）" : ""}`;
      toast("AnySearch 连接正常");
    } else {
      result.textContent = `失败：${data.error || "无结果"}`;
      toast("AnySearch 测试失败", "error");
    }
  } catch (error) {
    result.textContent = `失败：${error.message || "请求错误"}`;
    toast("AnySearch 测试失败", "error");
  }
}

function statusLabel(status) {
  return (
    {
      completed: "已完成",
      cancelled: "已停止",
      refused: "已拒绝",
      failed: "失败",
      running: "进行中",
    }[status] || status || "未知"
  );
}

function statusPill(status) {
  return `<span class="pill ${esc(status)}">${esc(statusLabel(status))}</span>`;
}

function renderHistory() {
  const data = state.history;
  const body = $("#history-body");
  if (!data.items.length) {
    body.innerHTML = `<tr><td colspan="7"><div class="empty">暂无记录</div></td></tr>`;
  } else {
    body.innerHTML = data.items
      .map(
        (item) => `
        <tr>
          <td class="check"><input type="checkbox" class="row-check" value="${esc(item.id)}" /></td>
          <td>${esc(formatDate(item.created_at))}</td>
          <td class="topic">${esc(item.topic || "（议题已隐藏）")}</td>
          <td>${statusPill(item.status)}</td>
          <td>${esc(item.rounds || 0)}</td>
          <td>${esc(formatTokens(item.total_tokens))}</td>
          <td>
            <button class="row-link" data-action="view" data-id="${esc(item.id)}">查看</button>
            <button class="row-link" data-action="export" data-id="${esc(item.id)}">导出</button>
            <button class="row-link" data-action="delete" data-id="${esc(item.id)}" style="color:var(--danger)">删除</button>
          </td>
        </tr>`,
      )
      .join("");
  }
  $("#history-count").textContent = `共 ${data.total} 条`;
  $("#page-info").textContent = `${data.page} / ${data.pages}`;
  $("#prev-page").disabled = data.page <= 1;
  $("#next-page").disabled = data.page >= data.pages;
  $("#select-all").checked = false;
}

async function loadHistory(page = state.history.page) {
  const params = {
    page,
    page_size: state.history.page_size,
    q: $("#history-q").value.trim(),
    status: $("#history-status").value,
    from: $("#history-from").value
      ? `${$("#history-from").value}T00:00:00`
      : "",
    to: $("#history-to").value ? `${$("#history-to").value}T23:59:59` : "",
  };
  try {
    const data = await apiGet("sessions", params);
    state.history = {
      page: data.page,
      page_size: data.page_size,
      total: data.total,
      pages: data.pages,
      items: data.items || [],
    };
    renderHistory();
  } catch (error) {
    toast(error.message || "历史加载失败", "error");
  }
}

function selectedIds() {
  return $$(".row-check:checked").map((node) => node.value);
}

function downloadText(filename, content, contentType) {
  const blob = new Blob([content], { type: contentType || "text/plain;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = filename;
  document.body.append(anchor);
  anchor.click();
  anchor.remove();
  window.setTimeout(() => URL.revokeObjectURL(url), 2000);
}

async function exportSessions(ids, format) {
  if (!ids.length) {
    toast("请先选择记录", "error");
    return;
  }
  try {
    const data = await apiPost("export", { ids, format });
    downloadText(data.filename, data.content, data.content_type);
    toast("导出已开始");
  } catch (error) {
    toast(error.message || "导出失败", "error");
  }
}

async function deleteSessions(ids) {
  if (!ids.length) {
    toast("请先选择记录", "error");
    return;
  }
  if (!window.confirm(`删除 ${ids.length} 条记录？删除后无法恢复。`)) return;
  try {
    const data = await apiPost("sessions/delete", { ids });
    toast(`已删除 ${data.deleted} 条记录`);
    await loadHistory(state.history.page);
  } catch (error) {
    toast(error.message || "删除失败", "error");
  }
}

function renderDetail(session) {
  state.currentDetail = session;
  $("#modal-title").textContent = session.topic || "完整记录";
  const safety = session.safety_decision === "refuse"
    ? `<div class="safety-banner">安全审核：${esc(session.safety_reason || "已拒绝")}</div>`
    : "";
  const meta = `
    <div class="detail-grid">
      <div><span>状态</span><br /><b>${esc(statusLabel(session.status))}</b></div>
      <div><span>创建时间</span><br /><b>${esc(formatDate(session.created_at))}</b></div>
      <div><span>轮数 / 发言</span><br /><b>${esc(session.rounds || 0)} / ${esc(session.speech_count || 0)}</b></div>
      <div><span>Token</span><br /><b>${esc(formatTokens(session.total_tokens))}</b></div>
      <div><span>耗时</span><br /><b>${esc(formatDuration(session.duration_ms))}</b></div>
      <div><span>输出 / 搜索</span><br /><b>${esc(session.render_mode || "—")} / ${esc(session.search_mode || "—")}</b></div>
      <div><span>停止原因</span><br /><b>${esc(session.stop_reason || "—")}</b></div>
      <div><span>会话</span><br /><b>${esc(session.umo || "—")}</b></div>
    </div>`;
  const summary = session.summary
    ? `<div class="detail-section"><h3>总结</h3><div class="prose">${esc(session.summary)}</div></div>`
    : "";
  const speeches = (session.speeches || [])
    .map(
      (speech) => `
      <div class="speech">
        <div class="speech-head">
          <b>第 ${esc(speech.round)} 轮 · ${esc(speech.speaker)}</b>
          <small>${esc(speech.provider_model || speech.provider_id || "")}</small>
        </div>
        <div class="speech-body">${esc(speech.content)}</div>
      </div>`,
    )
    .join("");
  const speechSection = speeches
    ? `<div class="detail-section"><h3>完整讨论记录</h3>${speeches}</div>`
    : "";
  const sources = (session.sources || [])
    .map(
      (source) => `
      <div class="source-item">
        <b>[S${esc(source.idx)}] ${esc(source.title || source.url)}</b><br />
        <a href="${esc(safeUrl(source.url))}" target="_blank" rel="noreferrer">${esc(source.url)}</a>
        ${source.snippet ? `<div class="muted">${esc(source.snippet)}</div>` : ""}
      </div>`,
    )
    .join("");
  const sourceSection = sources
    ? `<div class="detail-section"><h3>参考来源</h3>${sources}</div>`
    : "";
  $("#modal-body").innerHTML = `${safety}${meta}${summary}${speechSection}${sourceSection}`;
  $("#modal").hidden = false;
}

async function openDetail(id) {
  try {
    const data = await apiGet(`sessions/${encodeURIComponent(id)}`);
    renderDetail(data.session);
  } catch (error) {
    toast(error.message || "记录加载失败", "error");
  }
}

async function loadStats() {
  const days = $("#stats-days").value;
  try {
    const data = await apiGet("stats", { days });
    const overview = data.overview || {};
    const cards = [
      ["讨论场次", overview.sessions || 0],
      ["总 Token", formatTokens(overview.total_tokens || 0)],
      ["平均轮数", Number(overview.avg_rounds || 0).toFixed(1)],
      ["总发言", overview.speeches || 0],
    ];
    $("#stat-cards").innerHTML = cards
      .map(
        ([label, value]) => `<div class="stat"><b>${esc(value)}</b><span>${esc(label)}</span></div>`,
      )
      .join("");

    const daily = (data.daily || []).slice(-30);
    const maxDaily = Math.max(1, ...daily.map((item) => Number(item.count || 0)));
    $("#daily-bars").innerHTML = daily.length
      ? daily
          .map(
            (item) => `
        <div class="bar-row">
          <span>${esc(String(item.day || "").slice(5))}</span>
          <div class="bar-track"><div class="bar-fill" style="width:${Math.max(2, (Number(item.count || 0) / maxDaily) * 100)}%"></div></div>
          <span>${esc(item.count || 0)} 场</span>
        </div>`,
          )
          .join("")
      : `<div class="empty">暂无数据</div>`;

    const members = data.top_members || [];
    $("#top-members").innerHTML = members.length
      ? members
          .map(
            (item) => `
        <div class="rank-row">
          <span>${esc(item.speaker || "成员")}</span>
          <b>${esc(item.count || 0)} 次</b>
        </div>`,
          )
          .join("")
      : `<div class="empty">暂无数据</div>`;
  } catch (error) {
    toast(error.message || "统计加载失败", "error");
  }
}

async function loadActive() {
  try {
    const data = await apiGet("active");
    const items = data.items || [];
    $("#active-list").innerHTML = items.length
      ? items
          .map(
            (item) => `
        <div class="active-row">
          <div>
            <div class="topic">${esc(item.topic || "（无议题）")}</div>
            <div class="muted">第 ${esc(item.round || 0)} 轮 · ${esc(item.speech_count || 0)} 次发言 · ${esc(formatTokens(item.total_tokens))} tokens · ${esc(item.elapsed_sec || 0)} 秒</div>
          </div>
          <button class="btn danger ghost" data-cancel="${esc(item.session_id)}">停止</button>
        </div>`,
          )
          .join("")
      : `<div class="empty">当前没有运行中的讨论</div>`;
    $$("[data-cancel]").forEach((button) => {
      button.addEventListener("click", async () => {
        if (!window.confirm("停止这场讨论？")) return;
        try {
          const result = await apiPost("active/cancel", {
            session_id: button.dataset.cancel,
          });
          toast(result.message || "已请求停止");
          await loadActive();
        } catch (error) {
          toast(error.message || "停止失败", "error");
        }
      });
    });
  } catch (error) {
    toast(error.message || "运行状态加载失败", "error");
  }
}

function activateTab(name) {
  $$(".tab").forEach((tab) => tab.classList.toggle("active", tab.dataset.tab === name));
  $$(".panel").forEach((panel) => {
    panel.classList.toggle("active", panel.id === `panel-${name}`);
  });
  if (state.activeTimer) {
    window.clearInterval(state.activeTimer);
    state.activeTimer = null;
  }
  if (name === "history") loadHistory(1);
  if (name === "stats") loadStats();
  if (name === "active") {
    loadActive();
    state.activeTimer = window.setInterval(loadActive, 5000);
  }
}

function bindEvents() {
  $$(".tab").forEach((tab) => {
    tab.addEventListener("click", () => activateTab(tab.dataset.tab));
  });

  $("#settings-form").addEventListener("submit", saveSettings);
  $("#add-member").addEventListener("click", () => {
    if (state.members.length >= 6) return;
    state.members.push({
      display_name: `成员${state.members.length + 1}`,
      persona_id: "default",
      provider_id: "",
      role: "",
      stance: "",
    });
    renderMembers();
  });
  $("#reset-settings").addEventListener("click", resetSettings);
  $("#test-search").addEventListener("click", testSearch);
  $("#restore-safety-prompt").addEventListener("click", () => {
    const target = $("#settings-form").elements.namedItem(
      "safety_system_prompt",
    );
    target.value = state.meta.defaultSafetyPrompt || "";
    toast("已恢复默认审核 Prompt，保存后生效");
  });
  $("#clear-chat-context").addEventListener("click", async () => {
    if (!window.confirm("清空所有群的持久化群聊缓存？此操作无法恢复。")) return;
    try {
      const data = await apiPost("chat-context/clear", {});
      toast(`已清除 ${data.deleted || 0} 条群聊缓存`);
      await loadChatContextStats();
    } catch (error) {
      toast(error.message || "清除失败", "error");
    }
  });

  $("#history-search").addEventListener("click", () => loadHistory(1));
  $("#history-q").addEventListener("keydown", (event) => {
    if (event.key === "Enter") loadHistory(1);
  });
  $("#history-status").addEventListener("change", () => loadHistory(1));
  $("#prev-page").addEventListener("click", () => loadHistory(Math.max(1, state.history.page - 1)));
  $("#next-page").addEventListener("click", () => loadHistory(Math.min(state.history.pages, state.history.page + 1)));
  $("#select-all").addEventListener("change", (event) => {
    $$(".row-check").forEach((node) => {
      node.checked = event.target.checked;
    });
  });
  $("#delete-selected").addEventListener("click", () => deleteSessions(selectedIds()));
  $("#export-csv").addEventListener("click", () => exportSessions(selectedIds(), "csv"));
  $("#history-body").addEventListener("click", async (event) => {
    const button = event.target.closest("[data-action]");
    if (!button) return;
    const id = button.dataset.id;
    if (button.dataset.action === "view") await openDetail(id);
    if (button.dataset.action === "export") await exportSessions([id], "md");
    if (button.dataset.action === "delete") await deleteSessions([id]);
  });

  $("#stats-days").addEventListener("change", loadStats);
  $("#refresh-active").addEventListener("click", loadActive);
  $("#check-update").addEventListener("click", () => {
    const releaseUrl = safeUrl(state.versionStatus?.url);
    if (state.versionStatus?.status === "update_available" && releaseUrl) {
      window.open(releaseUrl, "_blank", "noopener");
      return;
    }
    loadVersion(true);
  });
  $("#refresh-all").addEventListener("click", async () => {
    await Promise.all([
      loadMeta(),
      loadSettings(),
      loadHistory(state.history.page),
      loadVersion(true),
      loadChatContextStats(),
    ]);
    if (state.settings) renderSettings();
    toast("已刷新");
  });

  $("#modal-close").addEventListener("click", () => {
    $("#modal").hidden = true;
  });
  $("#modal").addEventListener("click", (event) => {
    if (event.target === $("#modal")) $("#modal").hidden = true;
  });
  $("#modal-export-md").addEventListener("click", () => {
    if (state.currentDetail) exportSessions([state.currentDetail.id], "md");
  });
  $("#modal-export-json").addEventListener("click", () => {
    if (state.currentDetail) exportSessions([state.currentDetail.id], "json");
  });
}

function renderVersion(data) {
  const versionNode = $("#version");
  const button = $("#check-update");
  if (data?.current_display) {
    versionNode.textContent = data.current_display;
  }
  button.classList.remove("update-available");
  if (!data) {
    button.textContent = "检查更新";
    button.title = "无法获取版本状态";
    return;
  }
  if (data.status === "update_available") {
    button.textContent = `新版本 ${data.latest_display || data.latest}`;
    button.classList.add("update-available");
    button.title = `发现新版本，点击打开 ${data.url || "GitHub"}`;
  } else if (data.status === "up_to_date") {
    button.textContent = "已是最新";
    button.title = "点击重新检查";
  } else {
    button.textContent = "检查更新";
    button.title = data.error || "GitHub 仓库暂不可访问（可能尚未发布）";
  }
}

async function loadChatContextStats() {
  try {
    const data = await apiGet("chat-context/stats");
    const items = data.items || [];
    const total = items.reduce((sum, item) => sum + Number(item.count || 0), 0);
    $("#chat-context-stats").textContent = items.length
      ? `已缓存 ${total} 条 / ${items.length} 个群`
      : "暂无群聊缓存";
  } catch {
    $("#chat-context-stats").textContent = "";
  }
}

async function loadVersion(force = false) {
  try {
    const data = await apiGet("version", force ? { force: 1 } : {});
    state.versionStatus = data;
    renderVersion(data);
  } catch (error) {
    state.versionStatus = null;
    renderVersion(null);
    if (force) toast(error.message || "版本检查失败", "error");
  }
}

async function loadMeta() {
  try {
    const data = await apiGet("meta");
    state.meta.providers = data.providers || [];
    state.meta.personas = data.personas || [];
    state.meta.defaultSafetyPrompt = data.default_safety_prompt || "";
    $("#version").textContent = `v${data.version || "1.0.0"}`;
  } catch (error) {
    toast(`Provider/人格列表加载失败：${error.message || "未知错误"}`, "error");
  }
}

async function loadSettings() {
  try {
    const data = await apiGet("settings");
    state.settings = data.settings;
  } catch (error) {
    toast(error.message || "配置加载失败", "error");
    state.settings = null;
  }
}

async function init() {
  bindEvents();
  await getBridge();
  await Promise.all([loadMeta(), loadSettings()]);
  if (state.settings) renderSettings();
  await loadHistory(1);
  loadVersion(false);
  loadChatContextStats();
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", init);
} else {
  init();
}
