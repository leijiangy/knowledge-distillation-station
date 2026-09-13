/* 知识蒸馏站 —— 前端逻辑（原生 JS，无构建） */
(() => {
  "use strict";

  const $ = (id) => document.getElementById(id);
  const els = {
    hero: $("hero"), workspace: $("workspace"), cards: $("cards"),
    userArea: $("user-area"), userAvatar: $("user-avatar"), userName: $("user-name"),
    loginBtn: $("login-btn"), loginHint: $("login-hint"), logoutBtn: $("logout-btn"),
    sortSelect: $("sort-select"), refreshBtn: $("refresh-btn"),
    favlistTitle: $("favlist-title"), countBadge: $("count-badge"), cacheBadge: $("cache-badge"),
    stateLoading: $("state-loading"), loadingText: $("loading-text"),
    stateError: $("state-error"), errorText: $("error-text"), retryBtn: $("retry-btn"),
    stateEmpty: $("state-empty"), emptyText: $("empty-text"),
  };

  let allItems = [];
  let sortMode = "favtime";

  // ---- 工具 ----
  const TYPE_NAMES = { answer: "回答", article: "文章", zvideo: "视频", pin: "想法", question: "问题" };

  function formatDate(unixSeconds) {
    if (!unixSeconds) return "未知时间";
    const d = new Date(unixSeconds * 1000);
    const days = Math.floor((Date.now() - d.getTime()) / 86400000);
    const dateText = `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
    if (days <= 0) return `今天收藏 · ${dateText}`;
    if (days < 30) return `${days} 天前收藏 · ${dateText}`;
    if (days < 365) return `${Math.floor(days / 30)} 个月前收藏 · ${dateText}`;
    return `${Math.floor(days / 365)} 年前收藏 · ${dateText}`;
  }

  function showState(name) {
    for (const key of ["stateLoading", "stateError", "stateEmpty"]) {
      els[key].hidden = key !== "state" + name;
    }
  }
  function hideStates() { showState(""); }

  async function api(path, options) {
    const resp = await fetch(path, options);
    if (!resp.ok) throw new Error(`请求失败（HTTP ${resp.status}）`);
    return resp.json();
  }

  // ---- 渲染 ----
  function metricRow(kind, label, metric) {
    const score = metric?.score ?? 0;
    const basis = (metric?.basis || []).join(" · ");
    return `
      <div class="metric" data-kind="${kind}">
        <span class="metric-name">${label}</span>
        <div class="metric-track"><div class="metric-fill" style="width:${score}%"></div></div>
        <span class="metric-score">${score}</span>
        ${basis ? `<span class="metric-basis">${basis}</span>` : ""}
      </div>`;
  }

  function cardHtml(item) {
    const m = item.metrics || {};
    const author = item.Author?.Name || "";
    return `
      <article class="card">
        <div class="card-head">
          <div>
            <a class="card-title" href="${item.Url}" target="_blank" rel="noopener noreferrer">${escapeHtml(item.Title || "（无标题）")}</a>
            <div class="card-meta">
              <span class="type-tag">${TYPE_NAMES[item.ContentType] || item.ContentType || "内容"}</span>
              ${author ? `<span>${escapeHtml(author)}</span>` : ""}
              <span>${formatDate(item.FavTime)}</span>
            </div>
          </div>
        </div>
        ${item.Summary ? `<p class="card-summary">${escapeHtml(item.Summary)}</p>` : ""}
        <div class="metrics">
          ${metricRow("approval", "认可度", m.approval)}
          ${metricRow("richness", "信息量", m.richness)}
          ${metricRow("credibility", "准确性", m.credibility)}
        </div>
      </article>`;
  }

  function escapeHtml(text) {
    return String(text).replace(/[&<>"']/g, (ch) => (
      { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[ch]
    ));
  }

  function sortItems() {
    const by = {
      favtime: (a, b) => (b.FavTime || 0) - (a.FavTime || 0),
      approval: (a, b) => (b.metrics?.approval?.score || 0) - (a.metrics?.approval?.score || 0),
      richness: (a, b) => (b.metrics?.richness?.score || 0) - (a.metrics?.richness?.score || 0),
      credibility: (a, b) => (b.metrics?.credibility?.score || 0) - (a.metrics?.credibility?.score || 0),
    };
    return [...allItems].sort(by[sortMode] || by.favtime);
  }

  function renderCards() {
    const sorted = sortItems();
    els.cards.innerHTML = sorted.map(cardHtml).join("");
  }

  // ---- 数据流 ----
  async function loadCollections(force) {
    els.hero.hidden = true;
    els.workspace.hidden = true;
    showState("Loading");
    els.loadingText.textContent = "正在读取你的收藏夹（分页取全中）……";
    try {
      const data = await api("/api/collections" + (force ? "?force=1" : ""));
      if (!data.ok) throw new Error(data.error?.message || "读取收藏夹失败");
      allItems = data.items || [];
      els.favlistTitle.textContent = data.favlist?.Title || "我的收藏";
      els.countBadge.textContent = `${data.count} 条`;
      els.cacheBadge.hidden = !data.cached;
      if (allItems.length === 0) {
        els.workspace.hidden = false;
        showState("Empty");
        els.emptyText.textContent = data.message || "这个收藏夹还是空的。";
        return;
      }
      hideStates();
      els.workspace.hidden = false;
      renderCards();
    } catch (err) {
      showState("Error");
      els.errorText.textContent = err.message || "出了点问题，请重试。";
    }
  }

  function showHero(hint, isError) {
    els.hero.hidden = false;
    els.workspace.hidden = true;
    hideStates();
    if (hint) {
      els.loginHint.hidden = false;
      els.loginHint.textContent = hint;
      els.loginHint.classList.toggle("error", Boolean(isError));
    }
  }

  async function boot() {
    const params = new URLSearchParams(location.search);
    try {
      const status = await api("/api/oauth/status");
      if (status.authorized) {
        els.userArea.hidden = false;
        els.userName.textContent = status.profile?.name || "已连接";
        if (status.profile?.avatar_url) {
          els.userAvatar.src = status.profile.avatar_url;
          els.userAvatar.hidden = false;
        }
        await loadCollections(false);
        return;
      }
      let hint = "";
      let isError = false;
      if (params.get("oauth") === "error") {
        hint = status.error?.message || "登录未完成，请重试。";
        isError = true;
      } else if (!status.callback_configured) {
        hint = "说明：本地预览环境无法完成知乎登录（需要公网部署后的回调地址）。部署后此按钮即可正常使用。";
      }
      showHero(hint, isError);
    } catch (err) {
      showHero("服务暂时不可用：" + err.message, true);
    }
  }

  // ---- 事件 ----
  els.loginBtn.addEventListener("click", () => { location.href = "/api/oauth/start"; });
  els.logoutBtn.addEventListener("click", async () => {
    await api("/api/oauth/logout", { method: "POST" });
    location.href = "/";
  });
  els.sortSelect.addEventListener("change", () => {
    sortMode = els.sortSelect.value;
    renderCards();
  });
  els.refreshBtn.addEventListener("click", () => loadCollections(true));
  els.retryBtn.addEventListener("click", () => loadCollections(false));

  // 开发调试入口（本地预览用：可强制加载收藏列表验证 UI）
  window.__kd = { boot, loadCollections, get items() { return allItems; } };

  boot();
})();
