function browserAgentFactory() {
  const ids = new WeakMap(); let next = 1;
  const idOf = el => { let i = ids.get(el); if (!i) { i = next++; ids.set(el, i); try { el.setAttribute('data-jid', String(i)); } catch (e) {} } return i; };
  const byId = id => { const el = document.querySelector('[data-jid="' + id + '"]'); if (!el || !el.isConnected) throw new Error('element ' + id + ' is not on the page any more; read the page again'); return el; };
  const clean = (s, n) => { s = String(s == null ? '' : s).replace(/\s+/g, ' ').trim(); return n && s.length > n ? s.slice(0, n - 1) + '…' : s; };
  const SEL = 'a[href],button,input:not([type=hidden]),select,textarea,summary,[role=button],[role=link],[role=checkbox],[role=radio],[role=tab],[role=menuitem],[role=option],[role=switch],[role=combobox],[role=textbox],[onclick],[contenteditable=""],[contenteditable=true],[tabindex]:not([tabindex="-1"])';
  const visible = el => {
    const r = el.getBoundingClientRect(); if (r.width < 1 || r.height < 1) return false;
    const cs = getComputedStyle(el); if (cs.visibility === 'hidden' || cs.display === 'none' || +cs.opacity === 0) return false;
    return true;
  };
  const label = el => {
    const tag = el.tagName.toLowerCase();
    let t = el.getAttribute('aria-label') || '';
    if (!t && el.id) { const l = document.querySelector('label[for="' + CSS.escape(el.id) + '"]'); if (l) t = l.innerText; }
    if (!t && (tag === 'input' || tag === 'textarea' || tag === 'select')) { const l = el.closest('label'); t = (l && l.innerText) || el.placeholder || el.name || el.title || ''; if (tag === 'input' && /^(submit|button|reset)$/.test(el.type)) t = el.value || t; }
    if (!t) t = el.innerText || el.textContent || el.getAttribute('alt') || el.title || '';
    if (!t) { const img = el.querySelector && el.querySelector('img[alt]'); if (img) t = img.alt; }
    return clean(t, 90);
  };
  const describe = el => {
    const tag = el.tagName.toLowerCase(), r = el.getBoundingClientRect();
    const o = { id: idOf(el), tag };
    if (tag === 'input') o.type = el.type || 'text';
    const role = el.getAttribute('role'); if (role) o.role = role;
    o.text = label(el);
    if (tag === 'a' && el.href) o.href = el.href.length > 200 ? el.href.slice(0, 200) + '…' : el.href;
    if ('value' in el && el.type !== 'password' && el.value !== '' && !/^(submit|button|reset|checkbox|radio)$/.test(el.type || '')) o.value = clean(el.value, 80);
    if (el.type === 'password') o.secret = true;
    if (el.type === 'checkbox' || el.type === 'radio' || role === 'checkbox' || role === 'switch') o.checked = !!el.checked || el.getAttribute('aria-checked') === 'true';
    if (tag === 'select') o.options = [...el.options].slice(0, 25).map(x => clean(x.text, 40)), o.selected = clean(el.selectedOptions[0] && el.selectedOptions[0].text, 40);
    if (el.disabled || el.getAttribute('aria-disabled') === 'true') o.disabled = true;
    const vh = innerHeight; o.inView = r.bottom > 0 && r.top < vh && r.right > 0 && r.left < innerWidth;
    o.y = Math.round(r.top + scrollY);
    return o;
  };
  const textOf = (root, max) => { const t = clean(root.innerText || root.textContent || '', 0); return max && t.length > max ? t.slice(0, max) + '… [' + (t.length - max) + ' more characters; use read with from/max]' : t; };
  const scrollInfo = () => ({ y: Math.round(scrollY), height: Math.round(document.documentElement.scrollHeight), viewport: Math.round(innerHeight), atBottom: scrollY + innerHeight >= document.documentElement.scrollHeight - 4 });
  const fire = (el, type, init) => el.dispatchEvent(new (type.startsWith('key') ? KeyboardEvent : type.startsWith('pointer') ? PointerEvent : MouseEvent)(type, Object.assign({ bubbles: true, cancelable: true, view: window }, init || {})));
  const setValue = (el, v) => {
    const proto = el.tagName === 'TEXTAREA' ? HTMLTextAreaElement.prototype : el.tagName === 'SELECT' ? HTMLSelectElement.prototype : HTMLInputElement.prototype;
    const d = Object.getOwnPropertyDescriptor(proto, 'value'); if (d && d.set) d.set.call(el, v); else el.value = v;
  };
  const api = {
    version: 1,
    snapshot(o) {
      o = o || {}; const els = [];
      const all = [...document.querySelectorAll(SEL)].filter(visible);
      const seen = new Set();
      for (const el of all) { if (seen.has(el)) continue; seen.add(el); els.push(describe(el)); }
      els.sort((a, b) => (b.inView ? 1 : 0) - (a.inView ? 1 : 0) || a.y - b.y);
      const max = Math.min(400, Math.max(10, o.max_elements || 120));
      const out = { url: location.href, title: document.title, scroll: scrollInfo(), text: textOf(document.body || document.documentElement, o.max_text || 4000), elements: els.slice(0, max) };
      if (els.length > max) out.moreElements = els.length - max;
      const f = document.querySelector('iframe'); if (f) out.iframes = document.querySelectorAll('iframe').length;
      return out;
    },
    read(o) {
      o = o || {}; const root = o.selector ? document.querySelector(o.selector) : (document.body || document.documentElement);
      if (!root) throw new Error('nothing matches ' + o.selector);
      const t = clean(root.innerText || root.textContent || '', 0), from = Math.max(0, o.from | 0), max = Math.min(60000, Math.max(200, o.max || 8000));
      return { url: location.href, title: document.title, length: t.length, from, text: t.slice(from, from + max), more: t.length > from + max };
    },
    links(o) { o = o || {}; const out = []; for (const a of document.querySelectorAll('a[href]')) { if (!visible(a)) continue; const t = label(a); if (o.contains && !t.toLowerCase().includes(String(o.contains).toLowerCase())) continue; out.push({ id: idOf(a), text: t, href: a.href }); if (out.length >= (o.max || 60)) break; } return out; },
    find(text) {
      const q = String(text).toLowerCase(), out = [];
      for (const el of document.querySelectorAll(SEL)) { if (!visible(el)) continue; const t = (label(el) + ' ' + (el.value || '')).toLowerCase(); if (t.includes(q)) { out.push(describe(el)); if (out.length >= 15) break; } }
      if (!out.length) { const hit = (document.body.innerText || '').toLowerCase().indexOf(q); return { elements: [], inPageText: hit >= 0 }; }
      return { elements: out };
    },
    click(id) {
      const el = byId(id); if (el.disabled) throw new Error('element ' + id + ' is disabled');
      el.scrollIntoView({ block: 'center', inline: 'center' });
      const link = el.closest && el.closest('a[target]'); if (link && link.target !== '_self') link.target = '_self';   // one view: new-tab links open here
      const r = el.getBoundingClientRect(), x = r.left + r.width / 2, y = r.top + r.height / 2, init = { clientX: x, clientY: y, button: 0 };
      try { fire(el, 'pointerdown', init); fire(el, 'mousedown', init); } catch (e) {}
      if (el.focus) el.focus({ preventScroll: true });
      try { fire(el, 'pointerup', init); fire(el, 'mouseup', init); } catch (e) {}
      el.click();
      return { clicked: describe(el).text, url: location.href };
    },
    type(id, text, o) {
      o = o || {}; const el = byId(id); el.scrollIntoView({ block: 'center' }); el.focus();
      const editable = el.isContentEditable;
      if (!editable && !/^(INPUT|TEXTAREA)$/.test(el.tagName)) throw new Error('element ' + id + ' is not a text field');
      const cur = editable ? el.textContent : el.value, v = (o.clear === false ? cur : '') + String(text);
      if (editable) { el.textContent = v; el.dispatchEvent(new InputEvent('input', { bubbles: true, inputType: 'insertText', data: String(text) })); }
      else { setValue(el, v); el.dispatchEvent(new InputEvent('input', { bubbles: true, inputType: 'insertText', data: String(text) })); el.dispatchEvent(new Event('change', { bubbles: true })); }
      if (o.submit) api.press('Enter', el);
      return { typed: el.type === 'password' ? '(hidden)' : clean(v, 80), id };
    },
    press(key, target) {
      const el = target || document.activeElement || document.body, k = String(key);
      const code = k.length === 1 ? 'Key' + k.toUpperCase() : k, init = { key: k, code, keyCode: k === 'Enter' ? 13 : k === 'Escape' ? 27 : k === 'Tab' ? 9 : k.length === 1 ? k.toUpperCase().charCodeAt(0) : 0, which: k === 'Enter' ? 13 : 0 };
      const down = fire(el, 'keydown', init); if (k.length === 1 || k === 'Enter') fire(el, 'keypress', init); fire(el, 'keyup', init);
      if (k === 'Enter' && down) { const f = el.form || el.closest && el.closest('form'); if (f && /^(INPUT|SELECT)$/.test(el.tagName)) { if (f.requestSubmit) f.requestSubmit(); else f.submit(); } }
      return { pressed: k };
    },
    select(id, v) {
      const el = byId(id); if (el.tagName !== 'SELECT') throw new Error('element ' + id + ' is not a drop-down');
      const q = String(v).toLowerCase(), opt = [...el.options].find(o => o.value === v || o.text.trim().toLowerCase() === q) || [...el.options].find(o => o.text.toLowerCase().includes(q));
      if (!opt) throw new Error('no option "' + v + '"; options are: ' + [...el.options].map(o => o.text.trim()).slice(0, 20).join(' | '));
      setValue(el, opt.value); el.dispatchEvent(new Event('input', { bubbles: true })); el.dispatchEvent(new Event('change', { bubbles: true }));
      return { selected: clean(opt.text, 60) };
    },
    check(id, on) {
      const el = byId(id); const want = on !== false;
      if ((el.type === 'checkbox' || el.type === 'radio') && !!el.checked !== want) el.click();
      else if (!el.type) el.click();
      return { checked: !!el.checked };
    },
    scroll(o) {
      o = o || {}; const before = scrollY;
      if (o.id) byId(o.id).scrollIntoView({ block: 'center' });
      else if (o.to === 'top') scrollTo(0, 0); else if (o.to === 'bottom') scrollTo(0, document.documentElement.scrollHeight);
      else scrollBy(0, o.dy != null ? +o.dy : Math.round(innerHeight * 0.8));
      return Object.assign({ moved: Math.round(scrollY - before) }, scrollInfo());
    },
    state() { return { url: location.href, title: document.title, readyState: document.readyState, scroll: scrollInfo() }; }
  };
  return api;
}
