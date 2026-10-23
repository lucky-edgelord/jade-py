/* Jade's dashboard for jade-py. No build step: plain JavaScript against jade-py's HTTP API.
   Pages: home, chat (with the avatar and talk mode), vault, flows, schedules, Sentry, phones, logs, settings.
   On a phone it is laid out like the Jade Vault app: one screen at a time, tabs along the bottom, chats and status
   in the drawer. Inside Jade Link, talk mode uses the phone's own speech (window.NetrunnerAndroid, as in the app). */
'use strict';
const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => [...r.querySelectorAll(s)];
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c]);
const sleep = ms => new Promise(r => setTimeout(r, ms));
const ago = t => { if (!t) return 'never'; const s = Math.round(Date.now() / 1000 - t); return s < 60 ? `${s}s ago` : s < 3600 ? `${Math.round(s / 60)} min ago` : s < 86400 ? `${Math.round(s / 3600)} h ago` : `${Math.round(s / 86400)} d ago`; };
const clock = t => t ? new Date(t * 1000).toLocaleString([], { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' }) : '—';

/* ---------------- per-device settings (this browser only) ---------------- */
const store = {
  get(k, d) { try { const v = localStorage.getItem('jade:' + k); return v == null ? d : JSON.parse(v); } catch { return d; } },
  set(k, v) { try { localStorage.setItem('jade:' + k, JSON.stringify(v)); } catch {} },
};
const UI_DEFAULTS = { jadeVoice: false, voiceName: '', voicePitch: 1.25, voiceRate: 1.03, voiceLang: '', jadeFrame: 'portrait', jadeShow: true, jadeCompact: false, hfSolo: true, wakeWords: '', sttServer: false, sttOffline: false, vadMs: 900,
  jadeTheme: 'jade', jadeSpectrum: '#bd72f3', jadeGradientStart: '#f34b68', jadeGradientEnd: '#62a4ff', jadeGradientDirection: 'vertical', jadeHairMode: 'original', jadeHairColor: '#342c3c', jadeHairEnd: '#b58af4', jadeHairDirection: 'vertical',
  jadeClothesMode: 'original', jadeClothesColor: '#382f47', jadeClothesEnd: '#62a4ff', jadeClothesDirection: 'vertical', jadeStockingsMode: 'original', jadeStockingsColor: '#252330', jadeStockingsEnd: '#9c86bc', jadeStockingsDirection: 'vertical',
  jadeBootsMode: 'original', jadeBootsColor: '#20212a', jadeBootsEnd: '#f4c95d', jadeBootsDirection: 'vertical', jadeSkinMode: 'original', jadeSkinTone: 'fair', jadeSkinColor: '#d29b7c', jadeSkinEnd: '#f1d0c5', jadeSkinDirection: 'vertical' };
window.S = Object.assign({}, UI_DEFAULTS, store.get('ui', {}));
window.saveSettings = () => store.set('ui', S);
window.$ = $; window.esc = esc;

/* ---------------- the token and the API ---------------- */
(() => {   // #token=…&app=1 from Jade Link: keep the token, tidy the address
  const h = location.hash.replace(/^#/, ''); if (!/(^|&)(token|app|talk|kiosk)=/.test(h)) return;
  const p = new URLSearchParams(h);
  if (p.get('token')) store.set('token', p.get('token'));
  if (p.get('app')) sessionStorage.setItem('jade:app', '1');
  if (p.get('talk')) sessionStorage.setItem('jade:talk', '1');
  if (p.get('kiosk')) sessionStorage.setItem('jade:kiosk', '1');
  history.replaceState(null, '', location.pathname + '#/' + (p.get('talk') ? 'chat' : ''));
})();
let TOKEN = store.get('token', '');
const IN_APP = sessionStorage.getItem('jade:app') === '1' || !!window.NetrunnerAndroid?.speak;
const NA = () => window.NetrunnerAndroid;
const naHas = f => typeof NA()?.[f] === 'function';

class ApiError extends Error { constructor(status, msg, code) { super(msg); this.status = status; this.code = code; } }
async function api(path, { method = 'GET', body, raw } = {}) {
  const r = await fetch(path, { method, headers: { ...(TOKEN ? { Authorization: 'Bearer ' + TOKEN } : {}), ...(body !== undefined ? { 'Content-Type': 'application/json' } : {}) }, body: body !== undefined ? JSON.stringify(body) : undefined });
  if (r.status === 401) { showLogin(); throw new ApiError(401, 'the API token is needed'); }
  if (raw) return r;
  let j = null; try { j = await r.json(); } catch {}
  if (!r.ok || (j && j.ok === false && j.error)) { const e = j?.error || j?.detail?.error || {}; throw new ApiError(r.status, e.message || (typeof j?.detail === 'string' ? j.detail : '') || `HTTP ${r.status}`, e.code); }
  return j;
}
const tool = (name, args) => api('/v1/tools/' + name, { method: 'POST', body: args }).then(j => j.result);
const rawUrl = p => `/v1/vault/raw?path=${encodeURIComponent(p)}&token=${encodeURIComponent(TOKEN)}`;

/* ---------------- toasts and modals ---------------- */
function toast(msg, kind = '', title = '') {
  const d = document.createElement('div'); if (kind === 'bad' || kind === 'err') d.className = 'bad';
  d.innerHTML = (title ? `<b>${esc(title)}</b>` : '') + esc(msg); $('#toasts').appendChild(d);
  setTimeout(() => d.remove(), kind === 'bad' || kind === 'err' ? 8000 : 5000);
}
window.toast = toast;
function modal(html, { onclose } = {}) {
  const bg = document.createElement('div'); bg.className = 'modal-bg';
  bg.innerHTML = `<div class="modal cut" role="dialog" aria-modal="true">${html}</div>`;
  const close = () => { bg.remove(); onclose?.(); document.removeEventListener('keydown', key); };
  const key = e => { if (e.key === 'Escape') close(); };
  bg.addEventListener('click', e => { if (e.target === bg || e.target.closest('[data-close]')) close(); });
  document.addEventListener('keydown', key);
  $('#modal').appendChild(bg); bg.querySelector('input,textarea,button')?.focus();
  return { el: bg.firstElementChild, close };
}
function ask(title, { value = '', placeholder = '', ok = 'OK', area = false } = {}) {
  return new Promise(res => {
    const m = modal(`<h2>${esc(title)}</h2>${area ? `<textarea class="field" rows="8" id="askIn" placeholder="${esc(placeholder)}">${esc(value)}</textarea>` : `<input class="field" id="askIn" value="${esc(value)}" placeholder="${esc(placeholder)}">`}
      <div class="row" style="justify-content:flex-end;margin-top:12px"><button class="btn btn-ghost" data-close>Cancel</button><button class="btn btn-solid" id="askOk">${esc(ok)}</button></div>`, { onclose: () => res(null) });
    const go = () => { const v = $('#askIn', m.el).value; res(v); m.close(); };
    $('#askOk', m.el).onclick = go;
    if (!area) $('#askIn', m.el).addEventListener('keydown', e => { if (e.key === 'Enter') go(); });
  });
}
function confirmBox(title, text, ok = 'Yes', danger = false) {
  return new Promise(res => {
    const m = modal(`<h2>${esc(title)}</h2><p>${esc(text)}</p><div class="row" style="justify-content:flex-end;margin-top:12px"><button class="btn btn-ghost" data-close>Cancel</button><button class="btn ${danger ? 'btn-red' : 'btn-solid'}" id="cfOk">${esc(ok)}</button></div>`, { onclose: () => res(false) });
    $('#cfOk', m.el).onclick = () => { res(true); m.close(); };
  });
}

/* ---------------- markdown (escaped first: nothing from a reply becomes HTML) ---------------- */
function md(src) {
  const blocks = []; let s = String(src ?? '').replace(/<think>[\s\S]*?<\/think>/g, '');
  s = s.replace(/```(\w*)\n?([\s\S]*?)```/g, (_, lang, code) => { blocks.push(`<pre><code>${esc(code.replace(/\n$/, ''))}</code></pre>`); return `\u0000${blocks.length - 1}\u0000`; });
  s = esc(s);
  const inline = t => t
    .replace(/`([^`]+)`/g, '<code>$1</code>')
    .replace(/!\[([^\]]*)\]\(([^)\s]+)\)/g, (_, alt, u) => /^https?:/.test(u) ? `<img alt="${alt}" src="${u}" style="max-width:100%">` : `<img alt="${alt}" src="${rawUrl(decodeURIComponent(u.replace(/&amp;/g, '&')))}" style="max-width:100%">`)
    .replace(/\[([^\]]+)\]\((https?:[^)\s]+)\)/g, '<a href="$2" target="_blank" rel="noopener noreferrer">$1</a>')
    .replace(/\[\[([^\]|#]+)(?:#[^\]|]*)?(?:\|([^\]]+))?\]\]/g, (_, t, l) => `<a href="#/vault?open=${encodeURIComponent(t)}">${l || t}</a>`)
    .replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>').replace(/(^|[^*])\*([^*\n]+)\*/g, '$1<em>$2</em>').replace(/~~([^~]+)~~/g, '<del>$1</del>')
    .replace(/(^|\s)(https?:\/\/[^\s<]+)/g, '$1<a href="$2" target="_blank" rel="noopener noreferrer">$2</a>');
  const out = []; const lines = s.split('\n'); let i = 0;
  while (i < lines.length) {
    const l = lines[i];
    if (/^\u0000\d+\u0000$/.test(l.trim())) { out.push(blocks[+l.trim().slice(1, -1)]); i++; continue; }
    let m;
    if ((m = /^(#{1,4})\s+(.*)/.exec(l))) { out.push(`<h${m[1].length}>${inline(m[2])}</h${m[1].length}>`); i++; continue; }
    if (/^\s*([-*_])\s*\1\s*\1[\s\1]*$/.test(l)) { out.push('<hr>'); i++; continue; }
    if (/^&gt;\s?/.test(l)) { const q = []; while (i < lines.length && /^&gt;\s?/.test(lines[i])) q.push(lines[i++].replace(/^&gt;\s?/, '')); out.push(`<blockquote>${inline(q.join('<br>'))}</blockquote>`); continue; }
    if (/^\s*([-*+]|\d+[.)])\s+/.test(l)) {
      const ordered = /^\s*\d/.test(l); const items = [];
      while (i < lines.length && /^\s*([-*+]|\d+[.)])\s+/.test(lines[i])) items.push(lines[i++].replace(/^\s*([-*+]|\d+[.)])\s+/, '').replace(/^\[( |x)\]\s*/i, (_, x) => `<span class="task-box">${x.trim() ? '☑' : '☐'}</span> `));
      out.push(`<${ordered ? 'ol' : 'ul'}>${items.map(x => `<li>${inline(x)}</li>`).join('')}</${ordered ? 'ol' : 'ul'}>`); continue;
    }
    if (/^\|.*\|\s*$/.test(l) && i + 1 < lines.length && /^\|[\s:|-]+\|\s*$/.test(lines[i + 1])) {
      const row = r => r.trim().replace(/^\||\|$/g, '').split('|').map(c => inline(c.trim()));
      const head = row(l); i += 2; const rows = [];
      while (i < lines.length && /^\|.*\|\s*$/.test(lines[i])) rows.push(row(lines[i++]));
      out.push(`<table><tr>${head.map(h => `<th>${h}</th>`).join('')}</tr>${rows.map(r => `<tr>${r.map(c => `<td>${c}</td>`).join('')}</tr>`).join('')}</table>`); continue;
    }
    if (!l.trim()) { i++; continue; }
    const para = []; while (i < lines.length && lines[i].trim() && !/^(#{1,4}\s|&gt;|\s*([-*+]|\d+[.)])\s|\u0000)/.test(lines[i])) para.push(lines[i++]);
    if (!para.length) { out.push(inline(l)); i++; continue; }
    out.push(`<p>${inline(para.join('<br>'))}</p>`);
  }
  return out.join('\n').replace(/\u0000(\d+)\u0000/g, (_, n) => blocks[+n]);
}

/* ---------------- sign in ---------------- */
function showLogin(msg = '') {
  if ($('#loginBox')) return;
  $('#view').className = ''; $('#view').innerHTML = `<div class="login pane cut" id="loginBox">
    <div class="pane-h">Sign in</div>
    <p class="help">Enter jade-py's API token (api_token in its config, or the one <code>jade serve</code> printed). It is kept in this browser only.</p>
    <input class="field" id="tokIn" type="password" autocomplete="current-password" placeholder="jv_…" style="margin-top:10px">
    <div class="row" style="margin-top:12px"><button class="btn btn-solid" id="tokGo">Sign in</button><span class="help" id="tokMsg">${esc(msg)}</span></div></div>`;
  const go = async () => { TOKEN = $('#tokIn').value.trim(); store.set('token', TOKEN); try { await api('/v1/status'); $('#loginBox').remove(); boot(); } catch (e) { $('#tokMsg').textContent = e.message; } };
  $('#tokGo').onclick = go; $('#tokIn').addEventListener('keydown', e => { if (e.key === 'Enter') go(); }); $('#tokIn').focus();
}
$('#logoutBtn').onclick = () => { store.set('token', ''); TOKEN = ''; showLogin('Signed out.'); };

/* ---------------- live events: notifications, approvals, Sentry, phones ---------------- */
const approvals = new Map();
async function eventsLoop() {
  let delay = 1000;
  for (;;) {
    try {
      const r = await fetch('/v1/events?token=' + encodeURIComponent(TOKEN));
      if (r.status === 401) return;
      if (!r.ok || !r.body) throw new Error('HTTP ' + r.status);
      delay = 1000; setDot(true);
      const rd = r.body.getReader(), dec = new TextDecoder(); let buf = '', ev = 'message';
      for (;;) {
        const { value, done } = await rd.read(); if (done) break;
        buf += dec.decode(value, { stream: true });
        let k; while ((k = buf.indexOf('\n')) >= 0) {
          const line = buf.slice(0, k).replace(/\r$/, ''); buf = buf.slice(k + 1);
          if (line.startsWith('event:')) ev = line.slice(6).trim();
          else if (line.startsWith('data:')) { let d = {}; try { d = JSON.parse(line.slice(5)); } catch {} onEvent(ev, d); }
          else if (!line) ev = 'message';
        }
      }
    } catch {}
    setDot(false); await sleep(delay); delay = Math.min(30000, delay * 2);
  }
}
function onEvent(ev, d) {
  if (ev === 'notify') { toast(d.body || '', '', d.title || 'Jade'); refreshDrawer(); }
  else if (ev === 'approval') { approvals.set(d.id, d); renderApprovals(); if (approvals.size === 1) openApproval(d.id); }
  else if (ev === 'approval_done') { approvals.delete(d.id); renderApprovals(); $(`[data-approval="${d.id}"]`)?.closest('.modal-bg')?.remove(); }
  else if (ev === 'sentry') toast(`${d.name || d.id}${d.phone ? ' on ' + d.phone : ''}: running ${d.flow}`, '', 'Sentry');
  else if (ev === 'phone') { toast(`${d.name} is connected`, '', 'Phone'); refreshDrawer(); }
}
function setDot(ok) { const d = $('#statusDot'); if (d) d.className = 'dot ' + (ok ? 'ok' : 'bad'); }
function renderApprovals() {
  const b = $('#apprBtn'); b.hidden = !approvals.size; $('#apprCount').textContent = approvals.size;
  b.onclick = () => { const first = approvals.keys().next().value; if (first) openApproval(first); };
}
function openApproval(id) {
  const a = approvals.get(id); if (!a) return;
  const m = modal(`<h2 data-approval="${esc(id)}">Jade wants to use ${esc(a.tool)}</h2><p class="help">She is waiting for your yes. Nothing runs until you choose.</p>
    <pre>${esc(JSON.stringify(a.arguments, null, 1))}</pre>
    <div class="row" style="justify-content:flex-end;margin-top:12px"><button class="btn btn-red" id="apNo">Don't</button><button class="btn btn-solid" id="apYes">Allow</button></div>`);
  const answer = async allow => { m.close(); try { await api('/v1/approvals/' + id, { method: 'POST', body: { allow } }); } catch (e) { toast(e.message, 'bad'); } approvals.delete(id); renderApprovals(); const next = approvals.keys().next().value; if (next) openApproval(next); };
  $('#apYes', m.el).onclick = () => answer(true); $('#apNo', m.el).onclick = () => answer(false);
}

/* ---------------- the drawer ("tray", as in the app): chats and status, and the pages that do not fit the tab bar ---------------- */
function drawerHtml() {
  return `<div class="drawer-bg" id="drawerBg"></div><aside class="drawer" id="drawer" aria-label="Chats and status">
    <div class="drawer-h"><span class="pi-mark">J</span><span class="logo" data-text="JADE">JADE</span><span class="grow"></span><button class="btn btn-ghost" id="drawerX" aria-label="Close">✕</button></div>
    <div class="drawer-sec"><div class="pane-h">Status</div><div id="drStatus" class="hud">…</div></div>
    <div class="drawer-sec"><div class="pane-h">Chats<span class="grow"></span><button class="btn btn-ghost" id="drNew">+ New</button></div><div id="drChats"></div></div>
    <div class="drawer-sec"><div class="pane-h">Go to</div><nav class="drawer-nav">
      <a href="#/flows">Flows</a><a href="#/schedules">Schedules</a><a href="#/logs">Logs</a><a href="#/settings">Settings</a>${IN_APP ? '<a href="#" id="drSetup">Phone setup</a>' : ''}</nav></div>
  </aside>`;
}
const openDrawer = () => { document.body.classList.add('drawer-open'); refreshDrawer(); };
const closeDrawer = () => document.body.classList.remove('drawer-open');
async function refreshDrawer() {
  if (!$('#drawer')) return;
  try {
    const [o, c] = await Promise.all([api('/v1/overview'), api('/v1/chats')]);
    const ph = o.phones || [];
    $('#drStatus').innerHTML = `<div><b>${esc(o.model)}</b> · ${esc(o.provider)}</div><div>${o.daemon ? 'background work on' : 'background work off'} · up ${Math.round(o.uptime_s / 60)} min</div>
      <div>${ph.filter(p => p.online).length}/${ph.length} phones online${o.sentry?.on ? ' · Sentry on' : ''}${o.busy?.length ? ' · <b>working</b>' : ''}</div>`;
    $('#drChats').innerHTML = (c.chats || []).slice(0, 40).map(x => `<a class="sess${x.id === chat.id ? ' on' : ''}" href="#/chat/${esc(x.id)}"><span>${esc(x.title || 'New chat')}</span></a>`).join('') || '<div class="help">No chats yet.</div>';
  } catch {}
}

/* ---------------- router ---------------- */
const PAGES = {};
let curPage = '';
async function route() {
  if (!TOKEN && !(await api('/v1/status').then(() => true, () => false))) return;
  closeDrawer();
  const h = location.hash.replace(/^#\/?/, '');
  const [path, qs] = h.split('?'); const parts = path.split('/').filter(Boolean);
  const page = parts[0] || 'home'; const q = new URLSearchParams(qs || '');
  curPage = page;
  $$('#rail a').forEach(a => a.classList.toggle('on', a.dataset.r === page));
  const v = $('#view'); v.className = ''; document.body.dataset.page = page;
  if (page !== 'chat') stageAway();
  try { await (PAGES[page] || PAGES.home)(v, parts.slice(1), q); } catch (e) { if (e.status !== 401) v.innerHTML = `<div class="empty">${esc(e.message)}</div>`; }
}
window.addEventListener('hashchange', route);

/* ---------------- home ---------------- */
PAGES.home = async v => {
  const o = await api('/v1/overview');
  const ph = o.phones || [];
  const stat = (n, l) => `<div class="pane stat"><b>${esc(n)}</b><span>${esc(l)}</span></div>`;
  v.innerHTML = `<h1 class="page">Jade <span class="chip ${o.daemon ? 'ok' : 'warn'}">${o.daemon ? 'background work on' : 'background work off'}</span>${o.busy?.length ? '<span class="chip ok"><span class="dot busy"></span>working</span>' : ''}</h1>
    <div class="grid" style="grid-template-columns:repeat(auto-fill,minmax(140px,1fr))">
      ${stat(o.model, o.provider)}${stat(o.notes, 'notes')}${stat(o.chats, 'chats')}${stat(o.tools, 'tools')}${stat(o.flows, 'flows')}${stat(o.schedules, 'schedules')}
      ${stat(`${ph.filter(p => p.online).length}/${ph.length}`, 'phones online')}${stat(o.sentry?.on ? 'on' : 'off', 'Sentry')}
    </div>
    <div class="pane cut" style="margin-top:12px"><div class="pane-h">Talk to her</div>
      <div class="row"><input class="field" id="homeAsk" placeholder="Ask Jade something…" style="flex:1;min-width:200px"><button class="btn btn-solid" id="homeGo">Send</button><button class="btn" id="homeTalk">Talk</button></div></div>
    <div class="grid2" style="margin-top:12px">
      <div class="pane"><div class="pane-h">Phones and tablets<span class="grow"></span><a class="btn btn-ghost" href="#/phones">All</a></div>
        ${ph.map(p => `<div class="row-item"><span class="dot ${p.online ? 'ok' : 'bad'}"></span><div class="col"><span class="t">${esc(p.name)}</span><span class="d">${esc(p.model || '')} · ${esc(p.via)} · ${p.online ? 'online' : 'last seen ' + ago(p.last_seen)}</span></div></div>`).join('') || '<div class="empty">No phone yet. Install Jade Link and connect it (Phones).</div>'}</div>
      <div class="pane"><div class="pane-h">Lately</div><pre class="log" style="max-height:260px">${esc((o.notifications || []).slice().reverse().join('\n') || 'Nothing yet.')}</pre></div>
      <div class="pane"><div class="pane-h">Heartbeat and curiosity</div>
        <div class="row-item"><div class="col"><span class="t">Heartbeat ${o.heartbeat.on ? 'on' : 'off'}</span><span class="d">${o.heartbeat.on ? 'every ' + o.heartbeat.every_min + ' min · ' : ''}${esc(o.heartbeat.status || 'not run yet')}</span></div></div>
        <div class="row-item"><div class="col"><span class="t">Curiosity ${o.curiosity.on ? 'on' : 'off'}</span><span class="d">self-directed wake-ups</span></div></div>
        <div class="row-item"><div class="col"><span class="t">Backup ${o.backup.on ? 'on' : 'off'}</span><span class="d">last ${clock(o.backup.last)}</span></div></div></div>
      <div class="pane"><div class="pane-h">Recent flow runs<span class="grow"></span><a class="btn btn-ghost" href="#/flows">Flows</a></div>
        ${(o.flow_runs || []).map(r => `<div class="row-item"><span class="dot ${r.status === 'ok' ? 'ok' : r.status === 'running' ? 'busy' : 'bad'}"></span><div class="col"><span class="t">${esc(r.flow)}</span><span class="d">${esc(r.via || '')} · ${clock(r.started)}${r.error ? ' · ' + esc(r.error) : ''}</span></div></div>`).join('') || '<div class="empty">No runs yet.</div>'}</div>
    </div>`;
  const go = () => { const t = $('#homeAsk').value.trim(); if (!t) return; pendingSend = t; location.hash = '#/chat/new'; };
  $('#homeGo').onclick = go; $('#homeAsk').addEventListener('keydown', e => { if (e.key === 'Enter') go(); });
  $('#homeTalk').onclick = () => { sessionStorage.setItem('jade:talk', '1'); location.hash = '#/chat'; };
};

/* ---------------- chat ---------------- */
const chat = { id: null, msgs: [], running: false, el: null, aiEl: null, attach: [] };
let pendingSend = null;
const cleanUser = t => String(t || '').replace(/^\[[^\]]{8,40}\]\s*/, '');

PAGES.chat = async (v, parts) => {
  v.className = 'full';
  v.innerHTML = `<div class="chat" id="chatWrap">
    <div class="chat-side"><div class="row" style="padding:0 6px 8px"><button class="btn btn-solid" id="newChat" style="flex:1">+ New chat</button></div><div id="sessList"></div></div>
    <div class="chat-main"><div id="stageSlot"></div>
      <div class="stream" id="stream" aria-live="polite"></div>
      <div class="attach-list" id="attachList"></div>
      <div class="composer"><button class="btn btn-ghost" id="attachBtn" title="Add a picture" aria-label="Add a picture">＋</button><input type="file" id="fileIn" accept="image/*" multiple hidden>
        <textarea id="input" rows="1" placeholder="Message Jade…" aria-label="Message"></textarea>
        <button class="btn" id="talkBtn" aria-pressed="false" title="Talk to Jade hands-free">Talk</button>
        <button class="btn btn-solid" id="sendBtn">Send</button></div></div></div>`;
  $('#newChat').onclick = () => { location.hash = '#/chat/new'; };
  const inp = $('#input');
  inp.addEventListener('input', () => { inp.style.height = 'auto'; inp.style.height = Math.min(inp.scrollHeight, innerHeight * 0.4) + 'px'; });
  inp.addEventListener('keydown', e => { if (e.key === 'Enter' && !e.shiftKey && !e.isComposing && innerWidth > 760) { e.preventDefault(); sendFromBox(); } });
  $('#sendBtn').onclick = () => chat.running ? stopTurn() : sendFromBox();
  $('#talkBtn').onclick = () => (HF.on ? hfStop() : hfStart().catch(e => toast(String(e.message || e), 'bad')));
  $('#attachBtn').onclick = () => $('#fileIn').click();
  $('#fileIn').onchange = async e => { for (const f of e.target.files) chat.attach.push(await imageB64(f)); e.target.value = ''; renderAttach(); };
  inp.addEventListener('paste', async e => { const fs = [...(e.clipboardData?.files || [])].filter(f => f.type.startsWith('image/')); if (!fs.length) return; e.preventDefault(); for (const f of fs) chat.attach.push(await imageB64(f)); renderAttach(); });
  stageInto($('#stageSlot'));
  hfSet(HF.state);
  const want = parts[0];
  if (want === 'new' || (!want && !chat.id)) { if (!chat.running) { chat.id = null; chat.msgs = []; } }
  else if (want && want !== chat.id) await loadChat(want);
  renderChat(); listSessions();
  if (pendingSend) { const t = pendingSend; pendingSend = null; send(t); }
  if (sessionStorage.getItem('jade:talk') === '1') { sessionStorage.removeItem('jade:talk'); setTimeout(() => hfStart().catch(e => toast(String(e.message || e), 'bad')), 400); }
  inp.focus({ preventScroll: true });
};
async function listSessions() {
  try {
    const c = await api('/v1/chats');
    const el = $('#sessList'); if (!el) return;
    el.innerHTML = (c.chats || []).map(x => `<div class="row" style="flex-wrap:nowrap;gap:0"><a class="sess${x.id === chat.id ? ' on' : ''}" href="#/chat/${esc(x.id)}" title="${esc(x.title)}"><span>${esc(x.title || 'New chat')}</span></a><button class="btn btn-ghost" data-del="${esc(x.id)}" aria-label="Delete chat" title="Delete">✕</button></div>`).join('') || '<div class="help" style="padding:8px">No chats yet.</div>';
    el.onclick = async e => { const id = e.target.closest('[data-del]')?.dataset.del; if (!id) return; e.preventDefault(); if (!(await confirmBox('Delete this chat?', 'It is removed from jade-py for good.', 'Delete', true))) return; await api('/v1/chats/' + id, { method: 'DELETE' }); if (chat.id === id) location.hash = '#/chat/new'; listSessions(); };
  } catch {}
}
async function loadChat(id) {
  const j = await api('/v1/chats/' + encodeURIComponent(id));
  chat.id = id; chat.msgs = j.chat.messages || [];
}
async function imageB64(file) {
  const url = URL.createObjectURL(file);
  try {
    const img = new Image(); img.src = url; await img.decode();
    const k = Math.min(1, 1600 / Math.max(img.width, img.height)); const c = document.createElement('canvas'); c.width = Math.round(img.width * k); c.height = Math.round(img.height * k);
    c.getContext('2d').drawImage(img, 0, 0, c.width, c.height); return c.toDataURL('image/jpeg', 0.85).split(',')[1];
  } finally { URL.revokeObjectURL(url); }
}
function renderAttach() { const el = $('#attachList'); if (!el) return; el.innerHTML = chat.attach.map((b, i) => `<img src="data:image/jpeg;base64,${b}" alt="picture ${i + 1}" title="Tap to remove" data-i="${i}">`).join(''); el.onclick = e => { const i = e.target.dataset.i; if (i != null) { chat.attach.splice(+i, 1); renderAttach(); } }; }

function toolArgText(a) { const v = a && (a.command || a.path || a.query || a.url || a.action || a.name || a.text); return typeof v === 'string' ? v.split('\n')[0] : JSON.stringify(a || {}).slice(0, 120); }
function toolCard(c, res) {
  const err = res && (res.error || /^Error:/.test(res.content || ''));
  const out = res ? (res.content ?? '') : '';
  return `<details class="tool${!res ? ' run' : err ? ' err' : ''}" data-call="${esc(c.id)}"><summary><span class="tname">${esc(c.name)}</span><span class="targ">${esc(toolArgText(c.arguments))}</span><span class="tstat">${!res ? 'running…' : err ? 'error' : 'done'}</span></summary>
    <pre>${esc(JSON.stringify(c.arguments || {}, null, 1))}</pre>${res ? `<pre class="tout">${esc(String(out).slice(0, 20000))}</pre>` : '<pre class="tout"></pre>'}</details>`;
}
function renderChat() {
  const el = $('#stream'); if (!el) return;
  const results = {}; for (const m of chat.msgs) if (m.role === 'tool') results[m.tool_call_id] = m;
  const html = [];
  for (const m of chat.msgs) {
    if (m.role === 'user' && m.auto) { html.push(`<div class="msg-ai"><div class="pics">${(m.images || []).map(b => `<img src="data:image/jpeg;base64,${b}" alt="a picture from a tool">`).join('')}</div></div>`); continue; }
    if (m.role === 'user') { html.push(`<div class="msg-user"><div class="who disp">You</div><div class="bubble">${esc(cleanUser(m.display || m.content))}</div>${m.images?.length ? `<div class="pics" style="justify-content:flex-end">${m.images.map(b => `<img src="data:image/jpeg;base64,${b}" alt="your picture">`).join('')}</div>` : ''}</div>`); continue; }
    if (m.role === 'assistant') {
      html.push(`<div class="msg-ai"><div class="who disp">Jade</div>${m.thinking ? `<details><summary class="hint">thinking…</summary><div class="think">${esc(m.thinking)}</div></details>` : ''}${m.content ? `<div class="md">${md(m.content)}</div>` : ''}${(m.tool_calls || []).map(c => toolCard(c, results[c.id])).join('')}</div>`);
    }
  }
  if (!chat.msgs.length) html.push(`<div class="empty" style="margin:auto;max-width:520px">Ask anything. She can use your vault, the web, your phones and tablets, flows and schedules.<div class="row" style="justify-content:center;margin-top:10px">${['What is on my phones right now?', 'Summarise my inbox', 'Where is my phone?'].map(x => `<button class="example" data-ex="${esc(x)}">${esc(x)}</button>`).join('')}</div></div>`);
  el.innerHTML = html.join('');
  el.onclick = e => { const x = e.target.closest('[data-ex]'); if (x) { $('#input').value = x.dataset.ex; $('#input').focus(); } const img = e.target.closest('.pics img'); if (img) modal(`<img src="${img.src}" alt="">`); };
  el.scrollTop = el.scrollHeight;
}
function sendFromBox() { const inp = $('#input'); const t = inp.value.trim(); if (!t && !chat.attach.length) return; inp.value = ''; inp.style.height = 'auto'; send(t || 'What do you see?'); }
async function stopTurn() { if (chat.id) { try { await api(`/v1/chats/${chat.id}/cancel`, { method: 'POST' }); } catch {} } chat.abort?.abort(); }

/** One turn, streamed: her words appear as they come, tool calls as cards; the avatar and talk mode follow along. */
async function send(text) {
  if (chat.running) { toast('Jade is still answering. Stop her first, or wait.', 'bad'); return; }
  const images = chat.attach.splice(0); renderAttach();
  chat.msgs.push({ role: 'user', display: text, content: text, images }); renderChat();
  chat.running = true; setSending(true); jadeMood('thinking'); jadeBubble(text, 'You');
  const el = $('#stream');
  const ai = document.createElement('div'); ai.className = 'msg-ai'; ai.innerHTML = '<div class="who disp">Jade</div><div class="md caret"></div>'; el?.appendChild(ai);
  let mdEl = ai.querySelector('.md'), text_ = '', think = '', all = '', final = '';
  const ctl = chat.abort = new AbortController();
  try {
    const r = await fetch('/v1/chat', { method: 'POST', signal: ctl.signal, headers: { 'Content-Type': 'application/json', Authorization: 'Bearer ' + TOKEN },
      body: JSON.stringify({ message: text, session: chat.id || 'new', stream: true, ...(images.length ? { images } : {}) }) });
    if (!r.ok) { let m = 'HTTP ' + r.status; try { const j = await r.json(); m = j.error?.message || j.detail?.error?.message || m; } catch {} throw new Error(m); }
    const rd = r.body.getReader(), dec = new TextDecoder(); let buf = '', ev = 'message';
    for (;;) {
      const { value, done } = await rd.read(); if (done) break;
      buf += dec.decode(value, { stream: true }); let k;
      while ((k = buf.indexOf('\n')) >= 0) {
        const line = buf.slice(0, k).replace(/\r$/, ''); buf = buf.slice(k + 1);
        if (line.startsWith('event:')) { ev = line.slice(6).trim(); continue; }
        if (!line.startsWith('data:')) continue;
        let d; try { d = JSON.parse(line.slice(5)); } catch { continue; }
        if (d.session && d.session !== chat.id) { chat.id = d.session; history.replaceState(null, '', '#/chat/' + chat.id); listSessions(); }
        if (ev === 'text') { text_ += d.text; all += d.text; mdEl.innerHTML = md(text_); jadeMood('writing'); jadeBubble(plain(text_).slice(-320)); }
        else if (ev === 'thinking') { think += d.text; let t = ai.querySelector('.think'); if (!t) { const det = document.createElement('details'); det.innerHTML = '<summary class="hint">thinking…</summary><div class="think"></div>'; ai.insertBefore(det, mdEl); t = det.querySelector('.think'); } t.textContent = think; jadeMood('thinking'); }
        else if (ev === 'tool_start') { const w = document.createElement('div'); w.innerHTML = toolCard({ id: d.id, name: d.name, arguments: d.arguments }, null); ai.appendChild(w.firstElementChild); jadeMood('working'); jadeBubble(toolCaption(d.name, d.arguments), 'Jade'); text_ = ''; const nm = document.createElement('div'); nm.className = 'md'; ai.appendChild(nm); mdEl = nm; }
        else if (ev === 'tool_progress') { const c = ai.querySelector(`[data-call="${CSS.escape(d.id)}"] .tout`); if (c) c.textContent += d.line + '\n'; }
        else if (ev === 'tool_end') { const c = ai.querySelector(`[data-call="${CSS.escape(d.id)}"]`); if (c) { c.classList.remove('run'); if (d.error) c.classList.add('err'); c.querySelector('.tstat').textContent = d.error ? (d.skipped ? 'not run' : 'error') : `${d.ms} ms`; c.querySelector('.tout').textContent = String(d.output).slice(0, 20000); } }
        else if (ev === 'error') { toast(d.message, 'bad', 'Jade'); jadeMood('error', 3000); }
        else if (ev === 'done') { final = d.text || all; }
        if (el) el.scrollTop = el.scrollHeight;
      }
    }
  } catch (e) { if (e.name !== 'AbortError') { toast(e.message, 'bad'); jadeMood('error', 3000); } }
  finally {
    ai.querySelectorAll('.caret').forEach(x => x.classList.remove('caret'));
    chat.running = false; chat.abort = null; setSending(false);
    if (final) { jadeMood('happy', 2200); jadeReply(final); } else if (Jade?.ready) jadeMood('idle');
    if (chat.id) { try { await loadChat(chat.id); if (curPage === 'chat') renderChat(); } catch {} }
    listSessions(); refreshDrawer();
  }
}
function setSending(on) { const b = $('#sendBtn'); if (b) { b.textContent = on ? 'Stop' : 'Send'; b.classList.toggle('btn-red', on); b.classList.toggle('btn-solid', !on); } }
function toolCaption(name, a = {}) {
  const short = (x, n = 70) => { x = String(x ?? '').split('\n')[0]; return x.length > n ? x.slice(0, n) + '…' : x; };
  if (/^mcp__/.test(name)) { const [, srv, t] = name.split('__'); return `${srv} › ${t}`; }
  return ({ read: () => `Reading ${short(a.path)}`, write: () => `Writing ${short(a.path)}`, edit: () => `Editing ${short(a.path)}`, bash: () => `$ ${short(a.command)}`,
    web_search: () => `Searching the web: ${short(a.query)}`, web_fetch: () => `Fetching ${short(a.url)}`, memory: () => `Memory: ${a.action || ''}`, notes_search: () => a.query ? `Searching notes: ${short(a.query)}` : 'Surveying the vault',
    phone: () => `Phone${a.phone ? ' (' + a.phone + ')' : ''}: ${a.action || ''}`, screen: () => `Screen: ${a.action || ''}`, senses: () => `Senses: ${a.action || ''}`, sentry: () => `Sentry: ${a.action || ''}`,
    schedule: () => `Scheduling: ${short(a.name || a.action)}`, flow: () => `Flow: ${a.action || ''} ${short(a.name || '')}`, browser: () => `Browser: ${a.action || ''}`, ssh: () => `SSH: ${a.action || ''}` }[name] || (() => `Using ${name}`))();
}

/* ---------------- the avatar (Jade's Live2D model, from the app) ---------------- */
let stage = null, avatarLoading = null;
window.Jade = null;
function loadScript(src) { return new Promise((res, rej) => { const s = document.createElement('script'); s.src = src; s.onload = res; s.onerror = () => rej(new Error('could not load ' + src)); document.head.appendChild(s); }); }
let themeLoading = null;
/** Her colours (the gemstone themes) apply to the whole page; the avatar code that holds them is small, so it loads at once. */
function loadTheme() {
  return themeLoading ??= (async () => {
    if (!$('link[href="/ui/avatar/avatar.css"]')) { const l = document.createElement('link'); l.rel = 'stylesheet'; l.href = '/ui/avatar/avatar.css'; document.head.appendChild(l); }
    await loadScript('/ui/avatar/avatar.js');
    try { applyJadeTheme(S.jadeTheme); } catch {}
  })();
}
function loadAvatar() {
  return avatarLoading ??= (async () => {
    await loadTheme();
    for (const f of ['pixi.min.js', 'live2dcubismcore.min.js', 'cubism4.min.js']) await loadScript('/ui/avatar/' + f);
    await window.Jade.init({ host: stage, onStatus: t => { const st = $('#jadeStatus'); if (st) st.textContent = t; }, onSpeak: () => {} });
    window.Jade.setFrame(S.jadeFrame);
    try { window.Jade.setAppearance(); } catch {}
    $('#jadeDot').className = 'dot ok'; jadeMood('idle');
  })().catch(e => { const st = $('#jadeStatus'); if (st) st.textContent = 'Her avatar could not load here (' + e.message + '). Talk mode still works.'; if (window.Jade?.state) window.Jade.state.failed = String(e.message); });
}
function makeStage() {
  stage = document.createElement('div'); stage.className = 'jade-stage'; stage.id = 'jadeStage';
  stage.innerHTML = `<div class="jade-label"><span class="dot busy" id="jadeDot"></span><button type="button" class="jade-theme-label" id="btnTheme" title="Choose Jade's gemstone colors">Jade</button><span class="jade-mood" id="jadeMood">booting</span></div>
    <div class="jade-ctrls"><button class="btn btn-ghost" id="btnVoice" aria-pressed="${S.jadeVoice}" title="Read replies aloud">Voice</button><button class="btn btn-ghost hide-compact" id="btnFrame" title="Portrait or full body">${S.jadeFrame === 'full' ? 'Portrait' : 'Full'}</button><button class="btn btn-ghost" id="btnCompact" title="Shrink the avatar">${S.jadeCompact ? 'Grow' : 'Shrink'}</button><button class="btn btn-ghost hide-compact" id="btnHideJade" title="Hide the avatar">Hide</button></div>
    <div class="jade-status" id="jadeStatus">waking her up…</div><div class="jade-bubble" id="jadeBubble" hidden></div>`;
  $('#btnVoice', stage).onclick = () => setVoice(!S.jadeVoice);
  $('#btnFrame', stage).onclick = e => { S.jadeFrame = S.jadeFrame === 'full' ? 'portrait' : 'full'; saveSettings(); e.target.textContent = S.jadeFrame === 'full' ? 'Portrait' : 'Full'; window.Jade?.setFrame?.(S.jadeFrame); };
  $('#btnCompact', stage).onclick = e => { S.jadeCompact = !S.jadeCompact; saveSettings(); e.target.textContent = S.jadeCompact ? 'Grow' : 'Shrink'; stage.classList.toggle('compact', S.jadeCompact); };
  $('#btnHideJade', stage).onclick = () => { S.jadeShow = false; saveSettings(); stageInto($('#stageSlot')); };
  $('#btnTheme', stage).onclick = () => openThemePicker();
  stage.classList.toggle('compact', S.jadeCompact);
}
function stageInto(slot) {
  if (!slot) return;
  if (!S.jadeShow && !HFS.on) { slot.innerHTML = `<div class="row" style="padding:4px 12px;border-bottom:1px solid var(--line)"><span class="hint">Her avatar is hidden.</span><button class="btn btn-ghost" id="showJade">Show Jade</button></div>`; $('#showJade').onclick = () => { S.jadeShow = true; saveSettings(); stageInto(slot); }; return; }
  if (!stage) makeStage();
  if (!HFS.on) { slot.innerHTML = ''; slot.appendChild(stage); }
  loadAvatar();
  try { window.Jade?.state?.app?.ticker.start(); } catch {}
}
function stageAway() { if (!stage || HFS.on || !stage.parentNode) return; stage.remove(); try { window.Jade?.state?.app?.ticker.stop(); } catch {} }
let bubbleT = 0;
function jadeBubble(text, who = 'Jade', ms = 0) {
  const b = $('#jadeBubble'); if (!b) return; clearTimeout(bubbleT);
  if (!text) { b.hidden = true; return; }
  b.innerHTML = `<span class="who disp">${esc(who)}</span>${esc(String(text).slice(0, 320))}`; b.hidden = false;
  if (ms) bubbleT = setTimeout(() => { b.hidden = true; }, ms);
}
function jadeMood(m, ms = 0, then = 'idle') { if (!window.Jade?.ready) return; window.Jade.setMood(m, ms, then); const e = $('#jadeMood'); if (e) e.textContent = window.Jade.label(); }
function setVoice(on) { S.jadeVoice = on; saveSettings(); const b = $('#btnVoice'); if (b) b.setAttribute('aria-pressed', String(on)); if (!on) window.Jade?.stop?.(); }
const plain = t => String(t || '').replace(/```[\s\S]*?```/g, '(code)').replace(/`([^`]+)`/g, '$1').replace(/!\[[^\]]*\]\([^)]*\)/g, '').replace(/\[([^\]]+)\]\([^)]*\)/g, '$1')
  .replace(/\[\[([^\]|]+)(?:\|([^\]]+))?\]\]/g, (_, a, b) => b || a).replace(/^#+\s*/gm, '').replace(/[*_~]{1,3}([^*_~]+)[*_~]{1,3}/g, '$1').replace(/^\s*[-*+]\s+/gm, '• ').trim();
function jadeReply(text) {
  if (!text) { jadeBubble(''); return; }
  const speakIt = S.jadeVoice || HF.on;
  if (speakIt && window.Jade?.speak) {
    const ok = window.Jade.speak(text, { voice: S.voiceName, rate: +S.voiceRate || 1.03, pitch: +S.voicePitch || 1.25 });
    if (ok) { jadeBubble(plain(text).slice(0, 320)); window.Jade.nod?.(); return; }
  }
  jadeBubble(plain(text).slice(0, 320), 'Jade', 9000);
}
function openThemePicker() {
  const m = modal(`<h2>Jade's colours</h2><div id="jadeThemePicker" class="gem-theme-grid"></div>
    <div id="jadeSpectrumControls" hidden><label class="lbl" for="jadeSpectrumColor">Spectrum colour</label><input type="color" class="theme-color-input" id="jadeSpectrumColor"></div>
    <div id="jadeGradientControls" hidden><label class="lbl">Gradient</label><div class="row"><input type="color" class="theme-color-input" id="jadeGradientStart"><input type="color" class="theme-color-input" id="jadeGradientEnd">
      <select class="field" id="jadeGradientDirection" style="width:auto"><option value="vertical">Vertical</option><option value="horizontal">Horizontal</option><option value="diagonal">Diagonal</option></select></div></div>
    <label class="lbl" style="margin-top:14px">Her look</label><div id="jadeAppearancePicker" class="jade-appearance-grid"></div>
    <div class="row" style="justify-content:flex-end;margin-top:12px"><button class="btn btn-solid" data-close>Done</button></div>`);
  loadAvatar().then(() => { try { renderJadeThemePicker(); renderJadeAppearancePicker(); applyJadeTheme(S.jadeTheme); } catch (e) { $('#jadeThemePicker', m.el).textContent = 'The avatar is not loaded: ' + e.message; } });
}

/* ---------------- talk mode: hands-free, as in the app (listen → she answers out loud → listen again) ---------------- */
const HF = { on: false, state: 'idle', errs: 0, timer: null, quiet: 0, pending: 0, rec: null, lock: null, lastBusy: 0, awakeUntil: 0 };
const HFS = { on: false };
const wkTok = s => String(s).toLowerCase().normalize('NFKD').replace(/[̀-ͯ]/g, '').replace(/[^a-z0-9']/g, '');
function wkDist(a, b) { const m = a.length, n = b.length; let p = Array.from({ length: n + 1 }, (_, j) => j); for (let i = 1; i <= m; i++) { const c = [i]; for (let j = 1; j <= n; j++) c[j] = Math.min(p[j] + 1, c[j - 1] + 1, p[j - 1] + (a[i - 1] === b[j - 1] ? 0 : 1)); p = c; } return p[n]; }
const WK_HEY = new Set(['hey', 'hay', 'hi', 'hei', 'ok', 'okay', 'a', 'ay']);
function wkWord(heard, want) { if (heard === want) return true; if (want === 'hey' && WK_HEY.has(heard)) return true; return want.length >= 5 && wkDist(heard, want) <= 2; }
function wakeParse(text) {
  const phrases = String(S.wakeWords || '').split(/[,;\n]/).map(x => x.trim().split(/\s+/).map(wkTok).filter(Boolean)).filter(x => x.length);
  if (!phrases.length) return null;
  const raw = String(text).trim().split(/\s+/), toks = raw.map(wkTok);
  for (const ph of phrases) for (let i = 0; i + ph.length <= toks.length; i++) if (ph.every((w, j) => wkWord(toks[i + j], w))) return { heard: true, command: raw.slice(i + ph.length).join(' ').replace(/^[\s,.:;!?-]+/, '') };
  return { heard: false };
}
function wakeGate(text) {
  const w = wakeParse(text); if (!w) return { text };
  const now = Date.now(), awake = HF.awakeUntil > now || now - HF.lastBusy < 15000;
  if (w.heard) { if (w.command.replace(/[^\w]/g, '').length >= 2) { HF.awakeUntil = now + 15000; return { text: w.command }; } HF.awakeUntil = now + 10000; return { chime: true }; }
  return awake ? { text } : { ignore: true };
}
function wakeChime() {
  try { const ac = new (window.AudioContext || window.webkitAudioContext)(); const g = ac.createGain(); g.gain.value = 0.2; g.connect(ac.destination); let t = ac.currentTime + 0.02;
    for (const [hz, ms] of [[660, 90], [990, 140]]) { const o = ac.createOscillator(); o.frequency.value = hz; o.connect(g); o.start(t); o.stop(t + ms / 1000); t += ms / 1000; } setTimeout(() => ac.close(), 600); } catch {}
}
const hfLabel = { idle: 'Talk', listening: '● Listening', thinking: '… Thinking', speaking: '♪ Speaking' };
function hfSet(st) {
  HF.state = st; const b = $('#talkBtn');
  if (b) { b.textContent = HF.on ? hfLabel[st] || 'Talk' : 'Talk'; b.setAttribute('aria-pressed', String(HF.on)); b.classList.toggle('btn-solid', HF.on); b.title = HF.on ? 'Talk mode is on: tap to stop' : 'Talk to Jade hands-free'; }
  const inp = $('#input'); if (inp && HF.on && st === 'listening' && !inp.value) { const wk = String(S.wakeWords || '').split(/[,;\n]/)[0].trim(); inp.placeholder = wk && !(HF.awakeUntil > Date.now() || Date.now() - HF.lastBusy < 15000) ? `Say “${wk}”…` : 'Listening…'; }
  if (HF.on) jadeMood(st === 'listening' ? 'listening' : st === 'thinking' ? 'thinking' : 'idle');
}
const sttNative = () => naHas('sttStart');
const sttBrowser = () => !!(window.SpeechRecognition || window.webkitSpeechRecognition);
const hfAvailable = () => S.sttServer ? !!navigator.mediaDevices?.getUserMedia : sttNative() ? !!NA().sttAvailable() : sttBrowser() || !!navigator.mediaDevices?.getUserMedia;
function hfEngine() {   // one small interface over the phone's recognizer (Jade Link), the browser's, and jade-py's transcription
  if (S.sttServer || (!sttNative() && !sttBrowser())) return hfServerEngine();
  if (sttNative()) return { start(cb) { window.__jadeSTT = (ev, text) => cb(ev, text); NA().sttStart(S.voiceLang || '', true, !!S.sttOffline); }, stop() { try { NA().sttStop(true); } catch {} window.__jadeSTT = null; } };
  const R = window.SpeechRecognition || window.webkitSpeechRecognition; let r = null;
  return {
    start(cb) {
      r = new R(); r.lang = S.voiceLang || navigator.language || 'en-US'; r.interimResults = true; r.continuous = false; r.maxAlternatives = 1;
      r.onstart = () => cb('ready', ''); r.onspeechstart = () => cb('begin', ''); r.onspeechend = () => cb('end', '');
      r.onresult = e => { const res = e.results[e.results.length - 1]; cb(res.isFinal ? 'final' : 'partial', res[0].transcript); };
      r.onerror = e => cb('error', e.error === 'no-speech' ? '6' : e.error === 'not-allowed' ? '9' : String(e.error)); try { r.start(); } catch { cb('error', '8'); }
    },
    stop() { try { r && (r.onresult = r.onerror = r.onend = null, r.abort()); } catch {} r = null; },
  };
}
function wavFromFloat(f, rate) {
  const b = new ArrayBuffer(44 + f.length * 2), v = new DataView(b), w = (o, s_) => { for (let i = 0; i < s_.length; i++) v.setUint8(o + i, s_.charCodeAt(i)); };
  w(0, 'RIFF'); v.setUint32(4, 36 + f.length * 2, true); w(8, 'WAVE'); w(12, 'fmt '); v.setUint32(16, 16, true); v.setUint16(20, 1, true); v.setUint16(22, 1, true); v.setUint32(24, rate, true); v.setUint32(28, rate * 2, true); v.setUint16(32, 2, true); v.setUint16(34, 16, true); w(36, 'data'); v.setUint32(40, f.length * 2, true);
  for (let i = 0; i < f.length; i++) v.setInt16(44 + i * 2, Math.max(-1, Math.min(1, f[i])) * 0x7fff, true);
  return new Uint8Array(b);
}
function resampleTo(f, from, to) { if (from === to) return f; const k = from / to, n = Math.floor(f.length / k), o = new Float32Array(n); for (let i = 0; i < n; i++) { const a = Math.floor(i * k), e = Math.max(a + 1, Math.min(f.length, Math.floor((i + 1) * k))); let t = 0; for (let j = a; j < e; j++) t += f[j]; o[i] = t / (e - a); } return o; }
async function transcribeWav(wav) {
  const r = await fetch('/v1/transcribe' + (S.voiceLang ? '?language=' + encodeURIComponent(S.voiceLang.split('-')[0]) : ''), { method: 'POST', headers: { Authorization: 'Bearer ' + TOKEN, 'Content-Type': 'audio/wav' }, body: wav });
  const j = await r.json().catch(() => ({})); if (!r.ok) throw new Error(j.error?.message || j.detail?.error?.message || 'HTTP ' + r.status);
  return String(j.text || '').trim();
}
/* listening through jade-py's transcription model: a simple voice detector cuts each phrase and sends it */
function hfServerEngine() {
  let st = null, ac = null, node = null, dead = false, timer = null;
  const cleanup = () => { dead = true; clearInterval(timer); try { node && (node.onaudioprocess = null, node.disconnect()); } catch {} try { ac && ac.close(); } catch {} try { st && st.getTracks().forEach(t => t.stop()); } catch {} };
  return {
    stop: cleanup,
    async start(cb) {
      try {
        st = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true } });
        if (dead) return cleanup(); ac = new (window.AudioContext || window.webkitAudioContext)(); const rate = ac.sampleRate;
        const src = ac.createMediaStreamSource(st); node = ac.createScriptProcessor(4096, 1, 1); src.connect(node); node.connect(ac.destination);
        const silenceMs = Math.max(400, Math.min(3000, +S.vadMs || 900)); let floor = 0.01, calib = 0, chunks = [], speaking = false, speechMs = 0, lastVoice = 0; const startAt = Date.now(); let sent = false;
        cb('ready', '');
        node.onaudioprocess = e => {
          if (dead || sent) return; const f = e.inputBuffer.getChannelData(0); let sum = 0; for (let i = 0; i < f.length; i++) sum += f[i] * f[i]; const rms = Math.sqrt(sum / f.length); const ms = f.length / rate * 1000; const now = Date.now();
          if (calib < 6) { floor = Math.max(0.004, (floor * calib + rms) / (calib + 1)); calib++; return; }
          const thr = Math.max(0.015, floor * 3.5);
          if (rms > thr) { if (!speaking) { speaking = true; speechMs = 0; cb('begin', ''); } speechMs += ms; lastVoice = now; }
          if (speaking) {
            chunks.push(new Float32Array(f));
            if (now - lastVoice > silenceMs || chunks.length * f.length / rate > 30) {
              sent = true; const all = new Float32Array(chunks.reduce((a, c) => a + c.length, 0)); let o = 0; for (const c of chunks) { all.set(c, o); o += c.length; }
              cb('end', ''); if (speechMs < 350) { cb('error', '7'); return; }
              transcribeWav(wavFromFloat(resampleTo(all, rate, 16000), 16000)).then(t => cb('final', t), err => cb('error', String(err.message || err)));
            }
          } else if (chunks.length < 3) chunks.push(new Float32Array(f)); else { chunks.shift(); chunks.push(new Float32Array(f)); }
        };
        timer = setInterval(() => { if (!dead && !speaking && !sent && Date.now() - startAt > 20000) { sent = true; cb('error', '6'); } }, 1000);
      } catch (e) { cb('error', /NotAllowed|Permission/i.test(e.name + e.message) ? '9' : String(e.message || e)); }
    },
  };
}
/* the companion screen: only her avatar on her background, full screen, captions at the bottom */
function hfSoloEnter() {
  if (HFS.on || S.hfSolo === false) return;
  if (!stage) makeStage();
  document.body.appendChild(stage); document.body.classList.add('hf-solo'); HFS.on = true; loadAvatar();
  let b = $('#hfExit'); if (!b) { b = document.createElement('button'); b.id = 'hfExit'; b.type = 'button'; b.textContent = '✕'; b.setAttribute('aria-label', 'Stop talk mode and go back to the chat'); stage.appendChild(b); }
  b.onclick = () => hfStop();
  let last = 0; stage.ondblclick = () => hfStop();
  stage.ontouchend = e => { const now = Date.now(); if (e.target.id !== 'hfExit' && now - last < 350) hfStop(); last = now; };
  try { window.Jade?.state?.app?.ticker.start(); } catch {}
}
function hfSoloExit() {
  if (!HFS.on) return;
  document.body.classList.remove('hf-solo'); HFS.on = false;
  if (stage) { stage.ondblclick = stage.ontouchend = null; }
  if (curPage === 'chat') stageInto($('#stageSlot')); else stageAway();
}
window.onAndroidBack = () => { if (HFS.on || HF.on) { hfStop(); return true; } if (document.body.classList.contains('drawer-open')) { closeDrawer(); return true; } return false; };
async function hfStart() {
  if (HF.on) return;
  if (!hfAvailable()) { toast('No speech recognizer here. In Chrome or Jade Link it works; elsewhere set a transcription model in Settings (stt_model) and turn on "Use jade-py to hear".', 'bad'); throw new Error('no speech recognizer available'); }
  if (sttNative() && naHas('micAllowed') && !NA().micAllowed()) toast('Allow the microphone for Jade Link when Android asks.', '', 'Talk mode');
  if (curPage !== 'chat') location.hash = '#/chat';
  HF.on = true; HF.errs = 0; HF.quiet = 0; HF.pending = 0;
  hfSoloEnter();
  try { HF.lock = await navigator.wakeLock?.request('screen'); } catch {}
  try { if (naHas('keepScreenOn')) NA().keepScreenOn(true); } catch {}
  hfSet('idle'); hfLoop(); toast('Talk mode is on. Just talk; Jade answers out loud.' + (S.wakeWords ? ` Start with “${S.wakeWords.split(/[,;\n]/)[0].trim()}”.` : ''), '', 'Talk');
}
function hfStop() {
  HF.on = false; hfSoloExit(); clearTimeout(HF.timer); try { HF.rec?.stop(); } catch {} HF.rec = null; try { HF.lock?.release(); } catch {} HF.lock = null;
  try { if (naHas('keepScreenOn')) NA().keepScreenOn(false); } catch {}
  window.Jade?.stop?.();
  const inp = $('#input'); if (inp) inp.placeholder = 'Message Jade…';
  hfSet('idle'); jadeMood('idle');
}
function hfBusy() { return !!(chat.running || window.Jade?.state?.speaking || (HF.pending && Date.now() < HF.pending)); }
function hfLoop() {
  clearTimeout(HF.timer); if (!HF.on) return;
  if (chat.running) HF.pending = 0;
  if (hfBusy()) { HF.quiet = 0; HF.lastBusy = Date.now(); hfSet(chat.running ? 'thinking' : 'speaking'); HF.timer = setTimeout(hfLoop, 250); return; }
  if (++HF.quiet < 3) { HF.timer = setTimeout(hfLoop, 250); return; }   // let her start talking before the microphone opens (no echo)
  hfListen();
}
function hfListen() {
  hfSet('listening'); const inp = $('#input'); let settled = false;
  const eng = HF.rec = hfEngine();
  const again = ms => { if (settled) return; settled = true; try { eng.stop(); } catch {} HF.rec = null; HF.quiet = 3; HF.timer = setTimeout(hfLoop, ms); };
  eng.start((ev, text) => {
    if (!HF.on || settled) return;
    if (ev === 'partial') { if (inp) inp.value = text; jadeBubble(text, 'You'); }
    else if (ev === 'final') {
      text = String(text || '').trim(); if (inp) inp.value = '';
      if (!text) return again(200);
      const wk = wakeGate(text); if (wk.ignore) return again(150); if (wk.chime) { wakeChime(); return again(150); } text = wk.text;
      HF.errs = 0; HF.pending = Date.now() + 5000; HF.quiet = 0; settled = true; try { eng.stop(); } catch {} HF.rec = null; hfSet('thinking');
      send(text).catch(() => {}); HF.timer = setTimeout(hfLoop, 300);
    } else if (ev === 'error') {
      const code = String(text);
      if (code === '6' || code === '7' || code === 'no-speech') return again(250);
      if (code === '9' || code === 'not-allowed') { toast('The microphone is not allowed: talk mode stopped.', 'bad'); hfStop(); return; }
      if (code === '5' || code === '8' || code === '2' || code === '1' || /^(aborted|network)$/.test(code)) { if (++HF.errs > 6) { toast('Speech recognition keeps failing' + (code === '2' || code === '1' || code === 'network' ? ' (it may need internet; try "Prefer offline")' : '') + ': talk mode stopped.', 'bad'); hfStop(); return; } return again(900 * HF.errs); }
      if (++HF.errs > 4) { toast('Speech recognition error (' + code + '): talk mode stopped.', 'bad'); hfStop(); return; }
      again(1200);
    }
  });
  setTimeout(() => { if (!settled && HF.on && HF.rec === eng && HF.state === 'listening') again(100); }, 40000);
}

/* ---------------- vault ---------------- */
const vault = { open: new Set(['/']), file: null, dirty: false, preview: false };
const IMG_RE = /\.(png|jpe?g|webp|gif|bmp|svg)$/i, AUDIO_RE = /\.(wav|mp3|ogg|m4a|opus|flac)$/i, VIDEO_RE = /\.(mp4|webm|mov|m4v)$/i;
PAGES.vault = async (v, parts, q) => {
  v.className = 'full';
  v.innerHTML = `<div class="vault" id="vaultWrap"><div class="tree"><div class="row" style="padding:0 6px 8px"><input class="field" id="vSearch" placeholder="Search notes…" style="flex:1"><button class="btn btn-ghost" id="vNew" title="New note">+</button></div><div id="treeBox"></div></div>
    <div class="editor" id="editor"><div class="empty" style="margin:auto">Pick a note on the left, or search.</div></div></div>`;
  $('#vSearch').addEventListener('keydown', async e => { if (e.key !== 'Enter') return; const t = e.target.value.trim(); if (!t) return renderTree(); const r = await api('/v1/vault/search?q=' + encodeURIComponent(t)); $('#treeBox').innerHTML = r.results.map(x => `<button class="tree-row" data-file="${esc(x.path)}" title="${esc(x.snippet)}"><span class="ic">◇</span>${esc(x.path)}</button>`).join('') || '<div class="help" style="padding:8px">Nothing found.</div>'; });
  $('#vNew').onclick = async () => { const p = await ask('New note (path in the vault)', { value: '/Inbox/' + new Date().toISOString().slice(0, 10) + ' note.md', ok: 'Create' }); if (!p) return; await api('/v1/vault/file', { method: 'PUT', body: { path: p, content: '' } }); await renderTree(); openFile(p); };
  $('#treeBox').onclick = async e => {
    const d = e.target.closest('[data-dir]'); if (d) { const p = d.dataset.dir; vault.open.has(p) ? vault.open.delete(p) : vault.open.add(p); return renderTree(); }
    const f = e.target.closest('[data-file]'); if (f) openFile(f.dataset.file);
  };
  await renderTree();
  const want = q.get('open') || (parts.length ? '/' + parts.map(decodeURIComponent).join('/') : '');
  if (want) { const p = want.startsWith('/') ? want : (await api('/v1/vault/search?q=' + encodeURIComponent(want))).results[0]?.path; if (p) openFile(p.endsWith('.md') || /\.\w+$/.test(p) ? p : p + '.md'); }
  else if (vault.file) openFile(vault.file);
};
async function renderTree() {
  const out = [];
  const walk = async (p, depth) => {
    let r; try { r = await api('/v1/vault/list?path=' + encodeURIComponent(p)); } catch { return; }
    for (const it of r.items) {
      const pad = `style="padding-left:${8 + depth * 14}px"`;
      if (it.dir) { const op = vault.open.has(it.path); out.push(`<button class="tree-row" data-dir="${esc(it.path)}" ${pad}><span class="ic">${op ? '▾' : '▸'}</span>${esc(it.name)}</button>`); if (op) await walk(it.path, depth + 1); }
      else out.push(`<button class="tree-row${it.path === vault.file ? ' sel' : ''}" data-file="${esc(it.path)}" ${pad}><span class="ic">${IMG_RE.test(it.name) ? '▣' : it.name.endsWith('.md') ? '◇' : '·'}</span>${esc(it.name)}</button>`);
    }
  };
  await walk('/', 0);
  const el = $('#treeBox'); if (el) el.innerHTML = out.join('') || '<div class="help" style="padding:8px">The vault is empty.</div>';
}
async function openFile(p) {
  if (vault.dirty && vault.file !== p && !(await confirmBox('Leave without saving?', `${vault.file} has changes that are not saved.`, 'Leave'))) return;
  vault.file = p; vault.dirty = false; $('#vaultWrap')?.classList.add('editing');
  $$('#treeBox .tree-row').forEach(r => r.classList.toggle('sel', r.dataset.file === p));
  const ed = $('#editor'); if (!ed) return;
  const f = await api('/v1/vault/file?path=' + encodeURIComponent(p));
  const bar = `<div class="editor-bar"><button class="btn btn-ghost show-sm" id="vBack">‹ Files</button><span class="path" title="${esc(f.path)}">${esc(f.path)}</span>
    ${f.binary ? '' : `<button class="btn btn-ghost" id="vPrev">${vault.preview ? 'Edit' : 'Preview'}</button><button class="btn btn-solid" id="vSave" disabled>Save</button>`}
    <button class="btn btn-ghost" id="vRen">Rename</button><a class="btn btn-ghost" href="${rawUrl(f.path)}&download=1">Download</a><button class="btn btn-red" id="vDel">Delete</button></div>`;
  if (f.binary) {
    const u = rawUrl(f.path);
    ed.innerHTML = bar + `<div class="preview">${IMG_RE.test(p) ? `<img class="full" src="${u}" alt="${esc(f.path)}">` : AUDIO_RE.test(p) ? `<audio controls src="${u}"></audio>` : VIDEO_RE.test(p) ? `<video controls src="${u}" style="max-width:100%"></video>` : `<p class="help">${esc(f.mime || 'binary file')}, ${f.size} bytes.</p>`}</div>`;
  } else {
    ed.innerHTML = bar + `<textarea id="vText" spellcheck="false" aria-label="Note text">${esc(f.content)}</textarea><div class="preview md" id="vPreview" hidden></div>`;
    const ta = $('#vText'); let mtime = f.mtime;
    const showPrev = () => { const pv = $('#vPreview'); pv.hidden = !vault.preview; ta.hidden = vault.preview; if (vault.preview) pv.innerHTML = /\.md$/i.test(p) ? md(ta.value) : `<pre>${esc(ta.value)}</pre>`; $('#vPrev').textContent = vault.preview ? 'Edit' : 'Preview'; };
    if (IMG_RE.test(p) && /\.svg$/i.test(p)) vault.preview = false;
    showPrev();
    ta.addEventListener('input', () => { vault.dirty = true; $('#vSave').disabled = false; });
    const save = async () => { try { const r = await api('/v1/vault/file', { method: 'PUT', body: { path: p, content: ta.value, mtime } }); mtime = r.mtime; vault.dirty = false; $('#vSave').disabled = true; toast('Saved ' + p); } catch (e) { toast(e.message, 'bad'); } };
    $('#vSave').onclick = save;
    ta.addEventListener('keydown', e => { if ((e.ctrlKey || e.metaKey) && e.key === 's') { e.preventDefault(); save(); } });
    $('#vPrev').onclick = () => { vault.preview = !vault.preview; showPrev(); };
  }
  $('#vBack') && ($('#vBack').onclick = () => $('#vaultWrap').classList.remove('editing'));
  $('#vDel').onclick = async () => { if (!(await confirmBox('Delete ' + p + '?', 'It is removed from the vault.', 'Delete', true))) return; await api('/v1/vault/file?path=' + encodeURIComponent(p), { method: 'DELETE' }); vault.file = null; vault.dirty = false; ed.innerHTML = '<div class="empty" style="margin:auto">Deleted.</div>'; renderTree(); };
  $('#vRen').onclick = async () => { const to = await ask('Rename or move to', { value: p, ok: 'Move' }); if (!to || to === p) return; const r = await api('/v1/vault/move', { method: 'POST', body: { from: p, to } }); vault.file = r.path; renderTree(); openFile(r.path); };
}

/* ---------------- flows ---------------- */
PAGES.flows = async v => {
  const [f, r] = await Promise.all([api('/v1/flows'), api('/v1/flows/runs?limit=30')]);
  v.innerHTML = `<h1 class="page">Flows</h1><div class="grid2">
    <div class="pane"><div class="pane-h">Flows</div>${f.flows.map(x => `<div class="row-item"><span class="dot ${x.invalid ? 'bad' : x.enabled ? 'ok' : ''}"></span><div class="col"><span class="t">${esc(x.name)}</span><span class="d">${esc(x.trigger?.type || 'manual')}${x.trigger?.when ? ' · ' + esc(x.trigger.when) : ''} · ${x.steps} steps${x.description ? ' · ' + esc(x.description) : ''}${x.invalid ? ' · ' + esc(x.invalid) : ''}</span></div>
      <button class="btn" data-run="${esc(x.name)}">Run</button><a class="btn btn-ghost" href="#/vault?open=${encodeURIComponent(x.path)}">Edit</a></div>`).join('') || '<div class="empty">No flows yet. Ask Jade to make one.</div>'}</div>
    <div class="pane"><div class="pane-h">Recent runs</div>${r.runs.map(x => `<details class="tool${x.status === 'ok' ? '' : x.status === 'running' ? ' run' : ' err'}"><summary><span class="tname">${esc(x.flow)}</span><span class="targ">${esc(x.via || '')} · ${clock(x.started)}</span><span class="tstat">${esc(x.status)}</span></summary><pre>${esc(JSON.stringify(x.error ? { error: x.error, output: x.output } : x.output, null, 1) || '')}</pre></details>`).join('') || '<div class="empty">No runs yet.</div>'}</div></div>`;
  v.onclick = async e => {
    const n = e.target.closest('[data-run]')?.dataset.run; if (!n) return;
    const inp = await ask(`Run ${n} with input (JSON, optional)`, { area: true, ok: 'Run', placeholder: '{}' }); if (inp === null) return;
    let input; try { input = inp.trim() ? JSON.parse(inp) : undefined; } catch { return toast('That is not valid JSON.', 'bad'); }
    toast('Running ' + n + '…');
    try { const j = await api(`/v1/flows/${encodeURIComponent(n)}/run`, { method: 'POST', body: { input } }); toast(`${n}: ${j.run.status}`); } catch (er) { toast(er.message, 'bad'); }
    route();
  };
};

/* ---------------- schedules ---------------- */
PAGES.schedules = async v => {
  const s = await api('/v1/schedules');
  v.innerHTML = `<h1 class="page">Schedules ${s.daemon ? '<span class="chip ok">running</span>' : '<span class="chip warn">not running: start jade serve or jade daemon</span>'}</h1>
    <div class="pane"><div class="pane-h">New<span class="grow"></span></div><div class="row"><input class="field" id="sWhen" placeholder="every day at 9am" style="flex:1;min-width:160px"><input class="field" id="sWhat" placeholder="what Jade should do" style="flex:2;min-width:200px"><button class="btn btn-solid" id="sAdd">Add</button></div></div>
    <div class="pane" style="margin-top:12px">${s.schedules.map(x => `<div class="row-item"><span class="dot ${x.enabled ? 'ok' : ''}"></span><div class="col"><span class="t">${esc(x.name)}</span><span class="d">${esc(x.when_text || x.cron || '')} · next ${clock(x.next_run)}${x.last_run ? ' · last ' + clock(x.last_run) + ' ' + esc(x.last_status || '') : ''} · ${esc(x.kind)}</span></div>
      <button class="btn btn-ghost" data-act="run" data-id="${esc(x.id)}">Run now</button><button class="btn btn-ghost" data-act="${x.enabled ? 'disable' : 'enable'}" data-id="${esc(x.id)}">${x.enabled ? 'Pause' : 'Resume'}</button><button class="btn btn-red" data-act="remove" data-id="${esc(x.id)}">Delete</button></div>`).join('') || '<div class="empty">No schedules yet.</div>'}</div>`;
  $('#sAdd').onclick = async () => { try { await tool('schedule', { action: 'add', when: $('#sWhen').value, prompt: $('#sWhat').value }); route(); } catch (e) { toast(e.message, 'bad'); } };
  v.onclick = async e => { const b = e.target.closest('[data-act]'); if (!b) return; if (b.dataset.act === 'remove' && !(await confirmBox('Delete this schedule?', '', 'Delete', true))) return; try { const r = await tool('schedule', { action: b.dataset.act, id: b.dataset.id }); toast(String(r).split('\n')[0]); } catch (er) { toast(er.message, 'bad'); } route(); };
};

/* ---------------- Sentry ---------------- */
const SY_PATH = '/Jade/Config/Sentry.json';
async function sentryFile() { try { return JSON.parse((await api('/v1/vault/file?path=' + encodeURIComponent(SY_PATH))).content); } catch { return { enabled: false, systems: {}, triggers: [] }; } }
async function sentrySave(fn) { const c = await sentryFile(); fn(c); await api('/v1/vault/file', { method: 'PUT', body: { path: SY_PATH, content: JSON.stringify(c, null, 2) + '\n' } }); }
PAGES.sentry = async v => {
  const [s, ph] = await Promise.all([api('/v1/sentry'), api('/v1/phones')]);
  const cfg = s.rules || {}; const devs = cfg.phones || []; const linked = ph.phones.map(p => p.name);
  const runOn = devs.length ? devs : linked;
  const sysState = (id, dev) => { const d = (cfg.devices || {})[dev]?.systems || {}; return id in d ? (d[id] ? 'on' : 'off') : 'inherit'; };
  const sysRow = x => `<tr><td><b>${esc(x.id)}</b><div class="help">${esc(x.state)} · battery ${esc(x.battery)}${x.sensitive ? ' · sensitive' : ''}</div></td>
    <td><label class="switch"><input type="checkbox" data-sys="${esc(x.id)}"${x.on ? ' checked' : ''}><span></span></label></td>
    ${runOn.map(d => `<td><select class="field" data-sysdev="${esc(x.id)}" data-dev="${esc(d)}" style="width:auto;padding:4px">${['inherit', 'on', 'off'].map(o => `<option${sysState(x.id, d) === o ? ' selected' : ''}>${o}</option>`).join('')}</select></td>`).join('')}</tr>`;
  v.innerHTML = `<h1 class="page">Sentry <label class="switch" title="Sentry on or off"><input type="checkbox" id="syOn"${s.sentry === 'on' ? ' checked' : ''}><span></span></label><span class="chip ${s.sentry === 'on' ? 'ok' : ''}">${s.sentry}</span>
      <span class="grow"></span><a class="btn btn-ghost" href="#/vault?open=${encodeURIComponent(SY_PATH)}">Edit Sentry.json</a></h1>
    ${(s.problems || []).length ? `<div class="pane" style="border-color:var(--rd)">${s.problems.map(p => `<div>⚠ ${esc(p)}</div>`).join('')}</div>` : ''}
    <div class="pane"><div class="pane-h">Runs on</div><p class="help">Which phones and tablets watch. None ticked means every linked one.</p><div class="row">
      ${linked.map(n => `<label class="row" style="gap:6px"><input type="checkbox" data-dev="${esc(n)}"${devs.includes(n) ? ' checked' : ''}> ${esc(n)} ${typeof s.phones === 'object' && s.phones[n] ? `<span class="chip ${s.phones[n].service ? 'ok' : ''}">${s.phones[n].service ? 'watching' : s.phones[n].online ? 'idle' : 'offline'}</span>` : ''}</label>`).join('') || '<span class="help">No phone is linked yet.</span>'}</div></div>
    <div class="grid2" style="margin-top:12px">
      <div class="pane"><div class="pane-h">Rules</div>${(s.triggers || []).map(t => `<div class="row-item"><span class="dot ${t.enabled ? 'ok' : ''}"></span><div class="col"><span class="t">${esc(t.name || t.id)}</span><span class="d">uses ${esc((t.needs || []).join(', ') || 'events')} · on ${esc(Array.isArray(t.phones) ? t.phones.join(', ') : 'all devices')}${t.flow ? ' · runs ' + esc(t.flow) : ''}${t.off_systems?.length ? ' · <b>needs ' + esc(t.off_systems.join(', ')) + ' switched on</b>' : ''}</span></div>
        <button class="btn btn-ghost" data-t="${t.enabled ? 'disable' : 'enable'}" data-id="${esc(t.id)}">${t.enabled ? 'Pause' : 'Resume'}</button>${t.flow ? `<button class="btn btn-ghost" data-t="test" data-id="${esc(t.id)}">Test</button>` : ''}<button class="btn btn-red" data-t="remove" data-id="${esc(t.id)}">✕</button></div>`).join('') || '<div class="empty">No rules yet. Ask Jade: “when my phone is face down and dark, buzz it”.</div>'}</div>
      <div class="pane"><div class="pane-h">Log</div><pre class="log" style="max-height:340px">${esc((s.log || []).map(x => `${new Date(x.at * 1000).toLocaleTimeString()} ${x.kind} ${x.id || ''}${x.phone ? ' @' + x.phone : ''}${x.flow ? ' → ' + x.flow : ''}${x.error ? ' ⚠ ' + x.error : ''}`).join('\n') || 'Nothing has fired yet.')}</pre></div></div>
    <div class="pane" style="margin-top:12px"><div class="pane-h">Systems</div><p class="help">A system only runs where it is switched on and a rule uses it. Per device: inherit takes the switch on the left.</p>
      <div class="scroll-x"><table class="t"><tr><th>System</th><th>All</th>${runOn.map(d => `<th>${esc(d)}</th>`).join('')}</tr>${(s.systems || []).map(sysRow).join('')}</table></div></div>`;
  const save = async fn => { try { await sentrySave(fn); await sleep(150); route(); } catch (e) { toast(e.message, 'bad'); } };
  $('#syOn').onchange = e => save(c => { c.enabled = e.target.checked; });
  v.onchange = e => {
    const t = e.target;
    if (t.dataset.dev && t.type === 'checkbox') save(c => { const l = new Set(c.phones || []); t.checked ? l.add(t.dataset.dev) : l.delete(t.dataset.dev); c.phones = [...l]; if (!c.phones.length) delete c.phones; });
    else if (t.dataset.sys) save(c => { (c.systems ||= {})[t.dataset.sys] = t.checked; });
    else if (t.dataset.sysdev) save(c => { const d = ((c.devices ||= {})[t.dataset.dev] ||= {}); d.systems ||= {}; if (t.value === 'inherit') delete d.systems[t.dataset.sysdev]; else d.systems[t.dataset.sysdev] = t.value === 'on'; });
  };
  v.onclick = async e => { const b = e.target.closest('[data-t]'); if (!b) return; if (b.dataset.t === 'remove' && !(await confirmBox('Remove this rule?', '', 'Remove', true))) return; try { const r = await tool('sentry', { action: b.dataset.t, id: b.dataset.id }); if (b.dataset.t === 'test') toast(String(r).slice(0, 200)); } catch (er) { toast(er.message, 'bad'); } route(); };
};

/* ---------------- phones and tablets ---------------- */
PAGES.phones = async v => {
  const p = await api('/v1/phones');
  const quick = [['Ring', 'phone', { action: 'ring', seconds: 15 }], ['Stop ring', 'phone', { action: 'ring_stop' }], ['Where?', 'senses', { action: 'location' }], ['Battery', 'senses', { action: 'battery' }], ['Screenshot', 'screen', { action: 'screenshot', look: false }], ['Photo', 'senses', { action: 'camera_snap', look: false }], ['Torch', 'senses', { action: 'torch', on: true, seconds: 20 }], ['Buzz', 'senses', { action: 'vibrate', ms: 600 }]];
  const card = x => `<div class="pane"><div class="pane-h"><span class="dot ${x.online ? 'ok' : 'bad'}"></span>${esc(x.name)}<span class="grow"></span><span class="chip">${esc(x.via)}</span></div>
    <div class="help">${esc(x.model || 'unknown model')}${x.android ? ' · Android ' + esc(x.android) : ''}${x.app ? ' · Jade Link ' + esc(x.app) : ''} · ${x.online ? 'online' : 'last seen ' + ago(x.last_seen)}${x.error ? ' · ' + esc(x.error) : ''}</div>
    <div class="row" style="margin-top:10px">${quick.map(([l], i) => `<button class="btn btn-ghost" data-q="${i}" data-p="${esc(x.name)}"${x.online ? '' : ' disabled'}>${l}</button>`).join('')}</div><div class="out" data-out="${esc(x.name)}"></div></div>`;
  v.innerHTML = `<h1 class="page">Phones and tablets <span class="chip">${p.phones.filter(x => x.online).length}/${p.phones.length} online</span></h1>
    ${p.phones.length > 1 ? `<div class="pane"><div class="pane-h">All at once</div><div class="row">${quick.map(([l], i) => `<button class="btn" data-q="${i}" data-p="all">${l}</button>`).join('')}</div><div class="out" data-out="all"></div></div>` : ''}
    <div class="grid2" style="margin-top:12px">${p.phones.map(card).join('') || '<div class="empty">No phone or tablet yet.</div>'}</div>
    <div class="pane" style="margin-top:12px"><div class="pane-h">Add a phone or tablet</div>
      <p><b>It calls in</b> (works anywhere it can reach this computer): install Jade Link, open it, and enter</p>
      <div class="row"><code class="chip" style="font-size:12px">${esc(location.origin)}</code><span class="help">and the API token</span>${IN_APP ? '' : `<button class="btn btn-ghost" id="showTok">Show token</button>`}</div>
      <p style="margin-top:12px"><b>Or this computer calls it</b> (same network or a VPN): in Jade Link turn on “Listen”, then add it here.</p>
      <div class="row"><input class="field" id="lpName" placeholder="name (tablet)" style="width:140px"><input class="field" id="lpUrl" placeholder="http://192.168.1.20:8765" style="flex:1;min-width:200px"><input class="field" id="lpTok" placeholder="its listening token" style="flex:1;min-width:160px"><button class="btn btn-solid" id="lpAdd">Add</button></div>
      <p class="help">Up to four (or more) phones and tablets can be linked at once. Jade can use one, several (“pixel, tablet”) or all of them at the same time.</p></div>`;
  $('#showTok') && ($('#showTok').onclick = e => { e.target.outerHTML = `<code class="chip">${esc(TOKEN)}</code>`; });
  $('#lpAdd').onclick = async () => {
    const name = $('#lpName').value.trim(), url = $('#lpUrl').value.trim(), token = $('#lpTok').value.trim(); if (!name || !url || !token) return toast('Name, address and token are all needed.', 'bad');
    const st = await api('/v1/settings'); const list = (st.values.phones || []).filter(x => x.name !== name).concat([{ name, url, token }]);
    try { await api('/v1/settings', { method: 'PATCH', body: { phones: list } }); toast('Added ' + name); route(); } catch (e) { toast(e.message, 'bad'); }
  };
  v.onclick = async e => {
    const b = e.target.closest('[data-q]'); if (!b) return; const [label, t, args] = quick[+b.dataset.q]; const out = $(`[data-out="${CSS.escape(b.dataset.p)}"]`);
    b.disabled = true; out.innerHTML = `<p class="help">${esc(label)}…</p>`;
    try {
      const r = await tool(t, { ...args, phone: b.dataset.p });
      const imgs = [...String(r).matchAll(/"(\/attachments\/phone\/[^"]+\.(?:jpg|png))"/g)].map(m => m[1]);
      out.innerHTML = (imgs.length ? `<div class="pics" style="margin:8px 0">${imgs.map(i => `<img src="${rawUrl(i)}" alt="${esc(i)}">`).join('')}</div>` : '') + `<pre class="log" style="max-height:220px;margin-top:8px">${esc(String(r).slice(0, 4000))}</pre>`;
      out.onclick = ev => { const im = ev.target.closest('img'); if (im) modal(`<img src="${im.src}" alt="">`); };
    } catch (er) { out.innerHTML = `<p class="help" style="color:var(--rd)">${esc(er.message)}</p>`; }
    b.disabled = false;
  };
};

/* ---------------- logs ---------------- */
PAGES.logs = async (v, parts) => {
  const l = await api('/v1/logs'); const name = parts[0] || l.logs[0]?.name;
  v.innerHTML = `<h1 class="page">Logs</h1><div class="row" style="margin-bottom:10px">${l.logs.map(x => `<a class="btn ${x.name === name ? 'btn-solid' : 'btn-ghost'}" href="#/logs/${esc(x.name)}">${esc(x.name)}</a>`).join('') || '<span class="help">No logs yet.</span>'}<span class="grow"></span>${name ? '<button class="btn btn-ghost" id="logRef">Refresh</button>' : ''}</div><pre class="log" id="logBox" style="max-height:calc(100vh - 200px)"></pre>`;
  const load = async () => { if (!name) return; const r = await api(`/v1/logs/${encodeURIComponent(name)}?lines=500`); const b = $('#logBox'); if (b) { b.textContent = r.lines.join('\n') || '(empty)'; b.scrollTop = b.scrollHeight; } };
  $('#logRef') && ($('#logRef').onclick = load); await load();
};

/* ---------------- settings ---------------- */
const GROUPS = [
  ['The model', ['provider', 'model', 'host', 'base_url', 'api_key', 'fallbacks', 'temperature', 'num_ctx', 'think', 'keep_alive', 'request_timeout']],
  ['The agent', ['approve', 'max_steps', 'tool_router', 'bash_timeout', 'shell', 'max_tool_output', 'memory_lines', 'journal', 'think_on', 'delegate_on', 'timezone']],
  ['Hearing (talk mode through jade-py)', ['stt_provider', 'stt_model']],
  ['Phones, tablets and Sentry', ['phones', 'link_on', 'phone_default', 'phone_confirm', 'sentry_phone', 'sentry_allow', 'sentry_limit', 'look_images']],
  ['Heartbeat', ['hb_on', 'hb_every', 'hb_start', 'hb_end', 'hb_prompt']],
  ['Curiosity', ['cu_on', 'cu_min', 'cu_max', 'cu_from', 'cu_to', 'cu_cap', 'cu_tools', 'cu_acts_off']],
  ['Backup', ['ab_on', 'ab_url', 'ab_token', 'ab_hours', 'ab_kind', 'ab_keys', 'ab_encrypt', 'ab_pass']],
  ['Web and search', ['search', 'searx_url', 'ollama_key', 'user_agent', 'browser_on', 'browser_path', 'browser_headless']],
  ['Embeddings and decisions', ['embed_on', 'embed_model', 'embed_host', 'ollaya_host', 'ollaya_model', 'ollaya_key']],
  ['Messengers', ['vector', 'discord']],
  ['Machines and secrets', ['ssh_hosts', 'secrets']],
  ['Server', ['vault', 'data_dir', 'api_host', 'api_port', 'api_token']],
];
function fieldKind(sch) {
  const t = sch.type || sch.anyOf?.map(x => x.type).find(x => x && x !== 'null');
  const nullable = !!sch.anyOf?.some(x => x.type === 'null');
  if (nullable && t === 'boolean') return 'tri';
  if (nullable && (t === 'string' || t === 'integer' || t === 'number')) return t === 'string' ? 'nstr' : 'num';
  if (sch.enum || sch.anyOf?.some(x => x.enum)) return 'enum';
  if (t === 'boolean') return 'bool'; if (t === 'integer' || t === 'number') return 'num';
  if (t === 'array' && (sch.items?.type === 'string' || sch.anyOf?.some(x => x.items?.type === 'string'))) return 'list';
  if (t === 'string') return 'str'; return 'json';
}
PAGES.settings = async v => {
  const st = await api('/v1/settings'); const props = st.schema.properties || {}; const vals = st.values;
  const voices = (() => { try { return speechSynthesis.getVoices(); } catch { return []; } })();
  const row = (k) => {
    const sch = props[k]; if (!sch) return ''; const val = vals[k]; const kind = fieldKind(sch); const id = 'set_' + k; const help = sch.description ? `<p class="help">${esc(sch.description)}</p>` : '';
    let input;
    if (kind === 'bool') input = `<label class="switch"><input type="checkbox" id="${id}" data-k="${k}" data-kind="bool"${val ? ' checked' : ''}><span></span></label>`;
    else if (kind === 'tri') input = `<select class="field" id="${id}" data-k="${k}" data-kind="tri" style="width:auto">${[['', 'default'], ['true', 'yes'], ['false', 'no']].map(([o, l]) => `<option value="${o}"${String(val ?? '') === o ? ' selected' : ''}>${l}</option>`).join('')}</select>`;
    else if (kind === 'nstr') input = `<input class="field" id="${id}" data-k="${k}" data-kind="nstr" value="${esc(val ?? '')}" placeholder="(not set)">`;
    else if (kind === 'enum') { const opts = sch.enum || sch.anyOf.flatMap(x => x.enum || []); input = `<select class="field" id="${id}" data-k="${k}" data-kind="enum">${opts.map(o => `<option${o === val ? ' selected' : ''}>${esc(o)}</option>`).join('')}</select>`; }
    else if (kind === 'num') input = `<input class="field" type="number" step="any" id="${id}" data-k="${k}" data-kind="num" value="${esc(val ?? '')}">`;
    else if (kind === 'list') input = `<input class="field" id="${id}" data-k="${k}" data-kind="list" value="${esc((val || []).join(', '))}">`;
    else if (kind === 'str') input = `<input class="field" id="${id}" data-k="${k}" data-kind="str" value="${esc(val ?? '')}"${/(token|key|pass)$/.test(k) ? ' autocomplete="off"' : ''}>`;
    else input = `<textarea class="field" rows="${Math.min(10, 2 + JSON.stringify(val ?? null, null, 1).split('\n').length)}" id="${id}" data-k="${k}" data-kind="json">${esc(JSON.stringify(val ?? null, null, 1))}</textarea>`;
    return `<div style="margin-top:8px"><label class="lbl" for="${id}">${esc(k)}${st.restart.includes(k) ? ' · restart' : ''}</label>${input}${help}</div>`;
  };
  const used = new Set(GROUPS.flatMap(g => g[1])); const other = Object.keys(props).filter(k => !used.has(k));
  v.innerHTML = `<h1 class="page">Settings</h1>
    <div class="pane cut"><div class="pane-h">This device: talk mode, voice and avatar</div><p class="help">Kept in this browser${IN_APP ? ' (this phone)' : ''} only.</p>
      <div class="grid2">
        <div><label class="lbl" for="uWake">Wake phrase (talk mode waits for it)</label><input class="field" id="uWake" value="${esc(S.wakeWords)}" placeholder="hey jade"><p class="help">Several: separate with commas. Empty: she listens to everything.</p>
          <label class="row" style="margin-top:10px"><input type="checkbox" id="uSolo"${S.hfSolo !== false ? ' checked' : ''}> Talk mode shows only her avatar (tap ✕, double-tap, or Back to leave)</label>
          <label class="row" style="margin-top:6px"><input type="checkbox" id="uSttServer"${S.sttServer ? ' checked' : ''}> Use jade-py to hear (its transcription model, below) instead of ${IN_APP ? 'the phone\'s' : 'the browser\'s'} recognizer</label>
          ${IN_APP ? `<label class="row" style="margin-top:6px"><input type="checkbox" id="uOffline"${S.sttOffline ? ' checked' : ''}> Prefer offline recognition</label>` : ''}
          <label class="lbl" for="uLang">Language (for hearing)</label><input class="field" id="uLang" value="${esc(S.voiceLang)}" placeholder="en-US (blank: the device's)"></div>
        <div>${IN_APP ? '<p class="help">Her voice is the phone\'s text-to-speech (change it in Android settings).</p>' : `<label class="lbl" for="uVoice">Her voice</label><select class="field" id="uVoice"><option value="">Pick for me</option>${voices.map(x => `<option${x.name === S.voiceName ? ' selected' : ''}>${esc(x.name)}</option>`).join('')}</select>`}
          <label class="lbl" for="uRate">Speed ${esc(S.voiceRate)}</label><input type="range" id="uRate" min="0.6" max="1.6" step="0.01" value="${esc(S.voiceRate)}" style="width:100%">
          <label class="lbl" for="uPitch">Pitch ${esc(S.voicePitch)}</label><input type="range" id="uPitch" min="0.6" max="1.8" step="0.01" value="${esc(S.voicePitch)}" style="width:100%">
          <div class="row" style="margin-top:10px"><button class="btn" id="uTest">Hear her</button><button class="btn" id="uTheme">Her colours and look</button><label class="row"><input type="checkbox" id="uShow"${S.jadeShow ? ' checked' : ''}> Show her avatar</label></div></div></div>
      ${IN_APP && naHas('setKiosk') ? `<div class="row-item" style="margin-top:12px"><div class="col"><span class="t">Kiosk mode</span><span class="d" id="kioskSt">…</span></div><label class="switch"><input type="checkbox" id="uKiosk"><span></span></label><button class="btn btn-ghost" id="uSetup">Phone setup</button></div>` : ''}
    </div>
    ${GROUPS.map(([title, keys]) => `<details class="pane" style="margin-top:12px"${title === 'The model' ? ' open' : ''}><summary class="pane-h" style="margin:0">${esc(title)}</summary>${keys.map(row).join('')}</details>`).join('')}
    ${other.length ? `<details class="pane" style="margin-top:12px"><summary class="pane-h" style="margin:0">Other</summary>${other.map(row).join('')}</details>` : ''}
    <div class="row" style="margin:14px 0 30px;position:sticky;bottom:0;background:var(--void);padding:8px 0"><button class="btn btn-solid" id="saveSet">Save jade-py settings</button><span class="help">Saved to ${esc(st.config)}</span></div>`;
  // this device
  const bindU = (id, key, conv = x => x) => { const e = $('#' + id); if (!e) return; e.addEventListener(e.type === 'checkbox' ? 'change' : 'input', () => { S[key] = e.type === 'checkbox' ? e.checked : conv(e.value); saveSettings(); if (id === 'uRate' || id === 'uPitch') e.previousElementSibling.textContent = (id === 'uRate' ? 'Speed ' : 'Pitch ') + e.value; }); };
  bindU('uWake', 'wakeWords'); bindU('uSolo', 'hfSolo'); bindU('uSttServer', 'sttServer'); bindU('uOffline', 'sttOffline'); bindU('uLang', 'voiceLang'); bindU('uVoice', 'voiceName'); bindU('uRate', 'voiceRate', Number); bindU('uPitch', 'voicePitch', Number); bindU('uShow', 'jadeShow');
  $('#uTest').onclick = async () => { await loadAvatar(); window.Jade?.speak?.('Hi. This is how I sound on this device.', { voice: S.voiceName, rate: +S.voiceRate, pitch: +S.voicePitch }) || toast('This browser cannot speak.', 'bad'); };
  $('#uTheme').onclick = () => openThemePicker();
  if ($('#uKiosk')) {
    const ks = () => { let k = {}; try { k = JSON.parse(NA().kioskStatus()); } catch {} $('#uKiosk').checked = !!k.on; $('#kioskSt').textContent = !k.on ? 'Off: Jade Link is an ordinary app.' : k.locked ? 'On and locked' + (k.owner ? ' (device owner).' : '.') : k.pinned ? 'On: pinned to the screen.' : 'On: Jade Link is the home screen.'; };
    ks(); $('#uKiosk').onchange = e => { NA().setKiosk(e.target.checked); setTimeout(ks, 900); };
    $('#uSetup').onclick = () => NA().setup?.();
  }
  // jade-py
  $('#saveSet').onclick = async () => {
    const body = {};
    for (const e of $$('[data-k]', v)) {
      const k = e.dataset.k, kind = e.dataset.kind; let val;
      if (kind === 'bool') val = e.checked; else if (kind === 'num') val = e.value === '' ? null : Number(e.value);
      else if (kind === 'tri') val = e.value === '' ? null : e.value === 'true';
      else if (kind === 'nstr') val = e.value === '' ? null : e.value;
      else if (kind === 'list') val = e.value.split(',').map(x => x.trim()).filter(Boolean);
      else if (kind === 'json') { try { val = JSON.parse(e.value); } catch { return toast(`${k} is not valid JSON`, 'bad'); } } else val = e.value;
      if (JSON.stringify(val ?? null) !== JSON.stringify(vals[k] ?? null) && !(kind === 'str' && !e.value && !vals[k])) body[k] = val;
    }
    if (!Object.keys(body).length) return toast('Nothing changed.');
    try { const r = await api('/v1/settings', { method: 'PATCH', body }); toast(`Saved: ${r.changed.join(', ')}${r.notes?.length ? '. ' + r.notes.join('; ') : ''}`); route(); } catch (e) { toast(e.message, 'bad'); }
  };
};

/* ---------------- start ---------------- */
async function boot() {
  if (IN_APP) { $('#phoneSetup').hidden = false; $('#phoneSetup').onclick = e => { if (naHas('setup')) { e.preventDefault(); NA().setup(); } }; $('#logoutBtn').hidden = true; }
  document.body.insertAdjacentHTML('beforeend', drawerHtml());
  $('#drawerBtn').onclick = openDrawer; $('#drawerBg').onclick = closeDrawer; $('#drawerX').onclick = closeDrawer;
  $('#drNew').onclick = () => { closeDrawer(); location.hash = '#/chat/new'; };
  $('#drSetup') && ($('#drSetup').onclick = e => { e.preventDefault(); NA().setup?.(); });
  if (sessionStorage.getItem('jade:kiosk') === '1') document.body.classList.add('kiosk');
  try { const s = await api('/v1/status'); $('#modelName').textContent = s.model; } catch (e) { if (e.status === 401) return; }
  try { const a = await api('/v1/approvals'); for (const x of a.approvals) approvals.set(x.id, x); renderApprovals(); } catch {}
  eventsLoop();
  loadTheme().catch(() => {});
  route();
}
if (!TOKEN) api('/v1/status').then(boot, () => {}); else boot();
