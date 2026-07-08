// ══════════════════════════════════════════════════════════════════════════
// LP TAB
// ══════════════════════════════════════════════════════════════════════════
let STATE = {rows:[], sort:{key:"isk_per_lp_best", dir:-1}, ctx:{}, selOffer:null,
             colw:{}, colVis:{}, hideIlliquid:false, hideUnaffordable:false, lastScanData:null,
             tradeWeight:0.5,  // liquidity↔competition blend: 0=all competition, 1=all liquidity
             lotTrackerOpen:false, recipeOpen:false,
             shoppingOpen:true, costOpen:false, cargoOpen:false, saleOpen:false};

// Tradeability = a 0–100 blend of two raw signals, each scored by its rank
// against the other offers in this store (so there's no invented "good volume"
// constant): liquidity (higher daily_vol = better) and low competition (lower
// days_to_clear = better). STATE.tradeWeight sets the proportion. Recomputed
// here on every render and whenever the user changes the balance preset.
function _computeTradeability(rows, dayField, weight){
  const loaded=rows.filter(r=>r.liq_loaded && r.daily_vol!==null);
  if(!loaded.length){ rows.forEach(r=>r.tradeability=null); return; }
  const sortedVols=[...loaded.map(r=>r.daily_vol)].sort((a,b)=>a-b);
  const sortedDays=[...loaded.map(r=>{const d=r[dayField]; return d===null?Infinity:d;})].sort((a,b)=>a-b);
  const bisect=(arr,v)=>{let lo=0,hi=arr.length;while(lo<hi){const m=(lo+hi)>>1;if(arr[m]<v)lo=m+1;else hi=m;}return lo;};
  const pctRank=(sorted,v,higherBetter)=>{
    const n=sorted.length; if(n<=1) return 100;
    const pos=bisect(sorted,v);
    const beats=higherBetter? pos : n-pos-(sorted[pos]===v?1:0);
    return beats/(n-1)*100;
  };
  for(const r of rows){
    if(!r.liq_loaded || r.daily_vol===null){ r.tradeability=null; continue; }
    const dayVal=r[dayField]===null?Infinity:r[dayField];
    const liq=pctRank(sortedVols, r.daily_vol, true);
    const comp=pctRank(sortedDays, dayVal, false);
    r.tradeability=Math.round(weight*liq + (1-weight)*comp);
  }
}
function computeTradeability(){ _computeTradeability(STATE.rows, 'days_to_clear', STATE.tradeWeight); }
let LP_RESIZING = false;

const fmtIpl = v => (v===null||v===undefined) ? "—" : v.toLocaleString(undefined,{maximumFractionDigits:1});
const COLS = [
  {k:"name",               t:"Reward Item",     w:220, defvis:true,  tip:"The item this LP offer gives you.  * = a required input has no Jita price  ·  ^ = costs Analysis Kredits  ·  ! = illiquid (spread ≥25%)"},
  {k:"isk_per_lp_patient", t:"List ISK/LP",        w:100, defvis:true,  tip:"Profit per Loyalty Point if you LIST a sell order at the ask and wait (pay sales tax + broker fee).", f:fmtIpl, pn:true},
  {k:"isk_per_lp_instant", t:"Instant-sell ISK/LP",w:120, defvis:true,  tip:"Profit per Loyalty Point if you INSTANT-SELL into a buy order at the bid (pay sales tax only).", f:fmtIpl, pn:true},
  {k:"total_profit_patient",t:"List profit",       w:105, defvis:true,  tip:"Total profit across your whole LP budget, listing sell orders at the ask.", f:(v,r)=>r.max_units===0?"—":(v===null?"—":fmtISK(v)), pn:true, rowCtx:true},
  {k:"total_profit_instant",t:"Instant-sell profit",w:120, defvis:true,  tip:"Total profit across your whole LP budget, instant-selling into buy orders.", f:(v,r)=>r.max_units===0?"—":(v===null?"—":fmtISK(v)), pn:true, rowCtx:true},
  {k:"tradeability", t:"Tradeability",  w: 95, defvis:true,  tip:"0–100: how realistically you can sell at your price. Blends liquidity (Daily Vol) and low competition (Days to Clear), weighted by the Balance buttons. Higher is better; ranked within this store.", f:fmtTrade, rowCtx:true, cls:"spread"},
  {k:"daily_vol",    t:"Daily Vol",     w: 90, defvis:true,  tip:"Units traded per day at the hub (30-day median). High = deep market you can sell into; low = thin and hard to offload.", f:fmtVolPerDay, rowCtx:true},
  {k:"days_to_clear",t:"Days to Clear", w: 95, defvis:true,  tip:"Sell-side backlog: units listed ÷ units sold per day. “5 d” = 5 days of stock ahead of you. <1 d sells fast; ∞ = barely trades.", f:fmtDays, rowCtx:true, cls:"spread"},
  {k:"spread_pct",   t:"Spread",        w: 70, defvis:true,  tip:"Ask vs bid gap. ≥25% (!) means the ask isn't backed by real buyers — the patient (sell) figure is unreliable, prefer the buy column.", f:fmtSpread, cls:"spread"},
  {k:"max_units",    t:"Max Runs",      w: 80, defvis:true,  tip:"Redemptions your LP budget affords (budget ÷ LP per run). Affordability only — it doesn't check whether the market can absorb them.", f:v=>v===0?"—":fmtNum(v)},
  {k:"lp_cost",      t:"LP / Run",      w: 80, defvis:true,  tip:"Loyalty Points per redemption.", f:fmtNum},
  {k:"cost_ea",      t:"ISK / Run",     w: 95, defvis:true,  tip:"ISK + required input costs per redemption.", f:fmtISK},
  {k:"list_price",   t:"List @",        w:100, defvis:true,  tip:"Suggested per-unit price to put on your sell order: the lowest current sell, unless that's below the 30-day fair value (someone's dumping) — then it holds at fair value. Per unit of the reward item.", f:fmtListPrice, rowCtx:true},
  {k:"floor_age",    t:"Floor age",     w: 95, defvis:true,  tip:"How long ago the current cheapest sell order at the hub was posted (from its issued timestamp). A fresh floor in a thin market means the price is actively moving. “no orders” = nothing listed.", f:fmtFloorAge, rowCtx:true, cls:"spread"},
  {k:"ask",          t:"Ask (sell)",    w: 95, defvis:false, tip:"Lowest sell order price at the hub — what the patient column lists at.", f:fmtISK},
  {k:"bid",          t:"Bid (buy)",     w: 95, defvis:false, tip:"Highest buy order price at the hub — what the instant column dumps into.", f:fmtISK},
  {k:"buy_volume",   t:"Buy Demand",    w: 95, defvis:false, tip:"Units on hub buy orders — how many you could sell instantly.", f:fmtNum},
  {k:"qty",          t:"Units",         w: 55, defvis:false, tip:"Units per redemption.", f:fmtNum},
  {k:"output_volume",t:"Vol m³",        w:140, defvis:false, tip:"Packaged m³ per redemption, and total for all runs in parentheses.", f:(v,r)=>{ if(v===null) return "?"; const per=fmtVol(v); return r.max_units>0?`${per} (${fmtVol(v*r.max_units)})`:per; }, rowCtx:true},
];
COLS.forEach(c=>{ STATE.colVis[c.k]=c.defvis; STATE.colw[c.k]=c.w; });
const COL_BY_KEY=Object.fromEntries(COLS.map(c=>[c.k,c]));
STATE.colOrder=COLS.map(c=>c.k);  // user-reorderable; persisted with col widths
// Resolve STATE.colOrder to column objects, dropping unknown keys and appending
// any columns that aren't listed yet (so a saved order survives COLS additions).
function orderedCols(){
  const seen=new Set(), out=[];
  for(const k of STATE.colOrder){ const c=COL_BY_KEY[k]; if(c&&!seen.has(k)){ out.push(c); seen.add(k); } }
  for(const c of COLS) if(!seen.has(c.k)){ out.push(c); seen.add(c.k); }
  return out;
}
function visCols(){ return orderedCols().filter(c=>STATE.colVis[c.k]!==false); }

function lpSetColgroup(){
  $("#cg").innerHTML=visCols().map(c=>`<col style="width:${STATE.colw[c.k]||c.w}px">`).join("");
}

const _LP_RESIZE_CTX={get resizing(){return LP_RESIZING;},set resizing(v){LP_RESIZING=v;},tblSel:'#tbl',get colw(){return STATE.colw;},setCg:lpSetColgroup,save:saveLPColWidths};
function startLPResize(e,key){ startResize(e,key,_LP_RESIZE_CTX); }

// ── Column drag-to-reorder ────────────────────────────────────────────────
// HTML5 drag-and-drop on the <th>s. The resizer's mousedown preventDefault()
// suppresses a drag starting from the resize grip, and a sort-click never fires
// after a real drag, so the three header interactions stay independent.
let LP_DRAG_KEY=null;
function clearLPDropMarks(){
  document.querySelectorAll("#tbl thead th").forEach(th=>th.classList.remove("drop-before","drop-after"));
}
function lpDropAfter(th,clientX){
  const r=th.getBoundingClientRect();
  return clientX > r.left + r.width/2;
}
function reorderLPCols(srcKey,dstKey,after){
  if(!srcKey||srcKey===dstKey) return;
  const order=orderedCols().map(c=>c.k);   // full order, hidden cols included
  order.splice(order.indexOf(srcKey),1);
  let to=order.indexOf(dstKey);
  if(after) to+=1;
  order.splice(to,0,srcKey);
  STATE.colOrder=order;
  saveLPColWidths();   // col_order rides along with widths under the same version
  renderTable();
}
function wireLPColDrag(th){
  th.addEventListener("dragstart",e=>{
    LP_DRAG_KEY=th.dataset.k;
    e.dataTransfer.effectAllowed="move";
    try{ e.dataTransfer.setData("text/plain",LP_DRAG_KEY); }catch(_){}
    th.classList.add("col-dragging");
    document.body.classList.add("col-dragging-active");
  });
  th.addEventListener("dragend",()=>{
    th.classList.remove("col-dragging");
    document.body.classList.remove("col-dragging-active");
    clearLPDropMarks();
    setTimeout(()=>{ LP_DRAG_KEY=null; },0);
  });
  th.addEventListener("dragover",e=>{
    if(!LP_DRAG_KEY) return;
    e.preventDefault();
    e.dataTransfer.dropEffect="move";
    clearLPDropMarks();
    if(th.dataset.k!==LP_DRAG_KEY)
      th.classList.add(lpDropAfter(th,e.clientX)?"drop-after":"drop-before");
  });
  th.addEventListener("dragleave",()=>th.classList.remove("drop-before","drop-after"));
  th.addEventListener("drop",e=>{
    e.preventDefault();
    const after=lpDropAfter(th,e.clientX);
    clearLPDropMarks();
    reorderLPCols(LP_DRAG_KEY, th.dataset.k, after);
  });
}

function renderTable(){
  const _il=$("#init-loading"); if(_il) _il.remove();
  computeTradeability();
  const thead=$("#tbl thead"), tbody=$("#tbl tbody");
  const vc=visCols();
  $("#tbl").style.tableLayout="fixed";
  lpSetColgroup();
  thead.innerHTML="<tr>"+vc.map(c=>{
    const active=STATE.sort.key===c.k;
    const arrow=active?(STATE.sort.dir<0?" ▼":" ▲"):"";
    const tip=c.tip?` data-tip="${c.tip.replace(/"/g,'&quot;')}"`: "";
    return `<th draggable="true" data-k="${c.k}"${tip}${active?' class="sorted"':''}>${c.t}${arrow}<span class="resizer"></span></th>`;
  }).join("")+"</tr>";
  thead.querySelectorAll("th").forEach((th,i)=>{
    th.onclick=()=>{
      if(LP_RESIZING){ LP_RESIZING=false; return; }
      if(LP_DRAG_KEY){ return; }  // tail end of a reorder, not a sort click
      const k=th.dataset.k;
      if(STATE.sort.key===k) STATE.sort.dir*=-1;
      else STATE.sort={key:k, dir:k==="name"?1:-1};
      saveLPSort(); renderTable();
    };
    th.querySelector(".resizer").addEventListener("mousedown",e=>startLPResize(e,vc[i].k));
    wireLPColDrag(th);
  });
  const _lpSearch=($("#lp-search").value||"").trim().toLowerCase();
  const _maxSpread=parseFloat($("#maxspread").value);
  const rows=[...STATE.rows]
    .filter(r=>!_lpSearch||r.name.toLowerCase().includes(_lpSearch))
    .filter(r=>_lpSearch||isNaN(_maxSpread)||r.unsellable||r.spread_pct===null||r.spread_pct<=_maxSpread)
    .filter(r=>!STATE.hideIlliquid||!r.illiquid||r.unsellable)
    .filter(r=>!STATE.hideUnaffordable||r.max_units>0)
    .sort((a,b)=>{
      const k=STATE.sort.key, d=STATE.sort.dir;
      let x=a[k], y=b[k];
      if(typeof x==="string") return x.localeCompare(y)*d;
      if(x===null) x=-Infinity; if(y===null) y=-Infinity;
      return (x-y)*d;
    });
  tbody.innerHTML=rows.map(r=>{
    const tds=vc.map(c=>{
      let v=r[c.k], txt=c.f?(c.rowCtx?c.f(v,r):c.f(v)):v;
      let cls=c.cls||"";
      if(c.k==="spread_pct"&&v!==null) cls+=v<10?" tight":v<25?" mid":"";
      if(c.k==="name"){
        let flag=""; if(r.req_missing) flag+="*"; if(r.ak_cost) flag+="^"; if(r.illiquid) flag+="!";
        txt=txt+(flag?` <span class="flag">${flag}</span>`:"");
      }
      if(c.pn) cls+=(v>0?" pos":(v<0?" neg":""));
      // Mark the better of the two sell-mode cells so the comparison reads at a glance.
      if((c.k==="isk_per_lp_patient"||c.k==="isk_per_lp_instant")
         && r.isk_per_lp_best!==null && v!==null && v===r.isk_per_lp_best) cls+=" win";
      if((c.k==="total_profit_patient"||c.k==="total_profit_instant")
         && r.total_profit_best!==null && v!==null && v===r.total_profit_best && r.max_units>0) cls+=" win";
      return `<td class="${cls}">${txt}</td>`;
    }).join("");
    return `<tr class="${r.illiquid?'illiquid':''} ${r.unsellable?'unsellable':''} ${r.offer_id===STATE.selOffer?'sel':''}" data-id="${r.offer_id}">${tds}</tr>`;
  }).join("");
  tbody.querySelectorAll("tr").forEach(tr=>tr.onclick=()=>openDetail(+tr.dataset.id));
}

// A scan supersedes anything already running. `_scanSeq` bumps on every scan so
// a slow response from an earlier corp can't land late and clobber the current
// one, and `_scanAbort` cancels the previous scan + liquidity fetches outright
// (closing those connections) so the browser stops waiting on stale work.
let _scanSeq=0, _scanAbort=null;
async function scan(forceRefresh=false){
  const _il=$("#init-loading"); if(_il) _il.remove();
  const corp=$("#corp").value.trim();
  if(!corp){ setStatus("Enter a corporation name.",true); return; }
  // Refresh only re-pulls offers + prices from ESI; it does NOT re-run the
  // expensive tradeability / liquidity fill. Snapshot the current saturation
  // values first so we can carry them onto the refreshed rows (the numbers move
  // slowly, so last scan's figures stay meaningful) — only an explicit Scan
  // fetches them fresh.
  let carry=null, carryComputedAt=null;
  if(forceRefresh){
    carry={};
    // Keep the original compute time so the age shown in the status line reflects
    // when these numbers were actually calculated, not this refresh.
    carryComputedAt=STATE.lastScanData && STATE.lastScanData.liq_computed_at || null;
    for(const r of STATE.rows) if(r.liq_loaded)
      carry[r.offer_id]={daily_vol:r.daily_vol, days_to_clear:r.days_to_clear,
        list_price:r.list_price, floor_age:r.floor_age};
  }
  // Supersede any in-flight scan: abort it, bump the token, and wipe the table
  // now so the previous corp's rows don't linger while the new data loads.
  if(_scanAbort) _scanAbort.abort();
  _scanAbort=new AbortController();
  const seq=++_scanSeq, signal=_scanAbort.signal;
  STATE.rows=[]; STATE.selOffer=null; STATE.lastScanData=null; closeDetail(); renderTable();
  const btn=$("#refresh");
  if(forceRefresh){ btn.disabled=true; btn.textContent="⟳ Fetching…"; }
  setStatus("Scanning "+corp+(forceRefresh?" (refreshing from ESI)":"")+" …");
  STATE.ctx={lp:$("#lp").value, tax:pctToFrac($("#g-tax").value), broker:pctToFrac($("#g-broker").value), station:$("#market").value};
  const p=new URLSearchParams({corp, ...STATE.ctx});
  const ms=$("#maxspread").value.trim(); if(ms) p.set("max_spread",ms);
  if(forceRefresh) p.set("refresh","1");
  try{
    const res=await fetch("/api/scan?"+p, {signal});
    const data=await res.json();
    if(seq!==_scanSeq) return;  // a newer scan started while we waited
    if(data.error){ setStatus(data.error,true); return; }
    STATE.rows=data.rows; STATE.ctx.corp_id=data.corp_id; STATE.selOffer=null;
    STATE.lastScanData=data; closeDetail(); renderLPStatus();
    if(forceRefresh){
      // Carry over the prior saturation values instead of re-fetching; rows with
      // no prior data (e.g. a newly added offer) render "no data" rather than
      // spinning, since we won't fetch until the next Scan.
      for(const r of STATE.rows){
        const c=carry[r.offer_id];
        if(c){ r.daily_vol=c.daily_vol; r.days_to_clear=c.days_to_clear;
          r.list_price=c.list_price; r.floor_age=c.floor_age; }
        r.liq_loaded=true;
      }
      if(carryComputedAt) data.liq_computed_at=carryComputedAt;
      renderTable(); renderLPStatus();
      persistScan("lp", {...data, rows:STATE.rows});
    } else {
      renderTable();
      fillLiquidity(seq, signal);
    }
  }catch(e){
    if(e.name==="AbortError") return;  // superseded; the newer scan owns the UI
    setStatus("Request failed: "+e,true);
  }
  finally{ if(seq===_scanSeq){ btn.disabled=false; btn.textContent="⟳ Refresh"; } }
}

// Background-fill the market-saturation columns (Days to Clear / Capped Profit)
// after the table is already on screen. One history call per type server-side,
// so this can take a few seconds on a fresh corp; rows show "…" until it lands.
//
// Give up after this long so the saturation columns can't spin forever when the
// fill stalls (a slow / unfetchable market). Generous enough that a normal fill
// on a big corp finishes first — if real data lands after we've given up it just
// replaces the "no data" placeholders.
const LIQ_TIMEOUT_MS = 45000;
// Stop the spinners on every row still waiting: mark them loaded so the columns
// fall back to their "no data" / "—" / "no orders" text instead of "…". Used
// both when the fill fails and when an offer simply has no market data (e.g. an
// untradeable / unsellable item the server returns no liquidity entry for).
function _liqGiveUp(seq){
  if(seq!==_scanSeq) return;  // superseded by a newer scan
  let changed=false;
  for(const r of STATE.rows) if(!r.liq_loaded){ r.liq_loaded=true; changed=true; }
  if(changed){ renderTable(); if(STATE.detail&&STATE.selOffer) renderDetail(); }
}
async function fillLiquidity(seq, signal){
  const corpId=STATE.ctx.corp_id; if(!corpId) return;
  const p=new URLSearchParams({corp_id:corpId, lp:STATE.ctx.lp,
    tax:STATE.ctx.tax, broker:STATE.ctx.broker, station:STATE.ctx.station});
  let settled=false;
  const giveUp=setTimeout(()=>{ if(!settled) _liqGiveUp(seq); }, LIQ_TIMEOUT_MS);
  try{
    const d=await (await fetch("/api/liquidity?"+p, {signal})).json();
    settled=true; clearTimeout(giveUp);
    if(seq!==_scanSeq) return;  // a newer scan superseded this fill
    if(d.error||!d.liquidity){ _liqGiveUp(seq); return; }
    if(STATE.ctx.corp_id!==corpId) return;  // user re-scanned; drop stale fill
    const liq=d.liquidity;
    // Mark EVERY row loaded, not just the ones with an entry: untradeable /
    // unsellable offers get no liquidity entry, so keying off the map alone left
    // them spinning forever. They keep their null values and render "no data".
    for(const r of STATE.rows){
      const e=liq[r.offer_id];
      if(e){ r.daily_vol=e.daily_vol; r.days_to_clear=e.days_to_clear; r.list_price=e.list_price; r.floor_age=e.floor_age; }
      r.liq_loaded=true;
    }
    // Stamp when these saturation figures were computed so the status line can
    // flag them as stale once carried across enough Refreshes (see renderLPStatus).
    if(STATE.lastScanData) STATE.lastScanData.liq_computed_at=Math.floor(Date.now()/1000);
    renderTable(); renderLPStatus();
    if(STATE.detail&&STATE.selOffer) renderDetail();
    persistScan("lp", STATE.lastScanData ? {...STATE.lastScanData, rows:STATE.rows} : null);
  }catch(e){
    settled=true; clearTimeout(giveUp);
    if(e.name==="AbortError"||seq!==_scanSeq) return;  // superseded; leave it be
    _liqGiveUp(seq);  // network / parse error — give up so rows don't spin forever
  }
}

// Tradeability etc. are carried across Refreshes rather than recomputed, so they
// can drift out of date. Flag them once they're older than this.
const LIQ_STALE_SEC = 2*3600;
function renderLPStatus(){
  const d=STATE.lastScanData; if(!d||ACTIVE_TAB!=="lp") return;
  let trade="";
  if(d.liq_computed_at){
    const stale=(Date.now()/1000 - d.liq_computed_at) > LIQ_STALE_SEC;
    trade = stale
      ? ` · <span class="ts-stale" data-tip="Tradeability, Daily Vol, Days to Clear, List @ and Floor age were calculated ${fmtTs(d.liq_computed_at)} and aren't refreshed by ⟳ Refresh. Click Scan to recompute.">⚠ tradeability ${fmtTs(d.liq_computed_at)}</span>`
      : ` · tradeability ${fmtTs(d.liq_computed_at)}`;
  }
  setStatus(
    `<span class="pill"><b>${d.corp_name}</b></span>`
    +`<span class="pill"><b>${d.count}</b> offers</span>`
    +`<span class="pill"><b>${Number(d.lp).toLocaleString()}</b> LP · list vs instant sell</span>`
    +`<span class="ts">offers ${fmtTs(d.offers_fetched_at)} · prices ${fmtTs(d.scanned_at)}${trade}</span>`);
}

function saveLPSort(){
  const s=STATE.sort;
  postPrefs('/api/prefs',{sort_key:s.key,sort_dir:s.dir}); saveLS();
}
function saveLPColWidths(){
  postPrefs('/api/prefs',{col_widths:JSON.stringify(STATE.colw),col_order:JSON.stringify(STATE.colOrder),col_layout_v:String(COL_LAYOUT_VERSION)}); saveLS();
}

// ── Column picker ─────────────────────────────────────────────────────────
(function(){
  const btn=document.getElementById("colPickerBtn");
  const picker=document.getElementById("colPicker");
  function renderPicker(){
    picker.innerHTML=COLS.map(c=>`<label><input type="checkbox" data-k="${c.k}"${STATE.colVis[c.k]!==false?' checked':''}> ${c.t}</label>`).join("");
    picker.querySelectorAll("input").forEach(cb=>{
      cb.onchange=()=>{ STATE.colVis[cb.dataset.k]=cb.checked; renderTable(); saveLS(); };
    });
  }
  btn.onclick=e=>{
    e.stopPropagation();
    if(!picker.classList.contains("hidden")){ picker.classList.add("hidden"); return; }
    renderPicker();
    const r=btn.getBoundingClientRect();
    picker.style.top=(r.bottom+4)+"px";
    picker.style.left=r.left+"px";
    picker.classList.remove("hidden");
  };
  document.addEventListener("click",()=>picker.classList.add("hidden"));
  picker.addEventListener("click",e=>e.stopPropagation());
})();

// ── LP detail panel ───────────────────────────────────────────────────────
async function openDetail(offerId){
  STATE.selOffer=offerId; STATE.recipeOpen=false; renderTable();
  const p=new URLSearchParams({corp_id:STATE.ctx.corp_id, offer_id:offerId,
    lp:STATE.ctx.lp, tax:STATE.ctx.tax, broker:STATE.ctx.broker,
    station:STATE.ctx.station});
  const inner=$("#detail .inner");
  inner.innerHTML="<div class='muted'>Loading volumes…</div>";
  $("#detail").classList.add("open");
  try{
    const d=await (await fetch("/api/detail?"+p)).json();
    if(d.error){ inner.innerHTML=`<span style='color:var(--red)'>${d.error}</span>`; return; }
    STATE.detail=d; renderDetail();
  }catch(e){ inner.innerHTML=`<span style='color:var(--red)'>${e}</span>`; }
}
function closeDetail(){ $("#detail").classList.remove("open"); STATE.selOffer=null; }

function renderDetail(){
  const d=STATE.detail;
  const def=Math.max(d.max_units||0,1);
  const inner=$("#detail .inner");
  inner.innerHTML=`
    <div class="dheader">
      <div><h2>${d.output.name} <button class="lp-copy" title="Copy item name to clipboard">⧉ Copy</button></h2>
        <div class="sub">${d.output.quantity}× per redemption · offer #${d.offer_id} ·
          list vs instant sell</div>
      </div>
      <span class="close" id="closeBtn">✕</span>
    </div>
    <div class="chart-wrap"><canvas class="chart-canvas" id="detailChart"></canvas><div class="chart-tip" id="detailChartTip"></div><div class="chart-cross"></div><button class="chart-expand-btn" data-tip="Expand chart">⤢</button></div>
    <div class="chart-stats" id="detailChartStats"></div>
    <div class="redrow">
      <label>Redemptions</label>
      <input id="reds" type="number" min="1" value="${def}">
      <span class="maxlink">max LP affords: <a href="#" id="maxLink">${fmtNum(d.max_units)}</a></span>
      ${AUTH.loggedIn&&AUTH.data&&AUTH.data.wallet!=null&&d.total_cost>0
        ?`<span class="maxlink">max ISK affords: <a href="#" id="maxIskLink">${fmtNum(Math.floor(AUTH.data.wallet/d.total_cost))}</a></span>`:''}
    </div>
    <div id="dbody"></div>`;
  $("#closeBtn").onclick=closeDetail;
  const lpCopyBtn=inner.querySelector(".lp-copy");
  lpCopyBtn.onclick=e=>{
    e.stopPropagation();
    const done=()=>{ lpCopyBtn.textContent="✓ Copied"; setTimeout(()=>{lpCopyBtn.textContent="⧉ Copy";},1200); };
    if(navigator.clipboard&&navigator.clipboard.writeText)
      navigator.clipboard.writeText(d.output.name).then(done).catch(()=>fallbackCopy(d.output.name,done));
    else fallbackCopy(d.output.name, done);
  };
  $("#reds").oninput=renderBody;
  const ml=$("#maxLink");
  if(ml) ml.onclick=e=>{ e.preventDefault(); $("#reds").value=Math.max(d.max_units,1); renderBody(); };
  const mil=document.getElementById("maxIskLink");
  if(mil) mil.onclick=e=>{ e.preventDefault(); $("#reds").value=Math.max(Math.floor(AUTH.data.wallet/d.total_cost),1); renderBody(); };
  renderBody();
  const regionId=_STATION_TO_REGION[parseInt(STATE.ctx.station)]||10000002;
  requestAnimationFrame(()=>{
    const c=document.getElementById('detailChart');
    if(c) _attachChart(c,document.getElementById('detailChartTip'),document.getElementById('detailChartStats'),d.output.type_id,regionId,d.ask||d.bid||null,d.output.name);
  });
}

function walkBook(book, qty){
  let need=qty, cost=0, filled=0, last=null;
  for(const lvl of (book||[])){
    if(need<=0) break;
    const take=Math.min(need,lvl[1]);
    cost+=take*lvl[0]; filled+=take; need-=take; last=lvl[0];
  }
  return {cost, filled, avg:filled>0?cost/filled:null, shortBy:Math.max(0,qty-filled), lastPrice:last};
}

function bindLotCalcs(savedLots){
  document.querySelectorAll(".lot-row[data-tid]").forEach(row=>{
    const tid=row.dataset.tid;
    const need=parseInt(row.dataset.need)||0;
    const tagsEl=row.querySelector(".lot-tags");
    const numEl=row.querySelector(".lot-num");
    const sumEl=row.querySelector(".lot-sum");
    row._lotNums=(savedLots&&savedLots[tid])?[...savedLots[tid]]:[];

    function renderChips(){
      tagsEl.innerHTML=row._lotNums.map((v,i)=>
        `<span class="lot-tag">${fmtNum(v)}<span class="rm" data-i="${i}">×</span></span>`
      ).join("");
      tagsEl.querySelectorAll(".rm").forEach(rm=>{
        rm.onclick=()=>{ row._lotNums.splice(+rm.dataset.i,1); renderChips(); };
      });
      const tot=row._lotNums.reduce((a,b)=>a+b,0);
      if(!row._lotNums.length){ sumEl.textContent=""; return; }
      const rem=need-tot;
      if(rem<=0){ sumEl.textContent=`${fmtNum(tot)} ✓`; sumEl.style.color="var(--green2)"; }
      else { sumEl.textContent=`${fmtNum(tot)} · ${fmtNum(rem)} more`; sumEl.style.color="var(--yellow)"; }
    }

    numEl.addEventListener("keydown",e=>{
      if(e.key==="Enter"||e.key===" "){
        e.preventDefault();
        const v=parseInt(numEl.value);
        if(v>0){ row._lotNums.push(v); numEl.value=""; renderChips(); }
      }
    });
    renderChips();
  });
  const toggle=document.getElementById("lotTrackerToggle");
  if(toggle) toggle.onclick=()=>{
    STATE.lotTrackerOpen=!STATE.lotTrackerOpen;
    toggle.textContent=(STATE.lotTrackerOpen?"▼":"▶")+" Lot tracker";
    document.querySelector(".lot-tracker").style.display=STATE.lotTrackerOpen?"":"none";
  };
  const recipeToggle=document.getElementById("recipeToggle");
  if(recipeToggle) recipeToggle.onclick=()=>{
    STATE.recipeOpen=!STATE.recipeOpen;
    recipeToggle.textContent=(STATE.recipeOpen?"▼":"▶")+" Base Recipe (1× redemption)";
    document.querySelector(".recipe-list").style.display=STATE.recipeOpen?"":"none";
  };
  ["shoppingToggle","costToggle","cargoToggle","saleToggle"].forEach((id,i)=>{
    const keys=["shoppingOpen","costOpen","cargoOpen","saleOpen"];
    const el=document.getElementById(id);
    if(!el) return;
    const labelText=el.textContent.replace(/^[▼▶] /,"");
    el.onclick=()=>{
      const key=keys[i];
      STATE[key]=!STATE[key];
      el.textContent=(STATE[key]?"▼":"▶")+" "+labelText;
      document.querySelector(`[data-sec="${id}"]`).style.display=STATE[key]?"":"none";
    };
  });
}

function renderBody(){
  const d=STATE.detail;
  const n=Math.max(1,parseInt($("#reds").value||"1"));
  const tax=parseFloat(STATE.ctx.tax)||0.045, broker=parseFloat(STATE.ctx.broker)||0.015;
  const hub=(STATE.lastScanData&&STATE.lastScanData.station_name)||"the selected hub";
  const pn=v=>v>0?"pos":(v<0?"neg":"");
  const savedLots={};
  document.querySelectorAll(".lot-row[data-tid]").forEach(row=>{ if(row._lotNums&&row._lotNums.length) savedLots[row.dataset.tid]=[...row._lotNums]; });
  let reqCost=0, anyShort=false, reqVol=0, reqVolMissing=false;
  const reqRows=d.required_items.map(it=>{
    const need=it.quantity*n;
    const w=walkBook(it.book,need);
    const remPrice=w.lastPrice||it.unit_price||0;
    const line=w.cost+w.shortBy*remPrice;
    const noPrice=(it.unit_price===null&&w.filled===0);
    if(!noPrice) reqCost+=line;
    const short=w.shortBy>0; if(short) anyShort=true;
    if(it.line_volume===null) reqVolMissing=true; else reqVol+=it.line_volume*n;
    const vol=it.line_volume===null?'?':fmtVol(it.line_volume*n);
    return `<tr><td>${it.name}${short?' <span class="flag" data-tip="Not enough on market">!</span>':''}</td>
      <td>${fmtNum(need)}</td>
      <td>${w.avg===null?(it.unit_price===null?'<span class="flag">*</span>':fmtISK(it.unit_price)):fmtISK(w.avg)}</td>
      <td>${noPrice?'<span class="flag">?</span>':fmtISK(line)}</td>
      <td>${vol}</td></tr>`;
  }).join("");
  // Patient: list the whole reward quantity at the ask, pay sales tax + broker fee.
  const soldQtyP=d.output.quantity*n;
  const grossP=d.ask?soldQtyP*d.ask:null;
  const taxP=grossP===null?0:grossP*tax, brokerP=grossP===null?0:grossP*broker;
  const revenueP=grossP===null?null:grossP-taxP-brokerP;
  // Instant: walk down the live buy orders, pay sales tax only.
  const wI=walkBook(d.output.buy_book,d.output.quantity*n);
  const soldQtyI=wI.filled, sellShort=wI.shortBy>0;
  const grossI=(d.bid!==null&&soldQtyI>0)?wI.cost:null;
  const taxI=grossI===null?0:grossI*tax;
  const revenueI=grossI===null?null:grossI-taxI;

  const lpTot=d.lp_cost*n, isk_fee=d.isk_fee*n, cost=isk_fee+reqCost;
  const profitP=revenueP===null?null:revenueP-cost;
  const profitI=revenueI===null?null:revenueI-cost;
  const inVol=d.input_volume_per_redemption*n, outVol=(d.output_volume_per_redemption||0)*n;
  const pcls=v=>v===null?'':v>=0?'pos':'neg';
  let warn="";
  if(anyShort) warn+=`<div class="note">! Not enough sell orders at ${hub} for some required items.</div>`;
  if(sellShort) warn+=`<div class="note">Instant sell: only ${fmtNum(soldQtyI)} of ${fmtNum(d.output.quantity*n)} fit the current ${hub} buy orders.</div>`;
  if(d.spread_pct===null) warn+=`<div class="note bad">No buy orders exist — instant-sell can't fill and a listed sell order may never clear.</div>`;
  else if(d.spread_pct>=d.high_spread_pct) warn+=`<div class="note">${Math.round(d.spread_pct)}% spread — the ask isn't backed by real demand; the list figure is optimistic.</div>`;
  if(d.req_missing_price) warn+=`<div class="note">* A required item has no ${hub} price — true cost is higher.</div>`;

  const recipeItems=[];
  recipeItems.push(`
    <div class="recipe-list-item">
      <span class="name">Loyalty Points (LP)</span>
      <span class="val lp">${fmtNum(d.lp_cost)} LP</span>
    </div>`);
  if(d.isk_fee>0) {
    recipeItems.push(`
      <div class="recipe-list-item">
        <span class="name">Redemption ISK</span>
        <span class="val isk">${fmtISK(d.isk_fee)} ISK</span>
      </div>`);
  }
  for(const it of d.required_items) {
    recipeItems.push(`
      <div class="recipe-list-item">
        <span class="name">${it.name}</span>
        <span class="val">× ${fmtNum(it.quantity)}</span>
      </div>`);
  }
  const recipeHTML = `
    <h3 id="recipeToggle" style="cursor:pointer;user-select:none">${STATE.recipeOpen?'▼':'▶'} Base Recipe (1× redemption)</h3>
    <div class="recipe-list" style="${STATE.recipeOpen?'':'display:none'}">
      ${recipeItems.join("")}
    </div>`;

  const sec=(id, stateKey, label, content)=>`
    <h3 id="${id}" style="cursor:pointer;user-select:none">${STATE[stateKey]?'▼':'▶'} ${label}</h3>
    <div class="detail-section" data-sec="${id}" style="${STATE[stateKey]?'':'display:none'}">${content}</div>`;

  // Freshness of the current cheapest sell order — how recently the floor was
  // set and how thin the sell side is (fresh floor + few sellers = price moving).
  let freshHTML="";
  const sos=d.sell_order_stats;
  if(sos){
    const sellers=sos.sell_orders_total;
    const tie=sos.orders_at_best>1?` · ${sos.orders_at_best} orders tied at the floor`:"";
    freshHTML=`<p class="muted" style="margin:-4px 0 12px" data-tip="From each order's issued timestamp. The cheapest price has held for at least this long; later sellers undercut to match it.">Cheapest sell listed <b style="color:var(--fg)">${fmtAgo(sos.age_seconds)}</b>${tie} · ${fmtNum(sellers)} sell order${sellers===1?'':'s'} at ${hub}.</p>`;
  }

  $("#dbody").innerHTML=`
    <div class="kpis">
      <div class="kpi accent"><div class="l">List profit</div><div class="v ${pcls(profitP)}">${profitP===null?'—':fmtISK(profitP)}</div></div>
      <div class="kpi accent"><div class="l">Instant-sell profit</div><div class="v ${pcls(profitI)}">${profitI===null?'—':fmtISK(profitI)}</div></div>
      <div class="kpi" data-tip="Item cost + redemption ISK per ${n}× run${n>1?'s':''} (the LP cost is shown separately).">
        <div class="l">Item + ISK cost</div><div class="v">${fmtISK(cost)}</div></div>
      <div class="kpi"><div class="l">LP cost</div><div class="v">${fmtNum(lpTot)} LP</div></div>
      <div class="kpi" data-tip="Suggested per-unit sell-order price: the lowest current sell, unless that's below the 30-day fair value (someone's dumping) — then it holds at fair value.">
        <div class="l">Suggested list / unit</div><div class="v">${d.suggested_list===null?'—':fmtISK(d.suggested_list)}</div></div>
      <div class="kpi"><div class="l">Volume</div><div class="v">${fmtVol(Math.max(inVol||0,outVol||0))}</div></div>
    </div>
    ${warn}
    ${sec("shoppingToggle","shoppingOpen",`Shopping list — ${n}× redemption${n>1?'s':''}`,
      d.required_items.length?`<table class="mini"><thead><tr>
          <th style="text-align:left">Required item</th><th>Total qty</th><th>Avg unit</th><th>Line cost</th><th>Volume</th></tr></thead>
          <tbody>${reqRows}
          <tr class="total"><td>Total</td><td></td><td></td><td>${fmtISK(reqCost)}</td><td>${reqVolMissing?'?':fmtVol(reqVol)}</td></tr></tbody></table>
      <h3 id="lotTrackerToggle" style="cursor:pointer;user-select:none">${STATE.lotTrackerOpen?'▼':'▶'} Lot tracker</h3>
      <div class="lot-tracker" style="${STATE.lotTrackerOpen?'':'display:none'}">${d.required_items.map(it=>`
        <div class="lot-row" data-tid="${it.type_id}" data-need="${it.quantity*n}">
          <div class="lot-label">${it.name} <span class="lot-need">× ${fmtNum(it.quantity*n)} needed</span></div>
          <div class="lot-controls">
            <input type="number" class="lot-num" min="1" placeholder="qty" data-tip="Type a quantity, then press Enter or Space to add">
            <div class="lot-tags"></div>
            <span class="lot-sum"></span>
          </div>
        </div>`).join("")}
      </div>`
        :`<div class="muted">No required items — just LP + ISK.</div>`)}
    ${recipeHTML}
    ${sec("costToggle","costOpen","Cost breakdown",`
      <table class="mini"><tbody>
        <tr><td>Required items total</td><td>${fmtISK(reqCost)}</td></tr>
        <tr><td>Redemption ISK</td><td>${fmtISK(isk_fee)}</td></tr>
        <tr class="total"><td>Total acquisition cost</td><td>${fmtISK(cost)}</td></tr>
      </tbody></table>`)}
    ${sec("cargoToggle","cargoOpen","Cargo volume",`
      <table class="mini"><tbody>
        <tr><td style="text-align:left">Required items → LP corp station</td><td>${fmtVol(inVol)}</td></tr>
        <tr><td style="text-align:left">Reward (${fmtNum(d.output.quantity*n)}× ${d.output.name}) → ${hub}</td><td>${fmtVol(outVol)}</td></tr>
        <tr class="total"><td style="text-align:left">Ship cargo needed (larger leg)</td><td>${fmtVol(Math.max(inVol||0,outVol||0))}</td></tr>
      </tbody></table>`)}
    ${sec("saleToggle","saleOpen","Profit breakdown",`
      <div class="recipe-list-item" style="border:1px solid var(--line2);border-radius:6px;padding:9px 12px;margin-bottom:12px">
        <span class="name" data-tip="Per-unit price to put on your sell order. The lowest current sell, unless that's below the 30-day fair value (someone's dumping) — then it holds at fair value.">Suggested list price <span style="color:var(--dim2)">/ unit</span></span>
        <span class="val isk">${d.suggested_list===null?'—':fmtISK(d.suggested_list)}</span>
      </div>
      ${d.suggested_list===null?'':`<p class="muted" style="margin:-4px 0 6px">Lowest sell ${d.ask===null?'—':fmtISK(d.ask)} · 30-day fair value ${d.fair_price===null?'—':fmtISK(d.fair_price)}.</p>`}
      ${freshHTML}
      <table class="mini"><thead><tr>
        <th style="text-align:left"></th>
        <th data-tip="Sell value (listed at ask) — list the reward at the lowest sell order and pay sales tax + broker fee.">List<br><span style="color:var(--dim);font-weight:400">sell order</span></th>
        <th data-tip="Sell value (walking buy orders) — instant-sell the reward into the highest buy orders and pay sales tax only.">Instant sell<br><span style="color:var(--dim);font-weight:400">buy order</span></th>
      </tr></thead><tbody>
        <tr><td style="text-align:left">Sell value</td>
          <td>${grossP===null?'—':fmtISK(grossP)}</td>
          <td>${grossI===null?'—':fmtISK(grossI)}</td></tr>
        <tr><td style="text-align:left">− Sales tax (${(tax*100).toFixed(1)}%)</td>
          <td class="neg">${grossP===null?'—':'−'+fmtISK(taxP)}</td>
          <td class="neg">${grossI===null?'—':'−'+fmtISK(taxI)}</td></tr>
        <tr><td style="text-align:left">− Broker fee (${(broker*100).toFixed(1)}%)</td>
          <td class="neg">${grossP===null?'—':'−'+fmtISK(brokerP)}</td>
          <td style="color:var(--dim)">n/a</td></tr>
        <tr class="subtotal"><td style="text-align:left">Net revenue</td>
          <td>${revenueP===null?'—':fmtISK(revenueP)}</td>
          <td>${revenueI===null?'—':fmtISK(revenueI)}</td></tr>
        <tr><td style="text-align:left">− Items cost</td>
          <td class="neg">−${fmtISK(reqCost)}</td><td class="neg">−${fmtISK(reqCost)}</td></tr>
        <tr><td style="text-align:left">− Redemption ISK</td>
          <td class="neg">−${fmtISK(isk_fee)}</td><td class="neg">−${fmtISK(isk_fee)}</td></tr>
        <tr class="total"><td style="text-align:left">Profit</td>
          <td class="${pcls(profitP)}">${profitP===null?'—':fmtISK(profitP)}</td>
          <td class="${pcls(profitI)}">${profitI===null?'—':fmtISK(profitI)}</td></tr>
      </tbody></table>
      <p class="muted" style="margin-top:14px">Costs use the live ${hub} order book.
        List values the reward at the lowest sell order (sales tax + broker fee);
        instant-sell walks down the buy orders (sales tax only).</p>`)}`;
  bindLotCalcs(savedLots);
}

// LP control wiring
$("#go").onclick = ()=>scan(false);
$("#refresh").onclick = ()=>scan(true);
let ALL_CORPS=[], _corpsLoading=false, _corpsRetry=0;
async function _fetchCorps(){
  if(_corpsLoading||_corpsRetry>8) return;
  _corpsLoading=true;
  try{
    const r=await (await fetch("/api/corps")).json();
    if(Array.isArray(r)&&r.length){
      ALL_CORPS=r; _corpsRetry=0;
      if(document.activeElement===_corpInput&&_corpInput.value.length>=2)
        _corpOpen(_corpInput.value);
    } else {
      _corpsRetry++;
      setTimeout(_fetchCorps, 3000);
    }
  }catch(e){ _corpsRetry++; setTimeout(_fetchCorps,3000); }
  _corpsLoading=false;
}
_fetchCorps();

// ── Corp search dropdown ──────────────────────────────────────────────────
// Appended to <body> so no parent CSS interferes.
const _corpInput=$("#corp");
let _corpHi=-1;
const _corpDrop=document.createElement("div");
_corpDrop.className="corp-drop";
_corpDrop.style.display="none";
document.body.appendChild(_corpDrop);

function _corpClose(){ _corpDrop.style.display="none"; _corpHi=-1; }
function _corpItems(){ return _corpDrop.querySelectorAll(".corp-drop-item"); }

function _corpSelect(name){
  _corpInput.value=name; _corpClose();
  if(typeof updateMyLpBadge==="function") updateMyLpBadge();  // lock LP budget to this corp's character LP
  saveLS(); clearTimeout(lpScanTimer); scan(false);
}

function _corpOpen(q){
  if(!q||q.length<2){ _corpClose(); return; }
  if(!ALL_CORPS.length){ _fetchCorps(); }
  const lower=q.toLowerCase();
  const hits=ALL_CORPS.filter(c=>c.name.toLowerCase().includes(lower)).slice(0,20);
  _corpDrop.innerHTML = hits.length
    ? hits.map(c=>`<div class="corp-drop-item">${c.name.replace(/</g,"&lt;")}</div>`).join("")
    : `<div class="corp-drop-empty">${ALL_CORPS.length?'No match':'Loading corp list — retrying…'}</div>`;
  _corpDrop.querySelectorAll(".corp-drop-item").forEach(el=>{
    el.addEventListener("mousedown",e=>{ e.preventDefault(); _corpSelect(el.textContent); });
  });
  _corpHi=-1;
  const r=_corpInput.getBoundingClientRect();
  Object.assign(_corpDrop.style,{
    top:(r.bottom+3)+"px",
    left:r.left+"px",
    width:Math.max(240,r.width)+"px",
    display:"block"
  });
}

function _corpHighlight(idx){
  const items=_corpItems();
  items.forEach(el=>el.classList.remove("hi"));
  _corpHi=Math.max(-1,Math.min(idx,items.length-1));
  if(_corpHi>=0){ items[_corpHi].classList.add("hi"); items[_corpHi].scrollIntoView({block:"nearest"}); }
}

_corpInput.addEventListener("input",e=>_corpOpen(e.target.value));
_corpInput.addEventListener("change",()=>{
  if(typeof updateMyLpBadge==="function") updateMyLpBadge();
  saveLS(); clearTimeout(lpScanTimer); scan(false);
});
_corpInput.addEventListener("blur",()=>setTimeout(_corpClose,150));
_corpInput.addEventListener("keydown",e=>{
  const items=_corpItems();
  if(e.key==="ArrowDown"){ e.preventDefault(); _corpHighlight(_corpHi+1); }
  else if(e.key==="ArrowUp"){ e.preventDefault(); _corpHighlight(_corpHi-1); }
  else if(e.key==="Enter"){
    if(_corpHi>=0&&items[_corpHi]){ _corpSelect(items[_corpHi].textContent); }
    else{ _corpClose(); if(typeof updateMyLpBadge==="function") updateMyLpBadge(); saveLS(); clearTimeout(lpScanTimer); scan(false); }
  }
  else if(e.key==="Escape"){ _corpClose(); }
});
document.addEventListener("click",e=>{ if(!_corpInput.contains(e.target)&&!_corpDrop.contains(e.target)) _corpClose(); });
let lpScanTimer;
function scheduleScan(delay=800){ clearTimeout(lpScanTimer); lpScanTimer=setTimeout(()=>scan(false),delay); }
["#lp","#market"].forEach(sel=>{
  const el=$(sel); if(!el) return;
  el.addEventListener("change",()=>{ saveLS(); scheduleScan(800); });
  if(sel!=="#market") el.addEventListener("input",()=>{ saveLS(); scheduleScan(800); });
});
{const el=$("#maxspread"); if(el){
  el.addEventListener("change",()=>{ saveLS(); if(STATE.rows.length) renderTable(); else scheduleScan(800); });
  el.addEventListener("input",()=>{ saveLS(); if(STATE.rows.length) renderTable(); else scheduleScan(800); });
}}
$("#toggleIlliquid").onchange=()=>{
  STATE.hideIlliquid=$("#toggleIlliquid").checked;
  postPrefs('/api/prefs',{hide_illiquid:STATE.hideIlliquid?'1':'0'}); saveLS();
  renderTable();
};
$("#toggleAffordable").onchange=()=>{
  STATE.hideUnaffordable=$("#toggleAffordable").checked;
  postPrefs('/api/prefs',{hide_unaffordable:STATE.hideUnaffordable?'1':'0'}); saveLS();
  renderTable();
};
$("#lp-search").addEventListener("input", ()=>{
  $("#lp-search-clear").classList.toggle("hidden", !$("#lp-search").value);
  renderTable();
});
$("#lp-search-clear").addEventListener("click", ()=>{
  $("#lp-search").value="";
  $("#lp-search-clear").classList.add("hidden");
  renderTable();
  $("#lp-search").focus();
});
// Tradeability balance presets — set the liquidity↔competition weight, re-rank.
function syncBalanceButtons(){
  document.querySelectorAll(".balance-btn").forEach(b=>
    b.classList.toggle("on", parseFloat(b.dataset.w)===STATE.tradeWeight));
}
document.querySelectorAll(".balance-btn").forEach(b=>{
  b.onclick=()=>{
    STATE.tradeWeight=parseFloat(b.dataset.w);
    syncBalanceButtons();
    postPrefs('/api/prefs',{trade_weight:String(STATE.tradeWeight)}); saveLS();
    renderTable();
  };
});
syncBalanceButtons();
setInterval(renderLPStatus, 30000);

// ══════════════════════════════════════════════════════════════════════════
// PRICE HISTORY CHART
// ══════════════════════════════════════════════════════════════════════════
const _STATION_TO_REGION = {
  60003760:10000002, 60008494:10000043,
  60004588:10000030, 60011866:10000032, 60005686:10000042,
};
const _histCache = {};
const _CHART_PAD = {t:18,r:76,b:20,l:6};

function _sma(vals, n){
  return vals.map((_,i)=>i<n-1?null:vals.slice(i-n+1,i+1).reduce((s,v)=>s+v,0)/n);
}

function _drawChart(canvas, hist, currentPrice){
  const dpr=window.devicePixelRatio||1;
  const W=canvas.offsetWidth||560, H=canvas.offsetHeight||160;
  canvas.width=W*dpr; canvas.height=H*dpr;
  const ctx=canvas.getContext('2d');
  ctx.scale(dpr,dpr);
  ctx.clearRect(0,0,W,H);

  if(!hist.length){
    ctx.fillStyle='#5a7a95'; ctx.font='12px system-ui'; ctx.textAlign='center';
    ctx.fillText('No market history for this region',W/2,H/2); return;
  }

  const PAD=_CHART_PAD;
  const volH=Math.floor(H*.22);
  const priceH=H-PAD.t-PAD.b-volH-2;
  const cW=W-PAD.l-PAD.r;
  const n=hist.length;

  const avgs=hist.map(d=>d.average);
  const vols=hist.map(d=>d.volume);
  const maArr=_sma(avgs,30);
  const ath=Math.max(...avgs);
  const allP=[...avgs,...hist.map(d=>d.highest),...hist.map(d=>d.lowest)].filter(Boolean);
  if(currentPrice) allP.push(currentPrice);
  const pMin=Math.min(...allP)*.99, pMax=Math.max(...allP)*1.01;
  const vMax=Math.max(...vols)||1;

  const px=i=>PAD.l+(i/Math.max(n-1,1))*cW;
  const py=v=>PAD.t+priceH*(1-(v-pMin)/(pMax-pMin));
  const vy=v=>H-PAD.b-(v/vMax)*volH;

  // Grid
  ctx.strokeStyle='rgba(31,48,68,.9)'; ctx.lineWidth=.5;
  for(let i=0;i<=3;i++){
    const y=PAD.t+(priceH/3)*i;
    ctx.beginPath(); ctx.moveTo(PAD.l,y); ctx.lineTo(W-PAD.r,y); ctx.stroke();
  }

  // Reference lines (ATH and current price)
  ctx.save(); ctx.lineWidth=1;
  ctx.setLineDash([3,3]);
  ctx.strokeStyle='rgba(224,85,85,.55)';
  ctx.beginPath(); ctx.moveTo(PAD.l,py(ath)); ctx.lineTo(W-PAD.r,py(ath)); ctx.stroke();
  if(currentPrice&&currentPrice>=pMin&&currentPrice<=pMax){
    ctx.strokeStyle='rgba(76,175,118,.55)';
    ctx.beginPath(); ctx.moveTo(PAD.l,py(currentPrice)); ctx.lineTo(W-PAD.r,py(currentPrice)); ctx.stroke();
  }
  ctx.restore();

  // Volume bars (green above MA, red below)
  const bw=Math.max(1,cW/n*.7);
  hist.forEach((d,i)=>{
    const above=maArr[i]===null||d.average>=maArr[i];
    ctx.fillStyle=above?'rgba(76,175,118,.28)':'rgba(224,85,85,.18)';
    const yTop=vy(d.volume);
    ctx.fillRect(px(i)-bw/2,yTop,bw,H-PAD.b-yTop);
  });

  // 30-day MA line
  ctx.save(); ctx.strokeStyle='#f0c040'; ctx.lineWidth=1.2;
  ctx.beginPath(); let maFirst=true;
  maArr.forEach((v,i)=>{
    if(v===null) return;
    if(maFirst){ctx.moveTo(px(i),py(v));maFirst=false;}
    else ctx.lineTo(px(i),py(v));
  });
  ctx.stroke(); ctx.restore();

  // Price area gradient fill
  const grad=ctx.createLinearGradient(0,PAD.t,0,PAD.t+priceH);
  grad.addColorStop(0,'rgba(79,195,247,.18)');
  grad.addColorStop(1,'rgba(79,195,247,.01)');
  ctx.beginPath();
  avgs.forEach((v,i)=>i===0?ctx.moveTo(px(i),py(v)):ctx.lineTo(px(i),py(v)));
  ctx.lineTo(px(n-1),PAD.t+priceH); ctx.lineTo(px(0),PAD.t+priceH);
  ctx.closePath(); ctx.fillStyle=grad; ctx.fill();

  // Price line
  ctx.beginPath(); ctx.strokeStyle='#4fc3f7'; ctx.lineWidth=1.5;
  avgs.forEach((v,i)=>i===0?ctx.moveTo(px(i),py(v)):ctx.lineTo(px(i),py(v)));
  ctx.stroke();

  // Right-side labels
  ctx.font='9px system-ui'; ctx.textAlign='left';
  ctx.fillStyle='#e05555';
  ctx.fillText('ATH '+fmtISK(ath),W-PAD.r+3,py(ath)+3);
  if(currentPrice&&currentPrice>=pMin&&currentPrice<=pMax){
    ctx.fillStyle='#4caf76';
    ctx.fillText(fmtISK(currentPrice),W-PAD.r+3,py(currentPrice)+3);
  }
  const lastMA=maArr[n-1];
  if(lastMA){ ctx.fillStyle='#f0c040'; ctx.fillText('MA '+fmtISK(lastMA),W-PAD.r+3,py(lastMA)+3); }

  // X-axis date labels
  ctx.fillStyle='#3d5a70'; ctx.font='8px system-ui'; ctx.textAlign='center';
  const step=Math.ceil(n/5);
  for(let i=0;i<n;i+=step) ctx.fillText(hist[i].date.slice(5),px(i),H-PAD.b+10);
  if((n-1)%step!==0) ctx.fillText(hist[n-1].date.slice(5),px(n-1),H-PAD.b+10);
}

function _chartStats(hist, currentPrice){
  if(!hist.length) return '';
  const avgs=hist.map(d=>d.average);
  const ath=Math.max(...avgs);
  const lastMA=_sma(avgs,30).at(-1);
  const price=currentPrice||avgs.at(-1);
  const pctAth=ath>0?((price-ath)/ath*100):null;
  const pctMA=lastMA?((price-lastMA)/lastMA*100):null;
  let s=`<span data-tip="Latest sell price — the figure used for profit calculations.">`
    +`<span class="k">Current</span><span class="v" style="color:var(--cyan)">${fmtISK(price)}</span></span>`;
  if(pctAth!==null){
    const col=pctAth>=-3?'var(--red)':pctAth>=-15?'var(--yellow)':'var(--dim)';
    s+=`<span data-tip="All-time high daily average over the chart window, and how far current price sits below it.">`
      +`<span class="k">ATH</span><span class="v">${fmtISK(ath)}</span>`
      +`<span class="d" style="color:${col}">${pctAth.toFixed(1)}%</span></span>`;
  }
  if(pctMA!==null){
    const col=pctMA>=0?'var(--green2)':'var(--red)';
    s+=`<span data-tip="Current price vs the 30-day moving average. Positive means trading above trend.">`
      +`<span class="k">vs 30d MA</span><span class="v">${fmtISK(lastMA)}</span>`
      +`<span class="d" style="color:${col}">${pctMA>=0?'+':''}${pctMA.toFixed(1)}% ${pctMA>=0?'▲':'▼'}</span></span>`;
  }
  return s;
}

async function _loadHistory(typeId, regionId){
  const k=`${typeId}_${regionId}`;
  if(!_histCache[k]){
    try{
      const d=await (await fetch(`/api/history?type_id=${typeId}&region_id=${regionId}`)).json();
      _histCache[k]=(d.history||[]).slice(-90);
    }catch{ _histCache[k]=[]; }
  }
  return _histCache[k];
}

async function _attachChart(canvas, tipEl, statsEl, typeId, regionId, currentPrice, title=''){
  canvas.style.opacity='.4';
  const hist=await _loadHistory(typeId, regionId);
  canvas.style.opacity='1';
  _drawChart(canvas, hist, currentPrice);
  if(statsEl) statsEl.innerHTML=_chartStats(hist, currentPrice);
  // Wire expand button if the parent wrap has one
  const expandBtn=canvas.parentElement&&canvas.parentElement.querySelector('.chart-expand-btn');
  if(expandBtn) expandBtn.onclick=()=>openExpandChart(typeId,regionId,currentPrice,title);
  if(!tipEl) return;
  const crossEl=canvas.parentElement&&canvas.parentElement.querySelector('.chart-cross');
  canvas.onmousemove=e=>{
    if(!hist.length) return;
    const r=canvas.getBoundingClientRect();
    const W=canvas.offsetWidth||r.width;
    // Map mouse X into the data drawing area (accounts for left/right padding)
    const drawW=W-_CHART_PAD.l-_CHART_PAD.r;
    const xInDraw=Math.max(0,Math.min(drawW,(e.clientX-r.left)-_CHART_PAD.l));
    const idx=Math.round(xInDraw/Math.max(drawW,1)*(hist.length-1));
    // Snap crosshair to the exact data-point x
    const crossX=_CHART_PAD.l+idx/Math.max(hist.length-1,1)*drawW;
    if(crossEl){crossEl.style.left=crossX+'px';crossEl.style.display='block';}
    const d=hist[idx];
    const ma=_sma(hist.map(h=>h.average),30)[idx];
    const pctMA=ma?((d.average-ma)/ma*100):null;
    const tx=Math.min(crossX+12,W-158);
    const ty=Math.max(2,e.clientY-r.top-75);
    tipEl.style.cssText=`display:block;left:${tx}px;top:${ty}px`;
    tipEl.innerHTML=`<div style="color:var(--dim);margin-bottom:2px">${d.date}</div>`
      +`<div>Avg <b style="color:var(--cyan)">${fmtISK(d.average)}</b></div>`
      +`<div>H/L ${fmtISK(d.highest)} / ${fmtISK(d.lowest)}</div>`
      +(ma?`<div>MA30 ${fmtISK(ma)} <span style="color:${pctMA>=0?'var(--green2)':'var(--red)'}">${pctMA>=0?'+':''}${pctMA.toFixed(1)}%</span></div>`:'')
      +`<div style="color:var(--dim)">Vol ${fmtNum(d.volume)}</div>`;
  };
  canvas.onmouseleave=()=>{
    tipEl.style.display='none';
    if(crossEl) crossEl.style.display='none';
  };
}

