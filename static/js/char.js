// ══════════════════════════════════════════════════════════════════════════
// EVE SSO / CHARACTER
// ══════════════════════════════════════════════════════════════════════════
const AUTH = { loggedIn:false, name:null, charId:null, data:null,
               characters:[], activeCharId:null };
const CHAR_REFRESH_MS = 300000;  // ESI caches character industry jobs for 5 min
let charRefreshDeadline = 0;
// After a manual sync we briefly flash "Synced HH:MM:SS" in place of the
// countdown, then revert to the running countdown once this timestamp passes.
let _syncedFlashUntil = 0;
function tickCharRefreshTimer(){
  const el=$("#char-refresh-timer");
  if(!AUTH.loggedIn || !charRefreshDeadline){ el.classList.add("hidden"); return; }
  el.classList.remove("hidden");
  if(Date.now() < _syncedFlashUntil) return;   // holding the "Synced …" flash
  // Restore the countdown markup if we were just showing the "Synced …" flash.
  if(!$("#char-refresh-secs")) el.innerHTML=`Next sync in <span id="char-refresh-secs">—</span>`;
  const remaining=charRefreshDeadline-Date.now();
  $("#char-refresh-secs").textContent=remaining>0?fmtCountdownShort(remaining):"0:00";
  // The server pushes a "sync" event each background sweep to reset this in
  // lockstep with every other client. Only if none has arrived well past the
  // deadline (stream down) do we fall back to a single self-driven re-pull.
  if(remaining<=-30000 && !_charDataInFlight){
    charRefreshDeadline=0;
    refreshCharData();
  }
}
setInterval(tickCharRefreshTimer, 1000);
// The countdown only ever displays the schedule the server hands us.
function setSyncCountdown(secs){
  if(secs==null) return;
  charRefreshDeadline=Date.now()+secs*1000; tickCharRefreshTimer();
}
// ── Overview-tab notification badge ─────────────────────────────────────────
// The nav badge counts order-activity events (sales/expiries) the user hasn't
// looked at yet. Seen event ids are server-authoritative (one 'char_events_seen'
// pref) so dismissing the badge on one device clears it everywhere.
function _loadSeenEvents(){
  const v = (typeof getPref==="function") ? getPref('char_events_seen', []) : [];
  return new Set(Array.isArray(v) ? v : []);
}
function _saveSeenEvents(set){
  if(typeof setPref==="function") setPref('char_events_seen', [...set]);
}
// Mark every current event as seen and clear the badge (called on tab open).
function markCharEventsSeen(){
  const events=(AUTH.data&&AUTH.data.order_events)||[];
  const seen=_loadSeenEvents();
  const live=new Set(events.map(e=>e.id));
  // Keep only ids still live so the store can't grow unbounded, plus the ones
  // we're marking seen now.
  const next=new Set([...seen].filter(id=>live.has(id)));
  events.forEach(e=>next.add(e.id));
  _saveSeenEvents(next);
  const badge=$("#char-tab-badge");
  if(badge) badge.classList.add("hidden");
  const tab=$("#char-tab-btn");
  if(tab) tab.classList.remove("has-activity");
}
function updateCharBadge(){
  const badge=$("#char-tab-badge");
  const tab=$("#char-tab-btn");
  if(!badge||!tab) return;
  // When the Overview tab is already open, activity is visible in-place — no
  // point flagging it in the nav; treat everything as seen instead.
  if(ACTIVE_TAB==="char"){ markCharEventsSeen(); return; }
  const events=(AUTH.loggedIn&&AUTH.data&&AUTH.data.order_events)||[];
  const seen=_loadSeenEvents();
  const unseen=events.filter(e=>!seen.has(e.id)).length;
  badge.textContent=unseen>99?"99+":String(unseen);
  badge.classList.toggle("hidden", unseen===0);
  tab.classList.toggle("has-activity", unseen>0);
}
const ROMAN=["0","I","II","III","IV","V"];
function authEsc(s){ return String(s==null?"":s).replace(/[&<>"]/g,
  c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c])); }
function romanLvl(n){ return ROMAN[n]||String(n||""); }

// EVE login settings popover removed — login is fully env-configured.

function renderAuthChip(){
  $("#login-eve").classList.toggle("hidden", AUTH.loggedIn);
  $("#char-chip").classList.toggle("hidden", !AUTH.loggedIn);
  $("#char-sync").classList.toggle("hidden", !AUTH.loggedIn);
  $("#settings-btn").classList.toggle("hidden", !AUTH.loggedIn);
  $("#char-tab-btn").classList.toggle("hidden", !AUTH.loggedIn);
  $("#char-empty").classList.toggle("hidden", AUTH.loggedIn);
  $("#char-body").classList.toggle("hidden", !AUTH.loggedIn);
  if(typeof expApplyAuth==="function") expApplyAuth();
  if(AUTH.loggedIn){
    $("#chip-name").textContent=accountLabel();
    renderSettingsPanel();
  }
  if(typeof renderPageCharBadges==="function") renderPageCharBadges();
  if(ACTIVE_TAB==="char" && !AUTH.loggedIn) switchTab("ind");
  else updateIndGate();
}

// The header label for the whole account. EVE SSO gives us no account name — a
// token only carries a per-character name — so we use the first-linked
// character's name, plus a count when more than one character is linked.
function accountLabel(){
  const chars=AUTH.characters||[];
  if(!chars.length) return AUTH.name||"Capsuleer";
  const first=chars[0].name||AUTH.name||"Capsuleer";
  return chars.length>1 ? `${first} · ${chars.length} chars` : first;
}
// Character management — add, remove, switch default, per-page assignment — all
// live in the ⚙ settings panel (renderSettingsPanel). The chip is just a label
// that opens the Overview tab.
async function switchActiveChar(cid){
  await fetch(`/api/auth/switch?active_char_id=${cid}`);
  await checkAuth();
  // The default character drives every page left on "Use default" (plus the
  // header wallet + Overview). Re-run the visible page if it's following the
  // default, so a table/journal already on screen reflects the new character.
  PAGE_CHAR_PAGES.forEach(p=>{ if(!pageHasAssignment(p)) applyPageChar(p); });
}

// ── Per-page character assignment ────────────────────────────────────────────
// Some pages (Industry, Exploration, LP Store) can run against a specific
// character rather than the account-wide active one. The mapping lives in a
// single server-authoritative pref, `page_char = {ind:<cid>, exp:<cid>, lp:<cid>}`.
// A page with no entry — or a stale id whose character was since removed — falls
// back to the active character, mirroring the server's own fallback.
const PAGE_CHAR_PAGES = ["ind", "exp", "lp"];
function charById(cid){ return AUTH.characters.find(c=>c.character_id===cid) || null; }
function charName(cid){ const c=charById(cid); return c?c.name:null; }
// Resolved character id for a page: the assignment if it still exists, else active.
function assignedCharId(page){
  const map=getPref('page_char', {}) || {};
  const cid=map[page];
  if(cid!=null && charById(cid)) return cid;
  return AUTH.activeCharId;
}
// True when the page is running on its own assigned char (not the active fallback).
function pageHasAssignment(page){
  const map=getPref('page_char', {}) || {};
  const cid=map[page];
  return cid!=null && charById(cid) && cid!==AUTH.activeCharId;
}
// Assign (cid) or clear (cid==null) a page's character, persist, and re-run the
// affected page if it's on screen so the change takes effect immediately.
function setPageChar(page, cid){
  const map=Object.assign({}, getPref('page_char', {}) || {});
  if(cid==null) delete map[page]; else map[page]=cid;
  setPref('page_char', Object.keys(map).length ? map : null);
  applyPageChar(page);
}
function applyPageChar(page){
  if(page==="ind"){ if(ACTIVE_TAB==="ind" && IND.rows && IND.rows.length) scanInd(false); }
  else if(page==="lp"){ updateMyLpBadge(); }
  else if(page==="exp"){ if(typeof refreshJournal==="function") refreshJournal(); }
  renderPageCharBadges();
}

// Render the small "👤 <name> ▾" chips that live in each page's control bar so
// the user can see (and change) which character that tool is using. Clicking a
// chip opens the ⚙ settings panel where the assignment lives. Gold when the page
// is on its own assigned character; neutral when falling back to the active one.
function renderPageCharBadges(){
  document.querySelectorAll(".page-char-slot").forEach(slot=>{
    const page=slot.dataset.page;
    if(!AUTH.loggedIn){ slot.innerHTML=""; return; }
    const cid=assignedCharId(page);
    const name=charName(cid)||accountLabel();
    const assigned=pageHasAssignment(page);
    slot.innerHTML=`<span class="page-char-badge${assigned?' assigned':''}" title="${assigned?'This page uses ':'Using the active character: '}${authEsc(name)} — click to change">`
      +`👤 ${authEsc(name)} <span class="pcb-caret">▾</span></span>`;
    const badge=slot.querySelector(".page-char-badge");
    if(badge) badge.onclick=e=>{ e.stopPropagation(); openSettingsPanel(); };
  });
}

async function checkAuth(){
  let st; try{ st=await (await fetch("/api/auth/status")).json(); }catch(e){ return null; }
  AUTH.loggedIn=!!st.logged_in; AUTH.name=st.name; AUTH.charId=st.character_id;
  AUTH.characters=st.characters||[];
  AUTH.activeCharId=st.active_char_id||null;
  renderAuthChip();
  if(AUTH.loggedIn){
    // Lock LP + corp fields until char data arrives so the UI doesn't show
    // stale/default values that get overwritten moments later. When we don't
    // yet have any char data (first load), also blank them and show a spinner
    // instead of the stale value; on later refreshes the fields already hold
    // the correct values so we leave them visible to avoid a flicker.
    const _lp=$("#lp"), _corp=$("#corp");
    _lp.readOnly=true; _lp.classList.add("locked");
    _corp.readOnly=true; _corp.classList.add("locked");
    if(!AUTH.data){ _lp.classList.add("loading"); _corp.classList.add("loading"); }
    refreshCharData();
    openCharStream();
    if(location.pathname==="/character" || location.pathname==="/char") switchTab("char", {url:false});
    if(!NOTES.loaded || !NOTES.items.length) loadNotes();
  } else {
    closeCharStream();
  }
  return st;
}

// ── Live sync: the backend pushes a "changed" event on /api/char/stream the
// moment it detects new character data (wallet, LP, orders, jobs), so an open
// browser updates without waiting for the fallback poll. EventSource reconnects
// on its own, so a dropped stream self-heals; the 5-min countdown covers any
// gap. Comments (heartbeats) don't fire onmessage, so only real pushes refresh.
let _charStream=null;
function openCharStream(){
  if(_charStream || !AUTH.loggedIn || typeof EventSource==="undefined") return;
  try{
    const es=new EventSource("/api/char/stream");
    _charStream=es;
    es.onmessage=(ev)=>{
      let m; try{ m=JSON.parse(ev.data); }catch(_){ return; }
      if(!m) return;
      // Every event carries the shared, server-defined countdown — just display it.
      setSyncCountdown(m.next_sync_in);
      // "hello" (re)connect → catch up on anything missed while disconnected;
      // a "sync" with changed=true means this account's data actually changed, so
      // re-pull. A plain sweep tick (changed=false) only moves the countdown.
      if(m.type==="hello" || (m.type==="sync" && m.changed)){
        refreshCharData();
        // The same stream carries live journal changes (system entered, auto-pause);
        // refresh the exploration journal when it's the visible tab.
        if(ACTIVE_TAB==="exp" && typeof trackOnLivePush==="function") trackOnLivePush();
      }
    };
    es.onerror=()=>{ /* EventSource auto-reconnects; nothing to do */ };
  }catch(_){ _charStream=null; }
}
function closeCharStream(){
  if(_charStream){ try{ _charStream.close(); }catch(_){} _charStream=null; }
}

async function doLogin(){
  let r; try{ r=await (await fetch("/api/auth/login")).json(); }catch(e){ setStatus("Could not start EVE login.", true); return; }
  if(r.url){ window.location.href=r.url; }
  else if(r.error){ setStatus(authEsc(r.error), true); }
}
async function doLogout(charId){
  const url=charId?`/api/auth/logout?char_id=${charId}`:"/api/auth/logout";
  await fetch(url).catch(()=>{});
  await checkAuth();
  if(!AUTH.loggedIn){
    AUTH.data=null; IND.timers={};
    charRefreshDeadline=0; tickCharRefreshTimer();
    renderAuthChip(); updateMyLpBadge(); updateCharBadge(); renderIndTable();
    if(IND.openDetail) renderIndDetail(IND.openDetail);
  } else {
    refreshCharData();
  }
}

let charRetryCount = 0;
function _retryCharDataSoon(){
  // Covers both a network-level failure (fetch/json throws) and a transient
  // backend error surfaced as a normal {"error":...} 500/400 (e.g. a stale
  // pooled ESI/Fuzzwork connection) — retry after a short delay instead of
  // leaving the error on screen for a full CHAR_REFRESH_MS cycle. Capped so a
  // *persistent* error (e.g. a revoked EVE session) doesn't hammer the
  // backend/ESI every 10s forever — after a few tries fall back to the normal
  // slow cadence.
  charRetryCount++;
  const delay = charRetryCount <= 3 ? 10000 : CHAR_REFRESH_MS;
  setTimeout(()=>{ if(AUTH.loggedIn) refreshCharData(); }, delay);
  charRefreshDeadline=Date.now()+delay; tickCharRefreshTimer();
}
let _charDataInFlight=null;
async function refreshCharData(force){
  if(_charDataInFlight) return _charDataInFlight;
  _charDataInFlight=_doRefreshCharData(force).finally(()=>{ _charDataInFlight=null; });
  return _charDataInFlight;
}
async function _doRefreshCharData(force){
  if(!AUTH.data){
    $("#char-body").innerHTML=`<div class="init-loading"><div class="init-spinner"></div>Loading…</div>`;
  }
  let d;
  const url=force?"/api/char/data?refresh=1":"/api/char/data";
  try{ d=await (await fetch(url)).json(); }
  catch(e){ _retryCharDataSoon(); return; }
  if(d.error){ setStatus(authEsc(d.error), true); _retryCharDataSoon(); return; }
  charRetryCount = 0;
  AUTH.data=d;
  _walletHistoryCache=null;
  // Auto-fill sales tax from Accounting skill: base 7.5% × (1 − 0.11 × level)
  if(d.accounting_level!=null){
    const tax=7.5*(1-0.11*d.accounting_level);
    $("#g-tax").value=tax.toFixed(2);
  }
  // Auto-fill broker fee from Broker Relations: base 3% − 0.3% × level (no standings)
  if(d.broker_relations_level!=null){
    const fee=3.0-0.3*d.broker_relations_level;
    $("#g-broker").value=fee.toFixed(2);
  }
  // Persist the derived global costs (server-authoritative) to the keys each tool
  // reads them from. Fractions for LP/arb, whole-percent for the ind section
  // (mirrors how the fields were stored in the old blob).
  setPref('tax', pctToFrac($("#g-tax").value));
  setPref('broker', pctToFrac($("#g-broker").value));
  setPref('arb.sales_tax', pctToFrac($("#g-tax").value));
  setPref('ind.sales_tax', $("#g-tax").value);
  setPref('ind.broker', $("#g-broker").value);
  recalcIndProfits();
  // Display the server-defined countdown that came with this data.
  setSyncCountdown(d.next_sync_in);
  const prevLp=$("#lp").value;
  renderCharData(); syncJobTimers(); updateMyLpBadge(); updateCharBadge();
  // Fresh jobs arrived — re-derive tracked-build statuses (link new jobs, mark
  // finished ones done). Loads the frozen builds on first sight if not yet done.
  if(typeof reconcileBuilds==="function"){
    if(IND.buildsLoaded) reconcileBuilds(); else if(typeof loadIndBuilds==="function") loadIndBuilds();
  }
  // Fresh sale fills accrue server-side each refresh; re-pull the portfolio
  // roll-up when the Industry tab's Summary mode is showing so its totals stay live.
  if(ACTIVE_TAB==="ind" && typeof IND!=="undefined" && IND.mode==="summary"
     && typeof loadSummary==="function") loadSummary();
  // Corp field was locked during the char-data fetch; unlock now that data
  // arrived. Clear the loading spinner on both fields — updateMyLpBadge() above
  // has already set the LP budget, and renderCharData() restored the corp value.
  $("#corp").readOnly=false; $("#corp").classList.remove("locked");
  $("#corp").classList.remove("loading"); $("#lp").classList.remove("loading");
  // Re-run the LP scan when the budget changed OR when this is the first char
  // data load and no scan has run yet (we skip auto-scan at boot until char
  // data arrives so the budget is fresh).
  const lpChanged=$("#lp").value!==prevLp;
  const needsInitialScan=!STATE.rows.length && !STATE.lastScanData;
  if((lpChanged||needsInitialScan) && ACTIVE_TAB==="lp" && ($("#corp").value||"").trim()){
    clearTimeout(lpScanTimer); scan(false);
  }
}

let _charTabIdx=0;
function _evPortrait(cid,sz){ return `https://images.evetech.net/characters/${cid}/portrait?size=${sz||64}`; }
function _fmtAgo(ts){ const s=Math.floor((Date.now()/1000)-ts); if(s<60) return "just now"; if(s<3600) return Math.floor(s/60)+"m ago"; if(s<86400) return Math.floor(s/3600)+"h ago"; return Math.floor(s/86400)+"d ago"; }
// Order-expiry column: relative countdown when it's within ~2 days ("expired",
// "3h", "1d 4h"); an absolute date once it's further out. Returns {text, cls}
// so the caller can flag imminent/expired orders.
function _fmtExpires(expiresMs){
  if(!isFinite(expiresMs)) return {text:"—", cls:""};
  const rem=expiresMs-Date.now();
  if(rem<=0) return {text:"expired", cls:"exp-gone"};
  if(rem<2*86400000){
    const s=Math.floor(rem/1000);
    const d=Math.floor(s/86400), h=Math.floor((s%86400)/3600), m=Math.floor((s%3600)/60);
    const text=d>0?`${d}d ${h}h`:(h>0?`${h}h ${m}m`:`${m}m`);
    return {text, cls:rem<86400000?"exp-soon":""};
  }
  return {text:new Date(expiresMs).toLocaleDateString([],{day:'2-digit',month:'short'}), cls:""};
}
// EVE only publishes loyalty points to ESI about once an hour, so the LP value
// can look "stuck" between updates even though sync is working. Format ESI's
// Last-Modified into a short local clock time so the number is visibly "as of X".
function _fmtLpAsOf(httpDate){
  if(!httpDate) return "";
  const d=new Date(httpDate);
  if(isNaN(d)) return "";
  return d.toLocaleTimeString([], {hour:"2-digit", minute:"2-digit"});
}
function _fmtOrdersExpires(httpDate){
  if(!httpDate) return "";
  const d=new Date(httpDate);
  if(isNaN(d)) return "";
  return d.toLocaleTimeString([], {hour:"2-digit", minute:"2-digit"});
}

// ── Market-order table sorting ──────────────────────────────────────────
// Shared across the per-character and combined "All" order tables. Default is
// newest-posted first; clicking a column header re-sorts, and clicking the
// active column again flips direction.
let _ordersSort={key:'posted', dir:'desc'};

// Column descriptors keyed by header. `val` extracts a comparable value from an
// order (numbers for numeric columns, lowercase strings for text). The optional
// `char` column is only present in the combined view.
const _ORDER_COLS=[
  {key:'char',      label:'Character',   align:'left',  val:o=>(o._char||'').toLowerCase()},
  {key:'item',      label:'Item',        align:'left',  val:o=>(o.type_name||'').toLowerCase()},
  {key:'side',      label:'Side',        align:'left',  val:o=>o.is_buy_order?1:0},
  {key:'remaining', label:'Remaining',   align:'right', val:o=>o.volume_remain??0},
  {key:'price',     label:'Price',       align:'right', val:o=>o.price||0},
  {key:'value',     label:'Total value', align:'right', val:o=>(o.volume_remain??0)*(o.price||0)},
  {key:'jita',      label:'Jita sell',   align:'right', val:o=>o.market_sell??-Infinity},
  {key:'queue',     label:'Queue',       align:'right', val:o=>o.is_best?-1:(o.queue_rank??Infinity)},
  {key:'posted',    label:'Posted',      align:'right', val:o=>o.issued?Date.parse(o.issued):-Infinity},
  {key:'expires',   label:'Expires',     align:'right', val:o=>{
    const i=o.issued?Date.parse(o.issued):NaN;
    return isFinite(i)&&o.duration!=null?i+o.duration*86400000:Infinity;
  }},
];

// Columns to show; the combined view prepends the Character column.
function _orderCols(withChar){
  return withChar?_ORDER_COLS:_ORDER_COLS.filter(c=>c.key!=='char');
}

// Text columns read naturally ascending (A→Z); numeric columns default to
// descending (largest/newest first) when first clicked.
function _defaultSortDir(key){ return (key==='char'||key==='item'||key==='side')?'asc':'desc'; }

function _ordersHeaderHTML(cols){
  return cols.map(c=>{
    const active=_ordersSort.key===c.key;
    const arrow=active?(_ordersSort.dir==='asc'?' ▲':' ▼'):'';
    const align=c.align==='right'?' style="text-align:right"':'';
    return `<th class="ord-th${active?' ord-th-active':''}" data-sort-key="${c.key}"${align}>${c.label}${arrow}</th>`;
  }).join('');
}

function _sortOrders(orders){
  const col=_ORDER_COLS.find(c=>c.key===_ordersSort.key);
  if(!col) return orders.slice();
  const dir=_ordersSort.dir==='asc'?1:-1;
  return orders.slice().sort((a,b)=>{
    const va=col.val(a), vb=col.val(b);
    if(va<vb) return -dir;
    if(va>vb) return dir;
    return 0;
  });
}

function _renderCharPanel(c){
  const cJobs=c.jobs||[], cQueue=c.skillqueue||[], cLp=c.loyalty||[], cOrders=c.market_orders||[];
  const cOrdersError=c.market_orders_error;
  const ordersVal=cOrders.reduce((s,o)=>s+(o.volume_remain??0)*(o.price||0),0);
  let h=`<div class="char-grid">`;

  // Wallet history chart
  h+=`<div class="wallet-chart-wrap">`;
  h+=`<div class="wallet-chart-header"><h3>Wallet Balance</h3>`;
  h+=`<div class="wallet-chart-range">`;
  h+=`<button class="wcr-btn${_walletChartDays===7?' active':''}" data-days="7">7d</button>`;
  h+=`<button class="wcr-btn${_walletChartDays===30?' active':''}" data-days="30">30d</button>`;
  h+=`<button class="wcr-btn${_walletChartDays===90?' active':''}" data-days="90">90d</button>`;
  h+=`</div></div>`;
  h+=`<div id="walletChartContainer" style="min-height:200px"></div>`;
  h+=`<div class="wallet-chart-stats" id="walletChartStats"></div>`;
  h+=`</div>`;

  // Industry jobs
  h+=`<section class="char-card${cJobs.length>3?' char-card-wide':''}">`;
  h+=`<div class="char-card-header"><h3>Industry jobs (${cJobs.length})</h3></div><div class="char-card-body">`;
  if(cJobs.length){
    h+=`<div class="char-card-scroll"><table class="mini char-jobs-tbl"><thead><tr>`;
    h+=`<th>Product</th><th>Activity</th><th>Location</th><th>Runs</th><th>Status</th><th style="text-align:right">Time left</th>`;
    h+=`</tr></thead><tbody>`;
    for(const j of cJobs){
      const end=Date.parse(j.end), rem=end-Date.now();
      let tcell="—";
      if(isFinite(end)) tcell=rem>0
        ?`<span class="ind-live-timer timer-cell" data-end="${end}">${fmtCountdownShort(rem)}</span>`
        :`<span class="timer-cell done">✓ Ready</span>`;
      const tb=_trackedBuildForJob(j);
      const tracked=!!tb;
      const link=tracked?` <span class="char-job-tracked" data-peek="${tb.id}" title="Quick look at this tracked build">🔗</span>`:"";
      const cls=tracked?" char-job-row":"";
      const tip=tracked?` title="Open its tracked build in Industry"`:"";
      h+=`<tr class="${cls.trim()}" data-job-id="${j.job_id}"${tip}>`
        +`<td>${_peekName(j.product_name, tb)}${link}</td><td>${authEsc(j.activity)}</td>`
        +`<td>${authEsc(j.location||"?")}</td><td>${j.runs??""}</td><td>${authEsc(j.status||"")}</td><td class="tl">${tcell}</td></tr>`;
    }
    h+=`</tbody></table></div>`;
  } else h+=`<div class="char-none">No active jobs.</div>`;
  h+=`</div></section>`;

  // Skill queue
  h+=`<section class="char-card"><div class="char-card-header"><h3>Skill queue (${cQueue.length})</h3></div><div class="char-card-body">`;
  if(cQueue.length){
    h+=`<div class="char-card-scroll"><table class="mini"><thead><tr><th>Skill</th><th>Lvl</th><th style="text-align:right">Finishes</th></tr></thead><tbody>`;
    for(const s of cQueue){
      const fin=s.finish_date?new Date(s.finish_date).toLocaleString([],{day:'2-digit',month:'short',hour:'2-digit',minute:'2-digit'}):"—";
      h+=`<tr><td>${authEsc(s.skill_name)}</td><td>${romanLvl(s.finished_level)}</td><td style="text-align:right">${fin}</td></tr>`;
    }
    h+=`</tbody></table></div>`;
  } else h+=`<div class="char-none">Skill queue is empty.</div>`;
  h+=`</div></section>`;

  // Loyalty points
  const lpAsOf=_fmtLpAsOf(c.loyalty_last_modified);
  h+=`<section class="char-card"><div class="char-card-header"><h3>Loyalty points</h3>`
    +(lpAsOf?`<span class="char-card-note" title="EVE publishes loyalty points to ESI roughly once an hour.">as of ${lpAsOf}</span>`:"")
    +`</div><div class="char-card-body">`;
  if(cLp.length){
    h+=`<div class="char-card-scroll"><table class="mini"><thead><tr><th>Corporation</th><th style="text-align:right">LP</th></tr></thead><tbody>`;
    for(const l of cLp) h+=`<tr><td>${authEsc(l.corp_name)}</td><td style="text-align:right">${(l.loyalty_points||0).toLocaleString()}</td></tr>`;
    h+=`</tbody></table></div>`;
  } else h+=`<div class="char-none">No loyalty points.</div>`;
  h+=`</div></section>`;

  // Market orders
  const ordersExp=_fmtOrdersExpires(c.market_orders_expires);
  h+=`<section class="char-card char-card-wide"><div class="char-card-header"><h3>Market orders`;
  if(cOrders.length) h+=` <span class="char-card-sub">(${cOrders.length} · ${fmtISK(ordersVal)} ISK)</span>`;
  h+=`</h3>`+(ordersExp?`<span class="char-card-note" title="ESI caches market orders ~20 min. New/changed orders won't appear until this time.">updates at ${ordersExp}</span>`:"")+`</div><div class="char-card-body">`;
  if(cOrders.length){
    const cols=_orderCols(false);
    h+=`<div class="char-card-scroll char-orders-scroll"><table class="mini char-orders-tbl"><thead><tr>`;
    h+=_ordersHeaderHTML(cols);
    h+=`</tr></thead><tbody>`;
    for(const o of _sortOrders(cOrders)){
      const issuedMs=o.issued?Date.parse(o.issued):NaN;
      const posted=isFinite(issuedMs)?fmtDur((Date.now()-issuedMs)/1000)+" ago":"—";
      const postedTip=isFinite(issuedMs)?` title="${new Date(issuedMs).toLocaleString()}"`:"";
      const expiresMs=isFinite(issuedMs)&&o.duration!=null?issuedMs+o.duration*86400000:NaN;
      const exp=_fmtExpires(expiresMs);
      const expTip=isFinite(expiresMs)?` title="${new Date(expiresMs).toLocaleString()}"`:"";
      const queueCell=o.is_best==null?`<span style="color:var(--dim)">—</span>`
        :o.is_best?`<span class="ord-best">Best ✓</span>`:`<span class="ord-queue ${heatClass(o.queue_rank,o.queue_total)}">#${o.queue_rank} / ${o.queue_total}</span>`;
      const saleTip=o.last_sale_ts?` title="Last sale: ${o.last_sale_qty} unit${o.last_sale_qty>1?'s':''} sold ${_fmtAgo(o.last_sale_ts)}" style="text-align:right;color:var(--green2)"`
        :` style="text-align:right"`;
      const oTb=_trackedBuildForOrder(o);
      const oTracked=!!oTb;
      const oLink=oTracked?` <span class="char-job-tracked" data-peek="${oTb.id}" title="Quick look at this tracked build">🔗</span>`:"";
      const oCls=oTracked?" char-order-row":"";
      h+=`<tr class="${oCls.trim()}" data-order-id="${o.order_id!=null?o.order_id:''}"><td>${_peekName(o.type_name, oTb)}${oLink}</td>`
        +`<td class="${o.is_buy_order?"tx-buy":"tx-sell"}">${o.is_buy_order?"Buy":"Sell"}</td>`
        +`<td${saleTip}>${(o.volume_remain??0).toLocaleString()} / ${(o.volume_total??0).toLocaleString()}</td>`
        +`<td style="text-align:right">${fmtISK(o.price)}</td>`
        +`<td style="text-align:right">${fmtISK((o.volume_remain??0)*o.price)}</td>`
        +`<td style="text-align:right">${o.market_sell!=null?fmtISK(o.market_sell):"—"}</td>`
        +`<td style="text-align:right">${queueCell}</td>`
        +`<td style="text-align:right"${postedTip}>${posted}</td>`
        +`<td class="exp-cell ${exp.cls}" style="text-align:right"${expTip}>${exp.text}</td></tr>`;
    }
    h+=`</tbody></table></div>`;
  } else h+=`<div class="char-none${cOrdersError?' char-none-warn':''}">${cOrdersError||'No open orders.'}</div>`;
  h+=`</div></section>`;

  h+=`</div>`;
  return h;
}

function _renderAllPanel(chars){
  const allJobs=[], allLp=[], allOrders=[];
  for(const c of chars){
    const name=c.name||'?';
    for(const j of (c.jobs||[])) allJobs.push({...j, _char:name});
    for(const l of (c.loyalty||[])) allLp.push({...l, _char:name});
    for(const o of (c.market_orders||[])) allOrders.push({...o, _char:name});
  }
  const ordersVal=allOrders.reduce((s,o)=>s+(o.volume_remain??0)*(o.price||0),0);
  let h=`<div class="char-grid">`;

  // Wallet history chart
  h+=`<div class="wallet-chart-wrap">`;
  h+=`<div class="wallet-chart-header"><h3>Wallet Balance</h3>`;
  h+=`<div class="wallet-chart-range">`;
  h+=`<button class="wcr-btn${_walletChartDays===7?' active':''}" data-days="7">7d</button>`;
  h+=`<button class="wcr-btn${_walletChartDays===30?' active':''}" data-days="30">30d</button>`;
  h+=`<button class="wcr-btn${_walletChartDays===90?' active':''}" data-days="90">90d</button>`;
  h+=`</div></div>`;
  h+=`<div id="walletChartContainer" style="min-height:200px"></div>`;
  h+=`<div class="wallet-chart-stats" id="walletChartStats"></div>`;
  h+=`</div>`;

  // Industry jobs — unified with character name
  h+=`<section class="char-card${allJobs.length>3?' char-card-wide':''}">`;
  h+=`<div class="char-card-header"><h3>Industry jobs (${allJobs.length})</h3></div><div class="char-card-body">`;
  if(allJobs.length){
    h+=`<div class="char-card-scroll"><table class="mini char-jobs-tbl"><thead><tr>`;
    h+=`<th>Character</th><th>Product</th><th>Activity</th><th>Location</th><th>Runs</th><th>Status</th><th style="text-align:right">Time left</th>`;
    h+=`</tr></thead><tbody>`;
    for(const j of allJobs){
      const end=Date.parse(j.end), rem=end-Date.now();
      let tcell="—";
      if(isFinite(end)) tcell=rem>0
        ?`<span class="ind-live-timer timer-cell" data-end="${end}">${fmtCountdownShort(rem)}</span>`
        :`<span class="timer-cell done">✓ Ready</span>`;
      const tb=_trackedBuildForJob(j);
      const tracked=!!tb;
      const link=tracked?` <span class="char-job-tracked" data-peek="${tb.id}" title="Quick look at this tracked build">🔗</span>`:"";
      const cls=tracked?" char-job-row":"";
      const tip=tracked?` title="Open its tracked build in Industry"`:"";
      h+=`<tr class="${cls.trim()}" data-job-id="${j.job_id}"${tip}>`
        +`<td>${authEsc(j._char)}</td><td>${_peekName(j.product_name, tb)}${link}</td><td>${authEsc(j.activity)}</td>`
        +`<td>${authEsc(j.location||"?")}</td><td>${j.runs??""}</td><td>${authEsc(j.status||"")}</td><td class="tl">${tcell}</td></tr>`;
    }
    h+=`</tbody></table></div>`;
  } else h+=`<div class="char-none">No active jobs.</div>`;
  h+=`</div></section>`;

  // Loyalty points — with character name
  h+=`<section class="char-card"><div class="char-card-header"><h3>Loyalty points</h3></div><div class="char-card-body">`;
  if(allLp.length){
    h+=`<div class="char-card-scroll"><table class="mini"><thead><tr><th>Character</th><th>Corporation</th><th style="text-align:right">LP</th></tr></thead><tbody>`;
    for(const l of allLp) h+=`<tr><td>${authEsc(l._char)}</td><td>${authEsc(l.corp_name)}</td><td style="text-align:right">${(l.loyalty_points||0).toLocaleString()}</td></tr>`;
    h+=`</tbody></table></div>`;
  } else h+=`<div class="char-none">No loyalty points.</div>`;
  h+=`</div></section>`;

  // Market orders — unified with totals
  const allOrdersExp=_fmtOrdersExpires(chars.reduce((latest,c)=>{
    const e=c.market_orders_expires; return e&&(!latest||e>latest)?e:latest;
  },null));
  h+=`<section class="char-card char-card-wide"><div class="char-card-header"><h3>Market orders`;
  if(allOrders.length) h+=` <span class="char-card-sub">(${allOrders.length} · ${fmtISK(ordersVal)} ISK)</span>`;
  h+=`</h3>`+(allOrdersExp?`<span class="char-card-note" title="ESI caches market orders ~20 min. New/changed orders won't appear until this time.">updates at ${allOrdersExp}</span>`:"")+`</div><div class="char-card-body">`;
  if(allOrders.length){
    const sellOrders=allOrders.filter(o=>!o.is_buy_order);
    const buyOrders=allOrders.filter(o=>o.is_buy_order);
    const sellVal=sellOrders.reduce((s,o)=>s+(o.volume_remain??0)*(o.price||0),0);
    const buyVal=buyOrders.reduce((s,o)=>s+(o.volume_remain??0)*(o.price||0),0);
    const cols=_orderCols(true);
    h+=`<div class="char-card-scroll char-orders-scroll"><table class="mini char-orders-tbl"><thead><tr>`;
    h+=_ordersHeaderHTML(cols);
    h+=`</tr></thead><tbody>`;
    for(const o of _sortOrders(allOrders)){
      const issuedMs=o.issued?Date.parse(o.issued):NaN;
      const posted=isFinite(issuedMs)?fmtDur((Date.now()-issuedMs)/1000)+" ago":"—";
      const postedTip=isFinite(issuedMs)?` title="${new Date(issuedMs).toLocaleString()}"`:"";
      const expiresMs=isFinite(issuedMs)&&o.duration!=null?issuedMs+o.duration*86400000:NaN;
      const exp=_fmtExpires(expiresMs);
      const expTip=isFinite(expiresMs)?` title="${new Date(expiresMs).toLocaleString()}"`:"";
      const queueCell=o.is_best==null?`<span style="color:var(--dim)">—</span>`
        :o.is_best?`<span class="ord-best">Best ✓</span>`:`<span class="ord-queue ${heatClass(o.queue_rank,o.queue_total)}">#${o.queue_rank} / ${o.queue_total}</span>`;
      const saleTip=o.last_sale_ts?` title="Last sale: ${o.last_sale_qty} unit${o.last_sale_qty>1?'s':''} sold ${_fmtAgo(o.last_sale_ts)}" style="text-align:right;color:var(--green2)"`
        :` style="text-align:right"`;
      const oTb=_trackedBuildForOrder(o);
      const oTracked=!!oTb;
      const oLink=oTracked?` <span class="char-job-tracked" data-peek="${oTb.id}" title="Quick look at this tracked build">🔗</span>`:"";
      const oCls=oTracked?" char-order-row":"";
      h+=`<tr class="${oCls.trim()}" data-order-id="${o.order_id!=null?o.order_id:''}"><td>${authEsc(o._char)}</td><td>${_peekName(o.type_name, oTb)}${oLink}</td>`
        +`<td class="${o.is_buy_order?"tx-buy":"tx-sell"}">${o.is_buy_order?"Buy":"Sell"}</td>`
        +`<td${saleTip}>${(o.volume_remain??0).toLocaleString()} / ${(o.volume_total??0).toLocaleString()}</td>`
        +`<td style="text-align:right">${fmtISK(o.price)}</td>`
        +`<td style="text-align:right">${fmtISK((o.volume_remain??0)*o.price)}</td>`
        +`<td style="text-align:right">${o.market_sell!=null?fmtISK(o.market_sell):"—"}</td>`
        +`<td style="text-align:right">${queueCell}</td>`
        +`<td style="text-align:right"${postedTip}>${posted}</td>`
        +`<td class="exp-cell ${exp.cls}" style="text-align:right"${expTip}>${exp.text}</td></tr>`;
    }
    h+=`<tr class="total"><td colspan="5">Totals</td>`
      +`<td style="text-align:right">${fmtISK(ordersVal)}</td>`
      +`<td colspan="4" style="text-align:right"><span class="tx-sell">${sellOrders.length} sell (${fmtISK(sellVal)})</span> · <span class="tx-buy">${buyOrders.length} buy (${fmtISK(buyVal)})</span></td></tr>`;
    h+=`</tbody></table></div>`;
  } else h+=`<div class="char-none">No open orders.</div>`;
  h+=`</div></section>`;

  h+=`</div>`;
  return h;
}

// ── Wallet History Chart ────────────────────────────────────────────────
let _walletChart=null;
let _walletHistoryCache=null;
let _walletChartDays=7;
let _walletChartCharId=null;

async function _loadWalletHistory(days){
  try{
    const r=await fetch(`/api/char/wallet-history?days=${days}`);
    const d=await r.json();
    if(d.error) return null;
    return d.series;
  }catch{ return null; }
}

function _combinedWalletSeries(series){
  const allPts=[];
  for(const cid of Object.keys(series)){
    for(const [ts,bal] of (series[cid].data||[]))
      allPts.push({ts,cid,bal});
  }
  if(!allPts.length) return [];
  allPts.sort((a,b)=>a.ts-b.ts);
  const last={};
  const combined=[];
  for(const p of allPts){
    last[p.cid]=p.bal;
    combined.push([p.ts, Object.values(last).reduce((s,v)=>s+v,0)]);
  }
  return combined;
}

function _walletChartStats(series, charId, minTs, maxTs){
  let points;
  if(charId){
    points=(series[charId]&&series[charId].data)||[];
  } else {
    points=_combinedWalletSeries(series);
  }
  if(minTs!=null || maxTs!=null){
    const lo=(minTs!=null)?minTs/1000:-Infinity;
    const hi=(maxTs!=null)?maxTs/1000:Infinity;
    points=points.filter(p=>p[0]>=lo && p[0]<=hi);
  }
  if(!points.length) return '';
  const vals=points.map(p=>p[1]);
  const min=Math.min(...vals), max=Math.max(...vals);
  const avg=vals.reduce((s,v)=>s+v,0)/vals.length;
  const change=vals[vals.length-1]-vals[0];
  const pct=vals[0]?(change/vals[0]*100):0;
  const col=change>=0?'var(--green2)':'var(--red)';
  return `<span><span class="k">Min</span><span class="v">${fmtISK(min)}</span></span>`
    +`<span><span class="k">Max</span><span class="v">${fmtISK(max)}</span></span>`
    +`<span><span class="k">Avg</span><span class="v">${fmtISK(avg)}</span></span>`
    +`<span><span class="k">Change</span><span class="v" style="color:${col}">${change>=0?'+':''}${fmtISK(change)} (${pct>=0?'+':''}${pct.toFixed(1)}%)</span></span>`;
}

let _walletChartRetry=0;
let _walletChartRenderSeq=0;
async function renderWalletChart(charId){
  if(typeof ApexCharts==='undefined'){
    const c=document.getElementById('walletChartContainer');
    if(c) c.innerHTML='<div class="wallet-chart-none">Chart unavailable (no internet)</div>';
    return;
  }
  // Re-entrancy guard. renderCharData() rebuilds #char-body's innerHTML — which
  // replaces the chart container node — and it can fire again (tab switch,
  // renderIndBuilds callback) while this async render is awaiting wallet
  // history. A superseded call must bail before it renders into a now-detached
  // container, which ApexCharts lays out as NaN geometry (the <svg> width /
  // transform "NaN" errors). Bump a sequence token and re-grab the container
  // AFTER every await.
  const seq=++_walletChartRenderSeq;
  if(!_walletHistoryCache){
    const c0=document.getElementById('walletChartContainer');
    if(c0) c0.innerHTML='<div class="wallet-chart-none">Loading…</div>';
    _walletHistoryCache=await _loadWalletHistory(_walletChartDays);
    if(seq!==_walletChartRenderSeq) return;   // superseded while awaiting
  }
  const container=document.getElementById('walletChartContainer');
  if(!container) return;
  if(!_walletHistoryCache||!Object.keys(_walletHistoryCache).length){
    container.innerHTML='<div class="wallet-chart-none">No wallet history yet. Data will appear after the next refresh cycle.</div>';
    return;
  }
  // ApexCharts divides by the container width to lay out the SVG; a zero-width
  // container (layout not yet settled on boot, the tab momentarily hidden, or a
  // freshly rebuilt #char-body not yet laid out) yields NaN geometry and a
  // broken chart. Defer until the container has a real width — retry a few
  // animation frames before giving up.
  if(!container.clientWidth){
    if(_walletChartRetry++ < 30){
      requestAnimationFrame(()=>renderWalletChart(charId));
    }
    return;
  }
  _walletChartRetry=0;

  let apexSeries=[];
  if(charId){
    const s=_walletHistoryCache[charId];
    if(s&&s.data.length)
      apexSeries=[{name:s.name, data:s.data.map(([ts,v])=>[ts*1000,v])}];
  } else {
    const combined=_combinedWalletSeries(_walletHistoryCache);
    if(combined.length)
      apexSeries.push({name:'Total', data:combined.map(([ts,v])=>[ts*1000,v])});
  }
  if(!apexSeries.length){
    container.innerHTML='<div class="wallet-chart-none">No wallet history for this character yet.</div>';
    return;
  }

  if(_walletChart){_walletChart.destroy();_walletChart=null;}
  container.innerHTML='';
  _walletChartCharId=charId;

  const opts={
    chart:{
      type:'area', height:200,
      background:'transparent',
      // Zoom, pan and mouse-wheel scroll are disabled: the range buttons (7d/30d/90d)
      // are the only way to change the window, so the chart never hijacks page scroll.
      toolbar:{show:false},
      zoom:{enabled:false,allowMouseWheelZoom:false},
      animations:{enabled:true,easing:'easeinout',speed:400},
      fontFamily:'inherit',
    },
    theme:{mode:'dark'},
    colors:charId?['#4fc3f7']:['#4fc3f7','#66bb6a','#f0c040','#e05555','#ab47bc'],
    series:apexSeries,
    xaxis:{type:'datetime',labels:{style:{colors:'#5a7a95',fontSize:'9.5px'}}},
    yaxis:{labels:{style:{colors:'#5a7a95',fontSize:'9.5px'},formatter:v=>fmtISK(v)}},
    stroke:{curve:'smooth',width:charId?2:[3,1.5,1.5,1.5,1.5]},
    fill:{type:'gradient',gradient:{opacityFrom:0.25,opacityTo:0.02}},
    tooltip:{theme:'dark',x:{format:'dd MMM HH:mm'},y:{formatter:v=>fmtISK(v)+' ISK'}},
    grid:{borderColor:'#1f3044',strokeDashArray:3},
    legend:{show:apexSeries.length>1,position:'top',fontSize:'10.5px',
      labels:{colors:'#c8d8e8'},markers:{size:4}},
    dataLabels:{enabled:false},
  };
  _walletChart=new ApexCharts(container,opts);
  _walletChart.render();

  const statsEl=document.getElementById('walletChartStats');
  if(statsEl) statsEl.innerHTML=_walletChartStats(_walletHistoryCache,charId);
}

function renderCharData(){
  const d=AUTH.data; if(!d) return;
  const chars=d.characters||[d];
  const multiChar=chars.length>1;
  const events=d.order_events||[];

  $("#chip-wallet").textContent=d.wallet!=null?fmtISK(d.wallet)+" ISK":"";

  let html='';

  // Character tabs (portraits + name + wallet)
  if(multiChar){
    if(_charTabIdx>chars.length) _charTabIdx=0;
    html+=`<div class="char-tabs">`;
    html+=`<button class="char-tab-btn${_charTabIdx===0?' active':''}" data-ci="0">`;
    html+=`<span class="char-tab-all">All</span></button>`;
    chars.forEach((c,i)=>{
      const cid=c.character_id;
      const active=_charTabIdx===i+1;
      html+=`<button class="char-tab-btn${active?' active':''}" data-ci="${i+1}">`;
      html+=`<img src="${_evPortrait(cid,64)}" alt="">`;
      html+=`<span>${authEsc(c.name||'?')}</span>`;
      if(c.wallet!=null) html+=`<span class="char-tab-wallet">${fmtISK(c.wallet)}</span>`;
      html+=`</button>`;
    });
    html+=`</div>`;
  }

  // Order activity (sales + expiries)
  if(events.length){
    const shown=events.slice(0,10);
    // Header total: ISK sold across the sale events (expired-unsold rows carry no
    // value), plus a count breakdown of sold vs expired.
    let totIsk=0, nSold=0, nExpired=0;
    for(const e of events){
      if(e.expired){ nExpired++; }
      else { nSold++; totIsk+=(e.sold||0)*(e.price||0); }
    }
    const parts=[];
    if(nSold) parts.push(`${nSold} sold`);
    if(nExpired) parts.push(`${nExpired} expired`);
    const totStr=`${fmtISK(totIsk)} ISK${parts.length?" · "+parts.join(" · "):""}`;
    html+=`<div class="char-events"><div class="char-events-hdr">`;
    html+=`<span class="char-events-title">Order activity (${events.length})</span>`;
    html+=`<span class="char-events-total" title="Total sale value of the listed activity (expired-unsold orders excluded), and the sold / expired counts">${totStr}</span>`;
    html+=`<button class="char-events-dismiss" data-eid="all">dismiss all</button></div>`;
    for(const e of shown){
      const isk=e.sold*e.price;
      // Three outcomes: partial sale (↓), fully sold (✓), or expired unsold (⌛).
      const icon=e.expired?'⌛':(e.filled?'✓':'↓');
      const cls=e.expired?' ev-expired':'';
      html+=`<div class="char-event-row${cls}">`;
      html+=`<span class="ev-icon">${icon}</span>`;
      html+=`<span class="ev-qty">${e.sold}x</span> ${authEsc(e.type_name)}`;
      if(e.expired){
        html+=` <span class="ev-tag">expired unsold</span>`;
      } else {
        html+=` <span class="ev-isk">${fmtISK(isk)} ISK</span>`;
      }
      if(multiChar) html+=` <span class="ev-char">${authEsc(e.character_name)}</span>`;
      html+=`<span class="ev-time">${_fmtAgo(e.ts)}</span>`;
      html+=`<span class="ev-x" data-eid="${e.id}">✕</span>`;
      html+=`</div>`;
    }
    if(events.length>10) html+=`<div class="char-none">… and ${events.length-10} more</div>`;
    html+=`</div>`;
  }

  // Render selected tab content
  if(!multiChar || _charTabIdx===0){
    // Combined "All" view
    html+=_renderAllPanel(chars);
  } else {
    const c=chars[_charTabIdx-1];
    if(c) html+=_renderCharPanel(c);
  }

  $("#char-body").innerHTML=html;

  // Load tracked builds in the background so the 🔗 tracked marker on industry
  // jobs is accurate (it needs IND.builds). loadIndBuilds() re-renders this view
  // via renderIndBuilds() once the list arrives.
  if(!IND.buildsLoaded && typeof loadIndBuilds==="function") loadIndBuilds();

  // Wire clicking an industry-job row → jump to the Industry tab and open that
  // blueprint's detail panel.
  $("#char-body").querySelectorAll("tr.char-job-row").forEach(tr=>{
    tr.style.cursor="pointer";
    tr.onclick=()=>{
      const jid=tr.dataset.jobId;
      const job=((AUTH.data&&AUTH.data.jobs)||[]).find(j=>String(j.job_id)===String(jid));
      if(job) openIndFromJob(job);
    };
  });

  // Wire clicking a linked market-order row → open its tracked build in Industry.
  $("#char-body").querySelectorAll("tr.char-order-row").forEach(tr=>{
    tr.style.cursor="pointer";
    tr.onclick=()=>{ openIndFromOrder({order_id:tr.dataset.orderId}); };
  });

  // Clicking the 🔗 icon itself opens a quick-look modal instead of jumping
  // straight to Industry. Stop propagation so the row's navigate handler above
  // doesn't also fire.
  $("#char-body").querySelectorAll("[data-peek]").forEach(el=>{
    el.style.cursor="pointer";
    el.onclick=e=>{ e.stopPropagation(); openBuildPeek(el.dataset.peek); };
  });

  // Wire market-order column sorting. Clicking a header sorts by that column;
  // clicking the active column again flips direction. Re-renders in place.
  $("#char-body").querySelectorAll(".char-orders-tbl .ord-th").forEach(th=>{
    th.onclick=()=>{
      const key=th.dataset.sortKey;
      if(_ordersSort.key===key) _ordersSort.dir=_ordersSort.dir==='asc'?'desc':'asc';
      else _ordersSort={key, dir:_defaultSortDir(key)};
      renderCharData();
    };
  });

  // Wire tab clicks
  $("#char-body").querySelectorAll(".char-tab-btn").forEach(btn=>{
    btn.onclick=()=>{ _charTabIdx=parseInt(btn.dataset.ci)||0; renderCharData(); };
  });
  // Wire event dismiss clicks
  $("#char-body").querySelectorAll("[data-eid]").forEach(el=>{
    el.onclick=()=>{
      const eid=el.dataset.eid;
      fetch(`/api/orders/dismiss?id=${encodeURIComponent(eid)}`,{method:'POST'}).catch(()=>{});
      if(eid==="all") d.order_events=[];
      else d.order_events=(d.order_events||[]).filter(e=>e.id!==eid);
      renderCharData();
    };
  });

  // Wallet chart — only render when the tab is visible; ApexCharts computes
  // NaN dimensions in a hidden (zero-width) container.
  const activeCharId=(!multiChar||_charTabIdx===0)?null:String(chars[_charTabIdx-1]?.character_id||'');
  if(ACTIVE_TAB==="char") renderWalletChart(activeCharId||null);

  // Wire range buttons
  document.querySelectorAll('.wcr-btn').forEach(btn=>{
    btn.onclick=async()=>{
      _walletChartDays=parseInt(btn.dataset.days);
      _walletHistoryCache=null;
      document.querySelectorAll('.wcr-btn').forEach(b=>b.classList.remove('active'));
      btn.classList.add('active');
      await renderWalletChart(activeCharId||null);
    };
  });
}

// Rebuild the Industry-table timers from the character's real manufacturing jobs,
// keyed by blueprint type id (== the planner's blueprint_id). This is the only
// source of timers — there is no manual timer any more.
// Alongside the manufacturing timers we build IND.research: blueprints currently
// occupied by a research/copy job (ME/TE research, copying), so the planner can
// warn that the BP is busy and shouldn't be scheduled for something else yet.
function syncJobTimers(){
  IND.timers={};
  IND.research={};
  const RESEARCH_ACTS={3:1,4:1,5:1};   // TE Research, ME Research, Copying
  (AUTH.data&&AUTH.data.jobs||[]).forEach(j=>{
    const end=Date.parse(j.end), bp=j.blueprint_type_id;
    if(!bp) return;
    if(j.activity_id===1){                 // manufacturing → crafting timer
      if(isFinite(end)) IND.timers[bp]=end;
    } else if(RESEARCH_ACTS[j.activity_id]){
      // Keep the job finishing latest so the note reflects when the BP frees up.
      const cur=IND.research[bp];
      if(!cur || (isFinite(end) && end>cur.end)){
        IND.research[bp]={activity:j.activity||"", activity_id:j.activity_id,
                          end:isFinite(end)?end:0, character_name:j.character_name||""};
      }
    }
  });
  if(ACTIVE_TAB==="ind") renderIndTable();
  if(IND.openDetail) renderIndDetail(IND.openDetail);
}

// Render a job/order name cell. When it's a tracked build the name itself
// becomes the quick-look trigger (data-peek) — same as the 🔗 icon — so there's
// no behavioural gap between clicking the name and clicking the link. Untracked
// names stay plain escaped text.
function _peekName(name, tb){
  const esc=authEsc(name);
  return tb?`<span class="char-peek-name" data-peek="${tb.id}" title="Quick look at this tracked build">${esc}</span>`:esc;
}

// Find the tracked build covering an industry job — by job_id first (exact
// link), then by blueprint (+ runs when the job reports them). job_id round-trips
// as a string, so compare via String().
function _trackedBuildForJob(j){
  const builds=(typeof IND!=="undefined" && IND.builds)||[];
  // Exact link wins. Failing that, fall back to blueprint (+ runs) — but only
  // onto a build that could still be awaiting/building this job. A build that
  // already reached built/listed/sold has had its own job delivered long ago, so
  // a live active job matching by blueprint is a *different* batch; matching it
  // would wrongly link the overview job to the old finished card.
  const stillOpen=b=>typeof _buildStage!=="function"
    || _buildStage(b)==="planned" || _buildStage(b)==="building";
  // Even the exact job_id match is gated on the build still being open: a
  // sold/listed build can carry a stale link to a *new* live batch's job (same
  // blueprint/runs) until reconcile releases it — don't show 🔗 on a finished
  // card in the meantime.
  return builds.find(b=>b.job_id!=null && String(b.job_id)===String(j.job_id) && stillOpen(b))
    || builds.find(b=>b.blueprint_id===j.blueprint_type_id
                      && (j.runs==null || b.runs===j.runs) && stillOpen(b))
    || null;
}
function _jobIsTracked(j){ return !!_trackedBuildForJob(j); }

// Find the tracked build whose sell links to this market order, by the order_id
// recorded in sell.order_ids (set when a listed sale auto-links to the order).
// order_id round-trips as a string on the build side, so compare via String().
function _trackedBuildForOrder(o){
  const oid=o&&(o.order_id!=null?o.order_id:o.id);
  if(oid==null || typeof IND==="undefined") return null;
  const builds=IND.builds||[];
  return builds.find(b=>((b.sell||{}).order_ids||[]).some(x=>String(x)===String(oid))) || null;
}
function _orderIsTracked(o){ return !!_trackedBuildForOrder(o); }

// Clicking a linked market-order row jumps to the Industry tab and opens the
// tracked build covering that order.
function openIndFromOrder(o){
  if(typeof IND==="undefined") return;
  const build=_trackedBuildForOrder(o);
  if(typeof switchTab==="function") switchTab("ind");
  if(!build || typeof openTrackedBuild!=="function") return;
  setTimeout(()=>openTrackedBuild(build.id), 60);
}

// Clicking an industry-job row jumps to the Industry tab and opens the detailed
// view of the tracked build covering that job (not the blueprint catalogue).
function openIndFromJob(j){
  if(typeof IND==="undefined") return;
  const build=_trackedBuildForJob(j);
  if(typeof switchTab==="function") switchTab("ind");
  if(!build || typeof openTrackedBuild!=="function") return;
  // Defer so the Industry tab (and its Tracked builds section) is rendered
  // before we expand + scroll to the card.
  setTimeout(()=>openTrackedBuild(build.id), 60);
}

// ── Tracked-build quick-look modal ───────────────────────────────────────────
// Clicking the 🔗 icon on a job/order opens a two-tab peek at the tracked build:
//   • Overview  — high-level: profit, progress, queue, key facts.
//   • Re-price  — the decision tool: market drift since tracking + a what-if
//     price simulator that answers "if I drop my price to climb the queue, after
//     a fresh broker fee, am I still above break-even and how much do I give up?"
// _PEEK holds the derived model shared by both tabs and by the live-quote fill.
let _buildPeekId=null;
let _buildPeekTab="overview";
let _PEEK=null;
const _peekIsk=v=>(v==null?"—":fmtISK(v));
const _peekSign=v=>(v!=null&&v>0?"+":"");
const _peekPn=v=>(v==null?"":(v>0?"pos":(v<0?"neg":"")));
const _peekPct=v=>(v==null?"—":(v>0?"+":"")+(v*100).toFixed(0)+"%");

function openBuildPeek(id){
  if(typeof IND==="undefined") return;
  const b=(IND.builds||[]).find(x=>x.id===id);
  const modal=$("#buildPeekModal");
  if(!b || !modal){ if(typeof openTrackedBuild==="function") openTrackedBuild(id); return; }
  _buildPeekId=id;
  _buildPeekTab="overview";

  const n=Math.max(1, b.runs||1);
  const s=b.snapshot||{};
  const stage=(typeof _buildStage==="function")?_buildStage(b):"";
  const econ=(typeof _batchEconomics==="function")?_batchEconomics(s, n):{};
  const units=(typeof _buildUnits==="function")?_buildUnits(b):null;
  const rz=((stage==="listed"||stage==="sold") && typeof _buildRealized==="function")?_buildRealized(b):null;
  // Fees are the OWNING character's live skill-derived rates (not the frozen
  // snapshot), so re-pricing reflects the broker/sales-tax that will actually
  // be charged when this character re-lists. Falls back to the snapshot rates.
  const fees=_peekOwnerFees(b);
  const stax=fees.stax, bfee=fees.bfee;
  // Break-even at the OWNER's live fees so the rail + warning stay consistent
  // with the simulator's own tax/broker math (revenue after fees == batch cost).
  const cost=econ.cost;
  const be={
    list:(cost!=null&&units&&(1-stax-bfee)>0)?cost/(units*(1-stax-bfee)):null,
    instant:(cost!=null&&units&&(1-stax)>0)?cost/(units*(1-stax)):null,
  };
  const target=(b.sell||{}).qty_target||units||0;
  // Units still to sell — the simulator works on these (with realized added on top).
  const remaining=rz?Math.max(0, target-rz.units):(units||0);
  // Per-unit cost basis: prefer the frozen sell cost_per_unit, else derive it.
  const cpu=(b.sell&&b.sell.cost_per_unit!=null)?b.sell.cost_per_unit
           :(typeof _buildCostPerUnit==="function"?_buildCostPerUnit(b):null);
  // Live linked order (its queue rank drives the re-price nudge).
  const order=_peekLinkedOrder(b);

  _PEEK={b, id, n, s, stage, econ, units, be, rz, stax, bfee, fees, target, remaining, cpu, order,
         cost, live:null, liveState:"loading"};

  // Pinned header: the tracker's colored stage badge + name + lifecycle stepper.
  const badge=(typeof _buildBadge==="function")?_buildBadge(b, stage):{key:"",label:stage};
  $("#build-peek-title").innerHTML=`<span class="ind-build-status ${badge.key}">${authEsc(badge.label)}</span>`
    +`<span class="build-peek-name">${authEsc(b.product_name||"Tracked build")}</span>`;
  $("#build-peek-stepper").innerHTML=(typeof _buildStepperHtml==="function")?_buildStepperHtml(b, stage):"";

  // The Re-price tab only earns its place once there's a live/finished sale to
  // reason about; hide it for planned/building/built.
  const showReprice=(stage==="listed"||stage==="sold"||stage==="built");
  const tabs=$("#build-peek-tabs");
  tabs.classList.toggle("hidden", !showReprice);
  if(!showReprice) _buildPeekTab="overview";

  _renderBuildPeekTab();
  modal.classList.remove("hidden");
  _fetchBuildPeekLive(b, id);   // async: fills the live market drift + simulator
}
function closeBuildPeek(){ const m=$("#buildPeekModal"); if(m) m.classList.add("hidden"); _buildPeekId=null; _PEEK=null; }

// All loaded character bundles (multi-char array, or the single active char).
function _peekChars(){
  return (AUTH.data&&AUTH.data.characters)||(AUTH.data?[AUTH.data]:[]);
}

// Which of the character's open sell orders this build is tracking (for queue
// position). Matches the build's linked order_id against every character bundle.
function _peekLinkedOrder(b){
  const oid=((b.sell||{}).order_ids||[])[0];
  if(oid==null) return null;
  for(const c of _peekChars()){
    const o=(c.market_orders||[]).find(o=>String(o.order_id)===String(oid));
    if(o) return o;
  }
  return null;
}

// The character whose fees govern this build's sale — the one who actually owns
// the market order (so the broker/sales-tax skills that apply are theirs). We
// resolve them by the linked order's owner first, then by the build's recorded
// char_name (the character who ran the job), else the active character.
function _peekOwnerChar(b){
  const chars=_peekChars();
  if(!chars.length) return null;
  const oid=((b.sell||{}).order_ids||[])[0];
  if(oid!=null){
    for(const c of chars){
      if((c.market_orders||[]).some(o=>String(o.order_id)===String(oid))) return c;
    }
  }
  if(b.char_name){
    const named=chars.find(c=>c.name===b.char_name);
    if(named) return named;
  }
  return chars.find(c=>c.character_id===(AUTH.data&&AUTH.data.active_char_id)) || chars[0];
}

// Recompute sales tax + broker fee from the OWNING character's live skills,
// mirroring the auto-fill formulas: sales tax = 7.5% × (1 − 0.11 × Accounting),
// broker = 3% − 0.3% × Broker Relations (standings not modelled). Falls back to
// the build's frozen snapshot rates when the character or a skill level is
// unavailable, so the sim always has usable numbers. Returns fractions + who.
function _peekOwnerFees(b){
  const s=b.snapshot||{};
  const snapTax=s.sales_tax||0, snapBroker=s.broker_fee||0;
  const c=_peekOwnerChar(b);
  const acc=c&&c.accounting_level, bro=c&&c.broker_relations_level;
  const stax=(acc!=null)?Math.max(0, 7.5*(1-0.11*acc))/100:snapTax;
  const bfee=(bro!=null)?Math.max(0, 3.0-0.3*bro)/100:snapBroker;
  return {stax, bfee, who:c?c.name:null,
          live:(acc!=null||bro!=null), snapTax, snapBroker};
}

function _renderBuildPeekTab(){
  const body=$("#build-peek-body"); if(!body||!_PEEK) return;
  $("#build-peek-tabs").querySelectorAll(".bpt-tab").forEach(t=>
    t.classList.toggle("active", t.dataset.tab===_buildPeekTab));
  body.innerHTML=(_buildPeekTab==="reprice")?_buildPeekRepriceHtml():_buildPeekOverviewHtml();
  if(_buildPeekTab==="reprice"){
    _wireBuildPeekSim();
    // If the live quote already landed (e.g. it resolved while Overview showed),
    // fill the freshly-rendered drift cells + sim right away.
    if(_PEEK.liveState==="done") _applyBuildPeekLive();
  }
}

// ── Overview tab — high-level, few numbers ───────────────────────────────────
function _buildPeekOverviewHtml(){
  const P=_PEEK, isk=_peekIsk, pn=_peekPn, sign=_peekSign;
  const {b,s,stage,econ,units,rz,cost,n,order}=P;

  let heroLabel, heroHtml, heroSub="", cls="";
  if(stage==="building"){
    const end=b.job_end?Date.parse(b.job_end):null;
    const loc=(typeof _buildJobLocation==="function")?_buildJobLocation(b):"";
    heroLabel="Finishes in";
    heroHtml=(end&&isFinite(end))
      ? `<span class="ind-live-timer build-peek-timer" data-end="${end}">${fmtCountdown(end-Date.now())}</span>`
      : `<span class="build-peek-timer">running</span>`;
    heroSub=(end&&isFinite(end))?`ETA ${new Date(end).toLocaleString([],{day:'2-digit',month:'short',hour:'2-digit',minute:'2-digit'})}`:"";
    if(loc) heroSub+=(heroSub?" · ":"")+"📍 "+authEsc(loc);
    cls="stage-building";
  } else if(stage==="planned"){
    heroLabel="Projected profit";
    heroHtml=`<span class="bph-val ${pn(econ.profitL)}">${sign(econ.profitL)}${isk(econ.profitL)}</span>`;
    heroSub=`Not started — begin ${n.toLocaleString()}× run(s) in EVE to link the job.`;
  } else if(stage==="built"){
    heroLabel="Projected profit";
    heroHtml=`<span class="bph-val ${pn(econ.profitL)}">${sign(econ.profitL)}${isk(econ.profitL)}</span>`;
    heroSub=`Ready to list at ${isk(_buildProposedPrice?_buildProposedPrice(b):s.ask)} / unit.`;
  } else {   // listed / sold
    heroLabel=stage==="sold"?"Realized profit":"Profit so far";
    heroHtml=`<span class="bph-val ${pn(rz.profit)}">${sign(rz.profit)}${isk(rz.profit)}</span>`;
  }
  const heroBase=rz?rz.profit:econ.profitL;
  const marginVal=(stage!=="building" && heroBase!=null && cost)?heroBase/cost:null;
  const marginTag=(marginVal!=null)?`<span class="bph-margin ${pn(heroBase)}">${_peekPct(marginVal)}</span>`:"";

  // Sold-progress bar.
  let progress="";
  if(rz){
    const frac=P.target?Math.min(1, rz.units/P.target):0;
    const closedEarly=stage==="sold"&&(b.sell||{}).closed_early;
    progress=`<div class="build-peek-progress ${stage==="sold"?"done":""}">
      <div class="bpp-track"><i style="width:${(frac*100).toFixed(1)}%"></i></div>
      <div class="bpp-count">${rz.units.toLocaleString()} / ${P.target.toLocaleString()} sold${closedEarly?" · rest written off":""}</div>
    </div>`;
  }

  // Queue chip — the reason to consider re-pricing. Only while genuinely listed.
  let queue="";
  if(stage==="listed" && order){
    queue=order.is_best
      ? `<div class="build-peek-queue best">◎ Best price in the queue</div>`
      : (order.queue_rank!=null
        ? `<div class="build-peek-queue ${heatClass(order.queue_rank,order.queue_total)}">▤ #${order.queue_rank} of ${order.queue_total} in the sell queue — undercut to climb</div>`
        : "");
  }

  const facts=[];
  if(s.me_used!=null||s.te_used!=null) facts.push(`<span class="bpf"><i>ME</i>${s.me_used??0} · <i>TE</i>${s.te_used??0}</span>`);
  if(s.build_time!=null) facts.push(`<span class="bpf"><i>Build</i>${fmtDur(econ.time)}</span>`);
  const loc=(typeof _buildJobLocation==="function"&&_buildJobLocation(b))||s.station_name||"";
  if(loc) facts.push(`<span class="bpf">📍 ${authEsc(loc)}</span>`);

  return `
    <div class="build-peek-hero ${cls}">
      <div class="bph-label">${heroLabel}${marginTag}</div>
      <div class="bph-main">${heroHtml}</div>
      ${heroSub?`<div class="bph-sub">${heroSub}</div>`:""}
    </div>
    ${progress}
    ${queue}
    ${facts.length?`<div class="build-peek-facts">${facts.join("")}</div>`:""}
    <div class="build-peek-meta">${n.toLocaleString()}× · ${units!=null?units.toLocaleString()+" units":"—"} · cost ${isk(cost)}</div>`;
}

// ── Re-price tab — market drift + what-if simulator ──────────────────────────
function _buildPeekRepriceHtml(){
  const P=_PEEK, isk=_peekIsk;
  const {s,be,order,remaining,rz,liveState}=P;

  // Market drift: frozen list/instant → live now, with Δ%. "now" cells fill in
  // once the live quote lands (liveState); until then a loading dash.
  const driftRow=(label,side,frozen)=>{
    return `<div class="bpr-row" data-side="${side}">`
      +`<span class="bpr-k">${label}</span>`
      +`<span class="bpr-then">${isk(frozen)}</span>`
      +`<span class="bpr-arrow">→</span>`
      +`<span class="bpr-now">${liveState==="loading"?'<span class="bpr-load">fetching…</span>':'<span class="bpr-na">—</span>'}</span></div>`;
  };
  const drift=`<div class="build-peek-prices">
    <div class="bpr-head"><span>Price / unit</span><span>frozen</span><span></span><span>now</span></div>
    ${driftRow("List","ask",s.ask)}
    ${driftRow("Instant","bid",s.bid)}
  </div>`;

  // Break-even reference. The simulator sim body is injected after the live fetch
  // resolves (it needs the live best-ask to place the slider sensibly), so here
  // we render a placeholder the fetch replaces.
  const beNote=(be.list!=null)
    ? `<div class="build-peek-benote">Break-even (list): <b>${isk(be.list)}</b> / unit — sell above this and the craft paid off.</div>`
    : "";

  // Whose fees the sim uses — the owning character's live skill-derived rates,
  // so a re-price reflects the broker/tax that'll actually be charged.
  const f=P.fees||{};
  const feePct=v=>(v*100).toFixed(1)+"%";
  const feeNote=`<div class="build-peek-fees">Fees: <b>${feePct(P.stax)}</b> sales tax · <b>${feePct(P.bfee)}</b> broker`
    +(f.live&&f.who?` <span class="bpf-who">from ${authEsc(f.who)}'s skills</span>`
      :` <span class="bpf-who">from the tracked snapshot</span>`)+`</div>`;

  const simSlot=`<div id="bp-sim" class="build-peek-sim">${
    liveState==="loading"
      ? `<div class="bp-sim-loading">Fetching current market to build the simulator…</div>`
      : `<div class="bp-sim-loading">Live market unavailable — can't simulate right now.</div>`
  }</div>`;

  const scopeNote=rz
    ? `<div class="build-peek-scope">Simulating the <b>${remaining.toLocaleString()}</b> unsold unit(s). Realized ${isk(rz.profit)} from ${rz.units.toLocaleString()} already sold is added to the total.</div>`
    : `<div class="build-peek-scope">Simulating all <b>${remaining.toLocaleString()}</b> unit(s).</div>`;

  return drift + beNote + feeNote + scopeNote + simSlot;
}

// Build the simulator DOM once the live quote is known. Slider spans a sensible
// window around break-even / frozen / live prices; dragging recomputes profit,
// margin and the give-up vs. an instant dump — after a FRESH broker fee, because
// re-listing pays broker again.
function _renderBuildPeekSim(){
  const P=_PEEK; if(!P) return;
  const slot=$("#bp-sim"); if(!slot) return;
  const {s,be,live,remaining,stax,bfee}=P;
  if(!live || (live.ask==null && live.bid==null)){
    slot.innerHTML=`<div class="bp-sim-loading">Live market unavailable — can't simulate right now.</div>`;
    return;
  }
  const isk=_peekIsk;
  // Reference prices for the slider ticks.
  const beList=be.list, frozen=s.ask, bestAsk=live.ask, bid=live.bid!=null?live.bid:s.bid;
  const undercut=bestAsk!=null?bestAsk*0.9999:null;   // one ISK-ish under best ask
  const refs=[beList,frozen,bestAsk,bid,undercut].filter(v=>v!=null);
  if(!refs.length){ slot.innerHTML=`<div class="bp-sim-loading">Not enough price data to simulate.</div>`; return; }
  // Slider window: a little below break-even (or bid) up to a little above the
  // highest reference, so every tick is reachable and there's headroom.
  const lo=Math.min(...refs)*0.9, hi=Math.max(...refs)*1.1;
  const start=(bestAsk!=null)?bestAsk:(frozen!=null?frozen:be.list);
  const step=Math.max(0.01, (hi-lo)/1000);

  const chip=(label,val)=> val==null?"" :
    `<button class="bp-chip" data-price="${val}" title="Set price to ${isk(val)}">${label}<b>${isk(val)}</b></button>`;
  slot.innerHTML=`
    <div class="bp-sim-head">If I re-price to</div>
    <div class="bp-sim-price"><span id="bp-sim-price">${isk(start)}</span><span class="bp-sim-unit">/ unit</span></div>
    <input id="bp-sim-slider" class="bp-sim-slider" type="range" min="${lo}" max="${hi}" step="${step}" value="${start}"${
      beList!=null?` style="${_peekRailStyle(lo,hi,beList)}"`:""}>
    <div class="bp-sim-chips">
      ${chip("Break-even ",beList)}
      ${chip("Best ask ",bestAsk)}
      ${chip("Undercut ",undercut)}
      ${chip("Frozen ",frozen)}
    </div>
    <div class="bp-sim-out" id="bp-sim-out"></div>
    <div class="bp-sim-floor" id="bp-sim-floor"></div>`;
  _updateBuildPeekSim(start);
}

// A track tinted red up to the break-even point and green above it, so the
// danger zone (selling at a loss) is visible before you even drag.
function _peekRailStyle(lo,hi,be){
  const pct=Math.max(0, Math.min(100, (be-lo)/(hi-lo)*100)).toFixed(1);
  return `--be-pct:${pct}%;background:linear-gradient(90deg,`
    +`var(--red-soft,rgba(220,80,80,.35)) 0 ${pct}%,`
    +`var(--green-soft,rgba(80,180,120,.35)) ${pct}% 100%);`;
}

// Recompute the simulator outputs for a chosen list price.
function _updateBuildPeekSim(price){
  const P=_PEEK; if(!P) return;
  const {be,remaining,rz,stax,bfee,live}=P, isk=_peekIsk, sign=_peekSign, pn=_peekPn;
  const cpu=P.cpu;
  const priceEl=$("#bp-sim-price"); if(priceEl) priceEl.textContent=isk(price);
  const slider=$("#bp-sim-slider"); if(slider && +slider.value!==price) slider.value=price;

  // Net per unit if LISTED at `price`: sales tax + a fresh broker fee (re-listing
  // always pays broker again). Profit = (net − cost) × remaining, plus realized.
  const netUnit=price*(1-stax-bfee);
  const profitUnit=(cpu!=null)?netUnit-cpu:null;
  const remProfit=(profitUnit!=null)?profitUnit*remaining:null;
  const totalProfit=(remProfit!=null)?remProfit+(rz?rz.profit:0):null;
  const aboveBE=(be.list!=null)?price-be.list:null;

  const out=$("#bp-sim-out");
  if(out){
    const beCls=(aboveBE==null)?"":(aboveBE>=0?"pos":"neg");
    const beMsg=(aboveBE==null)?"" : aboveBE>=0
      ? `<span class="bp-be ok">✓ ${isk(aboveBE)}/unit above break-even</span>`
      : `<span class="bp-be bad">⚠ ${isk(-aboveBE)}/unit below break-even — losing money on the craft</span>`;
    out.innerHTML=`
      <div class="bp-metric"><span class="bp-m-k">Net / unit <small>after tax + broker</small></span>
        <span class="bp-m-v ${pn(profitUnit)}">${isk(netUnit)}</span></div>
      <div class="bp-metric"><span class="bp-m-k">Profit on ${remaining.toLocaleString()} left</span>
        <span class="bp-m-v ${pn(remProfit)}">${sign(remProfit)}${isk(remProfit)}</span></div>
      ${rz?`<div class="bp-metric bp-total"><span class="bp-m-k">Batch total <small>incl. ${isk(rz.profit)} realized</small></span>
        <span class="bp-m-v ${pn(totalProfit)}">${sign(totalProfit)}${isk(totalProfit)}</span></div>`:""}
      <div class="bp-be-line ${beCls}">${beMsg}</div>`;
  }

  // The "settle for less" floors: instant-dump the remainder into buy orders
  // (bid, tax only, no broker), and the undercut-best-ask optimistic case.
  const floor=$("#bp-sim-floor");
  if(floor){
    const bid=live&&live.bid!=null?live.bid:P.s.bid;
    const instUnit=(bid!=null)?bid*(1-stax):null;
    const instProfit=(instUnit!=null&&cpu!=null)?(instUnit-cpu)*remaining:null;
    const giveUp=(remProfit!=null&&instProfit!=null)?remProfit-instProfit:null;
    const bestAsk=live&&live.ask!=null?live.ask:null;
    const ucUnit=(bestAsk!=null)?bestAsk*0.9999*(1-stax-bfee):null;
    const ucProfit=(ucUnit!=null&&cpu!=null)?(ucUnit-cpu)*remaining:null;
    floor.innerHTML=`
      <div class="bp-floor-head">Reality check on the ${remaining.toLocaleString()} unsold</div>
      ${instProfit!=null?`<div class="bp-floor-row"><span>⚡ Dump into buy orders now</span>
        <span class="${pn(instProfit)}">${sign(instProfit)}${isk(instProfit)}</span></div>`:""}
      ${ucProfit!=null?`<div class="bp-floor-row"><span>↧ Undercut best ask (${isk(bestAsk)})</span>
        <span class="${pn(ucProfit)}">${sign(ucProfit)}${isk(ucProfit)}</span></div>`:""}
      ${giveUp!=null&&giveUp>0?`<div class="bp-floor-note">Dumping now gives up <b>${isk(giveUp)}</b> vs. your simulated list price.</div>`:""}`;
  }
}

function _wireBuildPeekSim(){
  // Attach the interaction listeners once. Drawing the sim body is driven by
  // _applyBuildPeekLive (called from _renderBuildPeekTab when live is already in,
  // or by _fetchBuildPeekLive once the quote lands) — not from here.
  const slot=$("#bp-sim");
  if(!slot) return;
  slot.addEventListener("input", e=>{
    if(e.target && e.target.id==="bp-sim-slider") _updateBuildPeekSim(+e.target.value);
  });
  slot.addEventListener("click", e=>{
    const chip=e.target.closest && e.target.closest(".bp-chip");
    if(chip) _updateBuildPeekSim(+chip.dataset.price);
  });
}

// Fetch the live market quote once per open; feeds both the drift table and the
// simulator. Guards on _buildPeekId so a stale response is dropped.
function _fetchBuildPeekLive(b, id){
  const s=b.snapshot||{};
  const p=new URLSearchParams({
    blueprint_id:String(s.blueprint_id||""),
    station:String(s.station_id||""),
    job_rate:String(((s.job_rate||0)*100)),
    sales_tax:String(((s.sales_tax||0)*100)),
    broker:String(((s.broker_fee||0)*100)),
    runs:"1", refresh_prices:"1",
  });
  fetch("/api/ind/detail?"+p).then(r=>r.json()).then(fresh=>{
    if(_buildPeekId!==id || !_PEEK) return;
    _PEEK.live=(fresh&&!fresh.error)?{ask:fresh.ask, bid:fresh.bid}:null;
    _PEEK.liveState="done";
    _applyBuildPeekLive();
  }).catch(()=>{
    if(_buildPeekId!==id || !_PEEK) return;
    _PEEK.live=null; _PEEK.liveState="error";
    _applyBuildPeekLive();
  });
}

// Paint the live results into whichever tab is showing.
function _applyBuildPeekLive(){
  const P=_PEEK; if(!P) return;
  // Fill the drift table's "now" cells (present on the Re-price tab).
  const body=$("#build-peek-body"); if(!body) return;
  const isk=_peekIsk;
  const frozen={ask:P.s.ask, bid:P.s.bid};
  body.querySelectorAll(".bpr-row").forEach(row=>{
    const side=row.dataset.side, slot=row.querySelector(".bpr-now");
    if(!slot) return;
    const nowV=P.live?P.live[side]:null, thenV=frozen[side];
    if(nowV==null){ slot.innerHTML=`<span class="bpr-na">—</span>`; return; }
    const diff=(thenV!=null)?nowV-thenV:null;
    const dcls=diff>0?"pos":(diff<0?"neg":"");
    const arrow=diff>0?"▲":(diff<0?"▼":"");
    const pctTxt=(thenV)?` ${Math.abs(diff/thenV*100).toFixed(1)}%`:"";
    slot.innerHTML=`<b>${isk(nowV)}</b>`
      +(diff!=null&&diff!==0?` <span class="bpr-delta ${dcls}">${arrow}${pctTxt}</span>`:"");
  });
  // (Re)draw the simulator if the Re-price tab is active.
  if(_buildPeekTab==="reprice") _renderBuildPeekSim();
}

// When logged in, drive the LP budget from the character's loyalty points for
// the selected corp and lock the field read-only. Shows 0 if the character
// has no LP with that corp. Falls back to editable only when not logged in.
function updateMyLpBadge(){
  const badge=$("#lp-mylp"), inp=$("#lp");
  const corp=($("#corp").value||"").trim().toLowerCase();
  // Use the loyalty points of the character assigned to the LP Store page
  // (falling back to the active character). Per-character bundles live in
  // AUTH.data.characters[]; the active char's is also mirrored at AUTH.data top level.
  const lpCid=(typeof assignedCharId==="function")?assignedCharId("lp"):AUTH.activeCharId;
  const bundle=((AUTH.data&&AUTH.data.characters)||[]).find(c=>c.character_id===lpCid);
  const lp=(bundle?bundle.loyalty:(AUTH.data&&AUTH.data.loyalty))||[];
  const lpMod=bundle?bundle.loyalty_last_modified:(AUTH.data&&AUTH.data.loyalty_last_modified);
  const m=(AUTH.loggedIn&&corp)?lp.find(l=>(l.corp_name||"").toLowerCase()===corp):null;
  if(AUTH.loggedIn && m){
    inp.value=m.loyalty_points||0;
    inp.readOnly=true; inp.classList.add("locked");
    const asOf=_fmtLpAsOf(lpMod);
    const who=(typeof charName==="function"&&charName(lpCid))||"your character";
    inp.title=`Read from ${who}'s loyalty points.`
      +(asOf?` EVE updates LP roughly hourly; as of ${asOf}.`:"");
    badge.textContent=asOf?`🔒 from ${who} · as of ${asOf}`:`🔒 from ${who}`;
    badge.classList.remove("hidden");
  } else if(AUTH.loggedIn){
    // No LP with this corp — let the user type a manual budget. Clear any value
    // carried over from a previously-selected corp that WAS locked to its LP,
    // otherwise a scan would silently reuse the old corp's budget.
    if(inp.classList.contains("locked")) inp.value="";
    inp.readOnly=false; inp.classList.remove("locked");
    inp.title="No LP found for this corp — enter a manual budget.";
    badge.textContent="0 LP with this corp";
    badge.classList.remove("hidden");
  } else {
    inp.readOnly=false; inp.classList.remove("locked");
    inp.title="";
    badge.classList.add("hidden");
  }
}

$("#login-eve").onclick=doLogin;
$("#char-login-btn").onclick=doLogin;
$("#ind-login-btn").onclick=doLogin;
$("#landing-login").onclick=doLogin;

// Full-page login gate for unauthenticated visitors on a multi-user deploy.
// Hides the app chrome (see .landing-active in CSS) so no half-broken UI shows,
// and — because boot.js returns early — no settings/scan API calls fire and 401.
function showLoginLanding(){
  document.body.classList.add("landing-active");
  $("#login-landing").classList.remove("hidden");
}
document.addEventListener("click",e=>{
  const sp=$("#settings-panel");
  if(sp && !sp.classList.contains("hidden") && !sp.contains(e.target) && e.target.id!=="settings-btn")
    sp.classList.add("hidden");
});

// ── Settings ⚙ panel ─────────────────────────────────────────────────────────
// One floating panel holds all character management (add / remove / logout) and
// the per-page character assignment grid. The chip ▾ dropdown only switches the
// active character now.
const PAGE_CHAR_LABELS={ind:"Industry", exp:"Exploration", lp:"LP Store"};
function renderSettingsPanel(){
  const sp=$("#settings-panel");
  if(!sp || !AUTH.loggedIn) return;
  const map=getPref('page_char', {}) || {};
  const charOpts=(sel)=>AUTH.characters.map(c=>
    `<option value="${c.character_id}"${c.character_id===sel?" selected":""}>${authEsc(c.name)}</option>`).join("");
  const defName=charName(AUTH.activeCharId)||"—";
  // Character list: name + remove. Which character is the default is chosen by
  // the "Default character" select below, not per-row buttons.
  const chars=AUTH.characters.map(c=>{
    const isDefault=c.character_id===AUTH.activeCharId;
    return `<div class="set-char-row" data-cid="${c.character_id}">`
      +`<span class="set-char-name">${authEsc(c.name)}${isDefault?' <span class="set-char-active">★ default</span>':''}</span>`
      +`<button class="set-char-rm" data-cid="${c.character_id}" title="Remove ${authEsc(c.name)}">✕</button>`
      +`</div>`;
  }).join("");
  // Per-page assignment. Empty = "Use default (<default char>)".
  const assigns=PAGE_CHAR_PAGES.map(p=>
    `<div class="set-assign-row">`
      +`<span class="set-assign-page">${PAGE_CHAR_LABELS[p]}</span>`
      +`<select class="set-assign-sel" data-page="${p}">`
        +`<option value="">Use default (${authEsc(defName)})</option>`
        +charOpts(map[p]!=null?map[p]:null)
      +`</select>`
    +`</div>`).join("");
  sp.innerHTML=
    `<div class="set-section"><div class="set-head">Account</div>`
      +`<div class="set-account">${authEsc(accountLabel())}</div></div>`
    +`<div class="set-section"><div class="set-head">Characters</div>${chars}`
      +`<div class="set-char-row set-char-add">`
        +`<button id="set-add-char" class="auth-btn-sm">+ Add character</button>`
        +`<button id="set-logout-all" class="auth-btn-sm set-danger">Logout all</button>`
      +`</div></div>`
    +`<div class="set-section"><div class="set-head">Default character</div>`
      +`<div class="set-assign-hint">Used for the header wallet, the Overview tab, and any page below left on “Use default”.</div>`
      +`<div class="set-assign-row"><span class="set-assign-page">Default</span>`
        +`<select id="set-default-sel">${charOpts(AUTH.activeCharId)}</select></div></div>`
    +`<div class="set-section"><div class="set-head">Page assignments</div>`
      +`<div class="set-assign-hint">Choose which character each tool uses. Industry uses that character's skills &amp; blueprints; LP Store uses their loyalty points; Exploration tracks them.</div>`
      +assigns+`</div>`;
  sp.querySelectorAll(".set-char-rm").forEach(b=>
    b.onclick=e=>{ e.stopPropagation(); doLogout(parseInt(b.dataset.cid)); });
  const add=sp.querySelector("#set-add-char");
  if(add) add.onclick=e=>{ e.stopPropagation(); doLogin(); };
  const lall=sp.querySelector("#set-logout-all");
  if(lall) lall.onclick=e=>{ e.stopPropagation(); doLogout(); };
  const defSel=sp.querySelector("#set-default-sel");
  if(defSel) defSel.onchange=e=>{
    e.stopPropagation();
    if(defSel.value) switchActiveChar(parseInt(defSel.value));
  };
  sp.querySelectorAll(".set-assign-sel").forEach(sel=>
    sel.onchange=e=>{
      e.stopPropagation();
      const cid=sel.value?parseInt(sel.value):null;
      setPageChar(sel.dataset.page, cid);
    });
}
function openSettingsPanel(){
  if(!AUTH.loggedIn) return;
  renderSettingsPanel();
  $("#settings-panel").classList.remove("hidden");
}
$("#settings-btn").onclick=e=>{
  e.stopPropagation();
  const sp=$("#settings-panel");
  if(sp.classList.contains("hidden")) openSettingsPanel();
  else sp.classList.add("hidden");
};

$("#char-chip").onclick=()=>{ switchTab("char"); };

// Clicking the countdown — or the explicit ⟳ button — forces an immediate
// cache-busting re-fetch of every character from ESI. The button spins until
// the fetch settles (with a floor so a fast round-trip still reads as action).
$("#char-refresh-timer").onclick=()=>forceSync();
$("#char-refresh-timer").style.cursor="pointer";
$("#char-sync").onclick=()=>forceSync();
let _syncing=false;
async function forceSync(){
  if(!AUTH.loggedIn || _syncing) return;
  _syncing=true;
  const btn=$("#char-sync");
  btn.classList.add("syncing");
  const spun=new Promise(r=>setTimeout(r,600));   // minimum visible spin
  try{ await refreshCharData(true); }
  finally{
    await spun;
    btn.classList.remove("syncing");
    _syncing=false;
    // Flash "Synced HH:MM:SS" in the countdown slot for 5s, then let the
    // per-second ticker revert to the running "Next sync in …" countdown.
    const t=$("#char-refresh-timer");
    if(t){
      t.classList.remove("hidden");
      t.textContent="Synced "+new Date().toLocaleTimeString([],{hour:'2-digit',minute:'2-digit',second:'2-digit'});
      _syncedFlashUntil=Date.now()+5000;
    }
  }
}

// Live updates arrive via the SSE stream; the per-second countdown ticker
// (tickCharRefreshTimer) fires a fallback re-pull when its 5-min deadline
// elapses with no push, so no separate polling interval is needed here.

// When the tab returns from background (sleep, alt-tab, phone lock), the
// countdown may have drifted far past the deadline. Refresh immediately.
document.addEventListener("visibilitychange", ()=>{
  if(document.hidden || !AUTH.loggedIn) return;
  if(Date.now() >= charRefreshDeadline) refreshCharData();
});

function fallbackCopy(text, done){
  // execCommand path for non-secure contexts where navigator.clipboard is absent.
  try{
    const ta=document.createElement("textarea");
    ta.value=text; ta.style.position="fixed"; ta.style.opacity="0";
    document.body.appendChild(ta); ta.select();
    document.execCommand("copy"); document.body.removeChild(ta);
    if(done) done();
  }catch(e){}
}

function loadIndGroups(){
  fetch("/api/ind/groups").then(r=>r.json()).then(d=>{
    if(!d.groups) return;
    const sel=$("#ind-group");
    // The saved category can't be applied until the option list exists (it's
    // fetched async), so honour IND.savedGroup here once the options are in.
    const want=(sel.value && sel.value!=="all") ? sel.value : (IND.savedGroup||"all");
    sel.innerHTML='<option value="all">All (slow)</option>'
      +d.groups.map(g=>`<option value="${g.id}">${g.name}</option>`).join("");
    sel.value=[...sel.options].some(o=>o.value===want)?want:"all";
    IND.groupsLoaded=true;
  }).catch(()=>{});
}

// ── Build locations (station/structure job-cost profiles) ───────────
// A profile is {name, system_index, role_bonus, facility_tax, scc_surcharge};
// its effective Job cost % = system_index×(1−role_bonus/100) + facility_tax + SCC,
// matching the in-game Industry job-cost breakdown. (Legacy profiles may carry a
// flat job_rate instead.)
function structEffectiveRate(p){
  if(p && p.system_index!==undefined && p.system_index!==null){
    return (+p.system_index||0)*(1-(+p.role_bonus||0)/100)
         + (+p.facility_tax||0) + (+p.scc_surcharge||0);
  }
  return parseFloat(p&&p.job_rate)||0;
}
function renderIndProfiles(){
  const sel=$("#ind-profile");
  sel.innerHTML='<option value="">— custom —</option>'
    +IND.profiles.map((p,i)=>`<option value="${i}">${p.name}</option>`).join("");
}
function applyIndProfile(){
  const i=$("#ind-profile").value;
  setPref('ind.profile', i);
  if(i!==""&&IND.profiles[i]){
    $("#ind-jobrate").value=structEffectiveRate(IND.profiles[i]).toFixed(2);
    setPref('ind.job_rate', $("#ind-jobrate").value);
    recalcIndProfits();
  }
}

// Wizard ----------------------------------------------------------------
let IND_EDIT_IDX=null;
function swPreview(){
  const eff=(+$("#sw-index").value||0)*(1-(+$("#sw-bonus").value||0)/100)
          +(+$("#sw-facility").value||0)+(+$("#sw-scc").value||0);
  $("#sw-eff").textContent=eff.toFixed(2)+"%";
}
function openStructWizard(idx){
  IND_EDIT_IDX = (idx==null||idx==="")?null:+idx;
  const p = IND_EDIT_IDX!=null ? IND.profiles[IND_EDIT_IDX] : null;
  $("#sw-title").textContent = p ? "Edit build location" : "New build location";
  $("#sw-name").value     = p ? (p.name||"") : "";
  $("#sw-index").value    = p && p.system_index!=null ? p.system_index : "0";
  $("#sw-bonus").value    = p && p.role_bonus!=null ? p.role_bonus : "0";
  $("#sw-facility").value = p && p.facility_tax!=null ? p.facility_tax : "0";
  $("#sw-scc").value      = p && p.scc_surcharge!=null ? p.scc_surcharge : "4";
  $("#sw-delete").style.display = p ? "" : "none";
  swPreview();
  $("#indStructModal").classList.remove("hidden");
  $("#sw-name").focus();
}
function closeStructWizard(){ $("#indStructModal").classList.add("hidden"); }
// Build-location profiles live in their own server table (one row each), so a
// save/delete of one profile never rewrites the others. Each profile carries a
// stable profile_id; the #ind-profile dropdown still selects by array index.
// The id must be globally unique so it can never collide with an existing row
// (including the migration's "legacy-<i>" ids) and overwrite a different
// profile — prefer crypto.randomUUID, with a random+time fallback for old envs.
function _newProfileId(){
  if(typeof crypto!=="undefined" && crypto.randomUUID) return "p-"+crypto.randomUUID();
  return "p-" + Math.random().toString(36).slice(2) + "-" + Math.random().toString(36).slice(2);
}
function saveStructWizard(){
  const name=$("#sw-name").value.trim();
  if(!name){ $("#sw-name").focus(); return; }
  const existing = IND_EDIT_IDX!=null ? IND.profiles[IND_EDIT_IDX] : null;
  const p={ profile_id: (existing && existing.profile_id) || _newProfileId(), name,
    system_index:+$("#sw-index").value||0,
    role_bonus:+$("#sw-bonus").value||0,
    facility_tax:+$("#sw-facility").value||0,
    scc_surcharge:+$("#sw-scc").value||0 };
  let idx;
  if(IND_EDIT_IDX!=null){ IND.profiles[IND_EDIT_IDX]=p; idx=IND_EDIT_IDX; }
  else { IND.profiles.push(p); idx=IND.profiles.length-1; }
  p.pos = idx;
  renderIndProfiles();
  $("#ind-profile").value=String(idx);
  $("#ind-jobrate").value=structEffectiveRate(p).toFixed(2);
  setPref('ind.profile', $("#ind-profile").value);
  setPref('ind.job_rate', $("#ind-jobrate").value);
  saveProfile(p);
  closeStructWizard();
}
function deleteStruct(){
  if(IND_EDIT_IDX==null) return;
  const [removed] = IND.profiles.splice(IND_EDIT_IDX,1);
  renderIndProfiles();
  $("#ind-profile").value="";
  setPref('ind.profile', "");
  if(removed && removed.profile_id) deleteProfile(removed.profile_id);
  closeStructWizard();
}

// Persist the Industry scan/filter fields. Each is its own server key, so a
// change to one (e.g. the category) never disturbs another (columns, sort, …).
function saveIndPrefs(){
  setPref('ind.market_group', $("#ind-group").value);
  setPref('ind.station', $("#ind-station").value);
  setPref('ind.job_rate', $("#ind-jobrate").value);
  setPref('ind.buildable_only', $("#ind-buildable").checked?'1':'0');
  setPref('ind.include_unbuildable', $("#ind-unobtainable").checked?'1':'0');
  setPref('ind.hide_t2', $("#ind-hidet2").checked?'1':'0');
  setPref('ind.hide_bpc', $("#ind-hidebpc").checked?'1':'0');
  setPref('ind.min_tradeability', $("#ind-mintrade").value);
  setPref('ind.profile', $("#ind-profile").value);
  setPref('ind.sort_key', IND.sort.key);
  setPref('ind.sort_dir', IND.sort.dir);
  setPref('ind.hidden_bps', [...IND.hidden]);
  setPref('ind.col_order', IND.colOrder);
  setPref('ind.col_widths', IND.colw);
  setPref('ind.col_vis', IND.colVis);
  setPref('ind.ind_trade_weight', IND.tradeWeight);
}

// wiring
$("#ind-go").onclick=()=>scanInd(false);
$("#ind-refresh").onclick=()=>scanInd(true);

$("#ind-profile").addEventListener("change", applyIndProfile);
// Build-location wizard wiring
$("#ind-struct-new").onclick=()=>openStructWizard(null);
$("#ind-struct-edit").onclick=()=>{
  const i=$("#ind-profile").value;
  if(i==="") openStructWizard(null); else openStructWizard(i);
};
["#sw-index","#sw-bonus","#sw-facility","#sw-scc"].forEach(s=>$(s).addEventListener("input", swPreview));
$("#sw-save").onclick=saveStructWizard;
$("#sw-cancel").onclick=closeStructWizard;
$("#sw-delete").onclick=deleteStruct;
$("#indStructModal").addEventListener("click", e=>{ if(e.target.id==="indStructModal") closeStructWizard(); });
document.addEventListener("keydown", e=>{ if(e.key==="Escape" && !$("#indStructModal").classList.contains("hidden")) closeStructWizard(); });

// Tracked-build quick-look modal wiring.
$("#build-peek-close").onclick=closeBuildPeek;
$("#build-peek-open").onclick=()=>{
  const id=_buildPeekId; closeBuildPeek();
  if(id==null) return;
  if(typeof switchTab==="function") switchTab("ind");
  if(typeof openTrackedBuild==="function") setTimeout(()=>openTrackedBuild(id), 60);
};
$("#build-peek-tabs").addEventListener("click", e=>{
  const tab=e.target.closest && e.target.closest(".bpt-tab");
  if(!tab || !_PEEK) return;
  const which=tab.dataset.tab;
  if(which===_buildPeekTab) return;
  _buildPeekTab=which;
  _renderBuildPeekTab();
});
$("#buildPeekModal").addEventListener("click", e=>{ if(e.target.id==="buildPeekModal") closeBuildPeek(); });
document.addEventListener("keydown", e=>{ if(e.key==="Escape" && !$("#buildPeekModal").classList.contains("hidden")) closeBuildPeek(); });
function recalcIndProfits(){
  if(!IND.rows.length) return;
  const jobRate=parseFloat($("#ind-jobrate").value||"0")/100;
  const salesTax=parseFloat($("#g-tax").value||"0")/100;
  const broker=parseFloat($("#g-broker").value||"0")/100;
  const patientFactor=1-salesTax-broker;
  const instantFactor=1-salesTax;
  const n=Math.max(1,IND.detailRuns||1);
  for(const r of IND.rows){
    const jc=r.eiv*jobRate;
    const opCost=r.material_cost+jc+r.invention_cost;
    r.job_cost=jc; r.total_cost=opCost;
    const revP=r.ask!=null?(r.out_qty*r.ask*patientFactor):null;
    const revI=r.bid!=null?(r.out_qty*r.bid*instantFactor):null;
    r.profit_patient=revP!=null?(revP-opCost):null;
    r.profit_instant=revI!=null?(revI-opCost):null;
    r.profit_best=r.profit_patient!=null&&r.profit_instant!=null?Math.max(r.profit_patient,r.profit_instant):(r.profit_patient??r.profit_instant);
    const margin=pr=>(pr!=null&&opCost>0)?pr/opCost:null;
    r.margin_patient=margin(r.profit_patient);
    r.margin_instant=margin(r.profit_instant);
    r.margin_best=margin(r.profit_best);
    const hrs=r.build_time?r.build_time/3600:null;
    const iph=pr=>(pr!=null&&hrs)?pr/hrs:null;
    r.isk_per_hour_patient=iph(r.profit_patient);
    r.isk_per_hour_instant=iph(r.profit_instant);
    r.isk_per_hour_best=iph(r.profit_best);
    r.total_profit_patient=r.profit_patient!=null?r.profit_patient*r.runs:null;
    r.total_profit_instant=r.profit_instant!=null?r.profit_instant*r.runs:null;
  }
  renderIndTable();
  if(IND.openDetail) renderIndDetail(IND.openDetail);
}
// Each Industry control persists only its own server key.
$("#ind-group").addEventListener("change", ()=>setPref('ind.market_group', $("#ind-group").value));
$("#ind-station").addEventListener("change", ()=>setPref('ind.station', $("#ind-station").value));
$("#ind-jobrate").addEventListener("change", ()=>{ setPref('ind.job_rate', $("#ind-jobrate").value); recalcIndProfits(); });
$("#ind-buildable").addEventListener("change", ()=>setPref('ind.buildable_only', $("#ind-buildable").checked?'1':'0'));
$("#ind-unobtainable").addEventListener("change", ()=>setPref('ind.include_unbuildable', $("#ind-unobtainable").checked?'1':'0'));
$("#ind-hidet2").addEventListener("change", ()=>setPref('ind.hide_t2', $("#ind-hidet2").checked?'1':'0'));
$("#ind-hidebpc").addEventListener("change", ()=>{ setPref('ind.hide_bpc', $("#ind-hidebpc").checked?'1':'0'); renderIndTable(); });
// Min-tradeability is a client-side filter — re-render immediately (no rescan).
$("#ind-mintrade").addEventListener("input", ()=>{ setPref('ind.min_tradeability', $("#ind-mintrade").value); renderIndTable(); });
// Industry tradeability balance presets
function syncIndBalanceButtons(){
  document.querySelectorAll(".ind-balance-btn").forEach(b=>
    b.classList.toggle("on", parseFloat(b.dataset.w)===IND.tradeWeight));
}
document.querySelectorAll(".ind-balance-btn").forEach(b=>{
  b.onclick=()=>{
    IND.tradeWeight=parseFloat(b.dataset.w);
    syncIndBalanceButtons();
    computeIndTradeability();
    renderIndTable();
    setPref('ind.ind_trade_weight', IND.tradeWeight);
  };
});
syncIndBalanceButtons();
function updateIndSearchClear(){
  $("#ind-search-clear").classList.toggle("hidden", !$("#ind-search").value);
}
$("#ind-search").addEventListener("input", ()=>{ updateIndSearchClear(); renderIndTable(); });
$("#ind-search-clear").addEventListener("click", ()=>{
  $("#ind-search").value="";
  updateIndSearchClear();
  renderIndTable();
  $("#ind-search").focus();
});

