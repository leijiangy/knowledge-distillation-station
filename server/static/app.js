/* 知识蒸馏站 —— 前端逻辑（原生 JS，无构建） */
(() => {
  "use strict";

  const $ = (id) => document.getElementById(id);
  const els = {
    page: $("page"),
    sidebarToggle: $("sidebar-toggle"), sidebarExpand: $("sidebar-expand"),
    favGroup: $("fav-group"), favParent: $("fav-parent"), favSub: $("fav-sub"),
    navHome: $("nav-home"), navHistory: $("nav-history"),
    userBlock: $("user-block"), userAvatar: $("user-avatar"), userName: $("user-name"), userSub: $("user-sub"),
    favlistTitle: $("favlist-title"), countBadge: $("count-badge"), loadedBadge: $("loaded-badge"),
    sortSelect: $("sort-select"), refreshBtn: $("refresh-btn"),
    cards: $("cards"),
    stateLoading: $("state-loading"), loadingText: $("loading-text"),
    stateError: $("state-error"), errorText: $("error-text"), retryBtn: $("retry-btn"),
    stateEmpty: $("state-empty"), emptyText: $("empty-text"),
    stateLogin: $("state-login"), loginText: $("login-text"), loginBtn: $("login-btn"),
    pager: $("pager"),
  };

  const PAGE_SIZE = 8;   // 每页卡片数

  const TYPE_NAMES = { answer: "回答", article: "文章", zvideo: "视频", pin: "想法", question: "问题" };
  const METRIC_NAMES = { approval: "认可度", richness: "信息量", credibility: "准确性" };

  // ---- 状态 ----
  let status = null;          // /api/oauth/status 的结果
  let favlists = [];          // 收藏夹列表
  let currentToken = null;    // 当前收藏夹 UrlToken
  let allItems = [];          // 当前收藏夹的全部条目
  let sortMode = "favtime";
  let page = 1;               // 当前页码（客户端分页）

  // ---- 工具 ----
  function escapeHtml(text) {
    return String(text == null ? "" : text).replace(/[&<>"']/g, (ch) => (
      { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[ch]
    ));
  }

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
    for (const key of ["stateLoading", "stateError", "stateEmpty", "stateLogin"]) {
      els[key].hidden = key !== "state" + name;
    }
    if (name) {
      els.cards.innerHTML = "";
      els.pager.hidden = true;
    }
  }
  function hideStates() { showState(null); }

  async function api(path, options) {
    const resp = await fetch(path, options);
    if (!resp.ok) throw new Error(`请求失败（HTTP ${resp.status}）`);
    return resp.json();
  }

  // ---- 渲染：雷达图与卡片 ----
  function radarSvg(metrics) {
    const W = 78, H = 70, cx = 39, cy = 41, R = 34;
    const rad = (deg) => (deg * Math.PI) / 180;
    const pt = (angle, radius) => [cx + radius * Math.cos(rad(angle)), cy + radius * Math.sin(rad(angle))];
    const axes = [
      [metrics.approval && metrics.approval.score, -90],
      [metrics.richness && metrics.richness.score, 30],
      [metrics.credibility && metrics.credibility.score, 150],
    ].map(([v, a]) => [Number(v) || 0, a]);

    let grid = "";
    for (const level of [1 / 3, 2 / 3, 1]) {
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
      return `<circle cx="${x.toFixed(1)}" cy="${y.toFixed(1)}" r="2.8" class="radar-dot"/>`;
    }).join("");
    return `<svg class="radar" viewBox="0 0 ${W} ${H}" role="img" aria-label="三指标雷达图">
      ${grid}${spokes}<polygon points="${dataPts}" class="radar-area"/>${dots}</svg>`;
  }

  function cardHtml(item) {
    const m = item.metrics || {};
    const author = item.Author && item.Author.Name ? item.Author.Name : "";
    const labels = ["approval", "richness", "credibility"].map((kind) => {
      const metric = m[kind] || {};
      const basis = (metric.basis || []).join(" · ");
      return `<span title="${escapeHtml(basis)}">${METRIC_NAMES[kind]}<b>${metric.score != null ? metric.score : 0}</b></span>`;
    }).join("");
    return `
      <article class="card">
        <a class="card-title" href="${escapeHtml(item.Url)}" target="_blank" rel="noopener noreferrer">${escapeHtml(item.Title || "（无标题）")}</a>
        <div class="meta">
          <span class="tag">${TYPE_NAMES[item.ContentType] || item.ContentType || "内容"}</span>
          ${author ? `<span>${escapeHtml(author)}</span>` : ""}
          <span>${formatDate(item.FavTime)}</span>
        </div>
        <div class="card-main">
          <p class="summary">${escapeHtml(item.Summary || "")}</p>
          <div class="card-radar">
            ${radarSvg(m)}
            <div class="radar-labels">${labels}</div>
          </div>
        </div>
        <div class="foot">依据：收藏 ${item.FavoriteCount || 0} · 赞同 ${item.LikeCount || 0} · 评论 ${item.CommentCount || 0}</div>
      </article>`;
  }

  function sortItems() {
    const by = {
      favtime: (a, b) => (b.FavTime || 0) - (a.FavTime || 0),
      combined: (a, b) => (b.combined_score || 0) - (a.combined_score || 0),
      approval: (a, b) => ((b.metrics && b.metrics.approval && b.metrics.approval.score) || 0) - ((a.metrics && a.metrics.approval && a.metrics.approval.score) || 0),
      richness: (a, b) => ((b.metrics && b.metrics.richness && b.metrics.richness.score) || 0) - ((a.metrics && a.metrics.richness && a.metrics.richness.score) || 0),
      credibility: (a, b) => ((b.metrics && b.metrics.credibility && b.metrics.credibility.score) || 0) - ((a.metrics && a.metrics.credibility && a.metrics.credibility.score) || 0),
    };
    return [...allItems].sort(by[sortMode] || by.favtime);
  }

  function totalPages() {
    return Math.max(1, Math.ceil(allItems.length / PAGE_SIZE));
  }

  function renderCards() {
    const sorted = sortItems();
    const start = (page - 1) * PAGE_SIZE;
    els.cards.innerHTML = sorted.slice(start, start + PAGE_SIZE).map(cardHtml).join("");
    renderPager();
  }

  // 页码序列（超过 5 页时折叠中间部分为省略号）
  function pageNumbers(total, current) {
    if (total <= 5) return Array.from({ length: total }, (_, i) => i + 1);
    const set = new Set([1, total, current - 1, current, current + 1]);
    const nums = [...set].filter((n) => n >= 1 && n <= total).sort((a, b) => a - b);
    const out = [];
    let prev = 0;
    for (const n of nums) {
      if (n - prev > 1) out.push("gap");
      out.push(n);
      prev = n;
    }
    return out;
  }

  function renderPager() {
    const total = totalPages();
    if (total <= 1) { els.pager.hidden = true; return; }
    els.pager.hidden = false;
    closePagerPop();
    let html = `<button class="pager-btn" data-go="prev" ${page === 1 ? "disabled" : ""} aria-label="上一页">‹</button>`;
    for (const n of pageNumbers(total, page)) {
      if (n === "gap") html += `<button class="pager-btn pager-more" aria-label="选择页码" title="选择页码">…</button>`;
      else html += `<button class="pager-btn${n === page ? " active" : ""}" data-page="${n}">${n}</button>`;
    }
    html += `<button class="pager-btn" data-go="next" ${page === total ? "disabled" : ""} aria-label="下一页">›</button>`;
    els.pager.innerHTML = html;
    els.pager.querySelectorAll("[data-page]").forEach((btn) => {
      btn.addEventListener("click", () => goPage(Number(btn.getAttribute("data-page"))));
    });
    const prev = els.pager.querySelector('[data-go="prev"]');
    const next = els.pager.querySelector('[data-go="next"]');
    if (prev) prev.addEventListener("click", () => goPage(page - 1));
    if (next) next.addEventListener("click", () => goPage(page + 1));
    const more = els.pager.querySelector(".pager-more");
    if (more) {
      more.addEventListener("click", (event) => {
        event.stopPropagation();
        if (els.pager.querySelector(".pager-pop")) { closePagerPop(); return; }
        openPagerPop();
      });
    }
  }

  // 省略号浮层：窗口内小面板，手动选页
  function openPagerPop() {
    const total = totalPages();
    const pop = document.createElement("div");
    pop.className = "pager-pop";
    pop.innerHTML = Array.from({ length: total }, (_, i) => i + 1).map((n) =>
      `<button class="pop-page${n === page ? " active" : ""}" data-pop="${n}">${n}</button>`
    ).join("");
    pop.addEventListener("click", (event) => event.stopPropagation());
    pop.querySelectorAll("[data-pop]").forEach((btn) => {
      btn.addEventListener("click", () => {
        goPage(Number(btn.getAttribute("data-pop")));
        closePagerPop();
      });
    });
    els.pager.appendChild(pop);
    setTimeout(() => document.addEventListener("click", closePagerPop), 0);
  }

  function closePagerPop() {
    const pop = els.pager.querySelector(".pager-pop");
    if (pop) pop.remove();
    document.removeEventListener("click", closePagerPop);
  }

  function goPage(target) {
    const total = totalPages();
    const nextPage = Math.min(Math.max(1, target), total);
    if (nextPage === page) return;
    page = nextPage;
    renderCards();
    window.scrollTo({ top: 0, behavior: "smooth" });
  }

  // ---- 渲染：侧边栏 ----
  function renderUser() {
    if (!status) {
      els.userName.textContent = "未登录";
      els.userSub.textContent = "点击登录知乎账号";
      return;
    }
    if (status.authorized) {
      const profile = status.profile || {};
      els.userName.textContent = profile.name || "已连接";
      els.userSub.textContent = "已连接知乎账号";
      if (profile.avatar_url) {
        els.userAvatar.innerHTML = `<img src="${escapeHtml(profile.avatar_url)}" alt="">`;
      } else {
        els.userAvatar.textContent = (profile.name || "知").slice(0, 1);
      }
      return;
    }
    if (status.self_mode) {
      els.userName.textContent = "本人模式";
      els.userSub.textContent = "本地预览 · 未登录";
      return;
    }
    els.userName.textContent = "未登录";
    els.userSub.textContent = "点击登录知乎账号";
  }

  function renderFavLists() {
    if (!favlists.length) {
      els.favSub.innerHTML = `<div class="nav-sub-loading">没有可用的收藏夹</div>`;
      return;
    }
    els.favSub.innerHTML = favlists.map((f) => {
      const token = String(f.UrlToken);
      const active = token === String(currentToken);
      const countHtml = active && allItems.length ? ` <span class="sub-count">${allItems.length}</span>` : "";
      return `<button class="nav-subitem${active ? " active" : ""}" data-token="${token}">${escapeHtml(f.Title || "未命名收藏夹")}${countHtml}</button>`;
    }).join("");
    els.favSub.querySelectorAll(".nav-subitem").forEach((btn) => {
      btn.addEventListener("click", () => {
        const token = btn.getAttribute("data-token");
        if (token === String(currentToken)) return;
        loadCollections(token, false);
      });
    });
  }

  // ---- 数据流 ----
  async function loadFavlists() {
    try {
      const data = await api("/api/favlists");
      if (!data.ok) throw new Error((data.error && data.error.message) || "读取收藏夹列表失败");
      favlists = data.items || [];
      renderFavLists();
      return favlists;
    } catch (err) {
      els.favSub.innerHTML = `<div class="nav-sub-loading">${escapeHtml(err.message || "读取失败")}</div>`;
      return [];
    }
  }

  async function loadCollections(token, force) {
    currentToken = token != null ? token : currentToken;
    showState("Loading");
    els.loadingText.textContent = "正在读取收藏夹（分页取全中）……";
    try {
      const query = [];
      if (currentToken != null) query.push("favlist=" + encodeURIComponent(currentToken));
      if (force) query.push("force=1");
      const data = await api("/api/collections" + (query.length ? "?" + query.join("&") : ""));

      if (!data.ok) {
        const error = new Error((data.error && data.error.message) || "读取收藏夹失败");
        error.code = data.error && data.error.code;
        throw error;
      }
      allItems = data.items || [];
      page = 1;
      const meta = data.favlist || {};
      if (meta.UrlToken != null) currentToken = String(meta.UrlToken);
      els.favlistTitle.innerHTML = `${escapeHtml(meta.Title || "我的收藏")} <span class="count" id="count-badge">${data.count} 条</span>`;
      if (data.loaded_at) {
        els.loadedBadge.hidden = false;
        els.loadedBadge.textContent = "上次加载 " + formatClock(data.loaded_at);
      } else {
        els.loadedBadge.hidden = true;
      }
      renderFavLists();
      if (allItems.length === 0) {
        showState("Empty");
        els.emptyText.textContent = data.message || "这个收藏夹还是空的。";
        return;
      }
      hideStates();
      renderCards();
    } catch (err) {
      if (err.code === "LOGIN_REQUIRED") {
        showLoginState("请先登录知乎账号，查看属于你自己的收藏。");
        return;
      }
      showState("Error");
      els.errorText.textContent = err.message || "出了点问题，请重试。";
    }
  }

  function showLoginState(message) {
    hideStates();
    els.stateLogin.hidden = false;
    els.cards.innerHTML = "";
    if (message) els.loginText.textContent = message;
    if (!status || (!status.callback_configured && !status.self_mode)) {
      els.loginBtn.disabled = true;
      els.loginBtn.textContent = "部署后可用";
      if (!message) els.loginText.textContent = "本地预览环境无法完成知乎登录（需要公网部署后的回调地址）。";
    } else {
      els.loginBtn.disabled = false;
      els.loginBtn.textContent = "连接我的知乎收藏夹";
    }
  }

  async function boot() {
    try {
      status = await api("/api/oauth/status");
    } catch (err) {
      showState("Error");
      els.errorText.textContent = "服务暂时不可用：" + (err.message || "请稍后重试");
      return;
    }
    renderUser();

    const params = new URLSearchParams(location.search);
    if (params.get("oauth") === "error") {
      const msg = (status.error && status.error.message) || "登录未完成，请重试。";
      showLoginState(msg);
      return;
    }

    const canRead = status.authorized || status.self_mode;
    if (!canRead) {
      showLoginState("");
      return;
    }
    await loadFavlists();
    await loadCollections(null, false);
  }

  // ---- 事件 ----
  els.sidebarToggle.addEventListener("click", () => els.page.classList.add("sidebar-collapsed"));
  els.sidebarExpand.addEventListener("click", () => els.page.classList.remove("sidebar-collapsed"));
  els.favParent.addEventListener("click", () => els.favGroup.classList.toggle("open"));
  els.sortSelect.addEventListener("change", () => {
    sortMode = els.sortSelect.value;
    page = 1;
    renderCards();
  });
  els.refreshBtn.addEventListener("click", () => loadCollections(null, true));
  els.retryBtn.addEventListener("click", () => loadCollections(null, false));
  els.loginBtn.addEventListener("click", () => { location.href = "/api/oauth/start"; });
  els.userBlock.addEventListener("click", async () => {
    if (status && status.authorized) {
      await api("/api/oauth/logout", { method: "POST" });
      location.href = "/";
      return;
    }
    if (status && status.callback_configured) location.href = "/api/oauth/start";
  });
  // 占位导航：首页 / 学习记录（暂不跳转）
  for (const el of [els.navHome, els.navHistory]) {
    el.addEventListener("click", (event) => event.preventDefault());
  }

  // 开发调试入口（本地预览用）
  window.__kd = { boot, loadCollections, get items() { return allItems; } };

  boot();
})();
