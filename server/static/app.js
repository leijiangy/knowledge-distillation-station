/* 知识蒸馏站 —— 前端逻辑（原生 JS，无构建） */
(() => {
  "use strict";

  const $ = (id) => document.getElementById(id);
  const els = {
    page: $("page"),
    sidebarToggle: $("sidebar-toggle"), sidebarExpand: $("sidebar-expand"),
    favGroup: $("fav-group"), favParent: $("fav-parent"), favSub: $("fav-sub"),
    navHome: $("nav-home"), navRecommend: $("nav-recommend"),
    histGroup: $("hist-group"), histParent: $("hist-parent"), histSub: $("hist-sub"),
    viewRecommend: $("view-recommend"), recommendCards: $("recommend-cards"),
    recommendCount: $("recommend-count"), recommendRefresh: $("recommend-refresh"),
    recoSeedHint: $("reco-seed-hint"), recoExpand: $("reco-expand"),
    recoLoading: $("reco-loading"), recoError: $("reco-error"), recoErrorText: $("reco-error-text"),
    recoEmpty: $("reco-empty"), recoEmptyText: $("reco-empty-text"), recoRetry: $("reco-retry"),
    viewHome: $("view-home"), viewCollections: $("view-collections"),
    homeGotoCollections: $("home-goto-collections"),
    favFilter: $("fav-filter"), emptyGotoSearch: $("empty-goto-search"),
    navSearch: $("nav-search"), viewSearch: $("view-search"),
    searchExpand: $("search-expand"), searchInput: $("search-input"), searchBtn: $("search-btn"),
    searchCards: $("search-cards"),
    searchIdle: $("search-idle"), searchLoading: $("search-loading"),
    searchError: $("search-error"), searchErrorText: $("search-error-text"), searchRetry: $("search-retry"),
    searchEmpty: $("search-empty"), searchEmptyText: $("search-empty-text"),
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
    distillGuide: $("distill-guide"), distillGuideClose: $("distill-guide-close"),
    distillGuideDot: $("distill-guide-dot"), distillGuideTitle: $("distill-guide-title"),
    distillGuideBody: $("distill-guide-body"),
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
  let favFilter = "";         // 收藏列表内的标题/作者筛选（纯前端，F33）
  let distilledMap = {};      // 已保存内容索引：url(去参) -> {title, length, at}
  let historyMap = {};        // 学习记录索引：url(去参) -> {seg, total}

  // ---- 保存书签（动态生成：写入当前站点域名；带 #kd=1 来源标记的页面蒸完自动跳回） ----
  // 书签逻辑一改就改这里：书签代码是拖的那一刻生成的，旧标签页拖出来的是旧代码，
  // 用户靠这一行能自查装的是哪一版（v5：配图取址支持懒加载图与 srcset）
  const BOOKMARKLET_VERSION = "v5";

  function buildBookmarklet() {
    const origin = window.location.origin;
    return [
      "javascript:(function(){",
      "var el=document.querySelector('.RichContent-inner')||document.querySelector('.Post-RichText')||document.querySelector('.RichText');",
      "if(!el){",
      "var isZhihu=location.hostname.indexOf('zhihu.com')>=0;",
      "if(confirm((isZhihu?'这个知乎页面不是回答或文章，':'当前不是知乎页面，')+'是否前往知识蒸馏站？')){window.open('" + origin + "/','_blank');}",
      "return;}",
      "var title=(document.title||'').replace(/^(\\([^)]*\\)\\s+)+/,'').replace(/ ?[-—|] ?知乎.*$/,'').trim();",
      // 文章地址以地址栏为准，只有「点开大图」时地址栏才会变成图床地址，那时才退回 canonical/og:url。
      // 反过来做（优先 canonical/og:url）会踩两件事：①灯箱状态下 canonical 解析成当前地址；
      // ②知乎回答页的 og:url 是 .../question/undefined/answer/<id>，与卡片认的 /answer/<id> 对不上
      "var canon=document.querySelector('link[rel=canonical]')||document.querySelector('meta[property=\"og:url\"]');",
      "var canonUrl=(canon&&(canon.href||canon.content))||'';",
      "var isImg=function(u){return /(^|\\.)zhimg\\.com$/i.test((u||'').split('/')[2]||'');};",
      "var pageUrl=(location.href||'').split('#')[0];",
      "if(isImg(pageUrl))pageUrl=canonUrl;",
      "if(!pageUrl||isImg(pageUrl)){alert('当前地址是图片地址（可能是点开了大图）：请回到文章页面再点这个书签。');return;}",
      "pageUrl=pageUrl.replace(/\\/question\\/undefined\\/answer\\/(\\d+)/,'/answer/$1');",
      // 配图：先在每张图前插一个不可见标记，innerText 会把标记放在图片真实位置上，
      // 由此得到图片在正文里的字符下标；读完再把标记从文本和 DOM 里清掉（正文逐字不变）
      // 取图地址：知乎正文的图是懒加载的，没进过视口的图 currentSrc/src 还是 data: 占位符，
      // 真地址在 data-src / data-original 这些属性里——按顺序挑第一个像真地址的。
      "var pickUrl=function(im){",
      "var cands=[im.currentSrc,im.getAttribute('src'),im.getAttribute('data-src'),",
      "im.getAttribute('data-original'),im.getAttribute('data-actualsrc'),im.getAttribute('data-lazy-src')];",
      "var ss=String(im.getAttribute('srcset')||'');",
      "if(ss){var tail=ss.split(',').pop()||'';cands.push((tail.trim().split(/\\s+/)[0])||'');}",
      "for(var k=0;k<cands.length;k++){",
      "var u=cands[k];if(!u||typeof u!=='string')continue;",
      "if(u.indexOf('data:')===0||u.indexOf('blob:')===0)continue;",
      "try{u=new URL(u,location.href).href;}catch(e){continue;}",
      "var h=(u.split('/')[2]||'').toLowerCase();",
      "if(!/(^|\\.)(zhimg|zhihu)\\.com$/.test(h))continue;",
      "return u.split('?')[0];",
      "}",
      "return '';};",
      "var seen=[];var picked=[];var list=el.querySelectorAll('img');",
      "for(var i=0;i<list.length&&picked.length<9;i++){",
      "var im=list[i];var cls=String(im.className||'');",
      "if(/avatar|emoji|icon|badge|logo|symbol|sticker/i.test(cls))continue;",
      "var s=pickUrl(im);",
      "if(!s)continue;",
      // 只跳过"明确是小图"的：naturalWidth 为 0 说明还没加载，不能当成小图
      // （clientWidth 在这里不可用：图未加载或加载失败时它是浏览器的占位尺寸，会把正常图误杀）
      "if(im.naturalWidth>0&&im.naturalWidth<200)continue;",
      "if(seen.indexOf(s)>=0)continue;seen.push(s);",
      "var tok='\\u2063K'+picked.length+'\\u2063';",
      "var node=document.createTextNode(tok);",
      "im.parentNode.insertBefore(node,im);",
      "picked.push({url:s,tok:tok,node:node});",
      "}",
      "var raw=el.innerText||'';",
      "var lead=raw.length-raw.replace(/^\\s+/,'').length;",
      "var text=raw.slice(lead).replace(/\\u2063K\\d+\\u2063/g,'').replace(/\\s+$/,'');",
      "var imgs=[];var drop=0;",
      "for(var j=0;j<picked.length;j++){",
      "var p=picked[j];var at=raw.indexOf(p.tok);",
      "if(p.node.parentNode)p.node.parentNode.removeChild(p.node);",
      "if(at<0)continue;",
      "var pos=at-lead-drop;drop+=p.tok.length;",
      "if(pos>=0&&pos<=text.length)imgs.push({url:p.url,pos:pos});",
      "}",
      "if(text.length<100){alert('内容过短（'+text.length+' 字），可能不是文章页');return;}",
      "if(!confirm('保存这篇文章？（书签 " + BOOKMARKLET_VERSION + "）\\n\\n'+title+'\\n全文约 '+text.length+' 字'+(imgs.length?('，含 '+imgs.length+' 张配图'):''))){return;}",
      "var target=window.open('" + origin + "/?import=1','kd-import');",
      "if(!target){alert('浏览器拦截了保存窗口，请允许弹窗后重试。');return;}",
      "var packet={type:'knowledge-distiller-import-v1',payload:{title:title,url:pageUrl,content:text,images:imgs}};",
      "var attempts=0;var timer=setInterval(function(){",
      "if(target.closed||attempts++>120){clearInterval(timer);return;}",
      "target.postMessage(packet,'" + origin + "');",
      "},500);",
      "window.addEventListener('message',function(ev){if(ev.origin==='" + origin + "'&&ev.data&&ev.data.type==='knowledge-distiller-import-accepted'){clearInterval(timer);}});",      "})();",
    ].join("");
  }

  function initBookmarklet() {
    const code = buildBookmarklet();
    if (els.homeBookmarklet) els.homeBookmarklet.setAttribute("href", code);
    if (els.homeBookmarkletCode) els.homeBookmarkletCode.value = code;
  }

  // ---- 视图切换：首页 / 我的收藏 ----
  function showView(name) {
    if (els.viewHome) els.viewHome.hidden = name !== "home";
    if (els.viewCollections) els.viewCollections.hidden = name !== "collections";
    if (els.viewRecommend) els.viewRecommend.hidden = name !== "recommend";
    if (els.viewSearch) els.viewSearch.hidden = name !== "search";
    if (els.navHome) els.navHome.classList.toggle("current", name === "home");
    if (els.favParent) els.favParent.classList.toggle("current", name === "collections");
    if (els.navRecommend) els.navRecommend.classList.toggle("current", name === "recommend");
    if (els.navSearch) els.navSearch.classList.toggle("current", name === "search");
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
      els.fulltextMeta.textContent = "全文 " + data.length + " 字 · 由「保存书签」从知乎页面送入";
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

  // 卡片配图用知乎 CDN 缩略图：small = 250x0（小方图）、big = 720w（全宽/半宽图）
  function thumbCover(url, size) {
    const suffix = size === "big" ? "_720w" : "_250x0";
    return String(url || "").replace(/_\d+w\.(jpe?g|png|webp|gif)$/i, suffix + ".$1");
  }

  // URL 归一化：回答的长短格式统一（与后端 _norm_key 规则一致）
  // 问答 id 段放宽成 [^/]+：知乎回答页的 og:url 会出现 /question/undefined/answer/<id>
  function normKey(url) {
    const s = String(url || "").split("?")[0].split("#")[0];
    const m = s.match(/^https?:\/\/(?:www\.)?zhihu\.com\/question\/[^/]+\/answer\/(\d+)/);
    if (m) return "https://www.zhihu.com/answer/" + m[1];
    return s;
  }

  function setHistoryMap(items) {
    historyMap = {};
    for (const item of items || []) {
      const key = normKey(item.url);
      if (key) historyMap[key] = item;
    }
  }

  async function refreshHistoryMap() {
    try {
      const data = await api("/api/reading/history");
      setHistoryMap(data.items || []);
    } catch (err) { /* 续读失败不影响收藏列表本身 */ }
  }

  function readingHref(url) {
    const target = String(url || "");
    const history = historyMap[normKey(target)];
    const seg = history ? Math.max(0, Number.parseInt(history.seg, 10) || 0) : null;
    return "/reading.html?url=" + encodeURIComponent(target)
      + (seg == null ? "" : "&seg=" + seg);
  }

  function readingAction(url, length) {
    const history = historyMap[normKey(url)];
    if (!history) return "✓ 已保存 · 全文 " + (length || 0) + " 字 · 开始学习 →";
    const seg = Math.max(0, Number.parseInt(history.seg, 10) || 0);
    const total = Math.max(0, Number.parseInt(history.total, 10) || 0);
    const current = total ? Math.min(seg + 1, total) : seg + 1;
    return "继续阅读 · 第 " + current + (total > 1 ? "/" + total : "") + " 段 →";
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
    const data = await resp.json().catch(() => ({}));
    if (!resp.ok || data.ok === false) {
      const err = new Error((data.error && data.error.message) || `请求失败（HTTP ${resp.status}）`);
      err.code = data.error && data.error.code;
      err.details = data.error && data.error.details;
      throw err;
    }
    return data;
  }

  let importBusy = false;
  function importId() {
    return (window.crypto && crypto.randomUUID) ? crypto.randomUUID()
      : "xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx".replace(/[xy]/g, (c) => {
          const r = Math.random() * 16 | 0;
          return (c === "x" ? r : (r & 3 | 8)).toString(16);
        });
  }
  async function importArticle(payload) {
    if (importBusy || !payload) return;
    importBusy = true;
    sessionStorage.setItem("kd_pending_import_v1", JSON.stringify(payload));
    try {
      const preview = await api("/api/ingest", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ ...payload, preview_only: true }),
      });
      if (preview.unchanged) {
        sessionStorage.removeItem("kd_pending_import_v1");
        location.href = readingHref(payload.url);
        return;
      }
      const task = await api("/api/ingest", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ ...payload,
          expected_current_commit: preview.current_commit || null,
          idempotency_key: importId(),
        }),
      });
      for (;;) {
        const status = await api("/api/ingest/" + encodeURIComponent(task.update_id));
        if (status.state === "published") {
          sessionStorage.removeItem("kd_pending_import_v1");
          location.href = "/reading.html?url=" + encodeURIComponent(payload.url)
            + "&version=" + encodeURIComponent(status.commit || "");
          return;
        }
        if (["failed", "conflict"].includes(status.state)) {
          throw new Error(status.error_code || "内容版本没有发布成功，请重试。");
        }
        await new Promise((resolve) => setTimeout(resolve, 1500));
      }
    } catch (err) {
      if (["LOGIN_REQUIRED", "SESSION_EXPIRED", "ACCOUNT_ID_REQUIRED"].includes(err.code)) {
        location.href = "/api/oauth/start?next=" + encodeURIComponent("/?resume_import=1");
        return;
      }
      alert("保存失败：" + (err.message || "未知错误"));
    } finally {
      importBusy = false;
    }
  }
  window.addEventListener("message", (event) => {
    if (!["https://www.zhihu.com", "https://zhuanlan.zhihu.com"].includes(event.origin)) return;
    if (!event.data || event.data.type !== "knowledge-distiller-import-v1") return;
    try { event.source.postMessage({ type: "knowledge-distiller-import-accepted" }, event.origin); } catch (_) {}
    importArticle(event.data.payload);
  });
  if (new URLSearchParams(location.search).get("resume_import") === "1") {
    try { importArticle(JSON.parse(sessionStorage.getItem("kd_pending_import_v1") || "null")); } catch (_) {}
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
    const dKey = normKey(item.Url);
    const dist = distilledMap[dKey];
    const distImgs = (dist && dist.images) || [];
    // 单图：摘要左侧小缩略图；多图：摘要下方一排（最多三张）
    const thumbHtml = distImgs.length === 1
      ? `<img class="card-thumb" src="${escapeHtml(thumbCover(distImgs[0]))}" alt="" loading="lazy" referrerpolicy="no-referrer">`
      : "";
    const galleryImgs = distImgs.slice(0, 3);
    const galleryHtml = galleryImgs.length >= 2
      ? `<div class="card-gallery n${galleryImgs.length}">${galleryImgs.map((u) =>
          `<img src="${escapeHtml(thumbCover(u))}" alt="" loading="lazy" referrerpolicy="no-referrer">`).join("")}</div>`
      : "";
    // 已保存：标题/卡片点击直接进入学习；未保存：点击去知乎原文（新标签）
    const titleHtml = dist
      ? `<a class="card-title" href="${readingHref(item.Url)}">${escapeHtml(item.Title || "（无标题）")}</a>`
      : `<a class="card-title" href="${escapeHtml(item.Url)}" target="_blank" rel="noopener noreferrer">${escapeHtml(item.Title || "（无标题）")}</a>`;
    const badgeHtml = dist
      ? `<button class="distill-badge" data-learn="${escapeHtml(item.Url || "")}">${escapeHtml(readingAction(item.Url, dist.length))}</button>`
      : `<button class="go-distill" data-godistill="${escapeHtml(dKey)}" data-gourl="${escapeHtml(item.Url || "")}" data-gotitle="${escapeHtml(item.Title || "")}"><svg class="ui-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M12 5.5C8.5 3.5 5 3.5 3 4.5v14c2-1 5.5-1 9 1 3.5-2 7-2 9-1v-14c-2-1-5.5-1-9 1Z"/><path d="M12 5.5v14M6 8h3M15 8h3"/></svg> 保存全文</button>`;
    const labels = ["approval", "richness", "credibility"].map((kind) => {
      const metric = m[kind] || {};
      const basis = (metric.basis || []).join(" · ");
      return `<span title="${escapeHtml(basis)}">${METRIC_NAMES[kind]}<b>${metric.score != null ? metric.score : 0}</b></span>`;
    }).join("");
    return `
      <article class="card" data-url="${escapeHtml(item.Url || "")}" data-title="${escapeHtml(item.Title || "")}">
        ${titleHtml}
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
          ${thumbHtml}
          <p class="summary">${escapeHtml(item.Summary || "")}</p>
          <div class="card-radar">
            ${radarSvg(m)}
            <div class="radar-labels">${labels}</div>
          </div>
        </div>
        ${galleryHtml}
        ${badgeHtml}
        <div class="foot">依据：收藏 ${item.FavoriteCount || 0} · 赞同 ${item.LikeCount || 0} · 评论 ${item.CommentCount || 0}</div>
      </article>`;
  }

  function filteredItems() {
    const q = favFilter.trim().toLowerCase();
    if (!q) return allItems;
    return allItems.filter((it) => {
      const title = String(it.Title || "").toLowerCase();
      const author = String((it.Author && it.Author.Name) || "").toLowerCase();
      return title.includes(q) || author.includes(q);
    });
  }

  function sortItems() {
    const by = {
      favtime: (a, b) => (b.FavTime || 0) - (a.FavTime || 0),
      combined: (a, b) => (b.combined_score || 0) - (a.combined_score || 0),
      approval: (a, b) => ((b.metrics && b.metrics.approval && b.metrics.approval.score) || 0) - ((a.metrics && a.metrics.approval && a.metrics.approval.score) || 0),
      richness: (a, b) => ((b.metrics && b.metrics.richness && b.metrics.richness.score) || 0) - ((a.metrics && a.metrics.richness && a.metrics.richness.score) || 0),
      credibility: (a, b) => ((b.metrics && b.metrics.credibility && b.metrics.credibility.score) || 0) - ((a.metrics && a.metrics.credibility && a.metrics.credibility.score) || 0),
    };
    return [...filteredItems()].sort(by[sortMode] || by.favtime);
  }

  function totalPages() {
    return Math.max(1, Math.ceil(filteredItems().length / PAGE_SIZE));
  }

  function bindCardActions(container) {
    // 整卡点击：已保存 → 直接进入学习；未保存 → 去知乎原文（新标签）
    container.querySelectorAll("article.card[data-url]").forEach((card) => {
      card.style.cursor = "pointer";
      card.addEventListener("click", (e) => {
        if (e.target.closest("a, button")) return;   // 链接与按钮各自处理
        const url = card.getAttribute("data-url") || "";
        if (!url) return;
        if (distilledMap[normKey(url)]) location.href = readingHref(url);
        else window.open(url, "_blank", "noopener");
      });
    });
    // 已保存徽章：直接进入学习
    container.querySelectorAll("[data-learn]").forEach((btn) => {
      const go = (e) => {
        e.preventDefault();
        location.href = readingHref(btn.getAttribute("data-learn") || "");
      };
      btn.addEventListener("click", go);
    });
    container.querySelectorAll("[data-distill]").forEach((btn) => {
      btn.addEventListener("click", () => openFulltext(btn.getAttribute("data-distill"), btn.getAttribute("data-title")));
    });
    container.querySelectorAll("[data-godistill]").forEach((btn) => {
      btn.addEventListener("click", () => {
        goDistill(btn.getAttribute("data-godistill"), btn.getAttribute("data-gourl"), btn.getAttribute("data-gotitle"));
      });
    });
  }

  async function refreshDistilled() {
    try {
      const data = await api("/api/distilled");
      distilledMap = data.ok ? (data.items || {}) : {};
    } catch (err) { /* 保留现有索引 */ }
  }

  function renderCards() {
    const sorted = sortItems();
    // 筛选把收藏全筛没了：复用空态盒子给一句反馈（「去搜一篇」按钮只在收藏夹真空时出现）
    if (!sorted.length && favFilter.trim()) {
      els.cards.innerHTML = "";
      els.pager.hidden = true;
      showState("Empty");
      els.emptyText.textContent = `没有匹配「${favFilter.trim()}」的收藏。`;
      if (els.emptyGotoSearch) els.emptyGotoSearch.hidden = true;
      return;
    }
    const start = (page - 1) * PAGE_SIZE;
    els.cards.innerHTML = sorted.slice(start, start + PAGE_SIZE).map(cardHtml).join("");
    bindCardActions(els.cards);
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

  async function loadAccountBadge() {
    if (!status || !status.authorized) return;
    try {
      const data = await api("/api/billing/account");
      const account = data.account || {};
      const tier = account.membership_tier === "premium" ? "高级会员" : "普通用户";
      els.userSub.textContent = tier + " · 本周期剩余 "
        + Number(account.daily_available_tokens || 0).toLocaleString("zh-CN") + " Token";
    } catch (_) { /* 计费库尚未部署时保留普通登录文案 */ }
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
        // 已选中的收藏夹：点一次强制刷新（给用户反馈）；否则切换
        loadCollections(token, token === String(currentToken));
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
      // 已保存状态与学习位置一起取回：卡片三个入口都接到上次读到的段落
      await Promise.all([refreshDistilled(), refreshHistoryMap()]);
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
        if (els.emptyGotoSearch) els.emptyGotoSearch.hidden = false;  // 筛选空态会藏掉它，这里恢复
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
    loadAccountBadge();

    const params = new URLSearchParams(location.search);
    const canRead = status.authorized || status.self_mode;
    const savedUrl = params.get("saved");
    const requestedFavlist = params.get("favlist");
    if (params.get("oauth") === "error") {
      showHome();
      return;
    }
    if (!canRead) {
      // 默认授权：能走 OAuth 就直接发起（用户进入即授权，无需点按钮）；本地预览模式停在首页
      if (status.callback_configured) {
        // 保存后跳回（?saved=）要把目标一起带上：否则会被这次授权跳转吃掉，
        // 登录完只落回首页，用户看到的就是"保存了却没跳转"
        const next = savedUrl ? "/reading.html?url=" + encodeURIComponent(savedUrl)
          : (requestedFavlist ? "/?favlist=" + encodeURIComponent(requestedFavlist) : "");
        location.href = "/api/oauth/start" + (next ? "?next=" + encodeURIComponent(next) : "");
        return;
      }
      showHome();
      return;
    }
    // 保存完成跳回（?saved=文章地址）：直接进入该篇的学习
    if (savedUrl) {
      location.href = "/reading.html?url=" + encodeURIComponent(savedUrl);
      return;
    }
    showView("collections");
    await loadFavlists();
    await loadCollections(requestedFavlist, false);
  }

  // ---- 智能推荐：基于收藏画像的公共学习内容 ----
  let recommendItems = [];
  let recommendBatch = 0;
  let recommendLoaded = false;

  function recommendCardHtml(item, footHtml) {
    const foot = footHtml
      || `<div class="foot">同主题推荐 · 来自你收藏的《${escapeHtml(String(item.seed || "").slice(0, 24))}》</div>`;
    const key = normKey(item.Url);
    const dist = distilledMap[key];
    const author = item.AuthorName || "";
    const initial = author ? author.slice(0, 1) : "·";
    const badge = item.AuthorBadge
      ? `<img class="author-badge" src="${escapeHtml(item.AuthorBadge)}" alt="">`
      : "";
    const actionHtml = dist
      ? `<button class="distill-badge" data-learn="${escapeHtml(item.Url || "")}">${escapeHtml(readingAction(item.Url, dist.length))}</button>`
      : `<button class="go-distill" data-godistill="${escapeHtml(key)}" data-gourl="${escapeHtml(item.Url || "")}" data-gotitle="${escapeHtml(item.Title || "")}"><svg class="ui-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M12 5.5C8.5 3.5 5 3.5 3 4.5v14c2-1 5.5-1 9 1 3.5-2 7-2 9-1v-14c-2-1-5.5-1-9 1Z"/><path d="M12 5.5v14M6 8h3M15 8h3"/></svg> 保存全文</button>`;
    const titleHtml = dist
      ? `<a class="card-title" href="${readingHref(item.Url)}">${escapeHtml(item.Title || "（无标题）")}</a>`
      : `<a class="card-title" href="${escapeHtml(item.Url)}" target="_blank" rel="noopener noreferrer">${escapeHtml(item.Title || "（无标题）")}</a>`;
    return `
      <article class="card" data-url="${escapeHtml(item.Url || "")}" data-title="${escapeHtml(item.Title || "")}">
        ${titleHtml}
        <div class="meta">
          <span class="tag">${TYPE_NAMES[item.ContentType] || item.ContentType || "内容"}</span>
          <span class="meta-author">
            <span class="author-avatar" style="background:${authorColor(author)}">${escapeHtml(initial)}</span>
            <b class="author-name">${escapeHtml(author || "未知作者")}</b>
            ${badge}
          </span>
        </div>
        <p class="summary">${escapeHtml(item.ContentText || "")}</p>
        ${actionHtml}
        ${foot}
      </article>`;
  }

  function renderRecommendCards() {
    if (!els.recommendCards) return;
    els.recommendCards.innerHTML = recommendItems.map(recommendCardHtml).join("");
    bindCardActions(els.recommendCards);
  }

  function setRecoState(name) {
    for (const key of ["recoLoading", "recoError", "recoEmpty"]) {
      if (els[key]) els[key].hidden = key !== "reco" + name;
    }
    if (name && els.recommendCards) els.recommendCards.innerHTML = "";
  }

  async function loadRecommend(batch, force) {
    if (!els.viewRecommend) return;
    const nextBatch = batch == null ? recommendBatch : batch;
    setRecoState("Loading");
    try {
      await Promise.all([refreshDistilled(), refreshHistoryMap()]);
      const query = [];
      if (nextBatch) query.push("batch=" + nextBatch);
      if (force) query.push("force=1");
      const data = await api("/api/recommend" + (query.length ? "?" + query.join("&") : ""));
      if (!data.ok) {
        const error = new Error((data.error && data.error.message) || "推荐加载失败");
        error.code = data.error && data.error.code;
        throw error;
      }
      recommendItems = data.items || [];
      recommendBatch = nextBatch;
      recommendLoaded = true;
      if (els.recommendCount) els.recommendCount.textContent = data.total ? `共 ${data.total} 条` : "";
      if (els.recoSeedHint) {
        const seed = recommendItems[0] && recommendItems[0].seed;
        els.recoSeedHint.hidden = !seed;
        els.recoSeedHint.textContent = seed ? `根据你收藏的《${String(seed).slice(0, 18)}》等主题` : "";
      }
      if (!recommendItems.length) {
        setRecoState("Empty");
        if (els.recoEmptyText) els.recoEmptyText.textContent = data.message || "暂时没有推荐内容。";
        return;
      }
      setRecoState(null);
      renderRecommendCards();
    } catch (err) {
      if (err.code === "LOGIN_REQUIRED") { showHome(); return; }
      setRecoState("Error");
      if (els.recoErrorText) els.recoErrorText.textContent = err.message || "出了点问题，请重试。";
    }
  }

  // ---- 内容搜索（F32）：空收藏夹用户的冷启动入口 ----
  let searchQuery = "";
  let searchLoadedFor = "";    // 已经出过结果的 query（进视图时决定显示引导态还是保留结果）

  function setSearchState(name) {
    for (const key of ["searchIdle", "searchLoading", "searchError", "searchEmpty"]) {
      if (els[key]) els[key].hidden = key !== "search" + name;
    }
    if ((name === "Loading" || name === "Error" || name === "Empty") && els.searchCards) {
      els.searchCards.innerHTML = "";
    }
  }

  async function loadSearch(query, force) {
    const q = String(query != null ? query : (els.searchInput ? els.searchInput.value : "")).trim();
    if (!q) { setSearchState("Idle"); return; }
    searchQuery = q;
    setSearchState("Loading");
    try {
      // 先对齐已保存索引：搜到自己保存过的文章时，卡片要亮「开始学习」而不是「保存全文」
      await refreshDistilled();
      const data = await api("/api/search?q=" + encodeURIComponent(q) + (force ? "&force=1" : ""));
      if (!data.ok) {
        const error = new Error((data.error && data.error.message) || "搜索失败");
        error.code = data.error && data.error.code;
        throw error;
      }
      const items = data.items || [];
      if (!items.length) {
        setSearchState("Empty");
        if (els.searchEmptyText) els.searchEmptyText.textContent = data.message || "没有搜到相关内容，换个关键词试试。";
        return;
      }
      searchLoadedFor = q;
      setSearchState(null);
      els.searchCards.innerHTML = items.map((it) => recommendCardHtml(it,
        `<div class="foot">搜索「${escapeHtml(q)}」的结果 · 点卡片去知乎原文收藏或保存</div>`
      )).join("");
      bindCardActions(els.searchCards);
    } catch (err) {
      setSearchState("Error");
      if (els.searchErrorText) els.searchErrorText.textContent = err.message || "出了点问题，请重试。";
    }
  }

  // 已保存索引变化时，刷新当前可见视图（收藏 / 推荐）
  async function checkDistilledUpdates() {
    try {
      const data = await api("/api/distilled");
      const items = data.ok ? (data.items || {}) : null;
      if (!items) return;
      if (Object.keys(items).join("|") !== Object.keys(distilledMap).join("|")) {
        distilledMap = items;
        if (els.viewCollections && !els.viewCollections.hidden) renderCards();
        if (els.viewRecommend && !els.viewRecommend.hidden) renderRecommendCards();
      }
    } catch (err) { /* 静默 */ }
  }

  // ---- 事件 ----
  els.sidebarToggle.addEventListener("click", () => els.page.classList.add("sidebar-collapsed"));
  els.sidebarExpand.addEventListener("click", () => els.page.classList.remove("sidebar-collapsed"));
  // 「我的收藏」：从其他视图进入时展开；已在收藏视图时正常切换开合
  els.favParent.addEventListener("click", () => {
    const enteringCollections = els.viewCollections.hidden;
    showView("collections");
    if (enteringCollections) {
      els.favGroup.classList.add("open");
    } else {
      els.favGroup.classList.toggle("open");
    }
    els.favParent.setAttribute("aria-expanded", String(els.favGroup.classList.contains("open")));
  });
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
  // 学习记录：侧边栏下拉会话列表（点条目恢复阅读位置）
  let histListLoaded = false;
  let histItems = [];          // 最近一次加载的记录（删除后就地过滤，不重新请求）
  function renderHistList() {
    const sub = els.histSub;
    if (!sub) return;
    if (!histItems.length) {
      sub.innerHTML = '<div class="nav-sub-loading">还没有学习记录</div>';
      return;
    }
    sub.innerHTML = histItems.map((it) => {
      const href = "/reading.html?url=" + encodeURIComponent(it.url) + "&seg=" + (it.seg || 0);
      const label = it.total > 1 ? ((it.seg || 0) + 1) + "/" + it.total : "";
      return '<div class="nav-subrow">'
        + '<a class="nav-subitem" href="' + href + '" title="' + escapeHtml(it.title || "") + '">'
        + '<span class="nav-subitem-title">' + escapeHtml(it.title || it.url) + '</span>'
        + (label ? '<span class="sub-count">' + label + '</span>' : "")
        + '</a>'
        + '<button type="button" class="nav-subdel" data-del="' + escapeHtml(it.url || "") + '"'
        + ' title="删除这条学习记录，并把这篇重置为未保存全文" aria-label="删除这条学习记录">×</button>'
        + "</div>";
    }).join("");
  }
  async function loadHistoryList() {
    const sub = els.histSub;
    if (!sub) return;
    try {
      const data = await api("/api/reading/history");
      setHistoryMap(data.items || []);
      histItems = (data.items || []).slice(0, 8);
      if (histItems.length) histListLoaded = true;   // 空列表不记「已加载」，下次展开重新请求
      renderHistList();
    } catch (err) {
      sub.innerHTML = '<div class="nav-sub-loading">加载失败</div>';
    }
  }
  // 删除一条学习记录：二次点击确认（原生 confirm 弹窗可能被宿主屏蔽）
  let pendingHistDel = null;
  async function deleteHistory(btn) {
    const url = btn.getAttribute("data-del") || "";
    if (!url) return;
    if (pendingHistDel !== url) {
      pendingHistDel = url;
      btn.classList.add("confirm");
      btn.textContent = "确认";
      setTimeout(() => {
        if (pendingHistDel === url) {
          pendingHistDel = null;
          btn.classList.remove("confirm");
          btn.textContent = "×";
        }
      }, 4000);
      return;
    }
    pendingHistDel = null;
    btn.disabled = true;
    try {
      await api("/api/reading/history?url=" + encodeURIComponent(url), { method: "DELETE" });
      histItems = histItems.filter((it) => it.url !== url);
      delete historyMap[normKey(url)];
      renderHistList();
      // 这篇的全文已被重置：收藏列表的卡片要一起回到「🧪 去保存全文」。
      // 两个视图都重绘（切视图只是显隐切换、不重绘，这里不重绘的话卡片会一直显示旧状态）
      await refreshDistilled();
      if (allItems.length) renderCards();
      if (recommendItems.length) renderRecommendCards();
    } catch (err) {
      btn.disabled = false;
      btn.classList.remove("confirm");
      btn.textContent = "×";
      alert("删除失败：" + err.message);
    }
  }
  if (els.histSub) {
    els.histSub.addEventListener("click", (e) => {
      const del = e.target.closest(".nav-subdel");     // 删除按钮优先：不触发跳转
      if (!del) return;
      e.preventDefault();
      deleteHistory(del);
    });
  }
  if (els.histParent) {
    els.histParent.addEventListener("click", () => {
      const open = els.histGroup.classList.toggle("open");
      if (open && !histListLoaded) loadHistoryList();
    });
  }

  // 首页 / 收藏视图切换
  els.navHome.addEventListener("click", () => showHome());

  // 智能推荐：进入即加载；「换一批」循环切片；重试走 force
  if (els.navRecommend) {
    els.navRecommend.addEventListener("click", () => {
      showView("recommend");
      if (!recommendLoaded) loadRecommend(0, false);
    });
  }

  // 内容搜索：进入显示引导态（已出过结果则保留）；点按钮 / 回车触发
  if (els.navSearch) {
    els.navSearch.addEventListener("click", () => {
      showView("search");
      if (!searchLoadedFor) setSearchState("Idle");
    });
  }
  if (els.searchExpand) {
    els.searchExpand.addEventListener("click", () => els.page.classList.remove("sidebar-collapsed"));
  }
  if (els.searchBtn) {
    els.searchBtn.addEventListener("click", () => loadSearch(null, false));
  }
  if (els.searchInput) {
    els.searchInput.addEventListener("keydown", (event) => {
      if (event.key === "Enter") loadSearch(null, false);
    });
  }
  if (els.searchRetry) {
    els.searchRetry.addEventListener("click", () => loadSearch(searchQuery, true));
  }
  // 收藏空态 → 搜索的引导按钮；收藏筛选框（F33，纯前端过滤）
  if (els.emptyGotoSearch) {
    els.emptyGotoSearch.addEventListener("click", () => {
      showView("search");
      if (els.searchInput) els.searchInput.focus();
    });
  }
  if (els.favFilter) {
    els.favFilter.addEventListener("input", () => {
      favFilter = els.favFilter.value;
      page = 1;
      renderCards();
    });
  }
  if (els.recommendRefresh) {
    els.recommendRefresh.addEventListener("click", () => loadRecommend(recommendBatch + 1, false));
  }
  if (els.recoRetry) {
    els.recoRetry.addEventListener("click", () => loadRecommend(recommendBatch, true));
  }
  if (els.recoExpand) {
    els.recoExpand.addEventListener("click", () => els.page.classList.remove("sidebar-collapsed"));
  }
  if (els.homeGotoCollections) {
    els.homeGotoCollections.addEventListener("click", () => showView("collections"));
  }

  // 保存书签：初始化 / 复制书签（富文本，可粘贴成书签）/ 复制代码
  initBookmarklet();
  // 安装方式依据访问设备，不随窗口宽度变化；iPad 的桌面 UA 用触控能力辅助识别。
  const mobileBookmark = Boolean(navigator.userAgentData?.mobile
    || /Android|iPhone|iPad|iPod|Mobile/i.test(navigator.userAgent)
    || (/Macintosh/i.test(navigator.userAgent) && navigator.maxTouchPoints > 1));
  document.documentElement.dataset.bookmarkDevice = mobileBookmark ? "mobile" : "desktop";
  const bookmarkInstall = $("bookmark-install");
  const bookmarkCopyStatus = $("bookmark-copy-status");
  const localBookmarkHint = $("bookmark-local-hint");
  if (localBookmarkHint) localBookmarkHint.hidden = !["localhost", "127.0.0.1", "[::1]"].includes(location.hostname);
  function revealBookmarkInstall() {
    showHome();
    if (!bookmarkInstall) return;
    bookmarkInstall.open = true;
    bookmarkInstall.scrollIntoView({ block: "start", behavior: "instant" });
    const summary = bookmarkInstall.querySelector("summary");
    if (summary) summary.focus({ preventScroll: true });
  }
  const mobileInstallButton = $("mobile-install-bookmark");
  if (mobileInstallButton) mobileInstallButton.addEventListener("click", revealBookmarkInstall);
  if (els.distillGuideBody) els.distillGuideBody.addEventListener("click", (event) => {
    if (!event.target.closest("[data-bookmark-install]")) return;
    els.distillGuide.hidden = true;
    revealBookmarkInstall();
  });

  async function copyBookmarkAsLink() {
    const code = buildBookmarklet();
    const html = '<a href="' + escapeHtml(code) + '">🧪 保存这篇文章</a>';
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
      if (hint) hint.textContent = mobileBookmark
        ? "请编辑已添加的书签，把网址替换为刚复制的完整代码。"
        : "现在右键浏览器书签栏 →「粘贴」";
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
        if (bookmarkCopyStatus) bookmarkCopyStatus.textContent = mobileBookmark
          ? "代码已复制。编辑「保存这篇文章」书签，把网址替换成这段完整代码。"
          : "代码已复制，粘贴到书签的网址一栏即可。";
        setTimeout(() => { els.homeCopyBookmarklet.textContent = "复制书签代码"; }, 1800);
      } catch (err) {
        els.homeBookmarkletCode.select();
        els.homeBookmarkletCode.setSelectionRange(0, els.homeBookmarkletCode.value.length);
        els.homeCopyBookmarklet.textContent = "手动复制代码";
        if (bookmarkCopyStatus) bookmarkCopyStatus.textContent = mobileBookmark
          ? "请长按代码，选择「全选」后复制，再粘贴到书签的网址一栏。"
          : "代码已选中，请按 Ctrl+C 复制。";
      }
    });
  }
  // 全文弹窗关闭
  if (els.fulltextClose) els.fulltextClose.addEventListener("click", () => closeModal(els.fulltextModal));
  if (els.fulltextBackdrop) els.fulltextBackdrop.addEventListener("click", () => closeModal(els.fulltextModal));
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") closeModal(els.fulltextModal);
  });

  // ---- 「去保存全文」：打开知乎原文 + 自动等待结果（全文送达后卡片自动点亮） ----
  const WATCH_INTERVAL = 2500;    // 轮询间隔（毫秒）
  const WATCH_TIMEOUT = 180000;   // 3 分钟没等到就提示
  let watch = null;               // {key, title, timer, deadline}

  function goDistill(key, url, title) {
    // 加来源标记：书签在那页蒸完会自动关标签/跳回，用户无需手动切回
    const marked = url.indexOf("#") >= 0 ? url : url + "#kd=1";
    window.open(marked, "_blank", "noopener");
    startWatch(key, title);
  }

  function startWatch(key, title) {
    stopWatch();
    watch = { key, title, deadline: Date.now() + WATCH_TIMEOUT, timer: setInterval(checkWatch, WATCH_INTERVAL) };
    showGuide("watching", title);
  }

  function stopWatch() {
    if (watch && watch.timer) clearInterval(watch.timer);
    watch = null;
  }

  async function checkWatch() {
    if (!watch) return;
    try {
      const data = await api("/api/distilled");
      const items = data.ok ? (data.items || {}) : null;
      if (items && items[watch.key]) {
        const length = items[watch.key].length || 0;
        const title = watch.title;
        const key = watch.key;
        distilledMap = items;
        stopWatch();
        showGuide("success", title, length);
        if (els.viewCollections && !els.viewCollections.hidden) renderCards();
        if (els.viewRecommend && !els.viewRecommend.hidden) renderRecommendCards();
        // 全文到齐就直接进这篇的精读：学习记录是在精读页打开时才写入的，
        // 不自动进去的话用户会以为"保存了但学习记录里什么都没有"
        setTimeout(() => { location.href = "/reading.html?url=" + encodeURIComponent(key); }, 700);
        return;
      }
    } catch (err) { /* 网络抖动：下一轮再试 */ }
    if (watch && Date.now() > watch.deadline) {
      const title = watch.title;
      stopWatch();
      showGuide("timeout", title);
    }
  }

  function showGuide(state, title, length) {
    if (!els.distillGuide) return;
    els.distillGuide.hidden = false;
    els.distillGuide.classList.toggle("success", state === "success");
    if (state === "watching") {
      els.distillGuideDot.hidden = false;
      els.distillGuideTitle.textContent = "等待全文送达…";
      els.distillGuideBody.innerHTML =
        (mobileBookmark
          ? "请在同一浏览器打开知乎网页版并展开正文，再调用 <b>「保存这篇文章」</b> 书签。<br><button class=\"btn-ghost btn-small\" type=\"button\" data-bookmark-install>查看手机安装步骤</button>"
          : "已打开知乎原文。请在那一页点一下书签栏的 <b>「🧪 保存这篇文章」</b>，确认后这里会自动亮起。")
        + (title ? `<div class="gd-target">《${escapeHtml(title)}》</div>` : "");
    } else if (state === "success") {
      els.distillGuideDot.hidden = true;
      els.distillGuideTitle.textContent = "✓ 保存成功";
      els.distillGuideBody.innerHTML =
        `全文 <b>${length || 0} 字</b>已存入，正在进入这篇的精读…（若没自动跳转，点卡片上的「✓ 已保存」即可）`
        + (title ? `<div class="gd-target">《${escapeHtml(title)}》</div>` : "");
      setTimeout(() => { if (!watch && els.distillGuide) els.distillGuide.hidden = true; }, 12000);
    } else if (state === "timeout") {
      els.distillGuideDot.hidden = true;
      els.distillGuideTitle.textContent = "还没收到全文";
      els.distillGuideBody.innerHTML = mobileBookmark
        ? "请确认在知乎网页版运行了保存书签，并已展开全文。<br><button class=\"btn-ghost btn-small\" type=\"button\" data-bookmark-install>查看手机安装步骤</button>"
        : "书签还没装好？回首页拖一下（5 秒）；装好后到知乎文章页再点一次即可。";
    }
  }

  if (els.distillGuideClose) {
    els.distillGuideClose.addEventListener("click", () => {
      stopWatch();
      els.distillGuide.hidden = true;
    });
  }

  // 切回本页时立即检查一次：既覆盖「去蒸馏」等待中，也覆盖手动在知乎页蒸好的情况
  document.addEventListener("visibilitychange", async () => {
    if (document.visibilityState !== "visible" || !stationStarted) return;
    if (watch) { checkWatch(); return; }
    await checkDistilledUpdates();
  });

  // 从精读页按浏览器返回时，页面可能直接从内存恢复；重新取进度，避免卡片停在旧段落。
  window.addEventListener("pageshow", async (event) => {
    if (!event.persisted || !stationStarted) return;
    await refreshHistoryMap();
    if (els.viewCollections && !els.viewCollections.hidden && allItems.length) renderCards();
    if (els.viewRecommend && !els.viewRecommend.hidden && recommendItems.length) renderRecommendCards();
  });

  // 开发调试入口（本地预览用）
  window.__kd = { boot, loadCollections, get items() { return allItems; } };

  const TUTORIAL_ARTICLE = {
    Url: "#tutorial-example",
    Title: "教程示例：如何把收藏真正读懂？",
    Summary: "收藏只是起点。把长文章拆成更小的段落，再针对不理解的句子补充背景、展开推理，才能逐步形成自己的理解。",
    ContentType: "article",
    Author: { Name: "知识蒸馏站" },
    FavTime: 0,
    FavoriteCount: 1280,
    LikeCount: 2360,
    CommentCount: 86,
    metrics: {
      approval: { score: 88, basis: ["收藏与赞同"] },
      richness: { score: 82, basis: ["内容结构与信息密度"] },
      credibility: { score: 76, basis: ["来源与表达完整度"] },
    },
  };

  function showTutorialArticle() {
    showView("collections");
    let host = document.getElementById("tutorial-demo-cards");
    if (!host) {
      host = document.createElement("div");
      host.id = "tutorial-demo-cards";
      host.className = "cards tutorial-demo-cards";
      els.cards.parentNode.insertBefore(host, els.cards);
    }
    host.innerHTML = cardHtml(TUTORIAL_ARTICLE);
    const card = host.querySelector(".card");
    if (card) {
      card.id = "tutorial-demo-card";
      card.classList.add("tutorial-demo-card");
      card.querySelectorAll("a, button").forEach((control) => {
        control.tabIndex = -1;
        control.setAttribute("aria-disabled", "true");
      });
    }
    els.viewCollections.classList.add("tutorial-example-active");
  }

  function hideTutorialArticle() {
    if (els.viewCollections) els.viewCollections.classList.remove("tutorial-example-active");
    const host = document.getElementById("tutorial-demo-cards");
    if (host) host.remove();
  }

  function registerStationTutorial(autoStart) {
    if (!window.KDTutorial) return;
    window.KDTutorial.register({
      id: "station-basics",
      version: 2,
      autoStart: Boolean(autoStart),
      delay: 180,
      steps: [
        {
          target: ".home-hero",
          beforeShow: () => { hideTutorialArticle(); showHome(); },
          title: "欢迎来到知识蒸馏站",
          description: "这里会把你收藏过的知乎内容整理成一条可继续的学习路径。先选文章，再保存全文，最后逐段读懂。",
        },
        {
          target: ["#fav-parent", "#view-collections"],
          beforeShow: () => { hideTutorialArticle(); showView("collections"); },
          title: "从我的收藏开始",
          description: "这里同步你的知乎收藏夹。选择一个收藏夹，就能浏览其中的文章和回答。",
        },
        {
          target: "#tutorial-demo-card",
          beforeShow: showTutorialArticle,
          title: "先判断哪篇值得读",
          description: "每张卡片会展示认可度、信息量和准确性，帮你快速判断。",
        },
        {
          target: ".home-bookmark",
          beforeShow: () => { hideTutorialArticle(); showHome(); },
          title: "第一次先安装保存书签",
          description: mobileBookmark
            ? "在首页打开“手机安装步骤”，复制保存代码，再把它粘贴到书签的网址一栏。以后在知乎网页版运行这个书签即可保存全文。"
            : "把首页的“保存这篇文章”拖到浏览器书签栏。以后在知乎文章页点一下这个书签，就能把完整正文送回这里。",
        },
        {
          target: ["#hist-parent", ".mobile-nav-toggle"],
          title: "从学习记录接着读",
          description: "读过的文章和段落进度都会留在学习记录中。手机先点底部“导航”，再打开“学习记录”，即可回到上次停下的位置。",
        },
        {
          target: ["#nav-recommend", ".mobile-nav-toggle"],
          title: "也可以看看智能推荐",
          description: "智能推荐会参考你的收藏兴趣，补充适合继续学习的公共内容。手机可从底部“导航”进入，现在去挑第一篇文章吧。",
        },
      ],
      onFinish: () => { hideTutorialArticle(); showView("collections"); },
      onDismiss: hideTutorialArticle,
    });
  }

  // 产品介绍与业务入口共用本页；只有点击进入后才请求收藏、发起授权和教学。
  const intro = $("product-intro");
  const introKey = "kd:intro:v1";
  const entryParams = new URLSearchParams(location.search);
  const directEntry = ["saved", "oauth", "favlist"].some((key) => entryParams.has(key));
  const stationTitle = document.title;
  let stationStarted = false;
  let introObserver = null;

  function observeIntroScenes() {
    if (!intro || intro.hidden) return;
    if (introObserver) introObserver.disconnect();
    // 以滚动容器的中线确定当前幕；像素边距避免宽屏百分比边距压没观察区域。
    const inset = Math.max(0, Math.floor(intro.clientHeight / 2) - 1);
    introObserver = new IntersectionObserver((entries) => {
      for (const entry of entries) {
        entry.target.classList.toggle("intro-visible", entry.isIntersecting);
        if (!entry.isIntersecting) continue;
        const step = entry.target.dataset.introStep;
        if ($("intro-position")) $("intro-position").textContent = String(step).padStart(2, "0") + " / 06";
        intro.querySelectorAll('a[href^="#intro-scene-"]').forEach((link) => {
          if (link.getAttribute("href") === "#intro-scene-" + step) link.setAttribute("aria-current", "step");
          else link.removeAttribute("aria-current");
        });
      }
    }, { root: intro, rootMargin: "-" + inset + "px 0px -" + inset + "px 0px" });
    intro.querySelectorAll(".intro-scene").forEach((scene) => introObserver.observe(scene));
  }

  function enterStation(remember) {
    if (stationStarted) return;
    stationStarted = true;
    if (remember) {
      try { localStorage.setItem(introKey, "entered"); }
      catch (err) { /* 无法持久化时仍可正常进入。 */ }
    }
    const url = new URL(location.href);
    url.searchParams.delete("intro");
    if (url.hash.startsWith("#intro-scene-")) url.hash = "";
    if (url.href !== location.href) history.replaceState(history.state, "", url);
    if (introObserver) introObserver.disconnect();
    window.removeEventListener("resize", observeIntroScenes);
    if (intro) intro.hidden = true;
    document.body.classList.remove("intro-active");
    document.title = stationTitle;
    els.page.hidden = false;
    showView("collections");
    showState("Loading");
    if (remember) {
      window.scrollTo({ top: 0, behavior: "instant" });
      els.favlistTitle.tabIndex = -1;
      els.favlistTitle.focus({ preventScroll: true });
    }
    boot().finally(() => registerStationTutorial(
      !entryParams.has("saved") && !entryParams.has("favlist") && entryParams.get("oauth") !== "error"
        && status && (status.authorized || status.self_mode)
    ));
  }

  document.querySelectorAll("[data-intro-enter]").forEach((button) => {
    button.addEventListener("click", () => enterStation(true));
  });
  document.querySelectorAll("[data-intro-open]").forEach((button) => {
    button.addEventListener("click", () => { location.href = "/?intro=1"; });
  });

  if (intro) {
    // 展示仅切换固定示例的层级，不写入真实文章、提问或学习进度。
    intro.addEventListener("click", (event) => {
      const depthButton = event.target.closest("[data-intro-depth]");
      if (depthButton) {
        const depth = depthButton.dataset.introDepth;
        const panel = intro.querySelector('[data-intro-panel="' + depth + '"]');
        if (!panel) return;
        intro.querySelectorAll("[data-intro-panel]").forEach((item) => {
          item.hidden = item !== panel;
        });
        intro.querySelectorAll("[data-intro-level]").forEach((button) => {
          button.setAttribute("aria-pressed", String(button.dataset.introDepth === depth));
        });
        // 返回或深入的按钮随旧面板隐藏时，将焦点交给新面板。
        if (!depthButton.hasAttribute("data-intro-level")) {
          panel.tabIndex = -1;
          panel.focus({ preventScroll: true });
        }
      }
      const anchor = event.target.closest('a[href^="#intro-scene-"]');
      if (anchor) {
        const section = $(anchor.getAttribute("href").slice(1));
        if (!section) return;
        event.preventDefault();
        section.scrollIntoView({
          behavior: matchMedia("(prefers-reduced-motion: reduce)").matches ? "instant" : "smooth",
          block: "start",
        });
        section.tabIndex = -1;
        section.focus({ preventScroll: true });
      }
    });
  }

  let introSeen = false;
  try { introSeen = Boolean(localStorage.getItem(introKey)); }
  catch (err) { /* 隐私模式下采用首次访问流程。 */ }
  if (intro && !directEntry && (entryParams.get("intro") === "1" || !introSeen)) {
    intro.hidden = false;
    els.page.hidden = true;
    document.body.classList.add("intro-active");
    document.title = "知识蒸馏站 · 围绕文章展开的 AI 精读";
    intro.focus({ preventScroll: true });
    if ("IntersectionObserver" in window) {
      observeIntroScenes();
      window.addEventListener("resize", observeIntroScenes);
    } else {
      intro.querySelectorAll(".intro-scene").forEach((scene) => scene.classList.add("intro-visible"));
    }
  } else {
    enterStation(false);
  }
})();
