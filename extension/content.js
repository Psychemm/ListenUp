// Injected on demand. Returns { title, text, source } for the selection or the main article.
(() => {
  const SKIP_TAGS = new Set(["SCRIPT", "STYLE", "NOSCRIPT", "TEMPLATE", "SVG", "CANVAS", "IFRAME", "NAV", "FOOTER", "ASIDE", "FORM", "BUTTON", "INPUT", "SELECT", "TEXTAREA", "VIDEO", "AUDIO", "FIGURE"]);
  const SKIP_ROLE = new Set(["navigation", "banner", "contentinfo", "complementary", "menu", "menubar", "toolbar", "search", "dialog", "alert"]);
  const SKIP_CLASS = /(^|[\s_-])(nav|menu|sidebar|footer|header|comment|comments|share|social|related|promo|advert|ads?|cookie|popup|modal|breadcrumb|toc|byline|meta|tags?)([\s_-]|$)/i;
  const BLOCK_TAGS = new Set(["P", "H1", "H2", "H3", "H4", "H5", "H6", "LI", "BLOCKQUOTE", "PRE", "DD", "DT", "TD", "TH", "FIGCAPTION", "SUMMARY"]);

  function isHidden(el) {
    const cs = getComputedStyle(el);
    return cs.display === "none" || cs.visibility === "hidden" || el.hidden || el.getAttribute("aria-hidden") === "true";
  }
  function shouldSkip(el) {
    if (SKIP_TAGS.has(el.tagName)) return true;
    const role = el.getAttribute("role");
    if (role && SKIP_ROLE.has(role)) return true;
    const id = (el.id || "") + " " + (typeof el.className === "string" ? el.className : "");
    if (SKIP_CLASS.test(id) && !/article|content|main|post|entry|body/i.test(id)) return true;
    return isHidden(el);
  }

  function textLength(el) {
    let n = 0;
    el.querySelectorAll("p").forEach(p => { n += (p.innerText || "").trim().length; });
    return n;
  }

  function pickRoot() {
    const candidates = [];
    for (const sel of ["article", "main", "[role=main]", "#content", "#main", ".post-content", ".entry-content", ".article-body", ".markdown-body"]) {
      document.querySelectorAll(sel).forEach(el => candidates.push(el));
    }
    let best = null, bestLen = 0;
    for (const el of candidates) {
      if (isHidden(el)) continue;
      const len = textLength(el);
      if (len > bestLen) { best = el; bestLen = len; }
    }
    // Fall back to the densest container of paragraphs.
    if (!best || bestLen < 500) {
      const counts = new Map();
      document.querySelectorAll("p").forEach(p => {
        const len = (p.innerText || "").trim().length;
        if (len < 40) return;
        let parent = p.parentElement;
        for (let i = 0; i < 3 && parent; i++, parent = parent.parentElement) {
          counts.set(parent, (counts.get(parent) || 0) + len);
        }
      });
      for (const [el, len] of counts) {
        if (len > bestLen) { best = el; bestLen = len; }
      }
    }
    return best || document.body;
  }

  function collect(root) { return collectBlocks(root).map(b => b.text); }

  function collectBlocks(root) {
    const out = [];
    const seen = new Set();
    const walker = document.createTreeWalker(root, NodeFilter.SHOW_ELEMENT, {
      acceptNode(el) {
        if (shouldSkip(el)) return NodeFilter.FILTER_REJECT;
        return BLOCK_TAGS.has(el.tagName) ? NodeFilter.FILTER_ACCEPT : NodeFilter.FILTER_SKIP;
      }
    });
    let el;
    while ((el = walker.nextNode())) {
      // Skip blocks nested inside a block we already took (e.g. <p> inside <li>).
      let p = el.parentElement, nested = false;
      while (p && p !== root) { if (seen.has(p)) { nested = true; break; } p = p.parentElement; }
      if (nested) continue;
      seen.add(el);
      let t = (el.innerText || "").replace(/\s+/g, " ").trim();
      if (!t) continue;
      if (el.tagName === "PRE") { t = "[Code block omitted.]"; }
      if (/^H[1-6]$/.test(el.tagName) && !/[.!?:]$/.test(t)) t += ".";
      if (t.length < 3) continue;
      out.push({ el, text: t });
    }
    return out;
  }

  const mode = window.__listenupMode || "auto";   // auto | page | selection | from-selection
  const sel = window.getSelection && window.getSelection();
  const selection = (sel && sel.toString().trim()) || "";
  const title = (document.querySelector("h1")?.innerText || document.title || "").trim();

  if (mode === "from-selection") {
    if (!selection || !sel.rangeCount) return { title: "", text: "", source: "none", url: location.href, error: "Select where reading should start." };
    const range = sel.getRangeAt(0);
    const startEl = range.startContainer.nodeType === Node.TEXT_NODE ? range.startContainer.parentElement : range.startContainer;
    let root = pickRoot();
    if (!root.contains(startEl)) root = document.body;
    const blocks = collectBlocks(root);
    // First block that is, contains, or comes after the selection start.
    const idx = blocks.findIndex(b => b.el === startEl || b.el.contains(startEl) ||
      (startEl.compareDocumentPosition(b.el) & Node.DOCUMENT_POSITION_FOLLOWING));
    if (idx < 0) return { title: "", text: "", source: "none", url: location.href, error: "Could not locate the selection in the page text." };
    const texts = blocks.slice(idx).map(b => b.text);
    // Trim the first block so reading begins at the selected words.
    const head = selection.slice(0, 40).replace(/\s+/g, " ");
    const at = texts[0].indexOf(head);
    if (at > 0) texts[0] = texts[0].slice(at);
    return { title: "", text: texts.join("\n\n"), source: "from-selection", url: location.href };
  }

  if (mode !== "page" && selection.length > 0) {
    return { title: "", text: selection, source: "selection", url: location.href };
  }
  const root = pickRoot();
  let paras = collect(root);
  if (paras.join("").length < 200) paras = collect(document.body);
  return { title, text: paras.join("\n\n"), source: "page", url: location.href };
})();
