// ══════════════════════════════════════════════════════════════════════════
// EVE SSO / CHARACTER
// ══════════════════════════════════════════════════════════════════════════
const AUTH = { loggedIn:false, name:null, charId:null, data:null,
               characters:[], activeCharId:null };
const CHAR_REFRESH_MS = 300000;  // ESI caches character industry jobs for 5 min
let charRefreshDeadline = 0;
function tickCharRefreshTimer(){
  const el=$("#char-refresh-timer");
  if(!AUTH.loggedIn || !charRefreshDeadline){ el.classList.add("hidden"); return; }
  el.classList.remove("hidden");
  const remaining=charRefreshDeadline-Date.now();
  $("#char-refresh-secs").textContent=remaining>0?fmtCountdownShort(remaining):"0:00";
  if(remaining<=0) refreshCharData();
}
setInterval(tickCharRefreshTimer, 1000);
const ROMAN=["0","I","II","III","IV","V"];
function authEsc(s){ return String(s==null?"":s).replace(/[&<>"]/g,
  c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c])); }
function romanLvl(n){ return ROMAN[n]||String(n||""); }

// EVE login settings popover removed — login is fully env-configured.

function renderAuthChip(){
  $("#login-eve").classList.toggle("hidden", AUTH.loggedIn);
  $("#char-chip").classList.toggle("hidden", !AUTH.loggedIn);
  $("#char-tab-btn").classList.toggle("hidden", !AUTH.loggedIn);
  $("#char-empty").classList.toggle("hidden", AUTH.loggedIn);
  $("#char-body").classList.toggle("hidden", !AUTH.loggedIn);
  if(AUTH.loggedIn){
    const active=AUTH.characters.find(c=>c.character_id===AUTH.activeCharId);
    $("#chip-name").textContent=(active?active.name:AUTH.name)||"Capsuleer";
    renderCharDropdown();
  }
  if(ACTIVE_TAB==="char" && !AUTH.loggedIn) switchTab("ind");
  else updateIndGate();
}
function renderCharDropdown(){
  const dd=$("#char-dropdown");
  if(!dd) return;
  dd.innerHTML=AUTH.characters.map(c=>{
    const active=c.character_id===AUTH.activeCharId?" ★":"";
    return `<div class="char-dd-row" data-cid="${c.character_id}">`
      +`<span class="char-dd-name">${authEsc(c.name)}${active}</span>`
      +`<button class="char-dd-rm" data-cid="${c.character_id}" title="Remove ${authEsc(c.name)}">✕</button>`
      +`</div>`;
  }).join("")
    +`<div class="char-dd-row char-dd-add"><button id="add-char-btn" class="auth-btn-sm">+ Add character</button></div>`
    +`<div class="char-dd-row char-dd-add"><button id="logout-all-btn" class="auth-btn-sm" style="color:var(--red,#e55)">Logout all</button></div>`;
  dd.querySelectorAll(".char-dd-name").forEach(el=>{
    el.onclick=e=>{
      e.stopPropagation();
      const cid=el.parentElement.dataset.cid;
      switchActiveChar(parseInt(cid));
    };
  });
  dd.querySelectorAll(".char-dd-rm").forEach(btn=>{
    btn.onclick=e=>{ e.stopPropagation(); doLogout(parseInt(btn.dataset.cid)); };
  });
  const addBtn=dd.querySelector("#add-char-btn");
  if(addBtn) addBtn.onclick=e=>{ e.stopPropagation(); doLogin(); };
  const logoutAllBtn=dd.querySelector("#logout-all-btn");
  if(logoutAllBtn) logoutAllBtn.onclick=e=>{ e.stopPropagation(); doLogout(); };
}
async function switchActiveChar(cid){
  await fetch(`/api/auth/switch?active_char_id=${cid}`);
  await checkAuth();
  // The active character also drives the Industry planner (skills / blueprints).
  // Re-run the scan so a table already on screen reflects the new character.
  if(ACTIVE_TAB==="ind" && IND.rows && IND.rows.length) scanInd(false);
}

async function checkAuth(){
  let st; try{ st=await (await fetch("/api/auth/status")).json(); }catch(e){ return null; }
  AUTH.loggedIn=!!st.logged_in; AUTH.name=st.name; AUTH.charId=st.character_id;
  AUTH.characters=st.characters||[];
  AUTH.activeCharId=st.active_char_id||null;
  renderAuthChip();
  if(AUTH.loggedIn){
    refreshCharData();
    if(location.pathname==="/character" || location.pathname==="/char") switchTab("char", {url:false});
    if(!NOTES.loaded || !NOTES.items.length) loadNotes();
  }
  return st;
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
    renderAuthChip(); updateMyLpBadge(); renderIndTable();
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
  saveLS(); recalcIndProfits();
  const refreshMs=d.next_refresh_in!=null?d.next_refresh_in*1000:CHAR_REFRESH_MS;
  charRefreshDeadline=Date.now()+refreshMs; tickCharRefreshTimer();
  const prevLp=$("#lp").value;
  renderCharData(); syncJobTimers(); updateMyLpBadge();
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
      h+=`<tr><td>${authEsc(j.product_name)}</td><td>${authEsc(j.activity)}</td>`
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
  h+=`<section class="char-card"><div class="char-card-header"><h3>Loyalty points</h3></div><div class="char-card-body">`;
  if(cLp.length){
    h+=`<div class="char-card-scroll"><table class="mini"><thead><tr><th>Corporation</th><th style="text-align:right">LP</th></tr></thead><tbody>`;
    for(const l of cLp) h+=`<tr><td>${authEsc(l.corp_name)}</td><td style="text-align:right">${(l.loyalty_points||0).toLocaleString()}</td></tr>`;
    h+=`</tbody></table></div>`;
  } else h+=`<div class="char-none">No loyalty points.</div>`;
  h+=`</div></section>`;

  // Market orders
  h+=`<section class="char-card char-card-wide"><div class="char-card-header"><h3>Market orders`;
  if(cOrders.length) h+=` <span class="char-card-sub">(${cOrders.length} · ${fmtISK(ordersVal)} ISK)</span>`;
  h+=`</h3></div><div class="char-card-body">`;
  if(cOrders.length){
    h+=`<div class="char-card-scroll"><table class="mini char-orders-tbl"><thead><tr>`;
    h+=`<th>Item</th><th>Side</th><th style="text-align:right">Remaining</th><th style="text-align:right">Price</th>`;
    h+=`<th style="text-align:right">Total value</th><th style="text-align:right">Jita sell</th>`;
    h+=`<th style="text-align:right">Queue</th><th style="text-align:right">Posted</th><th style="text-align:right">Expires</th></tr></thead><tbody>`;
    for(const o of cOrders){
      const issuedMs=o.issued?Date.parse(o.issued):NaN;
      const posted=isFinite(issuedMs)?fmtDur((Date.now()-issuedMs)/1000)+" ago":"—";
      const postedTip=isFinite(issuedMs)?` title="${new Date(issuedMs).toLocaleString()}"`:"";
      const expiresMs=isFinite(issuedMs)&&o.duration!=null?issuedMs+o.duration*86400000:NaN;
      const expires=isFinite(expiresMs)?new Date(expiresMs).toLocaleString([],{day:'2-digit',month:'short',hour:'2-digit',minute:'2-digit'}):"—";
      const queueCell=o.is_best==null?`<span style="color:var(--dim)">—</span>`
        :o.is_best?`<span class="tx-sell">Best ✓</span>`:`<span class="tx-buy">#${o.queue_rank} / ${o.queue_total}</span>`;
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
        +`<td style="text-align:right">${expires}</td></tr>`;
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
      h+=`<tr><td>${authEsc(j._char)}</td><td>${authEsc(j.product_name)}</td><td>${authEsc(j.activity)}</td>`
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
  h+=`<section class="char-card char-card-wide"><div class="char-card-header"><h3>Market orders`;
  if(allOrders.length) h+=` <span class="char-card-sub">(${allOrders.length} · ${fmtISK(ordersVal)} ISK)</span>`;
  h+=`</h3></div><div class="char-card-body">`;
  if(allOrders.length){
    const sellOrders=allOrders.filter(o=>!o.is_buy_order);
    const buyOrders=allOrders.filter(o=>o.is_buy_order);
    const sellVal=sellOrders.reduce((s,o)=>s+(o.volume_remain??0)*(o.price||0),0);
    const buyVal=buyOrders.reduce((s,o)=>s+(o.volume_remain??0)*(o.price||0),0);
    h+=`<div class="char-card-scroll"><table class="mini char-orders-tbl"><thead><tr>`;
    h+=`<th>Character</th><th>Item</th><th>Side</th><th style="text-align:right">Remaining</th><th style="text-align:right">Price</th>`;
    h+=`<th style="text-align:right">Total value</th><th style="text-align:right">Jita sell</th>`;
    h+=`<th style="text-align:right">Queue</th><th style="text-align:right">Posted</th></tr></thead><tbody>`;
    for(const o of allOrders){
      const issuedMs=o.issued?Date.parse(o.issued):NaN;
      const posted=isFinite(issuedMs)?fmtDur((Date.now()-issuedMs)/1000)+" ago":"—";
      const postedTip=isFinite(issuedMs)?` title="${new Date(issuedMs).toLocaleString()}"`:"";
      const queueCell=o.is_best==null?`<span style="color:var(--dim)">—</span>`
        :o.is_best?`<span class="tx-sell">Best ✓</span>`:`<span class="tx-buy">#${o.queue_rank} / ${o.queue_total}</span>`;
      const saleTip=o.last_sale_ts?` title="Last sale: ${o.last_sale_qty} unit${o.last_sale_qty>1?'s':''} sold ${_fmtAgo(o.last_sale_ts)}" style="text-align:right;color:var(--green2)"`
        :` style="text-align:right"`;
      h+=`<tr><td>${authEsc(o._char)}</td><td>${authEsc(o.type_name)}</td>`
        +`<td class="${o.is_buy_order?"tx-buy":"tx-sell"}">${o.is_buy_order?"Buy":"Sell"}</td>`
        +`<td${saleTip}>${(o.volume_remain??0).toLocaleString()} / ${(o.volume_total??0).toLocaleString()}</td>`
        +`<td style="text-align:right">${fmtISK(o.price)}</td>`
        +`<td style="text-align:right">${fmtISK((o.volume_remain??0)*o.price)}</td>`
        +`<td style="text-align:right">${o.market_sell!=null?fmtISK(o.market_sell):"—"}</td>`
        +`<td style="text-align:right">${queueCell}</td>`
        +`<td style="text-align:right"${postedTip}>${posted}</td></tr>`;
    }
    h+=`<tr class="total"><td colspan="5">Totals</td>`
      +`<td style="text-align:right">${fmtISK(ordersVal)}</td>`
      +`<td colspan="3" style="text-align:right"><span class="tx-sell">${sellOrders.length} sell (${fmtISK(sellVal)})</span> · <span class="tx-buy">${buyOrders.length} buy (${fmtISK(buyVal)})</span></td></tr>`;
    h+=`</tbody></table></div>`;
  } else h+=`<div class="char-none">No open orders.</div>`;
  h+=`</div></section>`;

  h+=`</div>`;
  return h;
}

// ── Wallet History Chart ────────────────────────────────────────────────
let _walletChart=null;
let _walletHistoryCache=null;
let _walletChartDays=30;
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

async function renderWalletChart(charId){
  const container=document.getElementById('walletChartContainer');
  if(!container) return;
  if(typeof ApexCharts==='undefined'){
    container.innerHTML='<div class="wallet-chart-none">Chart unavailable (no internet)</div>';
    return;
  }
  if(!_walletHistoryCache){
    container.innerHTML='<div class="wallet-chart-none">Loading…</div>';
    _walletHistoryCache=await _loadWalletHistory(_walletChartDays);
  }
  if(!_walletHistoryCache||!Object.keys(_walletHistoryCache).length){
    container.innerHTML='<div class="wallet-chart-none">No wallet history yet. Data will appear after the next refresh cycle.</div>';
    return;
  }

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

  function _updateStatsForRange(minTs, maxTs){
    const statsEl=document.getElementById('walletChartStats');
    if(statsEl) statsEl.innerHTML=_walletChartStats(_walletHistoryCache,_walletChartCharId,minTs,maxTs);
  }

  const opts={
    chart:{
      type:'area', height:200,
      background:'transparent',
      toolbar:{show:true, tools:{zoom:true,zoomin:true,zoomout:true,pan:true,reset:true,download:false}},
      zoom:{enabled:true,type:'x'},
      animations:{enabled:true,easing:'easeinout',speed:400},
      fontFamily:'inherit',
      events:{
        zoomed:function(_ctx,{xaxis}){ _updateStatsForRange(xaxis.min,xaxis.max); },
        scrolled:function(_ctx,{xaxis}){ _updateStatsForRange(xaxis.min,xaxis.max); },
        beforeResetZoom:function(){ _updateStatsForRange(null,null); },
      },
    },
    theme:{mode:'dark'},
    colors:charId?['#4fc3f7']:['#4fc3f7','#66bb6a','#f0c040','#e05555','#ab47bc'],
    series:apexSeries,
    xaxis:{type:'datetime',labels:{style:{colors:'#5a7a95',fontSize:'10px'}}},
    yaxis:{labels:{style:{colors:'#5a7a95',fontSize:'10px'},formatter:v=>fmtISK(v)}},
    stroke:{curve:'smooth',width:charId?2:[3,1.5,1.5,1.5,1.5]},
    fill:{type:'gradient',gradient:{opacityFrom:0.25,opacityTo:0.02}},
    tooltip:{theme:'dark',x:{format:'dd MMM HH:mm'},y:{formatter:v=>fmtISK(v)+' ISK'}},
    grid:{borderColor:'#1f3044',strokeDashArray:3},
    legend:{show:apexSeries.length>1,position:'top',fontSize:'11px',
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

  // Sale notifications
  if(events.length){
    const shown=events.slice(0,10);
    html+=`<div class="char-events"><div class="char-events-hdr">`;
    html+=`<span class="char-events-title">Sales activity (${events.length})</span>`;
    html+=`<button class="char-events-dismiss" data-eid="all">dismiss all</button></div>`;
    for(const e of shown){
      const isk=e.sold*e.price;
      html+=`<div class="char-event-row">`;
      html+=`<span class="ev-icon">${e.filled?'✓':'↓'}</span>`;
      html+=`<span class="ev-qty">${e.sold}x</span> ${authEsc(e.type_name)}`;
      html+=` <span class="ev-isk">${fmtISK(isk)} ISK</span>`;
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
function syncJobTimers(){
  IND.timers={};
  (AUTH.data&&AUTH.data.jobs||[]).forEach(j=>{
    if(j.activity_id!==1) return;          // manufacturing only
    const end=Date.parse(j.end), bp=j.blueprint_type_id;
    if(isFinite(end) && bp) IND.timers[bp]=end;
  });
  if(ACTIVE_TAB==="ind") renderIndTable();
  if(IND.openDetail) renderIndDetail(IND.openDetail);
}

// When logged in, drive the LP budget from the character's loyalty points for
// the selected corp and lock the field read-only. Shows 0 if the character
// has no LP with that corp. Falls back to editable only when not logged in.
function updateMyLpBadge(){
  const badge=$("#lp-mylp"), inp=$("#lp");
  const corp=($("#corp").value||"").trim().toLowerCase();
  const lp=(AUTH.data&&AUTH.data.loyalty)||[];
  const m=(AUTH.loggedIn&&corp)?lp.find(l=>(l.corp_name||"").toLowerCase()===corp):null;
  if(AUTH.loggedIn){
    inp.value=m?m.loyalty_points||0:0;
    inp.readOnly=true; inp.classList.add("locked");
    inp.title="Read from your character's loyalty points.";
    badge.textContent=m?"🔒 from character":"🔒 0 LP with this corp";
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
$("#chip-dd-toggle").onclick=e=>{
  e.stopPropagation();
  const dd=$("#char-dropdown");
  dd.classList.toggle("hidden");
};
document.addEventListener("click",e=>{
  const dd=$("#char-dropdown");
  if(dd && !dd.classList.contains("hidden") && !dd.contains(e.target) && e.target.id!=="chip-dd-toggle")
    dd.classList.add("hidden");
});
$("#char-chip").onclick=e=>{
  if(e.target.closest("#chip-dd-toggle")||e.target.closest("#char-dropdown")) return;
  switchTab("char");
};

// Clicking the countdown forces an immediate cache-busting refresh.
$("#char-refresh-timer").onclick=()=>{ if(AUTH.loggedIn) refreshCharData(true); };
$("#char-refresh-timer").style.cursor="pointer";

// Re-pull character data (wallet, jobs, skill queue, LP) on EVE's cache cadence
// so the job timers stay current. The per-second ticker handles the countdown
// itself; this just refreshes the underlying job list every 5 minutes.
setInterval(()=>{ if(AUTH.loggedIn) refreshCharData(); }, CHAR_REFRESH_MS);

// When the tab returns from background (sleep, alt-tab, phone lock), the
// setInterval may have drifted far past the deadline. Refresh immediately.
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
  if(i!==""&&IND.profiles[i]){
    $("#ind-jobrate").value=structEffectiveRate(IND.profiles[i]).toFixed(2);
    saveIndPrefs();
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
function saveStructWizard(){
  const name=$("#sw-name").value.trim();
  if(!name){ $("#sw-name").focus(); return; }
  const p={ name,
    system_index:+$("#sw-index").value||0,
    role_bonus:+$("#sw-bonus").value||0,
    facility_tax:+$("#sw-facility").value||0,
    scc_surcharge:+$("#sw-scc").value||0 };
  let idx;
  if(IND_EDIT_IDX!=null){ IND.profiles[IND_EDIT_IDX]=p; idx=IND_EDIT_IDX; }
  else { IND.profiles.push(p); idx=IND.profiles.length-1; }
  renderIndProfiles();
  $("#ind-profile").value=String(idx);
  $("#ind-jobrate").value=structEffectiveRate(p).toFixed(2);
  saveIndPrefs();
  closeStructWizard();
}
function deleteStruct(){
  if(IND_EDIT_IDX==null) return;
  IND.profiles.splice(IND_EDIT_IDX,1);
  renderIndProfiles();
  $("#ind-profile").value="";
  saveIndPrefs();
  closeStructWizard();
}

function saveIndPrefs(){
  const obj=Object.fromEntries(indParams({
    profiles: JSON.stringify(IND.profiles),
    profile:  $("#ind-profile").value,
    sort_key: IND.sort.key,
    sort_dir: String(IND.sort.dir),
    hidden_bps: JSON.stringify([...IND.hidden]),
    col_order: JSON.stringify(IND.colOrder),
    col_widths: JSON.stringify(IND.colw),
    col_vis: JSON.stringify(IND.colVis),
    ind_trade_weight: String(IND.tradeWeight),
  }));
  postPrefs('/api/ind/prefs',obj); saveLS();
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
["#ind-group","#ind-station"].forEach(sel=>{
  const el=$(sel); if(!el) return;
  el.addEventListener("change", saveIndPrefs);
});
["#ind-jobrate"].forEach(sel=>{
  const el=$(sel); if(!el) return;
  el.addEventListener("change", ()=>{ saveIndPrefs(); recalcIndProfits(); });
});
["#ind-buildable","#ind-unobtainable","#ind-hidet2","#ind-hidebpc"].forEach(sel=>$(sel).addEventListener("change", saveIndPrefs));
$("#ind-hidebpc").addEventListener("change", renderIndTable);
// Min-tradeability is a client-side filter — re-render immediately (no rescan).
$("#ind-mintrade").addEventListener("input", ()=>{ saveIndPrefs(); renderIndTable(); });
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
    saveIndPrefs();
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

