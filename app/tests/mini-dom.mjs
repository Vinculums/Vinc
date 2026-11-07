// 테스트용 작은 DOM: innerHTML 을 나무로 읽고, 단순 선택자(태그 · #id · .class · [attr] ·
// [attr="v"] · :checked, 자손 공백과 `>`)로 찾고, 이벤트를 부모로 올린다. 화면 배치는 없다.
// jsdom 을 의존성에 넣지 않으려고 둔다: 목록 · 체크 · 클릭 위임처럼 마크업이 곧 상태인 화면을
// 실제 코드 그대로 돌려 보는 데까지만 쓴다.
const VOID = new Set(['area', 'base', 'br', 'col', 'embed', 'hr', 'img', 'input', 'link', 'meta', 'source', 'wbr']);
const ENT = { amp: '&', lt: '<', gt: '>', quot: '"', '#39': "'", apos: "'" };
const decode = (s) => s.replace(/&(amp|lt|gt|quot|#39|apos);/g, (_, k) => ENT[k]);
const encAttr = (s) => String(s).replace(/&/g, '&amp;').replace(/"/g, '&quot;');

class Text {
  constructor(raw) { this.raw = raw; this.parent = null; }
  get textContent() { return decode(this.raw); }
  html() { return this.raw; }
}

export class El {
  constructor(tag, attrs = new Map()) {
    this.tagName = tag.toUpperCase();
    this.attrs = attrs;
    this.children = [];
    this.parent = null;
    this.listeners = {};
    this.style = {};
    this._root = false;
    this._value = attrs.has('value') ? decode(attrs.get('value')) : undefined;
    this.checked = attrs.has('checked');
    this.disabled = attrs.has('disabled');
    this.open = attrs.has('open');
    const self = this;
    this.dataset = new Proxy({}, {
      get: (_, k) => { const v = self.attrs.get('data-' + String(k).replace(/[A-Z]/g, (c) => '-' + c.toLowerCase())); return v === undefined ? undefined : decode(v); },
      set: (_, k, v) => { self.attrs.set('data-' + String(k).replace(/[A-Z]/g, (c) => '-' + c.toLowerCase()), encAttr(v)); return true; },
    });
    this.classList = {
      list: () => (self.attrs.get('class') || '').split(/\s+/).filter(Boolean),
      contains: (c) => self.classList.list().includes(c),
      add: (...cs) => self.attrs.set('class', [...new Set([...self.classList.list(), ...cs])].join(' ')),
      remove: (...cs) => self.attrs.set('class', self.classList.list().filter((x) => !cs.includes(x)).join(' ')),
      toggle: (c, on) => { const has = self.classList.contains(c); const want = on === undefined ? !has : !!on;
        if (want && !has) self.classList.add(c); if (!want && has) self.classList.remove(c); return want; },
    };
  }
  get id() { return this.attrs.get('id') || ''; }
  get className() { return this.attrs.get('class') || ''; }
  set className(v) { this.attrs.set('class', v); }
  get hidden() { return this.attrs.has('hidden'); }
  set hidden(v) { if (v) this.attrs.set('hidden', ''); else this.attrs.delete('hidden'); }
  get value() {
    if (this._value !== undefined) return this._value;
    if (this.tagName === 'TEXTAREA') return this.textContent;
    if (this.tagName === 'SELECT') {
      const opts = this.querySelectorAll('option');
      const sel = opts.find((o) => o.attrs.has('selected')) || opts[0];
      return sel ? sel.value : '';
    }
    if (this.tagName === 'OPTION') return this.textContent;
    return '';
  }
  set value(v) { this._value = String(v); }
  get isConnected() { let n = this; while (n.parent) n = n.parent; return n._root; }
  get parentElement() { return this.parent; }
  get childElementCount() { return this.children.filter((c) => c instanceof El).length; }
  get textContent() { return this.children.map((c) => c.textContent).join(''); }
  set textContent(v) { this.children = []; if (v !== '') this.append(new Text(String(v).replace(/&/g, '&amp;').replace(/</g, '&lt;'))); }
  get innerHTML() { return this.children.map((c) => c.html()).join(''); }
  set innerHTML(s) { for (const c of this.children) c.parent = null; this.children = []; for (const n of parse(String(s))) this.append(n); }
  html() {
    const a = [...this.attrs].map(([k, v]) => v === '' ? ` ${k}` : ` ${k}="${v}"`).join('');
    const tag = this.tagName.toLowerCase();
    return VOID.has(tag) ? `<${tag}${a}>` : `<${tag}${a}>${this.innerHTML}</${tag}>`;
  }
  get outerHTML() { return this.html(); }
  append(n) { if (n.parent) n.parent.children.splice(n.parent.children.indexOf(n), 1); n.parent = this; this.children.push(n); return n; }
  appendChild(n) { return this.append(n); }
  remove() { if (this.parent) { this.parent.children.splice(this.parent.children.indexOf(this), 1); this.parent = null; } }
  insertAdjacentHTML(where, s) {
    const nodes = parse(String(s));
    if (where === 'beforeend') nodes.forEach((n) => this.append(n));
    else if (where === 'afterbegin') { nodes.forEach((n) => { n.parent = this; }); this.children.unshift(...nodes); }
    else {
      const p = this.parent; const at = p.children.indexOf(this) + (where === 'afterend' ? 1 : 0);
      nodes.forEach((n) => { n.parent = p; }); p.children.splice(at, 0, ...nodes);
    }
  }
  setAttribute(k, v) { this.attrs.set(k, encAttr(v)); }
  getAttribute(k) { const v = this.attrs.get(k); return v === undefined ? null : decode(v); }
  hasAttribute(k) { return this.attrs.has(k); }
  removeAttribute(k) { this.attrs.delete(k); }
  contains(n) { for (let x = n; x; x = x.parent) if (x === this) return true; return false; }
  *walk() { for (const c of this.children) if (c instanceof El) { yield c; yield* c.walk(); } }
  matches(sel) { return parseSel(sel).some((chain) => matchChain(this, chain)); }
  closest(sel) { for (let x = this; x; x = x.parent) if (x.matches(sel)) return x; return null; }
  querySelectorAll(sel) { const g = parseSel(sel); return [...this.walk()].filter((e) => g.some((c) => matchChain(e, c))); }
  querySelector(sel) { return this.querySelectorAll(sel)[0] || null; }
  addEventListener(t, f) { (this.listeners[t] ||= []).push(f); }
  removeEventListener(t, f) { this.listeners[t] = (this.listeners[t] || []).filter((x) => x !== f); }
  dispatchEvent(ev) {
    ev.target ||= this;
    let stop = false;
    ev.stopPropagation = () => { stop = true; };
    ev.preventDefault = () => { ev.defaultPrevented = true; };
    for (let x = this; x && !stop; x = x.parent) for (const f of [...(x.listeners[ev.type] || [])]) f(ev);
    return !ev.defaultPrevented;
  }
  fire(type, extra = {}) { return this.dispatchEvent({ type, ...extra }); }
  click() { if (!this.disabled) this.fire('click'); }
  focus() {} blur() {} select() {} scrollIntoView() {} setSelectionRange() {}
  getBoundingClientRect() { return { left: 0, top: 0, right: 0, bottom: 0, width: 0, height: 0 }; }
}

function parse(s) {
  const out = []; const stack = [{ append: (n) => out.push(n) }];
  const top = () => stack[stack.length - 1];
  const re = /<!--[\s\S]*?-->|<\/([a-zA-Z][\w-]*)\s*>|<([a-zA-Z][\w-]*)((?:\s+[^\s=/>]+(?:\s*=\s*(?:"[^"]*"|'[^']*'|[^\s>]+))?)*)\s*\/?>|([^<]+|<)/g;
  let m;
  while ((m = re.exec(s))) {
    if (m[1]) {
      const tag = m[1].toUpperCase();
      for (let i = stack.length - 1; i > 0; i--) if (stack[i].tagName === tag) { stack.length = i; break; }
    } else if (m[2]) {
      const attrs = new Map();
      const ar = /([^\s=/>]+)(?:\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s>]+)))?/g;
      let a; while ((a = ar.exec(m[3] || ''))) attrs.set(a[1].toLowerCase(), a[2] ?? a[3] ?? a[4] ?? '');
      const el = new El(m[2], attrs);
      const parent = top();
      if (parent instanceof El) parent.append(el); else parent.append(el);
      if (!VOID.has(m[2].toLowerCase())) stack.push(el);
    } else if (m[4] !== undefined && m[0] !== undefined && !m[0].startsWith('<!--')) {
      const t = new Text(m[0]);
      const parent = top();
      if (parent instanceof El) parent.append(t); else parent.append(t);
    }
  }
  return out;
}

// 선택자: 쉼표로 가른 묶음마다 [{combinator, compound}] 사슬.
const selCache = new Map();
function parseSel(sel) {
  if (selCache.has(sel)) return selCache.get(sel);
  const groups = sel.split(',').map((g) => {
    const toks = g.trim().replace(/\s*>\s*/g, ' > ').match(/(?:[^\s"[]+|\[[^\]]*\]|"[^"]*")+|>/g) || [];
    const chain = []; let comb = ' ';
    for (const t of toks) { if (t === '>') { comb = '>'; continue; } chain.push({ comb, c: compound(t) }); comb = ' '; }
    return chain;
  });
  selCache.set(sel, groups);
  return groups;
}
function compound(t) {
  const c = { tag: null, id: null, cls: [], attrs: [], checked: false };
  const re = /^[a-zA-Z][\w-]*|#[\w-]+|\.[\w-]+|\[([\w-]+)(?:=(?:"([^"]*)"|'([^']*)'|([^\]]*)))?\]|:checked/g;
  let m;
  while ((m = re.exec(t))) {
    const s = m[0];
    if (s[0] === '#') c.id = s.slice(1);
    else if (s[0] === '.') c.cls.push(s.slice(1));
    else if (s[0] === '[') c.attrs.push([m[1], m[2] ?? m[3] ?? m[4]]);
    else if (s === ':checked') c.checked = true;
    else c.tag = s.toUpperCase();
  }
  return c;
}
function matchOne(e, c) {
  if (c.tag && e.tagName !== c.tag) return false;
  if (c.id && e.id !== c.id) return false;
  if (c.cls.some((k) => !e.classList.contains(k))) return false;
  if (c.attrs.some(([k, v]) => !e.attrs.has(k) || (v !== undefined && decode(e.attrs.get(k)) !== v))) return false;
  if (c.checked && !e.checked) return false;
  return true;
}
function matchChain(e, chain) {
  const go = (el, i) => {
    if (!matchOne(el, chain[i].c)) return false;
    if (i === 0) return true;
    if (chain[i].comb === '>') return !!el.parent && el.parent instanceof El && go(el.parent, i - 1);
    for (let p = el.parent; p instanceof El; p = p.parent) if (go(p, i - 1)) return true;
    return false;
  };
  return go(e, chain.length - 1);
}

/** 전역 document · window · location · localStorage 를 세운다. 되돌릴 것은 없다(테스트 파일마다 프로세스가 따로다). */
export function installDom({ hash = '' } = {}) {
  const body = new El('body'); body._root = true;
  const store = new Map();
  const g = globalThis;
  g.document = {
    body, documentElement: { dataset: {} },
    createElement: (tag) => new El(tag),
    addEventListener() {}, removeEventListener() {},
    querySelector: (s) => body.querySelector(s), querySelectorAll: (s) => body.querySelectorAll(s),
    scrollingElement: null,
  };
  g.location = { hash, reload() {} };
  g.history = { replaceState: (_, __, h) => { g.location.hash = h; } };
  g.localStorage = {
    getItem: (k) => store.has(k) ? store.get(k) : null,
    setItem: (k, v) => store.set(k, String(v)),
    removeItem: (k) => store.delete(k),
  };
  g.window = g;
  g.addEventListener ||= () => {};
  g.removeEventListener ||= () => {};
  g.HashChangeEvent ||= class { constructor(type) { this.type = type; } };
  g.HTMLTextAreaElement ||= class {};
  g.getComputedStyle ||= () => ({ overflowY: 'visible' });
  g.requestAnimationFrame ||= (f) => setTimeout(f, 0);
  g.innerHeight ||= 800; g.innerWidth ||= 1200;
  return { body, store };
}

/** 마이크로태스크와 타이머 한 바퀴를 넘긴다. */
export const settle = (ms = 0) => new Promise((r) => setTimeout(r, ms));
