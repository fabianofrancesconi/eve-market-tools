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

// ── localStorage + server-synced persistence ────────────────────────────────
const LS_KEY='eve-scanner';
function settingsBlob(){
  return {
    corp:$("#corp").value,lp:$("#lp").value,
    maxspread:$("#maxspread").value,tax:pctToFrac($("#g-tax").value),broker:pctToFrac($("#g-broker").value),
    market:$("#market").value,
    sort_key:STATE.sort.key,sort_dir:STATE.sort.dir,
    col_widths:STATE.colw,col_order:STATE.colOrder,col_layout_v:COL_LAYOUT_VERSION,col_vis:STATE.colVis,
    hide_illiquid:STATE.hideIlliquid?'1':'0',
    hide_unaffordable:STATE.hideUnaffordable?'1':'0',
    trade_weight:STATE.tradeWeight,
    active_tab:ACTIVE_TAB,
    exp_recent:(typeof EXP!=="undefined"&&EXP.recent)?EXP.recent:[],
    arb:{region:$("#arb-region").value,cross_station:$("#arb-cross").value,
      sales_tax:pctToFrac($("#g-tax").value),min_isk:$("#arb-minisk").value,
      max_jumps:$("#arb-maxjumps").value,route_flag:$("#arb-route").value,
      avoid_lowsec:ARB.avoidLowsec?'1':'0'},
    ind:{market_group:$("#ind-group").value,station:$("#ind-station").value,
      job_rate:$("#ind-jobrate").value,
      sales_tax:$("#g-tax").value,broker:$("#g-broker").value,
      buildable_only:$("#ind-buildable").checked?'1':'0',
      include_unbuildable:$("#ind-unobtainable").checked?'1':'0',
      hide_t2:$("#ind-hidet2").checked?'1':'0',
      hide_bpc:$("#ind-hidebpc").checked?'1':'0',
      min_tradeability:$("#ind-mintrade").value,
      profiles:JSON.stringify(IND.profiles),profile:$("#ind-profile").value,
      // Signals to the server that an empty profiles list is intentional (the
      // user deleted their last build location) vs. an accidental boot-race
      // default, so the server-side guard doesn't restore the old list.
      profiles_cleared:(IND.profiles.length===0 && IND.profilesCleared)?'1':'0',
      favorites:JSON.stringify([...IND.favorites]),
      sort_key:IND.sort.key,sort_dir:IND.sort.dir,
      col_order:JSON.stringify(IND.colOrder),col_widths:JSON.stringify(IND.colw),
      col_vis:JSON.stringify(IND.colVis),
      sections:JSON.stringify(IND.sections),
      ind_trade_weight:String(IND.tradeWeight)}
  };
}
// Debounced push of the full settings blob to the server so every device the
// logged-in character uses converges on the same columns/filters/etc. Cheap
// no-op server-side when nobody is logged in.
let _settingsSyncTimer=null;
let _pendingBlob=null;
function _postSettings(blob){
  // keepalive lets the POST survive a page navigation / tab close.
  return fetch('/api/settings/sync',{method:'POST',keepalive:true,
    headers:{'Content-Type':'application/json'},
    body:JSON.stringify({blob:JSON.stringify(blob)})}).catch(()=>{});
}
function syncSettingsToServer(blob){
  _pendingBlob=blob;
  clearTimeout(_settingsSyncTimer);
  _settingsSyncTimer=setTimeout(()=>{ _pendingBlob=null; _postSettings(blob); }, 800);
}
// Push any debounced change to the server immediately when the page is being
// hidden/closed, so opening another browser never shows a stale view. The server
// is the source of truth (see loadSettings); localStorage is only a paint cache.
function flushSettings(){
  if(_pendingBlob==null) return;
  const blob=_pendingBlob; _pendingBlob=null;
  clearTimeout(_settingsSyncTimer);
  _postSettings(blob);
}
window.addEventListener('pagehide',flushSettings);
document.addEventListener('visibilitychange',()=>{
  if(document.visibilityState==='hidden') flushSettings();
});
// Guard against persisting a half-initialised view. saveLS snapshots the current
// DOM fields (corp, LP, filters, …); if it runs before loadSettings() has applied
// the stored values — e.g. a warm-cache character-data refresh firing during boot
// calls saveLS() while #corp is still blank — it would clobber the account's saved
// corp with an empty string. loadSettings() flips this true once the DOM reflects
// stored state, so early boot-time saves are dropped instead of overwriting.
let _settingsApplied=false;
function markSettingsApplied(){ _settingsApplied=true; }
// When logged in but the server didn't return its authoritative settings blob
// (e.g. a post-deploy cold start where the session/DB pool isn't warm yet),
// loadSettings sets this so saveLS won't PUSH this session's possibly-default
// DOM over the durable Postgres copy. localStorage is still written (it's only
// a local paint cache), and a later reload — once the server is warm — restores
// and re-enables syncing. This is what stopped Industry filters/selection from
// silently reverting to defaults after a redeploy.
let _serverSyncSuppressed=false;
function suppressServerSync(){ _serverSyncSuppressed=true; }
function saveLS(){
  if(!_settingsApplied) return;
  const blob=settingsBlob();
  try{ localStorage.setItem(LS_KEY,JSON.stringify(blob)); }catch(e){}
  if(!_serverSyncSuppressed) syncSettingsToServer(blob);
}

// ── Tab switching ─────────────────────────────────────────────────────────
let ACTIVE_TAB = "lp";
// Each tab has a clean URL so a refresh/bookmark reopens the same module.
const TAB_PATH = { lp:"/", arb:"/arbitrage", ind:"/industry", char:"/character", notes:"/notes", exp:"/exploration", aby:"/abyss" };
const PATH_TAB = { "/":"lp", "/lp":"lp", "/arbitrage":"arb", "/arb":"arb",
                   "/industry":"ind", "/ind":"ind", "/character":"char", "/char":"char",
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
  postPrefs('/api/prefs',{active_tab:tab}); saveLS();
  if(tab==="aby" && typeof abyInit==="function") abyInit();
  if(tab==="ind" && AUTH.loggedIn){
    if(!IND.groupsLoaded) loadIndGroups();
    if(!IND.buildsLoaded) loadIndBuilds(); else reconcileBuilds();
    renderIndTable(); renderIndStatus();
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

