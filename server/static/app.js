/* 知识蒸馏站 —— 前端逻辑（原生 JS，无构建） */
(() => {
  "use strict";

  const $ = (id) => document.getElementById(id);
  const els = {
    hero: $("hero"), workspace: $("workspace"), cards: $("cards"),
    userArea: $("user-area"), userAvatar: $("user-avatar"), userName: $("user-name"),
    loginBtn: $("login-btn"), loginHint: $("login-hint"), logoutBtn: $("logout-btn"),
    sortSelect: $("sort-select"), refreshBtn: $("refresh-btn"),
    favlistTitle: $("favlist-title"), countBadge: $("count-badge"), loadedBadge: $("loaded-badge"),
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

  function formatClock(unixSeconds) {
    const d = new Date(unixSeconds * 1000);
    const now = new Date();
    const hm = `${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
    if (d.toDateString() === now.toDateString()) return `今天 ${hm}`;
    return `${d.getMonth() + 1} 月 ${d.getDate()} 日 ${hm}`;
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
  const METRIC_NAMES = { approval: "认可度", richness: "信息量", credibility: "准确性" };

  function radarSvg(metrics) {
    // 紧凑三轴雷达图（仅形状，无文字标签）：上=认可度，右下=信息量，左下=准确性
    const W = 78, H = 70, cx = 39, cy = 41, R = 34;
    const rad = (deg) => (deg * Math.PI) / 180;
    const pt = (angle, radius) => [
      cx + radius * Math.cos(rad(angle)),
      cy + radius * Math.sin(rad(angle)),
    ];
    const axes = [
      [metrics.approval?.score ?? 0, -90],
      [metrics.richness?.score ?? 0, 30],
      [metrics.credibility?.score ?? 0, 150],
    ];

    let grid = "";
    for (const level of [0.25, 0.5, 0.75, 1]) {
      const pts = axes.map(([, a]) => pt(a, R * level).map((v) => v.toFixed(1)).join(",")).join(" ");
      grid += `<polygon points="${pts}" class="radar-grid"/>`;
    }
    const spokes = axes.map(([, a]) => {
      const [x, y] = pt(a, R);
      return `<line x1="${cx}" y1="${cy}" x2="${x.toFixed(1)}" y2="${y.toFixed(1)}" class="radar-spoke"/>`;
    }).join("");
    const dataPts = axes.map(([v, a]) => pt(a, (R * Math.min(100, v)) / 100).map((v2) => v2.toFixed(1)).join(",")).join(" ");
    const dots = axes.map(([v, a]) => {
      const [x, y] = pt(a, (R * Math.min(100, v)) / 100);
      return `<circle cx="${x.toFixed(1)}" cy="${y.toFixed(1)}" r="2.6" class="radar-dot"/>`;
    }).join("");

    return `<svg class="radar" viewBox="0 0 ${W} ${H}" role="img" aria-label="三指标雷达图（上认可度 / 右下信息量 / 左下准确性）">
      ${grid}${spokes}
      <polygon points="${dataPts}" class="radar-area"/>
      ${dots}
    </svg>`;
  }

  function cardHtml(item) {
    const m = item.metrics || {};
    const author = item.Author?.Name || "";
    const legend = ["approval", "richness", "credibility"].map((kind) => {
      const metric = m[kind] || {};
      const basis = (metric.basis || []).join(" · ");
      return `<span class="legend-item" title="${escapeHtml(basis)}">
          <i class="dot dot-${kind}"></i>${METRIC_NAMES[kind]} <b>${metric.score ?? 0}</b>
        </span>`;
    }).join("");
    return `
      <article class="card">
        <a class="card-title" href="${item.Url}" target="_blank" rel="noopener noreferrer">${escapeHtml(item.Title || "（无标题）")}</a>
        <div class="card-main">
          <div class="card-body">
            <div class="card-meta">
              <span class="type-tag">${TYPE_NAMES[item.ContentType] || item.ContentType || "内容"}</span>
              ${author ? `<span>${escapeHtml(author)}</span>` : ""}
              <span>${formatDate(item.FavTime)}</span>
            </div>
            ${item.Summary ? `<p class="card-summary">${escapeHtml(item.Summary)}</p>` : ""}
            <div class="card-legend">${legend}</div>
          </div>
          <div class="card-radar">${radarSvg(m)}</div>
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
      combined: (a, b) => (b.combined_score || 0) - (a.combined_score || 0),
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
      if (!data.ok) {
        const error = new Error(data.error?.message || "读取收藏夹失败");
        error.code = data.error?.code;
        throw error;
      }
      allItems = data.items || [];
      els.favlistTitle.textContent = data.favlist?.Title || "我的收藏";
      els.countBadge.textContent = `${data.count} 条`;
      if (data.loaded_at) {
        els.loadedBadge.hidden = false;
        els.loadedBadge.textContent = "上次加载 " + formatClock(data.loaded_at);
      } else {
        els.loadedBadge.hidden = true;
      }
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
      if (err.code === "LOGIN_REQUIRED") {
        showHero("请先登录知乎账号，查看属于你自己的收藏。", false);
        return;
      }
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
