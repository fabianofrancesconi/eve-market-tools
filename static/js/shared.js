const $ = s => document.querySelector(s);
const COL_LAYOUT_VERSION = 6;

// Session-expiry handling (multi-user deploy): the server returns
// 401 {login_required:true} once a session cookie is gone/expired. Wrap fetch so
// any API call hitting that shows a one-time "log in again" banner instead of
// failing silently with confusing errors.
const _origFetch = window.fetch.bind(window);
let _sessionExpiredShown = false;
async function _onSessionExpired(){
  if(_sessionExpiredShown) return; _sessionExpiredShown = true;
  const bar = document.createElement('div');
  bar.style.cssText = 'position:fixed;top:0;left:0;right:0;z-index:99999;'
    + 'background:#7a1f1f;color:#fff;padding:10px 16px;text-align:center;'
    + 'font:14.5px system-ui,sans-serif';
  bar.innerHTML = 'Your session expired. <a href="#" style="color:#ffe08a">Log in with EVE again</a>';
  bar.querySelector('a').onclick = async (e) => {
    e.preventDefault();
    try { const j = await (await _origFetch('/api/auth/login')).json();
          if(j.url) location.href = j.url; } catch(_) { location.reload(); }
  };
  document.body.appendChild(bar);
}
window.fetch = async function(...args){
  const r = await _origFetch(...args);
  if(r.status === 401){
    try { const j = await r.clone().json(); if(j && j.login_required) _onSessionExpired(); }
    catch(_) {}
  }
  return r;
};
function postPrefs(path,params){fetch(path,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(params)}).catch(()=>{});}
function startResize(e,key,ctx){
  e.preventDefault(); e.stopPropagation();
  ctx.resizing=true;
  e.target.classList.add("active");
  document.body.classList.add("col-resizing");
  $(ctx.tblSel).style.tableLayout="fixed";
  const startX=e.clientX, startW=ctx.colw[key]||80;
  function mm(ev){ ctx.colw[key]=Math.max(40,startW+(ev.clientX-startX)); ctx.setCg(); }
  function mu(){
    document.removeEventListener("mousemove",mm);
    document.removeEventListener("mouseup",mu);
    e.target.classList.remove("active");
    document.body.classList.remove("col-resizing");
    if(ctx.save) ctx.save();
    setTimeout(()=>{ ctx.resizing=false; },0);
  }
  document.addEventListener("mousemove",mm);
  document.addEventListener("mouseup",mu);
}

// Tax / broker are shown to the user as percent (4.5) but stored & sent to the
// backend as fractions (0.045). Convert at the input boundary only.
function pctToFrac(v){ const n=parseFloat(v); return isNaN(n)?"":String(n/100); }
function fracToPct(v){ const n=parseFloat(v); return isNaN(n)?"":String(+(n*100).toFixed(4)); }

// Units of a material consumed by an N-run job after Material Efficiency.
// MUST mirror ind_core.effective_qty: EVE applies ME to the WHOLE job and
// rounds up ONCE, with a floor of one unit per run. Rounding base*runs*(1-ME/100)
// to 2 dp first absorbs float noise (matches the in-game numbers). This is used
// to rescale the batch shopping list live when the run count changes, so it must
// agree with the server's frozen eff_qty_batch to the unit.
function effectiveQty(baseQty, me, runs){
  runs = Math.max(1, runs||1);
  const raw = Math.round(baseQty * runs * (1 - (me||0) / 100.0) * 100) / 100;
  return Math.max(runs, Math.ceil(raw));
}

// ── Shared utils ─────────────────────────────────────────────────────────
function fmtISK(n){
  if(n===null||n===undefined) return "-";
  const a=Math.abs(n);
  if(a>=1e9) return (n/1e9).toFixed(2)+"B";
  if(a>=1e6) return (n/1e6).toFixed(2)+"M";
  if(a>=1e3) return (n/1e3).toFixed(1)+"K";
  return Math.round(n).toLocaleString();
}
function fmtNum(n){ return (n===null||n===undefined)? "-" : Math.round(n).toLocaleString(); }
function fmtVol(n){ return (n===null||n===undefined)? "?" : n.toLocaleString(undefined,{maximumFractionDigits:1})+" m³"; }
function fmtSpread(s){ return s===null? "no bid" : Math.round(s)+"%"; }
// Days-to-clear. capped_profit===null is the "not fetched yet" sentinel (the
// background /api/liquidity call hasn't landed); daily_vol distinguishes "never
// traded" (null) from "history exists but no recent volume" (0).
const _SPIN = "<span class='spin'></span>";
function fmtDays(v,r){
  if(!r.liq_loaded) return _SPIN;
  if(r.daily_vol===null) return "no data";
  if(r.daily_vol===0) return "∞";
  return v<1 ? "<1 d" : Math.round(v)+" d";
}
function fmtVolPerDay(v,r){
  if(!r.liq_loaded) return _SPIN;
  return v===null ? "no data" : fmtNum(v)+"/d";
}
// Suggested per-unit list price — needs market history, so it rides the same
// background /api/liquidity fill (spinner until it lands).
function fmtListPrice(v,r){
  if(!r.liq_loaded) return _SPIN;
  return (v===null||v===undefined) ? "no data" : fmtISK(v);
}
// Age of the current cheapest sell order ("8h ago"). Also from the background
// fill (one live order-book call per type), so spinner until it lands.
function fmtFloorAge(v,r){
  if(!r.liq_loaded) return _SPIN;
  return (v===null||v===undefined) ? "no orders" : fmtAgo(v);
}
// Tradeability: 0–100 blend of liquidity + low-competition, color-graded red→green.
function fmtTrade(v,r){
  if(!r.liq_loaded) return _SPIN;
  if(v===null||v===undefined) return "—";
  return `<span style="color:hsl(${Math.round(v*1.2)},70%,58%);font-weight:600">${Math.round(v)}</span>`;
}
function fmtTs(epoch){
  if(!epoch) return "unknown";
  return fmtAgo(Math.round((Date.now()/1000)-epoch));
}
// A raw age in seconds → "8h ago" / "3d ago".
function fmtAgo(sec){
  if(sec===null||sec===undefined) return "unknown";
  sec=Math.round(sec);
  if(sec<5) return "just now";
  if(sec<60) return `${sec}s ago`;
  if(sec<3600) return `${Math.floor(sec/60)}m ago`;
  if(sec<86400) return `${Math.floor(sec/3600)}h ago`;
  return `${Math.floor(sec/86400)}d ago`;
}
function setStatus(html,err){
  const s=$("#statusbar"); s.innerHTML=html; s.className=err?"err":"";
}
function persistScan(tab, blob){
  if(!blob) return;
  navigator.sendBeacon("/api/save-scan", new Blob(
    [JSON.stringify({tab, blob})], {type:"application/json"}));
}
function persistAllScans(){
  if(STATE.lastScanData && STATE.rows.length)
    persistScan("lp", {...STATE.lastScanData, rows:STATE.rows});
  if(IND.lastData && IND.rows.length && !IND.lastData.favorites_only && !IND.lastData.owned_only)
    persistScan("ind", {...IND.lastData, rows:IND.rows});
}
document.addEventListener("visibilitychange",()=>{ if(document.visibilityState==="hidden") persistAllScans(); });
window.addEventListener("beforeunload", persistAllScans);

// ── Server-authoritative settings ────────────────────────────────────────────
// The server owns every user preference. The browser NEVER pushes a whole
// snapshot of its state and NEVER overwrites the server: each change sends only
// the one key it touched (setPref), and the server merges it into its own row.
// On boot loadSettings() pulls the authoritative state and applies it. There is
// no localStorage settings cache and no "which copy wins" logic — the server
// always wins, so opening the app on another machine just shows the same thing.
//
// SETTINGS mirrors the server's prefs map in memory purely so a change to one
// field can be sent without re-reading the DOM for the others; it is seeded from
// the server on load and updated as the user acts. It is NEVER the source of
// truth — the server is.
const SETTINGS = { prefs:{}, favorites:[], profiles:[] };

// Per-key debounce: each distinct pref key coalesces its own rapid edits (e.g.
// typing in a number field) on an independent timer, so a burst of changes to
// DIFFERENT keys all get sent — one never cancels another. Values are stored
// with their JSON type intact; passing null deletes the key.
const _prefTimers = {};
function _sendPref(key, value){
  const patch = {}; patch[key] = value;
  return fetch('/api/prefs', { method:'POST', keepalive:true,
    headers:{'Content-Type':'application/json'},
    body: JSON.stringify({ patch: JSON.stringify(patch) }) }).catch(()=>{});
}
// setPref(key, value): the single funnel for persisting a preference. Updates the
// in-memory mirror immediately, then debounces the server write for that key.
// Set _settingsReady false during boot so applying stored values back into the
// DOM doesn't echo them straight back to the server.
//
// No-op when the value is unchanged from what's already stored: idempotent
// callers (e.g. the character refresh re-deriving tax/broker from skills on every
// poll) then neither spam the server nor overwrite an identical stored value.
let _settingsReady = false;
function setPref(key, value, opts){
  if(value===undefined) value=null;
  const cur = key in SETTINGS.prefs ? SETTINGS.prefs[key] : null;
  if(JSON.stringify(cur)===JSON.stringify(value)) return;   // unchanged → nothing to do
  if(value===null) delete SETTINGS.prefs[key]; else SETTINGS.prefs[key]=value;
  if(!_settingsReady) return;
  const delay = (opts && opts.immediate) ? 0 : 400;
  clearTimeout(_prefTimers[key]);
  _prefTimers[key] = setTimeout(()=>{ delete _prefTimers[key]; _sendPref(key, value); }, delay);
}
function getPref(key, dflt){
  const v = SETTINGS.prefs[key];
  return v===undefined ? dflt : v;
}
// Flush any pending debounced pref writes immediately (page hide/close) so
// another device never opens to a stale view.
function flushPrefs(){
  for(const key in _prefTimers){
    clearTimeout(_prefTimers[key]); delete _prefTimers[key];
    _sendPref(key, SETTINGS.prefs[key]===undefined ? null : SETTINGS.prefs[key]);
  }
}
window.addEventListener('pagehide', flushPrefs);
document.addEventListener('visibilitychange', ()=>{
  if(document.visibilityState==='hidden') flushPrefs();
});

// Favorites (Industry watchlist) — each toggle is its own server row. Keep the
// in-memory mirror in step with the write so SETTINGS never drifts from truth.
function setFavorite(bp, on){
  bp = +bp;
  const i = SETTINGS.favorites.indexOf(bp);
  if(on && i<0) SETTINGS.favorites.push(bp);
  else if(!on && i>=0) SETTINGS.favorites.splice(i,1);
  fetch('/api/favorites', { method:'POST', keepalive:true,
    headers:{'Content-Type':'application/json'},
    body: JSON.stringify({ blueprint_id: bp, on: on?1:0 }) }).catch(()=>{});
}
// Build-location profiles — each save/delete targets one profile row. The
// server echoes the full authoritative list back, so adopt it as the mirror.
function saveProfile(profile){
  const i = SETTINGS.profiles.findIndex(p=>p.profile_id===profile.profile_id);
  if(i>=0) SETTINGS.profiles[i]=profile; else SETTINGS.profiles.push(profile);
  return fetch('/api/profiles/save', { method:'POST',
    headers:{'Content-Type':'application/json'},
    body: JSON.stringify({ profile: JSON.stringify(profile) }) })
    .then(r=>r.json()).then(j=>{ if(j&&j.profiles) SETTINGS.profiles=j.profiles; return j; })
    .catch(()=>null);
}
function deleteProfile(profileId){
  SETTINGS.profiles = SETTINGS.profiles.filter(p=>p.profile_id!==profileId);
  return fetch('/api/profiles/delete', { method:'POST',
    headers:{'Content-Type':'application/json'},
    body: JSON.stringify({ profile_id: profileId }) })
    .then(r=>r.json()).then(j=>{ if(j&&j.profiles) SETTINGS.profiles=j.profiles; return j; })
    .catch(()=>null);
}
function markSettingsApplied(){ _settingsReady=true; }

// ── Tab switching ─────────────────────────────────────────────────────────
let ACTIVE_TAB = "lp";
// Each tab has a clean URL so a refresh/bookmark reopens the same module.
const TAB_PATH = { lp:"/", arb:"/arbitrage", ind:"/industry", char:"/character", notes:"/notes", exp:"/exploration", aby:"/abyss" };
const PATH_TAB = { "/":"lp", "/lp":"lp", "/arbitrage":"arb", "/arb":"arb",
                   "/industry":"ind", "/ind":"ind",
                   "/character":"char", "/char":"char",
                   "/notes":"notes", "/exploration":"exp", "/exp":"exp",
                   "/abyss":"aby", "/aby":"aby" };
function switchTab(tab, opts){
  opts = opts || {};
  ACTIVE_TAB = tab;
  // Reflect the tab in the URL (skip when we're reacting to a URL change, e.g.
  // back/forward, so we don't fight the browser's own history).
  if(opts.url!==false){
    const p = TAB_PATH[tab] || "/";
    if(location.pathname !== p) history.pushState({tab}, "", p);
  }
  document.querySelectorAll(".tab").forEach(t=>t.classList.toggle("active", t.dataset.tab===tab));
  $("#global-costs").classList.toggle("hidden", tab==="notes"||tab==="char"||tab==="exp"||tab==="aby");
  $("#lp-controls").classList.toggle("hidden", tab!=="lp");
  $("#arb-controls").classList.toggle("hidden", tab!=="arb");
  $("#lp-tablewrap").classList.toggle("hidden", tab!=="lp");
  $("#arb-tablewrap").classList.toggle("hidden", tab!=="arb");
  $("#char-tablewrap").classList.toggle("hidden", tab!=="char");
  $("#notes-tablewrap").classList.toggle("hidden", tab!=="notes");
  $("#exp-tablewrap").classList.toggle("hidden", tab!=="exp");
  $("#aby-tablewrap").classList.toggle("hidden", tab!=="aby");
  updateIndGate();
  if(tab!=="lp") closeDetail();
  setStatus("");
  document.title = tab==="lp" ? "EVE LP Store Scanner"
                : tab==="arb" ? "EVE Arbitrage Scanner"
                : tab==="char" ? "EVE Character Overview"
                : tab==="notes" ? "EVE Notes"
                : tab==="exp" ? "EVE Exploration Guide"
                : tab==="aby" ? "EVE Abyssal Deadspace Guide" : "EVE Industry Planner";
  setPref('active_tab', tab);
  if(tab==="aby" && typeof abyInit==="function") abyInit();
  if(tab==="ind" && AUTH.loggedIn){
    if(!IND.groupsLoaded) loadIndGroups();
    if(!IND.buildsLoaded) loadIndBuilds(); else reconcileBuilds();
    renderIndTable(); renderIndStatus();
    if(typeof indApplyMode==="function") indApplyMode();
  }
  if(tab==="char" && AUTH.loggedIn){ renderCharData(); refreshCharData(); markCharEventsSeen(); }
  if(tab==="exp" && typeof expOnEnterTab==="function") expOnEnterTab();
  if(tab==="notes" && !NOTES.loaded) loadNotes();
}
// The Industry planner has no manual ME/TE/skill inputs — it needs a real
// character's owned blueprints and trained skills, so it's gated behind login
// exactly like the Character tab, just without hiding the nav entry (so a
// logged-out visitor can discover why it needs EVE login).
function updateIndGate(){
  const show = ACTIVE_TAB==="ind" && AUTH.loggedIn;
  $("#ind-controls").classList.toggle("hidden", !show);
  $("#ind-tablewrap").classList.toggle("hidden", !show);
  $("#ind-empty").classList.toggle("hidden", !(ACTIVE_TAB==="ind" && !AUTH.loggedIn));
  // The Planner/Summary switch only shows inside a logged-in Industry tab;
  // indApplyMode then owns the per-mode visibility (the scan-filter controls bar
  // is a Planner-only concern, hidden in Summary mode).
  const mb=$("#ind-modebar"); if(mb) mb.classList.toggle("hidden", !show);
  if(show && typeof indApplyMode==="function") indApplyMode();
}
document.querySelectorAll(".tab").forEach(t=>{
  t.onclick = ()=>switchTab(t.dataset.tab);
});
// Back/forward between tab URLs — switch without re-pushing history. The
// Character tab needs a login; fall back to LP if the URL points there logged out.
window.addEventListener("popstate", ()=>{
  let tab = PATH_TAB[location.pathname] || "lp";
  if(tab==="char" && !AUTH.loggedIn) tab="lp";
  switchTab(tab, {url:false});
});

