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
      const tracked=_jobIsTracked(j);
      const link=tracked?` <span class="char-job-tracked" title="You're tracking a build for this job — click to open its tracked build in Industry">🔗</span>`:"";
      const cls=tracked?" char-job-row":"";
      const tip=tracked?` title="Open its tracked build in Industry"`:"";
      h+=`<tr class="${cls.trim()}" data-job-id="${j.job_id}"${tip}>`
        +`<td>${authEsc(j.product_name)}${link}</td><td>${authEsc(j.activity)}</td>`
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
    h+=`<div class="char-card-scroll char-orders-scroll"><table class="mini char-orders-tbl"><thead><tr>`;
    h+=`<th>Item</th><th>Side</th><th style="text-align:right">Remaining</th><th style="text-align:right">Price</th>`;
    h+=`<th style="text-align:right">Total value</th><th style="text-align:right">Jita sell</th>`;
    h+=`<th style="text-align:right">Queue</th><th style="text-align:right">Posted</th><th style="text-align:right">Expires</th></tr></thead><tbody>`;
    for(const o of cOrders){
      const issuedMs=o.issued?Date.parse(o.issued):NaN;
      const posted=isFinite(issuedMs)?fmtDur((Date.now()-issuedMs)/1000)+" ago":"—";
      const postedTip=isFinite(issuedMs)?` title="${new Date(issuedMs).toLocaleString()}"`:"";
      const expiresMs=isFinite(issuedMs)&&o.duration!=null?issuedMs+o.duration*86400000:NaN;
      const exp=_fmtExpires(expiresMs);
      const expTip=isFinite(expiresMs)?` title="${new Date(expiresMs).toLocaleString()}"`:"";
      const queueCell=o.is_best==null?`<span style="color:var(--dim)">—</span>`
        :o.is_best?`<span class="ord-best">Best ✓</span>`:`<span class="ord-queue">#${o.queue_rank} / ${o.queue_total}</span>`;
      const saleTip=o.last_sale_ts?` title="Last sale: ${o.last_sale_qty} unit${o.last_sale_qty>1?'s':''} sold ${_fmtAgo(o.last_sale_ts)}" style="text-align:right;color:var(--green2)"`
        :` style="text-align:right"`;
      h+=`<tr><td>${authEsc(o.type_name)}</td>`
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
      const tracked=_jobIsTracked(j);
      const link=tracked?` <span class="char-job-tracked" title="You're tracking a build for this job — click to open its tracked build in Industry">🔗</span>`:"";
      const cls=tracked?" char-job-row":"";
      const tip=tracked?` title="Open its tracked build in Industry"`:"";
      h+=`<tr class="${cls.trim()}" data-job-id="${j.job_id}"${tip}>`
        +`<td>${authEsc(j._char)}</td><td>${authEsc(j.product_name)}${link}</td><td>${authEsc(j.activity)}</td>`
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
    h+=`<div class="char-card-scroll char-orders-scroll"><table class="mini char-orders-tbl"><thead><tr>`;
    h+=`<th>Character</th><th>Item</th><th>Side</th><th style="text-align:right">Remaining</th><th style="text-align:right">Price</th>`;
    h+=`<th style="text-align:right">Total value</th><th style="text-align:right">Jita sell</th>`;
    h+=`<th style="text-align:right">Queue</th><th style="text-align:right">Posted</th><th style="text-align:right">Expires</th></tr></thead><tbody>`;
    for(const o of allOrders){
      const issuedMs=o.issued?Date.parse(o.issued):NaN;
      const posted=isFinite(issuedMs)?fmtDur((Date.now()-issuedMs)/1000)+" ago":"—";
      const postedTip=isFinite(issuedMs)?` title="${new Date(issuedMs).toLocaleString()}"`:"";
      const expiresMs=isFinite(issuedMs)&&o.duration!=null?issuedMs+o.duration*86400000:NaN;
      const exp=_fmtExpires(expiresMs);
      const expTip=isFinite(expiresMs)?` title="${new Date(expiresMs).toLocaleString()}"`:"";
      const queueCell=o.is_best==null?`<span style="color:var(--dim)">—</span>`
        :o.is_best?`<span class="ord-best">Best ✓</span>`:`<span class="ord-queue">#${o.queue_rank} / ${o.queue_total}</span>`;
      const saleTip=o.last_sale_ts?` title="Last sale: ${o.last_sale_qty} unit${o.last_sale_qty>1?'s':''} sold ${_fmtAgo(o.last_sale_ts)}" style="text-align:right;color:var(--green2)"`
        :` style="text-align:right"`;
      h+=`<tr><td>${authEsc(o._char)}</td><td>${authEsc(o.type_name)}</td>`
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
    html+=`<div class="char-events"><div class="char-events-hdr">`;
    html+=`<span class="char-events-title">Order activity (${events.length})</span>`;
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

// Find the tracked build covering an industry job — by job_id first (exact
// link), then by blueprint (+ runs when the job reports them). job_id round-trips
// as a string, so compare via String().
function _trackedBuildForJob(j){
  const builds=(typeof IND!=="undefined" && IND.builds)||[];
  return builds.find(b=>b.job_id!=null && String(b.job_id)===String(j.job_id))
    || builds.find(b=>b.blueprint_id===j.blueprint_type_id && (j.runs==null || b.runs===j.runs))
    || null;
}
function _jobIsTracked(j){ return !!_trackedBuildForJob(j); }

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

