/* 知识蒸馏站 —— 前端逻辑（原生 JS，无构建） */
(() => {
  "use strict";

  const $ = (id) => document.getElementById(id);
  const els = {
    page: $("page"),
    sidebarToggle: $("sidebar-toggle"), sidebarExpand: $("sidebar-expand"),
    favGroup: $("fav-group"), favParent: $("fav-parent"), favSub: $("fav-sub"),
    navHome: $("nav-home"), navHistory: $("nav-history"),
    viewHome: $("view-home"), viewCollections: $("view-collections"),
    homeLoginBtn: $("home-login-btn"), homeGotoCollections: $("home-goto-collections"),
    homeBookmarklet: $("home-bookmarklet"), homeBookmarkletCode: $("home-bookmarklet-code"),
    homeCopyBookmarklet: $("home-copy-bookmarklet"), homeCopyBookmark: $("home-copy-bookmark"),
    copyBookmarkHint: $("copy-bookmark-hint"),
    userBlock: $("user-block"), userAvatar: $("user-avatar"), userName: $("user-name"), userSub: $("user-sub"),
    favlistTitle: $("favlist-title"), countBadge: $("count-badge"), loadedBadge: $("loaded-badge"),
    sortSelect: $("sort-select"), refreshBtn: $("refresh-btn"),
    cards: $("cards"),
    stateLoading: $("state-loading"), loadingText: $("loading-text"),
    stateError: $("state-error"), errorText: $("error-text"), retryBtn: $("retry-btn"),
    stateEmpty: $("state-empty"), emptyText: $("empty-text"),
    pager: $("pager"),
    fulltextModal: $("fulltext-modal"), fulltextBackdrop: $("fulltext-backdrop"),
    fulltextClose: $("fulltext-close"), fulltextTitle: $("fulltext-title"),
    fulltextMeta: $("fulltext-meta"), fulltextBody: $("fulltext-body"),
  };

  const PAGE_SIZE = 8;   // 每页卡片数

  const TYPE_NAMES = { answer: "回答", article: "文章", zvideo: "视频", pin: "想法", question: "问题" };
  const METRIC_NAMES = { approval: "认可度", richness: "信息量", credibility: "准确性" };
  const AVATAR_COLORS = ["#dce8d5", "#dbe7ee", "#ece2cf", "#e4dced", "#d9e8e4", "#ecdfd8"];

  function authorColor(name) {
    let hash = 0;
    for (const ch of String(name || "?")) hash = (hash * 31 + ch.charCodeAt(0)) % 997;
    return AVATAR_COLORS[hash % AVATAR_COLORS.length];
  }

  // ---- 状态 ----
  let status = null;          // /api/oauth/status 的结果
  let favlists = [];          // 收藏夹列表
  let currentToken = null;    // 当前收藏夹 UrlToken
  let allItems = [];          // 当前收藏夹的全部条目
  let sortMode = "favtime";
  let page = 1;               // 当前页码（客户端分页）
  let distilledMap = {};      // 已蒸馏内容索引：url(去参) -> {title, length, at}

  // ---- 蒸馏书签（动态生成：写入当前站点域名） ----
  function buildBookmarklet() {
    const origin = window.location.origin;
    return [
      "javascript:(function(){",
      "var el=document.querySelector('.RichContent-inner')||document.querySelector('.Post-RichText')||document.querySelector('.RichText');",
      "if(!el){alert('请在知乎的回答或文章页面使用这个书签');return;}",
      "var text=(el.innerText||'').trim();",
      "var title=(document.title||'').replace(/ ?[-—|] ?知乎.*$/,'').trim();",
      "if(text.length<100){alert('内容过短（'+text.length+' 字），可能不是文章页');return;}",
      "if(!confirm('蒸馏这篇文章？\\n\\n'+title+'\\n全文约 '+text.length+' 字')){return;}",
      "fetch('" + origin + "/api/ingest',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({title:title,url:location.href,content:text})})",
      ".then(function(r){return r.json()})",
      ".then(function(d){alert(d.ok?('✓ 已进入知识蒸馏站（'+text.length+' 字）'):('失败：'+((d.error&&d.error.message)||'未知错误')))});",
      "})();",
    ].join("");
  }

  function initBookmarklet() {
    const code = buildBookmarklet();
    if (els.homeBookmarklet) els.homeBookmarklet.setAttribute("href", code);
    if (els.homeBookmarkletCode) els.homeBookmarkletCode.value = code;
  }

  // ---- 视图切换：首页 / 我的收藏 ----
  function showView(name) {
    const isHome = name === "home";
    if (els.viewHome) els.viewHome.hidden = !isHome;
    if (els.viewCollections) els.viewCollections.hidden = isHome;
    if (els.navHome) els.navHome.classList.toggle("current", isHome);
    if (els.favParent) els.favParent.classList.toggle("current", !isHome);
  }

  function openModal(el) { if (el) el.hidden = false; }
  function closeModal(el) { if (el) el.hidden = true; }

  async function openFulltext(url, title) {
    els.fulltextTitle.textContent = title || "全文";
    els.fulltextMeta.textContent = "加载中…";
    els.fulltextBody.textContent = "";
    openModal(els.fulltextModal);
    try {
      const data = await api("/api/distilled/content?url=" + encodeURIComponent(url));
      if (!data.ok) throw new Error((data.error && data.error.message) || "读取失败");
      els.fulltextMeta.textContent = "全文 " + data.length + " 字 · 由「蒸馏书签」从知乎页面送入";
      els.fulltextBody.textContent = data.content;
    } catch (err) {
      els.fulltextMeta.textContent = err.message || "读取失败";
    }
  }

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
    for (const key of ["stateLoading", "stateError", "stateEmpty"]) {
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
    const initial = author ? author.slice(0, 1) : "·";
    const dKey = String(item.Url || "").split("?")[0];
    const dist = distilledMap[dKey];
    const badgeHtml = dist
      ? `<button class="distill-badge" data-distill="${escapeHtml(dKey)}" data-title="${escapeHtml(item.Title || "")}">✓ 已蒸馏 · 全文 ${dist.length} 字</button>`
      : "";
    const labels = ["approval", "richness", "credibility"].map((kind) => {
      const metric = m[kind] || {};
      const basis = (metric.basis || []).join(" · ");
      return `<span title="${escapeHtml(basis)}">${METRIC_NAMES[kind]}<b>${metric.score != null ? metric.score : 0}</b></span>`;
    }).join("");
    return `
      <article class="card" data-url="${escapeHtml(item.Url || "")}" data-title="${escapeHtml(item.Title || "")}">
        <a class="card-title" href="${escapeHtml(item.Url)}" target="_blank" rel="noopener noreferrer">${escapeHtml(item.Title || "（无标题）")}</a>
        <div class="meta">
          <span class="tag">${TYPE_NAMES[item.ContentType] || item.ContentType || "内容"}</span>
          <span class="meta-author">
            <span class="author-avatar" style="background:${authorColor(author)}">${escapeHtml(initial)}</span>
            <b class="author-name">${escapeHtml(author || "未知作者")}</b>
            <img class="author-badge" alt="" hidden>
            <span class="author-badge-text" hidden></span>
          </span>
          <span>${formatDate(item.FavTime)}</span>
        </div>
        <div class="card-main">
          <p class="summary">${escapeHtml(item.Summary || "")}</p>
          <div class="card-radar">
            ${radarSvg(m)}
            <div class="radar-labels">${labels}</div>
          </div>
        </div>
        ${badgeHtml}
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
    els.cards.querySelectorAll("[data-distill]").forEach((btn) => {
      btn.addEventListener("click", () => openFulltext(btn.getAttribute("data-distill"), btn.getAttribute("data-title")));
    });
    renderPager();
    observeCards();
  }

  // ---- 作者信息懒加载：卡片进入视口时才批量请求头像/徽章/签名（服务端有 1 天缓存） ----
  let metaObserver = null;
  let metaQueue = [];
  let metaTimer = null;

  function observeCards() {
    if (!("IntersectionObserver" in window)) return;  // 不支持则保持字母头像
    if (!metaObserver) {
      metaObserver = new IntersectionObserver((entries) => {
        const visible = entries.filter((e) => e.isIntersecting).map((e) => e.target);
        for (const card of visible) {
          metaObserver.unobserve(card);
          metaQueue.push(card);
        }
        if (visible.length) scheduleFlush();
      }, { rootMargin: "300px 0px" });
    }
    els.cards.querySelectorAll(".card[data-url]").forEach((card) => metaObserver.observe(card));
  }

  function scheduleFlush() {
    if (metaTimer) clearTimeout(metaTimer);
    metaTimer = setTimeout(flushMeta, 150);
  }

  async function flushMeta() {
    const batch = metaQueue.splice(0, 12);
    if (!batch.length) return;
    try {
      const data = await api("/api/article_meta", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          items: batch.map((card) => ({
            url: card.getAttribute("data-url"),
            title: card.getAttribute("data-title"),
          })),
        }),
      });
      if (data.ok && data.meta) {
        for (const card of batch) {
          const meta = data.meta[card.getAttribute("data-url")];
          if (meta) applyMeta(card, meta);
        }
      }
    } catch (err) {
      // 静默失败：保留字母头像，不影响卡片
    }
    if (metaQueue.length) scheduleFlush();
  }

  function applyMeta(card, meta) {
    const avatar = card.querySelector(".author-avatar");
    if (avatar && meta.avatar) {
      avatar.innerHTML = `<img src="${escapeHtml(meta.avatar)}" alt="" loading="lazy" referrerpolicy="no-referrer">`;
    }
    const badge = card.querySelector(".author-badge");
    if (badge && meta.badge) {
      badge.src = meta.badge;
      badge.hidden = false;
    }
    const badgeText = card.querySelector(".author-badge-text");
    if (badgeText && meta.badge_text) {
      badgeText.textContent = meta.badge_text;
      badgeText.hidden = false;
    }
    // 作者签名：meta 行空间有限，作为悬浮提示展示
    const wrap = card.querySelector(".meta-author");
    if (wrap && meta.signature) wrap.title = meta.signature;
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
      // 拉取"已蒸馏"索引（书签送进来的全文标记）
      try {
        const dist = await api("/api/distilled");
        distilledMap = dist.ok ? (dist.items || {}) : {};
      } catch (err) {
        distilledMap = {};
      }
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
        showHome();
        return;
      }
      showState("Error");
      els.errorText.textContent = err.message || "出了点问题，请重试。";
    }
  }

  function showHome() {
    showView("home");
    hideStates();
    els.cards.innerHTML = "";
    if (els.homeLoginBtn) {
      const canLogin = Boolean(status && status.callback_configured);
      els.homeLoginBtn.disabled = !canLogin;
      els.homeLoginBtn.textContent = canLogin ? "连接我的知乎收藏夹" : "本地预览 · 部署后可登录";
    }
    if (els.homeGotoCollections) {
      els.homeGotoCollections.hidden = !(status && (status.authorized || status.self_mode));
    }
  }

  async function boot() {
    try {
      status = await api("/api/oauth/status");
    } catch (err) {
      showView("collections");
      showState("Error");
      els.errorText.textContent = "服务暂时不可用：" + (err.message || "请稍后重试");
      return;
    }
    renderUser();

    const params = new URLSearchParams(location.search);
    const canRead = status.authorized || status.self_mode;
    if (params.get("oauth") === "error") {
      showHome();
      return;
    }
    if (!canRead) {
      showHome();
      return;
    }
    showView("collections");
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
  els.userBlock.addEventListener("click", async () => {
    if (status && status.authorized) {
      await api("/api/oauth/logout", { method: "POST" });
      location.href = "/";
      return;
    }
    if (status && status.callback_configured) location.href = "/api/oauth/start";
  });
  // 占位导航：学习记录（暂不跳转）
  els.navHistory.addEventListener("click", (event) => event.preventDefault());

  // 首页 / 收藏视图切换
  els.navHome.addEventListener("click", () => showView("home"));
  if (els.homeGotoCollections) {
    els.homeGotoCollections.addEventListener("click", () => showView("collections"));
  }
  if (els.homeLoginBtn) {
    els.homeLoginBtn.addEventListener("click", () => { location.href = "/api/oauth/start"; });
  }

  // 蒸馏书签：初始化 / 复制书签（富文本，可粘贴成书签）/ 复制代码
  initBookmarklet();

  async function copyBookmarkAsLink() {
    const code = buildBookmarklet();
    const html = '<a href="' + escapeHtml(code) + '">🧪 蒸馏这篇文章</a>';
    const hint = els.copyBookmarkHint;
    try {
      if (navigator.clipboard && window.ClipboardItem) {
        await navigator.clipboard.write([
          new ClipboardItem({
            "text/html": new Blob([html], { type: "text/html" }),
            "text/plain": new Blob([code], { type: "text/plain" }),
          }),
        ]);
      } else {
        await navigator.clipboard.writeText(code);
      }
      if (hint) hint.textContent = "现在右键浏览器书签栏 →「粘贴」";
      if (els.homeCopyBookmark) {
        els.homeCopyBookmark.textContent = "已复制 ✓";
        setTimeout(() => { els.homeCopyBookmark.textContent = "📋 复制书签"; }, 2500);
      }
    } catch (err) {
      if (hint) hint.textContent = "复制失败，请展开下方「手动添加」复制代码";
    }
  }

  if (els.homeCopyBookmark) els.homeCopyBookmark.addEventListener("click", copyBookmarkAsLink);

  if (els.homeCopyBookmarklet) {
    els.homeCopyBookmarklet.addEventListener("click", async () => {
      try {
        await navigator.clipboard.writeText(els.homeBookmarkletCode.value);
        els.homeCopyBookmarklet.textContent = "已复制 ✓";
        setTimeout(() => { els.homeCopyBookmarklet.textContent = "复制书签代码"; }, 1800);
      } catch (err) {
        els.homeBookmarkletCode.select();
        els.homeCopyBookmarklet.textContent = "请按 Ctrl+C 复制";
      }
    });
  }
  // 全文弹窗关闭
  if (els.fulltextClose) els.fulltextClose.addEventListener("click", () => closeModal(els.fulltextModal));
  if (els.fulltextBackdrop) els.fulltextBackdrop.addEventListener("click", () => closeModal(els.fulltextModal));
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") closeModal(els.fulltextModal);
  });

  // 开发调试入口（本地预览用）
  window.__kd = { boot, loadCollections, get items() { return allItems; } };

  boot();
})();
