/* 知识蒸馏站 · 可复用的新手教程 */
(() => {
  "use strict";

  const tutorials = new Map();
  let active = null;
  let ui = null;
  let renderToken = 0;

  function storageKey(config) {
    return "kd:tutorial:" + config.id + ":v" + (config.version || 1);
  }

  function hasSeen(config) {
    try { return Boolean(localStorage.getItem(storageKey(config))); }
    catch (err) { return false; }
  }

  function remember(config, status) {
    try { localStorage.setItem(storageKey(config), status); }
    catch (err) { /* 隐私模式下仍可正常展示，只是不记进度 */ }
  }

  function mount() {
    if (ui) return ui;
    const layer = document.createElement("div");
    layer.className = "tour-layer";
    layer.hidden = true;
    layer.innerHTML = `
      <div class="tour-spotlight" aria-hidden="true"></div>
      <section class="tour-card" role="dialog" aria-modal="true" aria-labelledby="tour-title" tabindex="-1">
        <div class="tour-topline">
          <span class="tour-kicker" id="tour-kicker"></span>
          <button class="tour-skip" type="button">跳过</button>
        </div>
        <h2 id="tour-title"></h2>
        <p class="tour-copy" id="tour-copy"></p>
        <div class="tour-footer">
          <div class="tour-dots" aria-hidden="true"></div>
          <div class="tour-actions">
            <button class="tour-back" type="button">上一步</button>
            <button class="tour-next" type="button">下一步</button>
          </div>
        </div>
      </section>`;
    document.body.appendChild(layer);
    ui = {
      layer,
      spotlight: layer.querySelector(".tour-spotlight"),
      card: layer.querySelector(".tour-card"),
      kicker: layer.querySelector("#tour-kicker"),
      title: layer.querySelector("#tour-title"),
      copy: layer.querySelector("#tour-copy"),
      dots: layer.querySelector(".tour-dots"),
      back: layer.querySelector(".tour-back"),
      next: layer.querySelector(".tour-next"),
      skip: layer.querySelector(".tour-skip"),
    };
    ui.back.addEventListener("click", () => move(-1));
    ui.next.addEventListener("click", () => {
      if (!active) return;
      if (active.index >= active.config.steps.length - 1) finish();
      else move(1);
    });
    ui.skip.addEventListener("click", dismiss);
    return ui;
  }

  function visibleElement(value) {
    const candidates = Array.isArray(value) ? value : [value];
    for (const candidate of candidates) {
      const element = typeof candidate === "function"
        ? candidate()
        : (candidate ? document.querySelector(candidate) : null);
      if (!element) continue;
      const rect = element.getBoundingClientRect();
      const style = getComputedStyle(element);
      if (rect.width > 0 && rect.height > 0 && style.visibility !== "hidden") return element;
    }
    return null;
  }

  function place(target) {
    if (!active || !ui) return;
    const viewportWidth = window.innerWidth;
    const viewportHeight = window.innerHeight;
    const mobile = viewportWidth <= 720;
    const card = ui.card;
    const spot = ui.spotlight;
    card.style.left = "";
    card.style.right = "";
    card.style.top = "";
    card.style.bottom = "";

    if (!target) {
      ui.layer.classList.add("tour-no-target");
      spot.hidden = true;
      card.style.left = "50%";
      card.style.top = mobile ? "auto" : "50%";
      card.style.bottom = mobile ? "12px" : "auto";
      card.style.transform = mobile ? "translateX(-50%)" : "translate(-50%, -50%)";
      return;
    }

    ui.layer.classList.remove("tour-no-target");
    spot.hidden = false;
    const rect = target.getBoundingClientRect();
    const pad = 7;
    spot.style.left = Math.max(6, rect.left - pad) + "px";
    spot.style.top = Math.max(6, rect.top - pad) + "px";
    spot.style.width = Math.min(viewportWidth - 12, rect.width + pad * 2) + "px";
    spot.style.height = Math.min(viewportHeight - 12, rect.height + pad * 2) + "px";

    if (mobile) {
      card.style.left = "12px";
      card.style.right = "12px";
      card.style.bottom = "12px";
      card.style.transform = "none";
      return;
    }

    card.style.transform = "none";
    const gap = 18;
    const edge = 14;
    const cardRect = card.getBoundingClientRect();
    let left = rect.left + (rect.width - cardRect.width) / 2;
    let top = rect.bottom + gap;
    if (viewportHeight - rect.bottom < cardRect.height + gap && rect.top >= cardRect.height + gap) {
      top = rect.top - cardRect.height - gap;
    } else if (viewportHeight - rect.bottom < cardRect.height + gap
               && viewportWidth - rect.right >= cardRect.width + gap) {
      left = rect.right + gap;
      top = rect.top;
    } else if (viewportHeight - rect.bottom < cardRect.height + gap
               && rect.left >= cardRect.width + gap) {
      left = rect.left - cardRect.width - gap;
      top = rect.top;
    }
    left = Math.max(edge, Math.min(left, viewportWidth - cardRect.width - edge));
    top = Math.max(edge, Math.min(top, viewportHeight - cardRect.height - edge));
    card.style.left = left + "px";
    card.style.top = top + "px";
  }

  async function showStep(index) {
    if (!active) return;
    const token = ++renderToken;
    const step = active.config.steps[index];
    active.index = index;
    if (typeof step.beforeShow === "function") await step.beforeShow();
    if (!active || token !== renderToken) return;

    let target = visibleElement(step.target);
    if (target) {
      const rect = target.getBoundingClientRect();
      if (rect.top < 12 || rect.bottom > window.innerHeight - 12) {
        target.scrollIntoView({ behavior: "smooth", block: "center", inline: "nearest" });
        await new Promise((resolve) => setTimeout(resolve, 260));
        if (!active || token !== renderToken) return;
        target = visibleElement(step.target);
      }
    }

    ui.kicker.textContent = "第 " + (index + 1) + " / " + active.config.steps.length + " 步";
    ui.title.textContent = step.title;
    ui.copy.textContent = step.description;
    ui.back.disabled = index === 0;
    ui.next.textContent = index === active.config.steps.length - 1 ? "开始使用" : "下一步";
    ui.dots.innerHTML = active.config.steps.map((_, dotIndex) =>
      '<span class="tour-dot' + (dotIndex === index ? ' active' : '') + '"></span>'
    ).join("");
    place(target);
    ui.card.focus({ preventScroll: true });
  }

  function cleanup(status) {
    if (!active) return;
    const current = active;
    remember(current.config, status);
    active = null;
    renderToken += 1;
    ui.layer.hidden = true;
    ui.layer.classList.remove("tour-no-target");
    document.body.classList.remove("tour-open");
    window.removeEventListener("resize", reposition);
    window.removeEventListener("scroll", reposition, true);
    if (current.restoreFocus && current.restoreFocus.isConnected) current.restoreFocus.focus();
    const callback = status === "completed" ? current.config.onFinish : current.config.onDismiss;
    if (typeof callback === "function") callback();
  }

  function finish() { cleanup("completed"); }
  function dismiss() { cleanup("dismissed"); }

  function move(delta) {
    if (!active) return;
    const next = Math.max(0, Math.min(active.config.steps.length - 1, active.index + delta));
    if (next !== active.index) showStep(next);
  }

  function reposition() {
    if (!active) return;
    place(visibleElement(active.config.steps[active.index].target));
  }

  function start(id, options) {
    const config = tutorials.get(id);
    const opts = options || {};
    if (!config || !config.steps.length || (hasSeen(config) && !opts.force)) return false;
    if (active) cleanup("dismissed");
    mount();
    active = {
      config,
      index: 0,
      restoreFocus: opts.source || document.activeElement,
    };
    ui.layer.hidden = false;
    document.body.classList.add("tour-open");
    window.addEventListener("resize", reposition);
    window.addEventListener("scroll", reposition, true);
    showStep(0);
    return true;
  }

  function register(config) {
    if (!config || !config.id || !Array.isArray(config.steps)) return false;
    const normalized = {
      ...config,
      version: config.version || 1,
      steps: config.steps.filter((step) => step && step.title && step.description),
    };
    tutorials.set(normalized.id, normalized);
    if (normalized.autoStart) setTimeout(() => start(normalized.id), normalized.delay || 0);
    return true;
  }

  document.addEventListener("click", (event) => {
    const trigger = event.target.closest("[data-tutorial-start]");
    if (!trigger) return;
    event.preventDefault();
    start(trigger.getAttribute("data-tutorial-start"), { force: true, source: trigger });
  });

  document.addEventListener("keydown", (event) => {
    if (!active) return;
    if (event.key === "Escape") dismiss();
    else if (event.key === "ArrowLeft") move(-1);
    else if (event.key === "ArrowRight") move(1);
  });

  window.KDTutorial = { register, start, dismiss, hasSeen: (id) => {
    const config = tutorials.get(id);
    return config ? hasSeen(config) : false;
  } };
})();
