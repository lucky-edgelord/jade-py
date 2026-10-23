function bsPageHelpers() {
  const vis = e => { const r = e.getBoundingClientRect(), st = getComputedStyle(e); return r.width > 0 && r.height > 0 && st.visibility !== 'hidden' && st.display !== 'none'; };
  const INTER = 'a,button,input,select,textarea,summary,label,[role=button],[role=link],[role=tab],[role=menuitem],[onclick]';
  const norm = s => String(s == null ? '' : s).replace(/\s+/g, ' ').trim().toLowerCase();
  const label = e => norm(e.getAttribute('aria-label') || (e.tagName === 'INPUT' && /^(submit|button|reset)$/i.test(e.type) ? e.value : '') || e.innerText || e.textContent || e.getAttribute('placeholder') || e.getAttribute('title') || e.value);
  function find(t) {
    let list = [];
    if (t.css) { try { list = [...document.querySelectorAll(t.css)]; } catch (e) { throw new Error('bad css selector: ' + t.css); } }
    else if (t.xpath) { const r = document.evaluate(t.xpath, document, null, XPathResult.ORDERED_NODE_SNAPSHOT_TYPE, null); for (let i = 0; i < r.snapshotLength; i++) { const n = r.snapshotItem(i); if (n.nodeType === 1) list.push(n); } }
    else if (t.text) {
      const w = norm(t.text), all = [...document.querySelectorAll('body *')].slice(0, 6000);
      let hit = all.filter(e => e.matches(INTER) && label(e) === w);
      if (!hit.length) hit = all.filter(e => { const x = norm(e.innerText); return x === w; });
      if (!hit.length && !t.exact) hit = all.filter(e => label(e).includes(w) && ![...e.children].some(c => label(c).includes(w)));
      list = hit.map(e => (e.closest(INTER) || e));
      list = list.filter((e, i) => list.indexOf(e) === i);
    }
    if (!t.hidden) list = list.filter(vis);
    return list;
  }
  function pick(t) { const l = find(t); if (t.unique && l.length > 1) throw new Error(`${l.length} elements match, expected exactly one`); return l[+t.nth || 0] || null; }
  function info(e) { return { tag: e.tagName.toLowerCase(), text: String(e.innerText || e.value || '').replace(/\s+/g, ' ').trim().slice(0, 80) }; }
  function setVal(e, v) {
    const proto = e.tagName === 'TEXTAREA' ? HTMLTextAreaElement.prototype : e.tagName === 'SELECT' ? HTMLSelectElement.prototype : HTMLInputElement.prototype;
    const d = Object.getOwnPropertyDescriptor(proto, 'value'); if (d && d.set) d.set.call(e, v); else e.value = v;
    e.dispatchEvent(new Event('input', { bubbles: true })); e.dispatchEvent(new Event('change', { bubbles: true }));
  }
  function key(e, k) {
    const o = { key: k, code: k === 'Enter' ? 'Enter' : k, bubbles: true, cancelable: true };
    e.dispatchEvent(new KeyboardEvent('keydown', o)); e.dispatchEvent(new KeyboardEvent('keypress', o));
    if (k === 'Enter') { const f = e.form || e.closest && e.closest('form'); if (f && /^(input|select)$/i.test(e.tagName)) { try { f.requestSubmit ? f.requestSubmit() : f.submit(); } catch (x) {} } else if (e.tagName === 'BUTTON' || e.tagName === 'A') e.click(); }
    e.dispatchEvent(new KeyboardEvent('keyup', o));
  }
  return { find, pick, info, setVal, key, vis };
}
