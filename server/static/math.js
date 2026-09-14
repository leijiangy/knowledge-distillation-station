/* 公式渲染 + 「渲染后 DOM ↔ 原文偏移」映射。
 *
 * 为什么需要偏移映射：左栏原文的字符下标是划选、标记锚点、层级嵌套、删除子树的唯一
 * 坐标（见 docs/学习会话设计-定稿.md 第三节）。公式一旦渲染成 KaTeX 的 DOM，
 * Range.toString() 的长度就与原文对不上了，所以每个公式元素都把它的原文区间记在
 * dataset 上，换算偏移时整体按原子处理：光标落在公式内部就吸附到公式的起点或终点。
 *
 * 识别两类公式：
 *   1. 带分隔符：$$…$$、\[…\]（独立成行）、\(…\)、$…$（行内）
 *   2. 裸 LaTeX：知乎正文送来的公式没有分隔符（形如 SDPA(Q,K,V) = \text{softmax}(...)），
 *      按行判断——整行不像中文散文、且带 LaTeX 命令或下标上标，才当公式，宁漏不误。
 */
(function (root) {
  "use strict";

  const ESC_MAP = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" };
  const esc = (s) => String(s == null ? "" : s).replace(/[&<>"']/g, (c) => ESC_MAP[c]);

  // CJK 汉字与中文标点：公式里出现这些字符就说明不是公式（而是散文）
  const CJK = /[\u2E80-\u9FFF\uF900-\uFAFF\u3000-\u303F\uFF00-\uFFEF]/;
  // 认识的 LaTeX 命令。用来把公式和「长得像命令的普通文本」分开——
  // 尤其是 Windows 路径（C:\Users\me 里的 \Users 不是公式）
  const LATEX_CMDS = new Set((
    "frac sqrt text textrm textit textbf textsf texttt mathrm mathbf mathit mathcal mathbb mathsf "
    + "operatorname overline underline widehat widetilde vec hat bar dot ddot tilde "
    + "left right big Big bigg Bigg bigl bigr Bigl Bigr langle rangle lfloor rfloor lceil rceil "
    + "vert Vert mid parallel perp sum prod coprod int iint iiint oint lim max min sup inf "
    + "log ln exp sin cos tan cot sec csc arcsin arccos arctan sinh cosh tanh "
    + "det dim ker deg gcd hom arg "
    + "prime circ ast star cdot cdots ldots dots vdots ddots times div pm mp "
    + "oplus otimes ominus oslash odot wedge vee cap cup setminus subset supset subseteq supseteq "
    + "in notin ni forall exists nexists neg land lor implies iff to mapsto "
    + "rightarrow leftarrow leftrightarrow Rightarrow Leftarrow Leftrightarrow uparrow downarrow "
    + "partial nabla infty propto equiv approx sim simeq cong neq ne le leq ge geq ll gg "
    + "prec succ angle triangle square diamond bullet dagger ddagger ell hbar "
    + "Re Im aleph wp top bot varnothing emptyset "
    + "quad qquad hspace vspace displaystyle textstyle scriptstyle scriptscriptstyle limits nolimits "
    + "begin end cases array matrix pmatrix bmatrix vmatrix Bmatrix smallmatrix align aligned "
    + "gather gathered split equation section item label ref cite "
    + "theta lambda sigma alpha beta gamma delta epsilon varepsilon zeta eta iota kappa mu nu xi "
    + "rho varsigma tau upsilon phi varphi chi psi omega vartheta "
    + "Gamma Delta Theta Lambda Xi Pi Sigma Upsilon Phi Psi Omega"
  ).split(/\s+/));

  /** 串里有没有真正的 LaTeX 命令（认识的命令名，或未知命令但带 {…} 参数） */
  function hasMathCommand(s) {
    const re = /\\[A-Za-z]+/g;
    let m;
    while ((m = re.exec(s)) !== null) {
      if (LATEX_CMDS.has(m[0].slice(1))) return true;
      if (/^\s*\{/.test(s.slice(m.index + m[0].length))) return true;
    }
    return false;
  }
  // 裸公式里允许出现的字符：字母数字、空白、LaTeX 结构符、常见运算符、希腊字母与数学符号
  const ALLOWED = /^[A-Za-z0-9\s_^{}()\[\]=+\-*/<>|,.':;!?&%~\\"@\u00B7\u2013\u2014\u2032\u2033\u2212\u00D7\u00F7\u2264\u2265\u2260\u2248\u2208\u2209\u2200\u2203\u2211\u220F\u222B\u221A\u221E\u2192\u21D2\u21D4\u03B1-\u03C9\u0391-\u03A9]*$/;

  // 行内数学原子的两种形态（见 atomsIn）
  const ATOM_CMD = /\\[A-Za-z]+(?:\s*\{[^{}]*\})*(?:[_^](?:\{[^{}]*\}|[A-Za-z0-9]+))*/g;
  const ATOM_SUB = /[A-Za-z][A-Za-z0-9]{0,4}(?:[_^](?:\{[^{}]{1,12}\}|[A-Za-z0-9]{1,2}(?![A-Za-z0-9])))+/g;
  const CONNECTORS = " \t,;:=+-*/<>|()[]{}^_'~!&.\\";
  const GAP_MAX = 12;      // 两个原子之间可合并的最大间隔
  const EDGE_MAX = 40;     // 单侧向外吸附的最大长度（式子前半段 "Q, K, V = " 也要吸进来）

  // ---------- 公式区间识别 ----------

  /** 带分隔符的公式 */
  function delimitedSpans(text) {
    const out = [];
    const re = /\$\$([\s\S]+?)\$\$|\\\[([\s\S]+?)\\\]|\\\(([\s\S]+?)\\\)|\$([^$\n]+?)\$/g;
    let m;
    while ((m = re.exec(text)) !== null) {
      if (text[m.index - 1] === "\\") continue;              // \$ 是转义美元号，不是分隔符
      const display = m[1] !== undefined || m[2] !== undefined;
      const tex = (m[1] !== undefined ? m[1]
        : m[2] !== undefined ? m[2]
          : m[3] !== undefined ? m[3] : m[4]).trim();
      if (!tex) continue;
      // 行内 $…$ 容易误伤「从 $5 到 $10」这类散文：含中文或首尾留空的一律不算
      if (!display && (CJK.test(tex) || /^\s|\s$/.test(tex))) continue;
      out.push({ start: m.index, end: m.index + m[0].length, tex, display });
    }
    return out;
  }

  /** 整行是不是一条裸公式 */
  function isWholeLineMath(seg) {
    if (!seg || CJK.test(seg) || !ALLOWED.test(seg)) return false;
    if (hasMathCommand(seg)) return true;
    // 没有命令时只认「等式 + 下标/上标」这种形状（Q, K, V = XW_Q, XW_K, XW_V）
    const hasSub = /[A-Za-z0-9)\]][_^][A-Za-z0-9({]/.test(seg);
    return hasSub && seg.includes("=");
  }

  /** LaTeX 括号是否还没配平（跨行公式要接着往下合并） */
  function unbalanced(tex) {
    let depth = 0;
    for (const ch of tex) {
      if (ch === "{" || ch === "(" || ch === "[") depth++;
      else if (ch === "}" || ch === ")" || ch === "]") depth--;
    }
    if (depth > 0) return true;
    return /\\$/.test(tex.trim());      // 行尾是反斜杠：续行
  }

  /** 行内片段：从「数学原子」出发，把同一表达式内的原子连起来
   *
   * 原子有两类：
   *   a. LaTeX 命令（\frac{a}{b}、\text{…}、\alpha，含尾巴上的上下标）
   *   b. 短上下标表达式（XW_Q、K^T、head_1、d_k、G_1）——注意下标必须短：
   *      file_name / snake_case 这类散文里的下划线标识符不能算公式
   * 两个原子之间只有「连接符 + 至多两位的字母数字」（公式里的单变量 X、Y），才合并；
   * 中文一律截断，所以不会把中文句子卷进来。
   */
  function atomsIn(line, from, to) {
    const out = [];
    const push = (m) => {
      if (m.index < from || m.index + m[0].length > to) return;
      out.push({ start: m.index, end: m.index + m[0].length });
    };
    let m;
    ATOM_CMD.lastIndex = from;
    while ((m = ATOM_CMD.exec(line)) !== null) {
      if (m.index >= to) break;
      const cmd = /^\\[A-Za-z]+/.exec(m[0])[0];
      // 只认认识的命令，或带 {…} 参数的未知命令（自定义宏）；C:\Users\me 里的 \Users 被挡在这里
      if (!LATEX_CMDS.has(cmd.slice(1)) && !/\{/.test(m[0].slice(cmd.length))) continue;
      push(m);
    }
    ATOM_SUB.lastIndex = from;
    while ((m = ATOM_SUB.exec(line)) !== null) {
      if (m.index >= to) break;
      push(m);
    }
    return out.sort((a, b) => a.start - b.start || b.end - a.end);
  }

  /** 一段文本是否只由连接符与「至多两位的字母数字」构成（可以安全并入公式） */
  function gapOk(text, max) {
    if (!text || text.length > max || CJK.test(text)) return false;
    if (/[A-Za-z0-9]\.\s/.test(text)) return false;    // 句号+空格 = 句子边界，不跨
    let i = 0;
    while (i < text.length) {
      if (CONNECTORS.includes(text[i])) { i++; continue; }
      if (!/[A-Za-z0-9]/.test(text[i])) return false;
      let j = i;
      while (j < text.length && /[A-Za-z0-9]/.test(text[j])) j++;
      if (j - i > 2) return false;                     // 三字母以上 = 散文词，不吞
      i = j;
    }
    return true;
  }

  function inlineSpans(line, from, to) {
    const spans = [];
    for (const atom of atomsIn(line, from, to)) {
      const last = spans[spans.length - 1];
      // 与上一个 span 重叠 → 已包含；之间只有「可并」的连接内容 → 合并
      if (last && atom.start < last.end) { last.end = Math.max(last.end, atom.end); continue; }
      if (last && gapOk(line.slice(last.end, atom.start), GAP_MAX)) { last.end = atom.end; continue; }
      spans.push({ start: atom.start, end: atom.end });
    }
    // 两端向外吸收表达式的其余部分（= Y、(...)、) ），直到中文或散文词
    for (const s of spans) {
      while (s.start > from && gapOk(line.slice(s.start - 1, s.end), EDGE_MAX)) s.start--;
      while (s.end < to && gapOk(line.slice(s.start, s.end + 1), EDGE_MAX)) s.end++;
      // 两端反复掐掉「纯英数词」（x_1 in code 里的 in / code）：公式两侧不会挂这种词
      for (let guard = 0; guard < 8; guard++) {
        const t = line.slice(s.start, s.end);
        const head = /^([A-Za-z0-9]{2,})[ \t]/.exec(t);
        const tail = /[ \t]([A-Za-z0-9]{2,})$/.exec(t);
        if (head) { s.start += head[0].length; continue; }
        if (tail) { s.end -= tail[0].length; continue; }
        break;
      }
      // 去掉两端空白与尾随标点（保留配对括号）
      while (s.start < s.end && /\s/.test(line[s.start])) s.start++;
      while (s.end > s.start && /[\s,;:]/.test(line[s.end - 1])) s.end--;
    }
    return spans
      .filter((s) => s.end > s.start && /[_^\\]/.test(line.slice(s.start, s.end)))
      .map((s) => ({ start: s.start, end: s.end, tex: line.slice(s.start, s.end) }));
  }

  /** 全部公式区间（按位置排序、互不重叠） */
  function mathSpans(text) {
    const src = String(text == null ? "" : text);
    if (!src) return [];
    const delim = delimitedSpans(src);
    const out = delim.slice();

    // 逐行扫裸公式。同一行可能既有 $…$ 又有裸命令，所以先取本行内被分隔符占用的
    // 区间的补集，只在补集里找裸公式（各认各的，互不吞掉）
    let lineStart = 0;
    let pending = null;
    const flush = () => {
      if (pending) { out.push(pending); pending = null; }
    };
    for (const line of src.split("\n")) {
      const lineEnd = lineStart + line.length;
      const holes = delim
        .filter((d) => d.start < lineEnd && lineStart < d.end)
        .map((d) => [Math.max(d.start, lineStart) - lineStart, Math.min(d.end, lineEnd) - lineStart])
        .sort((x, y) => x[0] - y[0]);
      const segs = [];
      let from = 0;
      for (const h of holes) {
        if (h[0] > from) segs.push([from, h[0]]);
        from = Math.max(from, h[1]);
      }
      if (from < line.length) segs.push([from, line.length]);

      for (const seg of segs) {
        const raw = line.slice(seg[0], seg[1]);
        const lead = raw.length - raw.replace(/^\s+/, "").length;
        // 去掉行首行尾空白与尾随标点后再判断
        const body = raw.replace(/^\s+/, "").replace(/[\s.,;:]+$/, "");
        if (!body) { flush(); continue; }
        const a = lineStart + seg[0] + lead;
        const b = a + body.length;

        if (isWholeLineMath(body)) {
          if (pending) {
            pending.end = b;
            pending.tex += "\n" + body;    // 跨行公式：换行交给 KaTeX 当空白处理
          } else {
            pending = { start: a, end: b, tex: body, display: true };
          }
          if (!unbalanced(pending.tex)) flush();
        } else {
          flush();
          for (const f of inlineSpans(line, seg[0], seg[1])) {
            out.push({ start: lineStart + f.start, end: lineStart + f.end, tex: f.tex, display: false });
          }
        }
      }
      lineStart = lineEnd + 1;
    }
    flush();

    // 排序 + 去重叠（分隔符规则优先，先入为主）
    out.sort((x, y) => x.start - y.start || y.end - x.end);
    const clean = [];
    let pos = 0;
    for (const s of out) {
      if (s.start < pos || s.end <= s.start) continue;
      clean.push(s);
      pos = s.end;
    }
    return clean;
  }

  // ---------- 渲染 ----------

  const htmlCache = new Map();       // tex|display -> HTML（划选拖动会反复重建 DOM，缓存掉 KaTeX 开销）
  const spanCache = new Map();
  const CACHE_MAX = 400;

  function cacheSet(map, key, value) {
    if (map.size > CACHE_MAX) map.clear();
    map.set(key, value);
    return value;
  }

  function cachedSpans(text) {
    if (spanCache.has(text)) return spanCache.get(text);
    return cacheSet(spanCache, text, mathSpans(text));
  }

  /** 单个公式的 HTML；KaTeX 解析不了就退回原文（宁可不渲染，也不显示红色报错） */
  function mathHtml(span) {
    const key = (span.display ? "D" : "I") + "\u0000" + span.tex;
    if (htmlCache.has(key)) return htmlCache.get(key);
    let inner = null;
    if (typeof katex !== "undefined" && katex && typeof katex.renderToString === "function") {
      try {
        inner = katex.renderToString(span.tex, {
          displayMode: Boolean(span.display),
          throwOnError: true,
          strict: false,
          trust: false,
          output: "html",        // 只出 HTML：DOM 更轻，复制文本也更干净
        });
      } catch (err) { inner = null; }
    }
    if (inner == null) return esc(span.tex);
    const html = '<span class="kd-math' + (span.display ? " kd-math-block" : "") + '"'
      + ' data-src-start="' + span.start + '" data-src-end="' + span.end + '"'
      + ' data-src-len="' + (span.end - span.start) + '"'
      + ' role="math" aria-label="' + esc(span.tex.replace(/\s+/g, " ")) + '">'
      + inner + "</span>";
    return cacheSet(htmlCache, key, html);
  }

  /** 配图原子：图片不在原文里占字符（原文长度 0），所以位置就是它的插入点 */
  function figHtml(fig) {
    const pos = Number(fig.pos);
    return '<span class="kd-img" data-src-start="' + pos + '" data-src-end="' + pos
      + '" data-src-len="0" data-fig="' + esc(fig.url) + '" title="点击看这张图的解释">'
      + '<img src="' + esc(fig.url) + '" alt="" loading="lazy" referrerpolicy="no-referrer">'
      + "</span>";
  }

  /**
   * 生成左栏/右栏的 HTML。
   * marks: [{start, end, kind, id}]（原文下标）；markWrapper(mark, innerHtml) 由页面提供，
   * 决定标记的 class 与 data 属性（页面还要给 .mk 绑点击事件）。
   * images: [{url, pos}] 配图（pos 是原文里的字符位置；缺 pos 的不在这里渲染）。
   */
  function buildHtml(opts) {
    const text = String((opts && opts.text) != null ? opts.text : "");
    const wrap = (opts && opts.markWrapper) || ((m, inner) => inner);
    const spans = cachedSpans(text);

    // 配图按位置归组：零长原子，落在哪个字符下标就插在哪里
    const figsAt = new Map();
    for (const fig of (opts && opts.images) || []) {
      const pos = Number(fig && fig.pos);
      if (!fig || !fig.url || !Number.isInteger(pos) || pos < 0 || pos > text.length) continue;
      if (!figsAt.has(pos)) figsAt.set(pos, []);
      figsAt.get(pos).push(fig);
    }

    // 标记与公式相交时，向外扩到整条公式：公式是原子的，划到一半也按整条显示
    let marks = (opts && opts.marks) || [];
    marks = marks.map((m) => {
      const hit = spans.find((s) => s.start < m.end && m.start < s.end);
      if (!hit) return m;
      return Object.assign({}, m, { start: Math.min(m.start, hit.start), end: Math.max(m.end, hit.end) });
    }).sort((a, b) => a.start - b.start);

    const cuts = new Set([0, text.length]);
    spans.forEach((s) => { cuts.add(s.start); cuts.add(s.end); });
    marks.forEach((m) => { cuts.add(m.start); cuts.add(m.end); });
    figsAt.forEach((_figs, pos) => { cuts.add(pos); });
    const points = [...cuts].filter((p) => p >= 0 && p <= text.length).sort((a, b) => a - b);

    let html = "";
    let markCursor = 0;
    for (let i = 0; i < points.length - 1; i++) {
      const a = points[i];
      const b = points[i + 1];
      if (a >= b) continue;
      const span = spans.find((s) => s.start <= a && b <= s.end);
      // 公式只在它的第一段输出一次，后续片段留空（避免重复渲染）
      let inner = span ? (a === span.start ? mathHtml(span) : "") : esc(text.slice(a, b));
      // 配图必须放在该片段文字之前（它的下标指向字符之间的位置），否则会跑到段落末尾
      if (figsAt.has(a)) inner = figsAt.get(a).map(figHtml).join("") + (inner || "");
      if (!inner) continue;
      // 同一时间只可能命中一个标记（标记之间互不重叠）
      while (markCursor < marks.length && marks[markCursor].end <= a) markCursor++;
      const mark = marks[markCursor] && marks[markCursor].start <= a ? marks[markCursor] : null;
      html += mark ? wrap(mark, inner) : inner;
    }
    // 落在文末的配图（那个位置之后没有片段，循环里不会输出）
    if (figsAt.has(text.length)) html += figsAt.get(text.length).map(figHtml).join("");
    return html;
  }

  /** 只读文本块里的公式渲染（回答卡片、全文弹层等，无标记） */
  function renderMathInto(el, text) {
    if (!el) return;
    el.innerHTML = buildHtml({ text });
  }

  // ---------- 渲染后 DOM → 原文偏移 ----------

  /** 把 Range 的 (node, offset) 归一成「文本节点内」或「某节点前/后」的边界 */
  function resolveBoundary(container, node, offset) {
    let n = node;
    let off = offset;
    while (n && n.nodeType === 1) {
      if (n.classList && n.classList.contains("kd-math")) {
        return { kind: off > 0 ? "after" : "before", node: n };
      }
      const kids = n.childNodes;
      if (off <= 0) return { kind: "before", node: n };
      if (off >= kids.length) return { kind: "after", node: n };
      n = kids[off];
      off = 0;
    }
    return n ? { kind: "inText", node: n, offset: off } : null;
  }

  /** 光标落在公式内部时，按前后半段吸附到公式原文区间的起点或终点 */
  function snapInMath(el, clientX) {
    const len = Number(el.dataset.srcLen) || 0;
    if (clientX == null || !el.getBoundingClientRect) return 0;
    const rect = el.getBoundingClientRect();
    if (!rect.width) return 0;
    return clientX < rect.left + rect.width / 2 ? 0 : len;
  }

  function lengthOf(el) {
    return el.nodeType === 3 ? el.data.length : (Number(el.dataset && el.dataset.srcLen) || 0);
  }

  /**
   * container 内某个 DOM 位置对应的原文下标。
   * clientX 可选：只在「光标落在公式内部」时用来判断吸附到起点还是终点。
   */
  function sourceOffset(container, node, offset, clientX) {
    if (!container || !node || !container.contains(node)) return null;
    const b = resolveBoundary(container, node, offset);
    if (!b) return null;
    if (b.node === container) return b.kind === "before" ? 0 : lengthOf(container);
    let acc = 0;
    let hit = false;
    const walk = (el) => {
      for (let i = 0; i < el.childNodes.length; i++) {
        if (hit) return;
        const child = el.childNodes[i];
        if (b.kind === "before" && child === b.node) { hit = true; return; }
        if (child.nodeType === 3) {
          if (b.kind === "inText" && child === b.node) { acc += b.offset; hit = true; return; }
          acc += child.data.length;
        } else if (child.nodeType === 1) {
          if (child.classList && child.classList.contains("kd-math")) {
            if (b.kind === "inText" && child.contains(b.node)) {
              acc += snapInMath(child, clientX);
              hit = true;
              return;
            }
            acc += lengthOf(child);
            if (b.kind === "after" && child === b.node) { hit = true; return; }
          } else {
            walk(child);
            if (hit) return;
            if (b.kind === "after" && child === b.node) { hit = true; return; }
            acc += 0;      // 普通元素（.mk 等）的内容已在递归里累加
          }
        }
      }
      if (b.kind === "after" && el === b.node) hit = true;
    };
    walk(container);
    return acc;
  }

  root.KDM = {
    mathSpans,
    buildHtml,
    renderMathInto,
    sourceOffset,
    esc,
    hasMath: (text) => cachedSpans(String(text == null ? "" : text)).length > 0,
  };
})(typeof globalThis !== "undefined" ? globalThis : this);
