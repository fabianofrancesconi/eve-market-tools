// ══════════════════════════════════════════════════════════════════════════
// INDUSTRY TAB
// ══════════════════════════════════════════════════════════════════════════
let IND = {rows:[], sort:{key:"isk_per_hour_patient", dir:-1}, lastData:null, es:null,
           groupsLoaded:false, profiles:[],
           favorites:new Set(), hidden:new Set(), notes:{},
           timers:{}, research:{}, savedGroup:null, openDetail:null, colOrder:null,
           colw:{}, colVis:{}, detailRuns:1,
           sim:{},   // blueprint_id -> {me,te} what-if override; in-memory only, never persisted
           fillTotal:0, fillDone:0, tradeWeight:50,
           builds:[], buildsLoaded:false, buildsExpanded:new Set(),
           decider:{},   // build id -> cached inline price-decider live/market state (survives re-renders)
           focusedBuild:null,   // pipeline board: id of the tile expanded into the focus panel
           buildGroups:{},   // stage key -> true when that status group is collapsed (legacy prefs; archived still uses it)
           mode:"planner",
           sections:{owned:false, hidden:false, builds:true}};
// IND.mode is seeded from server prefs by loadSettings (getPref 'ind_mode').
// Bumped whenever a scan starts or a new fill begins, so an in-flight background
// tradeability fill from a previous scan knows to abandon itself.
let IND_FILL_TOKEN = 0;

const fmtDur = s => {
  if(s===null||s===undefined) return "—";
  // Round to whole minutes FIRST, then split — rounding the minute component on
  // its own could yield 60 (e.g. 3599s → "60m", 7199s → "1h 60m").
  let mins=Math.round(s/60);
  const d=Math.floor(mins/1440); mins-=d*1440;
  const h=Math.floor(mins/60); mins-=h*60;
  if(d>0) return `${d}d ${h}h`;
  return h>0 ? `${h}h ${mins}m` : `${mins}m`;
};
const fmtPct1 = v => (v===null||v===undefined) ? "—" : (v*100).toFixed(1)+"%";
const fmtDaysSell = v => (v===null||v===undefined) ? "—" : (v<1 ? "<1 d" : v.toFixed(1)+" d");
const fmtTrainTime = h => { if(h<1) return Math.round(h*60)+"m"; if(h<24) return h.toFixed(1)+"h"; return (h/24).toFixed(1)+"d"; };

function computeIndTradeability(){ _computeTradeability(IND.rows, IND.tradeWeight); }

// Whether a row's market columns (Vol/day, Days to sell, Tradeability) should
// show a spinner. Only while a Scan's liquidity fill is actually in flight
// (IND.fillTotal>0) AND this row hasn't been filled yet. Outside a fill an
// unfilled row is never spinning — it just reads its cached value or "—",
// because we no longer auto-fetch the market on tab-open / cache-restore.
function _indLiqSpin(r){ return IND.fillTotal>0 && !r.liq_loaded; }

// When the live ESI order-book depth lands for a row, re-gate its instant-sell
// figures against the CURRENT book. The scan already gated on the (laggy)
// Fuzzwork aggregate; ESI is the accurate word. If the live buy book can't
// absorb the batch (out_qty × runs), an "instant sell" isn't real — blank every
// instant-derived field so the row reads "no market" instead of a phantom
// profit. We only ever suppress, never fabricate: a book that CAN absorb keeps
// the server's numbers untouched (we don't have the live bid price here to
// recompute revenue, and the scan's gate already let a real bid through).
//
// Only act on VERIFIED depth. When the verify didn't happen or errored, both
// volumes come back null/undefined (a rate-limit/timeout sends null, not 0) —
// leave the scan's figures alone rather than blank a real instant profit on a
// transient hiccup. `== null` catches both null and undefined.
function applyLiveDepth(r, e){
  if(e.buy_volume==null && e.sell_volume==null) return;   // depth unknown
  const need=(r.out_qty||1)*(r.runs||1);
  const buyVol=e.buy_volume||0;
  if(e.bid==null || buyVol<need){
    for(const k of ["profit_instant","total_profit_instant","isk_per_hour_instant",
                    "margin_instant","bid","payback_runs_instant"]) r[k]=null;
    // profit_best / its derivatives fall back to the patient figures.
    r.profit_best=r.profit_patient;
    r.margin_best=r.margin_patient;
    r.isk_per_hour_best=r.isk_per_hour_patient;
  }
}

const IND_COLS = [
  {k:"_fav",               t:"★",              w: 30, tip:"Add to Watchlist — track blueprints you don't own. Your owned blueprints appear in 'My Blueprints' automatically.", raw:true},
  {k:"product_name",       t:"Item",           w:210, tip:"The manufactured item. * = an input has no sell price at the source hub."},
  {k:"tech_level",         t:"Tech",           w: 46, tip:"Tech level.", f:v=>v?("T"+v):"—"},
  {k:"_timer",             t:"⏱ Timer",        w: 84, tip:"Live countdown for your running manufacturing job on this blueprint, pulled from EVE (refreshed every 5 min). Log in with EVE to populate.", raw:true},
  {k:"isk_per_hour_patient",t:"ISK/hr list",   w:110, tip:"Profit per hour when selling at the lowest ask (patient list order).", f:fmtISK, pn:true},
  {k:"isk_per_hour_instant",t:"ISK/hr instant",w:110, tip:"Profit per hour when selling instantly at the highest bid.", f:fmtISK, pn:true},
  {k:"profit_patient",     t:"Profit list",    w:105, tip:"Profit per run selling at the lowest ask (patient list order).", f:fmtISK, pn:true},
  {k:"profit_instant",     t:"Profit instant", w:105, tip:"Profit per run selling instantly at the highest bid.", f:fmtISK, pn:true},
  {k:"margin_patient",     t:"Margin list",    w: 75, tip:"Profit as % of cost when selling at the lowest ask.", f:fmtPct1, pn:true},
  {k:"margin_instant",     t:"Margin instant", w: 75, tip:"Profit as % of cost when selling instantly at the highest bid.", f:fmtPct1, pn:true},
  {k:"build_time",         t:"Build time",     w: 72, tip:"Time for one run after TE + skills.", f:fmtDur},
  {k:"total_cost",         t:"Cost/run",       w: 98, tip:"Materials + job install + blueprint, per run.", f:fmtISK},
  {k:"bp_price",           t:"BP price",       w:108, tip:"For blueprints you own: the type — BPO (green) or BPC (cyan, with its remaining runs in parentheses) — plus, for a researched original, an ME/TE pill. If only another of your characters owns it, their name is shown below the type. Otherwise the cheapest BPO sell price in The Forge (open an item to see WHERE it's sold). 'invent' = T2, obtained by invention.", f:_bpPriceCell},
  {k:"payback_runs",       t:"Payback",        w: 88, tip:"Runs of profit needed to recoup the BPO purchase (T1 you don't own).", f:(v,r)=> r.owned_bp_me_te?"—":(v==null?"—":fmtNum(v)+" runs")},
  {k:"ask",                t:"Sell price",     w: 98, tip:"Item's lowest sell order at the source hub.", f:v=>v===null?"—":fmtISK(v)},
  {k:"in_vol_run",         t:"Cargo in",       w: 85, tip:"m³ of materials to haul in per run.", f:v=>v?fmtVol(v):"—"},
  {k:"out_vol_run",        t:"Cargo out",      w: 85, tip:"m³ of finished items to haul out per run.", f:v=>v?fmtVol(v):"—"},
  {k:"daily_vol",          t:"Vol/day",        w: 84, tip:"Units of this item traded per day on the market (~30-day median), at the source hub. The market's appetite — how much it can absorb. Populated by a Scan; spins only while that fill is running.", f:(v,r)=> _indLiqSpin(r) ? _SPIN : (v==null?"no data":fmtNum(v))},
  {k:"days_to_sell",       t:"Days to sell",   w: 88, tip:"How many days to sell one run's output (output qty ÷ daily volume). Populated by a Scan; spins only while that fill is running.", f:(v,r)=> _indLiqSpin(r) ? _SPIN : fmtDaysSell(v)},
  {k:"tradeability",       t:"Tradeability",   w: 98, tip:"0–100: how realistically you can sell what you make. Scores daily traded volume against the Volume preset (Quiet 1 / Balanced 50 / Liquidity 1000 units/day = fully tradeable), then gates on the live order book — an empty/thin market scores ~0 no matter its history. Higher is better. Populated by a Scan (profitable rows only).", f:(v,r)=> _indLiqSpin(r) ? _SPIN : (v==null?"—":`<span style="color:${v>=70?'#4caf76':v>=40?'#c8a040':'#e0655a'};font-weight:600">${v}</span>`)},
  {k:"buildable",          t:"Buildable?",     w: 72, tip:"Can every required skill (at the Skills level) make it? Shows training time if not.", f:(v,r)=>v?"✓":("✗"+(r.train_hours?`<div class="train-time">${fmtTrainTime(r.train_hours)}</div>`:""))},
];

const IND_COL_BY_KEY=Object.fromEntries(IND_COLS.map(c=>[c.k,c]));
IND.colOrder=IND_COLS.map(c=>c.k);   // user-reorderable; persisted with the rest of the IND prefs
IND_COLS.forEach(c=>{ IND.colVis[c.k]=true; IND.colw[c.k]=c.w; });
// Resolve IND.colOrder to column objects, dropping unknown keys and appending any
// columns not yet listed (so a saved order survives IND_COLS additions/removals).
function indOrderedCols(){
  const seen=new Set(), out=[];
  for(const k of IND.colOrder){ const c=IND_COL_BY_KEY[k]; if(c&&!seen.has(k)){ out.push(c); seen.add(k); } }
  for(const c of IND_COLS) if(!seen.has(c.k)){ out.push(c); seen.add(c.k); }
  return out;
}
function indVisCols(){ return indOrderedCols().filter(c=>IND.colVis[c.k]!==false); }
function indSetColgroup(){
  $("#ind-cg").innerHTML=indVisCols().map(c=>`<col style="width:${IND.colw[c.k]||c.w}px">`).join("");
}

let IND_RESIZING=false;
const _IND_RESIZE_CTX={get resizing(){return IND_RESIZING;},set resizing(v){IND_RESIZING=v;},tblSel:'#ind-tbl',get colw(){return IND.colw;},setCg:indSetColgroup,save(){setPref('ind.col_widths', IND.colw);}};
function startIndResize(e,key){ startResize(e,key,_IND_RESIZE_CTX); }

// ── Industry column drag-to-reorder (mirrors the LP store) ─────────────────
let IND_DRAG_KEY=null;
function clearIndDropMarks(){
  document.querySelectorAll("#ind-tbl thead th").forEach(th=>th.classList.remove("drop-before","drop-after"));
}
function indDropAfter(th,clientX){
  const r=th.getBoundingClientRect();
  return clientX > r.left + r.width/2;
}
function reorderIndCols(srcKey,dstKey,after){
  if(!srcKey||srcKey===dstKey) return;
  const order=indOrderedCols().map(c=>c.k);
  order.splice(order.indexOf(srcKey),1);
  let to=order.indexOf(dstKey);
  if(after) to+=1;
  order.splice(to,0,srcKey);
  IND.colOrder=order;
  setPref('ind.col_order', IND.colOrder);
  renderIndTable();
}
function wireIndColDrag(th){
  th.addEventListener("dragstart",e=>{
    IND_DRAG_KEY=th.dataset.k;
    e.dataTransfer.effectAllowed="move";
    try{ e.dataTransfer.setData("text/plain",IND_DRAG_KEY); }catch(_){}
    th.classList.add("col-dragging");
    document.body.classList.add("col-dragging-active");
  });
  th.addEventListener("dragend",()=>{
    th.classList.remove("col-dragging");
    document.body.classList.remove("col-dragging-active");
    clearIndDropMarks();
    setTimeout(()=>{ IND_DRAG_KEY=null; },0);
  });
  th.addEventListener("dragover",e=>{
    if(!IND_DRAG_KEY) return;
    e.preventDefault();
    e.dataTransfer.dropEffect="move";
    clearIndDropMarks();
    if(th.dataset.k!==IND_DRAG_KEY)
      th.classList.add(indDropAfter(th,e.clientX)?"drop-after":"drop-before");
  });
  th.addEventListener("dragleave",()=>th.classList.remove("drop-before","drop-after"));
  th.addEventListener("drop",e=>{
    e.preventDefault();
    const after=indDropAfter(th,e.clientX);
    clearIndDropMarks();
    reorderIndCols(IND_DRAG_KEY, th.dataset.k, after);
  });
}

// ── Industry column picker (mirrors the LP store) ───────────────────────────
(function(){
  const btn=document.getElementById("indColPickerBtn");
  const picker=document.getElementById("indColPicker");
  function renderPicker(){
    picker.innerHTML=IND_COLS.map(c=>`<label><input type="checkbox" data-k="${c.k}"${IND.colVis[c.k]!==false?' checked':''}> ${c.t}</label>`).join("");
    picker.querySelectorAll("input").forEach(cb=>{
      cb.onchange=()=>{ IND.colVis[cb.dataset.k]=cb.checked; renderIndTable(); setPref('ind.col_vis', IND.colVis); };
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

function indSortRows(rows){
  const k=IND.sort.key, d=IND.sort.dir;
  return [...rows].sort((a,b)=>{
    let x=a[k], y=b[k];
    if(typeof x==="string") return String(x).localeCompare(String(y))*d;
    if(x===null||x===undefined) x=-Infinity;
    if(y===null||y===undefined) y=-Infinity;
    return (x-y)*d;
  });
}

// Tooltip for the "busy being researched" note: which activity, whose character,
// and when the blueprint frees up.
function indResearchTip(rz){
  const who=rz.character_name?` by ${rz.character_name}`:"";
  let when="";
  if(rz.end>0){
    const rem=rz.end-Date.now();
    when=rem>0
      ? ` — frees up in ${fmtCountdownShort(rem)} (${new Date(rz.end).toLocaleString([],{hour:'2-digit',minute:'2-digit',day:'2-digit',month:'short'})})`
      : " — job ready to deliver";
  }
  return `Blueprint busy: ${rz.activity||"research"}${who}${when}`.replace(/"/g,'&quot;');
}

// The "BP price" cell. For a blueprint you (or an alt) own, the concrete type IS
// the ownership indicator: a green "BPO" or a cyan "BPC (N)" showing its remaining
// runs, plus a small ME/TE pill for a researched original. When only another of
// your characters owns it, that character's name sits on a sub-line below the type.
// When an actual market price is involved the cell uses the regular text colour.
function _bpPriceCell(v, r){
  if(r.owned_bp_me_te){
    const bpo=r.owned_is_bpo||r.owned_max_runs===-1;
    const type=bpo?"BPO":`BPC (${r.owned_max_runs})`;
    const kind=bpo?"bp-bpo":"bp-bpc";
    const me=r.me_used||0, te=r.te_used||0;
    // Only originals get researched — a BPC's ME/TE are baked in and not "yours".
    const pill=(bpo && (me>0||te>0))
      ? `<span class="ind-group-sub"><span class="bp-research-pill" title="Researched blueprint — Material Efficiency ${me}, Time Efficiency ${te}">ME ${me} · TE ${te}</span></span>`
      : "";
    // The type IS the ownership indicator (green BPO / cyan BPC). Owned by the
    // selected industry character, so no owner name below.
    return `<span class="bp-owned ${kind}">${type}</span>${pill}`;
  }
  if(r.other_owners&&r.other_owners.length){
    // Owned only by other characters of yours — show the type (coloured) with the
    // owning character's name below, since it isn't the selected default.
    return r.other_owners.map(o=>{
      const kind=o.is_bpo?"bp-bpo":"bp-bpc";
      const label=o.is_bpo?"BPO":`BPC${o.max_runs>0?` (${o.max_runs})`:""}`;
      return `<span class="bp-owned ${kind}">${label}</span>`
           + `<span class="ind-group-sub">${o.name}</span>`;
    }).join("");
  }
  if(v!=null) return fmtISK(v);                         // a real market price — regular colour
  return r.bp_source==="invention"?"invent":"—";
}

function indRowHtml(r, idx){
  const fav=IND.favorites.has(r.blueprint_id);
  const hid=IND.hidden.has(r.blueprint_id);
  const canHide=r.owned_bp_me_te||fav;
  const tds=indVisCols().map(c=>{
    if(c.k==="_fav"){
      const hideBtn=canHide?`<span class="ind-hide-btn" data-bp="${r.blueprint_id}" title="${hid?"Unhide":"Hide"}">${hid?"👁":"⊘"}</span>`:"";
      return `<td class="fav-cell"><span class="fav-star${fav?" on":""}" data-bp="${r.blueprint_id}" title="${fav?"Remove from Watchlist":"Add to Watchlist"}">${fav?"★":"☆"}</span>${hideBtn}</td>`;
    }
    if(c.k==="_timer"){
      const end=IND.timers[r.blueprint_id];
      if(!end) return `<td class="timer-cell">—</td>`;
      const rem=end-Date.now();
      if(rem<=0) return `<td class="timer-cell done" title="Ready">✓ Ready</td>`;
      return `<td class="timer-cell ind-live-timer" data-end="${end}" title="Crafting timer — click the row to view/edit">${fmtCountdownShort(rem)}</td>`;
    }
    let v=r[c.k], txt=c.f?c.f(v,r):(v===null||v===undefined?"—":v);
    if(c.k==="product_name"){
      if(r.missing_price) txt+=" *";
      const rz=IND.research[r.blueprint_id];
      if(rz) txt+=` <span class="ind-busy-note" title="${indResearchTip(rz)}">🔬 ${rz.activity||"researching"}</span>`;
      const nt=indNote(r.blueprint_id);
      if(nt) txt+=` <span class="ind-note-mark" title="${nt.replace(/"/g,'&quot;')}">📝</span>`;
      if(r.group_name) txt+=`<span class="ind-group-sub">${r.group_name}</span>`;
    }
    let cls=c.cls||"";
    if(c.pn) cls+=(v>0?" pos":(v<0?" neg":""));
    if(c.k==="buildable") cls+=v?" pos":" neg";
    return `<td class="${cls.trim()}">${txt}</td>`;
  }).join("");
  return `<tr style="cursor:pointer" data-ridx="${idx}">${tds}</tr>`;
}

function renderIndTable(){
  const thead=$("#ind-tbl thead"), tbody=$("#ind-tbl tbody");
  const vc=indVisCols();
  $("#ind-tbl").style.tableLayout="fixed";
  indSetColgroup();
  thead.innerHTML="<tr>"+vc.map(c=>{
    const active=IND.sort.key===c.k;
    const arrow=active?(IND.sort.dir<0?" ▼":" ▲"):"";
    const tip=c.tip?` data-tip="${c.tip.replace(/"/g,'&quot;')}"`:"";
    const nosort=c.raw?' data-nosort="1"':"";
    return `<th draggable="true" data-k="${c.k}"${tip}${nosort}${active?' class="sorted"':''}>${c.t}${arrow}<span class="resizer"></span></th>`;
  }).join("")+"</tr>";
  thead.querySelectorAll("th").forEach((th,i)=>{
    wireIndColDrag(th);   // every column can be dragged to reorder
    th.querySelector(".resizer").addEventListener("mousedown",e=>startIndResize(e,vc[i].k));
    if(th.dataset.nosort) return;
    th.onclick=()=>{
      if(IND_RESIZING){ IND_RESIZING=false; return; }
      if(IND_DRAG_KEY) return;   // tail end of a reorder, not a sort click
      const k=th.dataset.k;
      if(IND.sort.key===k) IND.sort.dir*=-1;
      else IND.sort={key:k, dir:k==="product_name"?1:-1};
      setPref('ind.sort_key', IND.sort.key);
      setPref('ind.sort_dir', IND.sort.dir);
      renderIndTable();
    };
  });

  // Split into four sections: Favorites, My Blueprints (owned, visible),
  // Hidden (owned, explicitly hidden), All items (the rest).
  const search=($("#ind-search").value||"").trim().toLowerCase();
  const isFav=r=>IND.favorites.has(r.blueprint_id);
  const isOwned=r=>!!r.owned_bp_me_te||!!IND.timers[r.blueprint_id]||(r.other_owners&&r.other_owners.length>0);
  const isHidden=r=>IND.hidden.has(r.blueprint_id);
  let favs=IND.rows.filter(r=>isFav(r) && !isHidden(r));
  let myBps=IND.rows.filter(r=>isOwned(r) && !isHidden(r) && !isFav(r));
  let hiddenBps=IND.rows.filter(r=>isHidden(r));
  let rest=IND.rows.filter(r=>!isFav(r) && !isOwned(r) && !isHidden(r));
  if(search){
    const matches=r=>(r.product_name||"").toLowerCase().includes(search);
    favs=favs.filter(matches); myBps=myBps.filter(matches);
    hiddenBps=hiddenBps.filter(matches); rest=rest.filter(matches);
  } else {
    const minTrade=parseInt($("#ind-mintrade").value)||0;
    if(minTrade>0) rest=rest.filter(r=> !r.liq_loaded || (r.tradeability!=null && r.tradeability>=minTrade));
  }
  if($("#ind-hidebpc").checked){
    const isBpc=r=>r.owned_bp_me_te && !r.owned_is_bpo && r.owned_max_runs!==-1;
    favs=favs.filter(r=>!isBpc(r)); myBps=myBps.filter(r=>!isBpc(r));
    hiddenBps=hiddenBps.filter(r=>!isBpc(r)); rest=rest.filter(r=>!isBpc(r));
  }
  // Buildable only / Include unobtainable / Hide T2 are client-side filters now:
  // the scan returns the full superset, so toggling them re-renders instantly
  // with no rescan. These mirror the old server rules exactly — favourites are
  // exempt from all three (always visible), and owned blueprints are additionally
  // exempt from the unobtainable filter. `keep(pred)` applies a predicate across
  // every group, always sparing favourites (a fav can sit in the Hidden group).
  const keep=pred=>{
    const p=r=>isFav(r)||pred(r);
    myBps=myBps.filter(p); hiddenBps=hiddenBps.filter(p); rest=rest.filter(p);
  };
  if($("#ind-buildable").checked)
    keep(r=>r.buildable);
  if(!$("#ind-unobtainable").checked)
    keep(r=>r.bp_available||r.owned_bp_me_te);
  if($("#ind-hidet2").checked)
    keep(r=>!(r.requires_invention||r.tech_level===2));
  favs=indSortRows(favs); myBps=indSortRows(myBps);
  hiddenBps=indSortRows(hiddenBps); rest=indSortRows(rest);

  // Render filter chips — only the collapsible pinned sections (My Blueprints,
  // Hidden). Favorites and the rest of the catalogue are now one unified,
  // non-collapsible list, so they get no chip.
  const chips=$("#ind-chips");
  const hasPinned=myBps.length||hiddenBps.length||IND.rows.some(isOwned)||IND.hidden.size;
  if(hasPinned && IND.rows.length){
    const chip=(key,label,n)=>{
      const on=IND.sections[key];
      return `<span class="ind-chip${on?" active":""}" data-sect="${key}">${label} <span class="chip-count">(${n})</span></span>`;
    };
    let ch="";
    if(myBps.length||IND.rows.some(isOwned)) ch+=chip("owned","My Blueprints",myBps.length);
    if(hiddenBps.length||IND.hidden.size) ch+=chip("hidden","Hidden",hiddenBps.length);
    chips.innerHTML=ch;
    chips.querySelectorAll(".ind-chip").forEach(el=>{
      el.onclick=()=>{ const k=el.dataset.sect; IND.sections[k]=!IND.sections[k]; renderIndTable(); setPref('ind.sections', IND.sections); };
    });
  } else { chips.innerHTML=""; }

  const ncol=vc.length;
  // Two categorically different row types, so structure carries meaning:
  //   • drawer()  — a collapsible stash (My Blueprints, Hidden). Reads as a
  //     toggle: a chevron, interactive, a "closed drawer" look when collapsed.
  //   • catHeader() — the static, non-clickable label that OWNS the unified
  //     catalogue rows below it. No chevron; a cyan accent marks it as the main
  //     surface, not a drawer. Its presence is what stops a collapsed "My
  //     Blueprints" drawer from being read as the heading of the catalogue.
  const drawer=(key,label,n)=>{
    const col=IND.sections[key]?"":" collapsed";
    return `<tr class="ind-section ind-drawer${col}" data-sect="${key}"><td colspan="${ncol}"><span class="sect-arrow">▾</span>${label}<span class="sect-count">${n}</span></td></tr>`;
  };
  const catHeader=(label,n)=>
    `<tr class="ind-cathead"><td colspan="${ncol}"><span class="cathead-tick"></span>${label}<span class="sect-count">${n}</span></td></tr>`;

  const ordered=[];
  let html="";
  // Pinned, collapsible drawers up top: My Blueprints (collapsed by default)
  // then Hidden. Each is a stash you open on demand, distinct from the catalogue.
  if(myBps.length){
    html+=drawer("owned","My Blueprints", myBps.length);
    if(IND.sections.owned) myBps.forEach(r=>{ html+=indRowHtml(r, ordered.length); ordered.push(r); });
  }
  if(hiddenBps.length){
    html+=drawer("hidden","Hidden", hiddenBps.length);
    if(IND.sections.hidden) hiddenBps.forEach(r=>{ html+=indRowHtml(r, ordered.length); ordered.push(r); });
  }
  // The catalogue: one unified, non-collapsible view — favourites pinned to the
  // top (regardless of sort column), then the rest. Favourites carry their gold
  // star per-row, so no sub-heading is needed; it stays a single continuous list.
  // The header is shown only when drawers sit above it (that's when the rows need
  // an explicit owner); with no drawers the list stands alone and needs no label.
  const catTotal=favs.length+rest.length;
  if((myBps.length||hiddenBps.length) && catTotal)
    html+=catHeader("All items", catTotal);
  favs.forEach(r=>{ html+=indRowHtml(r, ordered.length); ordered.push(r); });
  const IND_LAZY_BATCH=60;
  let lazyRest=null, lazyIdx=0;
  if(rest.length){
    const show=Math.max(IND_LAZY_BATCH, IND._lazyRendered||0);
    const initial=rest.slice(0, Math.min(show, rest.length));
    initial.forEach(r=>{ html+=indRowHtml(r, ordered.length); ordered.push(r); });
    IND._lazyRendered=initial.length;
    if(rest.length>initial.length){ lazyRest=rest; lazyIdx=initial.length; }
  }
  tbody.innerHTML=html;

  // Lazy-load remaining "All Items" rows on scroll
  if(lazyRest){
    const sentinel=document.createElement("tr");
    sentinel.className="ind-sentinel";
    sentinel.innerHTML=`<td colspan="${ncol}"></td>`;
    tbody.appendChild(sentinel);
    const wrap=$("#ind-tablewrap");
    const obs=new IntersectionObserver(entries=>{
      if(!entries[0].isIntersecting) return;
      const batch=lazyRest.slice(lazyIdx, lazyIdx+IND_LAZY_BATCH);
      if(!batch.length){ obs.disconnect(); sentinel.remove(); return; }
      let bhtml="";
      batch.forEach(r=>{ bhtml+=indRowHtml(r, ordered.length); ordered.push(r); });
      sentinel.insertAdjacentHTML("beforebegin", bhtml);
      wireIndRows(tbody, ordered);
      lazyIdx+=IND_LAZY_BATCH;
      IND._lazyRendered=lazyIdx;
      if(lazyIdx>=lazyRest.length){ obs.disconnect(); sentinel.remove(); IND._lazyRendered=lazyRest.length; }
    }, {root:wrap, rootMargin:"200px"});
    obs.observe(sentinel);
  }

  wireIndRows(tbody, ordered);
  // Re-expand inline detail if one was open before the re-render
  if(IND.openDetail){
    const bpId=IND.openDetail.blueprint_id;
    const matchTr=[...tbody.querySelectorAll("tr[data-ridx]")].find(tr=>{
      const r=ordered[+tr.dataset.ridx];
      return r && r.blueprint_id===bpId;
    });
    if(matchTr){
      matchTr.classList.add("ind-active");
      const ncol=indVisCols().length;
      const dtr=document.createElement("tr");
      dtr.className="ind-detail-row";
      dtr.innerHTML=`<td colspan="${ncol}"></td>`;
      matchTr.after(dtr);
      renderIndDetail(IND.openDetail, dtr.querySelector("td"));
    }
  }
}
function wireIndRows(tbody, ordered){
  // Section header click toggles collapse
  tbody.querySelectorAll("tr.ind-section").forEach(tr=>{
    if(tr._wired) return; tr._wired=true;
    tr.onclick=()=>{ const k=tr.dataset.sect; IND.sections[k]=!IND.sections[k]; renderIndTable(); setPref('ind.sections', IND.sections); };
  });
  tbody.querySelectorAll("tr[data-ridx]").forEach(tr=>{
    if(tr._wired) return; tr._wired=true;
    const r=ordered[+tr.dataset.ridx];
    tr.onclick=ev=>{
      if(ev.target.classList.contains("fav-star")) return;
      if(ev.target.classList.contains("ind-hide-btn")) return;
      if(IND.openDetail && IND.openDetail.blueprint_id===r.blueprint_id){
        closeIndDetail();
      } else openIndDetail(r, tr);
    };
  });
  tbody.querySelectorAll(".fav-star").forEach(star=>{
    if(star._wired) return; star._wired=true;
    star.onclick=ev=>{ ev.stopPropagation(); toggleFavorite(+star.dataset.bp); };
  });
  tbody.querySelectorAll(".ind-hide-btn").forEach(btn=>{
    if(btn._wired) return; btn._wired=true;
    btn.onclick=ev=>{ ev.stopPropagation(); toggleHidden(+btn.dataset.bp); };
  });
}

function toggleFavorite(bp){
  const on = !IND.favorites.has(bp);
  if(on) IND.favorites.add(bp); else IND.favorites.delete(bp);
  // Each favorite is its own server row, so adding/removing one is a single-row
  // write that can't affect any other favorite (or setting). No blob, no guards.
  setFavorite(bp, on);
  renderIndTable();
}
function toggleHidden(bp){
  if(IND.hidden.has(bp)) IND.hidden.delete(bp); else IND.hidden.add(bp);
  setPref('ind.hidden_bps', [...IND.hidden]);
  renderIndTable();
}
// Per-blueprint notes live in one blob pref (ind.notes = {bp_id: text}), mirroring
// ind.hidden_bps. Empty/blank notes are dropped so the marker and blob stay clean.
function indNote(bp){ return IND.notes[bp]||""; }
function setIndNote(bp, text){
  bp=+bp;
  const t=(text||"").trim();
  if(t) IND.notes[bp]=t; else delete IND.notes[bp];
  setPref('ind.notes', IND.notes);
}

function renderIndStatus(){
  const d=IND.lastData; if(!d||ACTIVE_TAB!=="ind") return;
  if(d.favorites_only || d.owned_only){
    setStatus(`<span class="pill"><b>${d.count.toLocaleString()}</b> blueprint${d.count===1?"":"s"} loaded</span>`
      +`<span class="ts">press Scan for full catalogue</span>`);
    return;
  }
  const fillPill = IND.fillTotal>0
    ? `<span class="pill">${_SPIN} scoring tradeability <b>${IND.fillDone.toLocaleString()}</b> / ${IND.fillTotal.toLocaleString()}</span>`
    : "";
  setStatus(
    `<span class="pill"><b>${d.count.toLocaleString()}</b> items · source <b>${d.station_name}</b></span>`
    +fillPill
    +`<span class="ts">prices ${fmtTs(d.scanned_at)}</span>`);
}

function showIndProgress(msg, sub, pct){
  $("#ind-tbl").classList.add("hidden");
  closeIndDetail();
  $("#ind-progress").classList.remove("hidden");
  $("#ind-prog-label").textContent=msg;
  $("#ind-prog-sub").textContent=sub||"";
  $("#ind-prog-fill").style.width=(pct||0)+"%";
}
function hideIndProgress(){
  $("#ind-progress").classList.add("hidden");
  $("#ind-tbl").classList.remove("hidden");
}

function indParams(extra){
  const p={
    market_group: $("#ind-group").value,
    station:      $("#ind-station").value,
    job_rate:     $("#ind-jobrate").value||"0",
    sales_tax:    $("#g-tax").value||"0",
    broker:       $("#g-broker").value||"0",
    runs:         "1",
    // Buildable only / Include unobtainable / Hide T2 / Min trade are client-side
    // filters (see renderIndTable) — the scan returns the full superset, so these
    // are no longer sent as scan params.
    favorites:    JSON.stringify([...IND.favorites]),
  };
  // Compute against the character assigned to the Industry page (its skills &
  // owned blueprints), falling back to the account's active character.
  if(typeof assignedCharId==="function"){
    const cid=assignedCharId("ind");
    if(cid!=null) p.char_id=cid;
  }
  return new URLSearchParams(Object.assign(p, extra||{}));
}

// Merge the in-memory "what-if" ME/TE override for this blueprint (if any) into
// a detail-fetch param bag. Session-only: IND.sim lives in memory and is gone
// the moment the tab closes — nothing here touches prefs or the server's saved
// character/blueprint values.
function indSimParams(bpId, extra){
  const s=IND.sim[bpId];
  const out=Object.assign({}, extra||{});
  if(s){
    if(s.me!=null) out.sim_me=String(s.me);
    if(s.te!=null) out.sim_te=String(s.te);
  }
  return out;
}

// Re-fetch the open detail panel for the current sim state (ME/TE what-if
// changes the material quantities and build time, which are computed server-
// side, so a re-fetch is required — batch/run changes stay client-side).
function reloadIndDetail(bpId){
  const box=document.querySelector("tr.ind-detail-row>td");
  if(!box) return;
  const p=indParams(indSimParams(bpId, {blueprint_id:bpId,
    refresh_prices:(IND.openDetail&&IND.openDetail.esi_prices)?"1":"0"}));
  fetch("/api/ind/detail?"+p).then(r=>r.json()).then(fresh=>{
    if(fresh.error) return;
    renderIndDetail(fresh);
  }).catch(()=>{});
}

// Lock (or release) the controls that re-fire a scan while one is in flight —
// Category, Source hub, Build location, Job cost and Refresh SDE — so a second
// scan can't be kicked off over the top of a running one. `inert` blocks pointer
// AND keyboard interaction and removes the node from the accessibility tree
// (correct for a region that's temporarily unusable). The Scan button, the
// client-side display filters and search stay live. The .scanning class drives
// the dimming + the cyan sweep in CSS. The set is derived from the DOM (closest
// scan-triggering control → its group) so it survives control-bar reordering.
function setIndScanning(on){
  const bar=$("#ind-controls"); if(!bar) return;
  bar.classList.toggle("scanning", on);
  const groups=new Set();
  // One control per scan-triggering group → lock the whole group. Scope (Category
  // + Source hub) and Costs & fees (Build location + Job cost) are the two groups.
  ["#ind-group","#ind-station","#ind-profile","#ind-jobrate"].forEach(sel=>{
    const el=$(sel), g=el&&el.closest(".ctrl-group"); if(g) groups.add(g);
  });
  groups.forEach(g=>{ g.inert=on; });
  const refresh=$("#ind-refresh"); if(refresh) refresh.inert=on;
  const btn=$("#ind-go");
  if(btn){ btn.disabled=on; btn.innerHTML=on?`${_SPIN} Scanning…`:"Scan"; }
}

function scanInd(refreshSde){
  if(IND.es){ IND.es.close(); IND.es=null; }
  IND_FILL_TOKEN++; IND.fillTotal=0; IND._lazyRendered=0;
  setIndScanning(true);
  const p=indParams(refreshSde?{refresh_sde:"1"}:null);
  showIndProgress("Loading blueprint database…","",1);
  setStatus("Scanning…");
  const es=new EventSource("/api/ind/scan?"+p); IND.es=es;
  es.onmessage=e=>{
    let data; try{ data=JSON.parse(e.data); }catch(err){ return; }
    if(data.type==="progress"){
      showIndProgress(data.msg, data.sub||"", data.pct||0);
      setStatus(data.msg+(data.sub?" — "+data.sub:""));
    } else if(data.type==="result"){
      es.close(); IND.es=null; setIndScanning(false);
      IND.rows=data.rows; IND.lastData=data;
      computeIndTradeability();
      persistScan("ind", {...IND.lastData, rows:IND.rows});
      hideIndProgress(); renderIndStatus(); renderIndTable();
      fillIndTradeability(true);   // score the long tail; refetch live ESI prices
    } else if(data.type==="error"){
      es.close(); IND.es=null; setIndScanning(false);
      hideIndProgress(); setStatus(data.error, true);
    }
  };
  es.onerror=()=>{
    es.close(); IND.es=null; setIndScanning(false);
    hideIndProgress(); setStatus("Connection error — server may have stopped.", true);
  };
}

// The scan scores only the top-ranked rows inline (to return fast). This walks
// the rest of the catalogue afterwards in chunks, fetching market history per
// product so EVERY item ends up with a real tradeability — gracefully: pending
// rows spin, a status pill counts progress, and the table fills in as it lands.
// A newer scan/fill cancels this one via IND_FILL_TOKEN.
// `freshPrices` (set by a user-initiated Scan) forces the liquidity fill to
// re-pull live ESI prices instead of reusing the 5-minute server cache, so
// hitting Scan always reflects the latest order book. The tab-open preview
// leaves it off (reuse the cache — it's just a fast first paint).
async function fillIndTradeability(freshPrices){
  const token=++IND_FILL_TOKEN;
  const station=(IND.lastData && IND.lastData.station_id) || $("#ind-station").value;
  // Group still-pending rows by product type so one history lookup updates every
  // blueprint that builds the same item.
  const byProduct=new Map();
  for(const r of IND.rows){
    if(r.liq_loaded) continue;
    // Tradeability only matters once a build is worth making — don't spend an ESI
    // history/order-book call on rows that lose ISK in every sell mode. Retire
    // their spinner (liq_loaded) with a null score so they read "—", not "…".
    if(!_isProfitable(r)){ r.liq_loaded=true; r.tradeability=null; continue; }
    if(!byProduct.has(r.product_id)) byProduct.set(r.product_id, []);
    byProduct.get(r.product_id).push(r);
  }
  const ids=[...byProduct.keys()];
  // Even with nothing left to fetch we may have just retired unprofitable rows'
  // spinners above, so recompute + repaint before bailing.
  if(!ids.length){ IND.fillTotal=0; computeIndTradeability(); renderIndStatus(); renderIndTable(); return; }
  // Repaint once now that the fill is live so the rows being fetched flip to a
  // spinner immediately, instead of flashing "no data" until the first chunk lands.
  IND.fillTotal=ids.length; IND.fillDone=0; renderIndStatus(); renderIndTable();
  const CHUNK=60;
  for(let i=0;i<ids.length;i+=CHUNK){
    if(token!==IND_FILL_TOKEN) return;   // superseded by a newer scan
    const chunk=ids.slice(i,i+CHUNK);
    let liq=null;
    try{
      const qp={station:station, type_ids:chunk.join(",")};
      if(freshPrices) qp.refresh="1";
      const p=new URLSearchParams(qp);
      const d=await (await fetch("/api/ind/liquidity?"+p)).json();
      liq=d.liquidity||null;
    }catch(e){ liq=null; }
    if(token!==IND_FILL_TOKEN) return;
    for(const pid of chunk){
      const e=liq && liq[pid];
      for(const r of (byProduct.get(pid)||[])){
        if(e){
          r.daily_vol=e.daily_vol;
          r.days_to_sell=(e.daily_vol>0)?((r.out_qty*r.runs)/e.daily_vol):null;
          // Live order-book depth from the ESI verify — used to gate the phantom
          // instant-sell price and to score tradeability against the current book.
          if(e.buy_volume!==undefined) r.buy_volume=e.buy_volume;
          if(e.sell_volume!==undefined) r.sell_volume=e.sell_volume;
          applyLiveDepth(r, e);
        }
        r.liq_loaded=true;   // clear the spinner even on a failed/empty fetch
      }
    }
    IND.fillDone=Math.min(i+chunk.length, ids.length);
    computeIndTradeability();
    renderIndStatus(); renderIndTable();
  }
  IND.fillTotal=0; renderIndStatus();
  if(IND.lastData && !IND.lastData.favorites_only && !IND.lastData.owned_only)
    persistScan("ind", {...IND.lastData, rows:IND.rows});
}

// Loads all ESI-owned blueprints + favourites silently and without touching
// saved settings, so "My Blueprints" and the watchlist are visible the moment
// the Industry tab opens — before the user ever presses Scan. A later real
// Scan replaces these rows with the full category results.
function loadOwnedPreview(){
  if(IND.rows.length>0 || IND.es) return;
  const p=indParams({owned_only:"1"});
  const es=new EventSource("/api/ind/scan?"+p);
  IND.es=es;   // shares the slot scanInd() checks/clears, so a real Scan cancels this
  es.onmessage=e=>{
    let data; try{ data=JSON.parse(e.data); }catch(err){ return; }
    if(data.type==="result"){
      es.close(); IND.es=null;
      IND.rows=data.rows; IND.lastData=data;
      computeIndTradeability();
      if(ACTIVE_TAB==="ind"){ renderIndStatus(); renderIndTable(); }
      // No tradeability fill here: scoring the market is expensive (an ESI
      // history + order-book call per product) and must only run on an explicit
      // Scan, never just from opening the tab. Rows show cached scores or "—".
    } else if(data.type==="error"){
      es.close(); IND.es=null;
    }
  };
  es.onerror=()=>{ es.close(); IND.es=null; };
}

function closeIndDetail(){
  const old=document.querySelector("tr.ind-detail-row");
  if(old) old.remove();
  IND.openDetail=null;
  document.querySelectorAll("#ind-tbl tr.ind-active").forEach(r=>r.classList.remove("ind-active"));
}
function openIndDetail(row, clickedTr){
  closeIndDetail();
  if(!clickedTr) return;
  clickedTr.classList.add("ind-active");
  const ncol=indVisCols().length;
  const tr=document.createElement("tr");
  tr.className="ind-detail-row";
  tr.innerHTML=`<td colspan="${ncol}"><div class="ind-d-head">Loading ${row.product_name}…</div></td>`;
  clickedTr.after(tr);
  tr.querySelector("td").scrollIntoView({block:"nearest", behavior:"smooth"});
  const p=indParams(indSimParams(row.blueprint_id, {blueprint_id:row.blueprint_id}));
  fetch("/api/ind/detail?"+p).then(r=>r.json()).then(d=>{
    if(d.error){ tr.querySelector("td").innerHTML=`<div class="ind-d-head">${d.error}</div>`; return; }
    renderIndDetail(d, tr.querySelector("td"));
  }).catch(()=>{ tr.querySelector("td").innerHTML=`<div class="ind-d-head">Failed to load detail.</div>`; });
}

function renderIndDetail(d, container){
  IND.openDetail=d;   // remembered so a batch-size change can re-render this panel
  const box=container||document.querySelector("tr.ind-detail-row>td");
  const isk=v=>v===null||v===undefined?"—":fmtISK(v);
  const n=Math.max(1, IND.detailRuns||1);
  // Batch figures are derived from per-run values × current run count, so they
  // track the Batch (runs) field live (no re-fetch needed).
  // Materials table = the shopping list for the whole batch: every column scales
  // with the run count (qty, cost and m3 you actually buy for N runs), with a
  // totals row so the cargo required is summed and obvious.
  const mvol=v=> v==null?"—":(v.toLocaleString(undefined,{maximumFractionDigits:v<10?2:1})+" m³");
  // Material Efficiency rounds at the WHOLE-job level, so the batch shopping list
  // is effectiveQty(base, ME, N) — NOT the per-run qty × N (see shared.js). Falls
  // back to base_qty=eff_qty (ME already baked in) when base_qty is absent.
  let matTotCost=0, matTotVol=0, matHasVol=false;
  const sortedItems=[...d.required_items].sort((a,b)=>a.name.localeCompare(b.name));
  const me=d.me_used||0;
  const batchQty=m=>(m.base_qty!=null)?effectiveQty(m.base_qty, me, n):m.eff_qty*n;
  const mats=sortedItems.map(m=>{
    const qtyBatch = batchQty(m);
    const costBatch = m.unit_price==null?null:qtyBatch*m.unit_price;
    const volBatch = (m.volume_each!=null)? qtyBatch*m.volume_each : null;
    if(costBatch!=null) matTotCost+=costBatch;
    if(volBatch!=null){ matTotVol+=volBatch; matHasVol=true; }
    return `<tr><td>${m.name}</td><td class="num">${qtyBatch.toLocaleString()}</td>`
      +`<td class="num">${isk(m.unit_price)}</td><td class="num">${isk(costBatch)}</td>`
      +`<td class="num">${mvol(volBatch)}</td></tr>`;
  }).join("");
  const matTotal=`<tr class="ind-d-total"><td>Total — ${d.required_items.length} material${d.required_items.length===1?"":"s"}</td>`
    +`<td class="num"></td><td class="num"></td><td class="num">${isk(matTotCost)}</td>`
    +`<td class="num">${matHasVol?mvol(matTotVol):"—"}</td></tr>`;
  const inVolRun=d.required_items.reduce((s,m)=>s+((m.volume_each!=null)?m.eff_qty*m.volume_each:0),0);
  const outVolRun=(d.product.volume_each!=null)?d.product.quantity*d.product.volume_each:null;
  const inputBatch=matHasVol?matTotVol:inVolRun*n, outputBatch=outVolRun!=null?outVolRun*n:null;
  // Batch cost = batch materials (job-level ME rounding) + job & invention × N.
  const jobPlusInvRun=(d.job_cost||0)+(d.invention?d.invention_cost||0:0);
  const batchCost=d.total_cost!=null?matTotCost+jobPlusInvRun*n:null;
  const qty=d.product.quantity, qtyBatchTot=qty*n;
  // Instant sell = dump the whole batch into standing buy orders. Walk the live
  // buy book (highest bid first) for the batch, HONOURING each order's min_volume:
  // a buyer demanding e.g. 60 000 units per transaction can't be filled by a 4 200
  // batch, so it's skipped, not counted. This is what makes the instant path real
  // for the current run count — d.bid / d.buy_volume are the raw top-of-book
  // aggregates that ignore min_volume and would overstate an unfillable market.
  //   effBid   — proceeds-weighted average bid actually reachable (null = none reachable)
  //   fillQty  — units the reachable buy orders can absorb (≤ batch)
  const hasBook=Array.isArray(d.buy_book);
  const bw=(hasBook&&d.buy_book.length)?walkBook(d.buy_book,qtyBatchTot):null;
  // A shipped book (even empty) is authoritative for THIS batch; only when no book
  // came down do we fall back to the raw aggregates.
  const effBid=(bw&&bw.filled>0)?bw.avg:(hasBook?null:d.bid);
  const fillQty=hasBook?(bw?bw.filled:0):(d.buy_volume==null?null:Math.min(d.buy_volume,qtyBatchTot));
  const batchRevL=d.revenue_patient!=null?d.revenue_patient*n:null;
  // Batch instant revenue off what the reachable buy orders actually pay for the
  // units they can take (tax only, no broker), not qty × top bid × N.
  const batchRevI=(effBid!=null&&fillQty)?effBid*fillQty*(1-(d.sales_tax||0)):null;
  const batchProfitL=batchRevL!=null?batchRevL-batchCost:null;
  const batchProfitI=batchRevI!=null?batchRevI-batchCost:null;
  const batchTime=d.build_time?d.build_time*n:null;
  const pn=v=>v==null?"":(v>0?"pos":(v<0?"neg":""));
  // Fee/tax breakdown — re-derives the ISK amounts folded into revenue_patient
  // / revenue_instant (qty × price × rate) so they can surface as their own card.
  const brokerIsk=(d.ask!=null && d.broker_fee)?qty*d.ask*d.broker_fee*n:null;
  const taxListIsk=(d.ask!=null && d.sales_tax)?qty*d.ask*d.sales_tax*n:null;
  const taxInstantIsk=(effBid!=null && fillQty && d.sales_tax)?effBid*fillQty*d.sales_tax:null;
  const jobCostBatch=d.job_cost!=null?d.job_cost*n:null;
  const inventionCostBatch=d.invention?d.invention_cost*n:0;
  // "Max wallet" — the most runs the assigned character's wallet can afford,
  // counting the true cash outlay per run: build cost (materials + job install +
  // any invention) PLUS the fees paid to list the output at the suggested price
  // (broker + sales tax on qty × ask). d.total_cost already sums the build side.
  const listFeeRate=(d.broker_fee||0)+(d.sales_tax||0);
  const listFeePerRun=(d.ask!=null)?qty*d.ask*listFeeRate:0;
  const costPerRun=(d.total_cost!=null)?d.total_cost+listFeePerRun:null;
  const indCid=(typeof assignedCharId==="function")?assignedCharId("ind"):(AUTH&&AUTH.activeCharId);
  const indBundle=((AUTH&&AUTH.data&&AUTH.data.characters)||[]).find(c=>c.character_id===indCid);
  const walletBal=indBundle?indBundle.wallet:(AUTH&&AUTH.data?AUTH.data.wallet:null);
  const maxWho=(typeof charName==="function"&&charName(indCid))||"your character";
  const maxIskRuns=(AUTH&&AUTH.loggedIn&&walletBal!=null&&costPerRun!=null&&costPerRun>0)
    ? Math.max(1, Math.floor(walletBal/costPerRun)) : null;
  // "Max cargo" — the most runs whose input materials fit a given cargo m³. Uses
  // the per-run input volume (inVolRun); the m³ is user-supplied via an inline
  // box and persisted across sessions (pref 'ind.cargo_cap').
  const cargoCap=(typeof getPref==="function")?getPref("ind.cargo_cap", null):null;
  const maxCargoRuns=cap=>(inVolRun>0 && cap>0)?Math.max(1, Math.floor(cap/inVolRun)):null;
  // Cumulative runs delivered for this exact item, from the same tracker
  // backing the Character tab KPI — broken out per product there.
  const prodTrack=(AUTH.loggedIn && AUTH.data && AUTH.data.runs_tracked)
    ? AUTH.data.runs_tracked.by_product[String(d.product.type_id)] : null;
  // Break-even sell price per unit: the price that makes revenue exactly cover
  // total cost, solving qty*price*(1-fees) = total_cost. An instant sale to buy
  // orders pays sales tax only; a list (sell) order also pays the broker fee.
  const minPriceInstant=(batchProfitI!=null && batchProfitI<0
      && d.total_cost!=null && qty>0 && d.sales_tax!=null && d.sales_tax<1)
    ? d.total_cost/(qty*(1-d.sales_tax)) : null;
  const listFee=(d.sales_tax||0)+(d.broker_fee||0);
  const minPriceList=(d.total_cost!=null && qty>0 && listFee<1)
    ? d.total_cost/(qty*(1-listFee)) : null;
  const tier=d.product.tech_level?("T"+d.product.tech_level):"";
  const esiOwned = !!d.owned_me_te;
  const isBpo = esiOwned && (d.owned_me_te.is_bpo || d.owned_me_te.max_runs===-1);
  const bpcRuns = esiOwned && !isBpo ? d.owned_me_te.max_runs : null;
  const ownedLabel = isBpo
    ? `BPO (ME ${d.owned_me_te.me} / TE ${d.owned_me_te.te})`
    : esiOwned ? `BPC · ${bpcRuns} run${bpcRuns===1?"":"s"} left (ME ${d.owned_me_te.me} / TE ${d.owned_me_te.te})`
    : null;
  let bpSrc;
  if(esiOwned && !isBpo && d.bp_market){
    bpSrc = `${ownedLabel} — <b>buy BPO ${isk(d.bp_market.price)}</b> at ${d.bp_market.station}`;
  } else if(esiOwned && d.bp_market){
    bpSrc = `${ownedLabel} · market ${isk(d.bp_market.price)} at ${d.bp_market.station}`;
  } else if(esiOwned){
    bpSrc = ownedLabel;
  } else if(d.bp_market){
    bpSrc = `Buy BPO ${isk(d.bp_market.price)} at ${d.bp_market.station}`
          + ` · ${fmtNum(d.bp_market.orders)} on sale in ${d.bp_market.region}`;
  } else if(d.bp_source==="invention"){
    bpSrc = "Invent (T2) — no BPO on the market; datacore cost is in Cost/run";
  } else {
    bpSrc = "Not obtainable (no BPO for sale in The Forge)";
  }
  // "Simulate ME/TE" — a what-if override the planner computes against. It's
  // in-memory only (IND.sim, never persisted) so it evaporates when the tab
  // closes; d.sim_me_te flags that the shown ME/TE is hypothetical, not the
  // real character/owned value. Editing re-fetches (quantities & build time are
  // server-computed).
  const sim=IND.sim[d.blueprint_id];
  const meVal=d.me_used, teVal=d.te_used;
  let meTeHtml;
  if(sim){
    // Show the real (pre-sim) value struck through next to the field whenever it
    // differs, so it's obvious you're looking at a what-if, not the truth.
    const was=(real,cur)=> (real!=null && real!==cur)
      ? `<span class="ind-sim-was" title="Real value">${real}</span>` : "";
    meTeHtml=`<span class="ind-sim-wrap">
        ME ${was(sim.realMe,meVal)}<input class="ind-sim-me" type="text" inputmode="numeric" pattern="[0-9]*" value="${meVal}" style="width:34px" title="Material Efficiency (0–10). ↑/↓ or scroll to step, Enter to apply">
        TE ${was(sim.realTe,teVal)}<input class="ind-sim-te" type="text" inputmode="numeric" pattern="[0-9]*" value="${teVal}" style="width:34px" title="Time Efficiency (0–20). ↑/↓ or scroll to step, Enter to apply">
        <button class="ind-sim-reset" title="Stop simulating — revert to the real ME/TE">↺ reset</button>
        <span class="ind-sim-tag" title="Hypothetical values — not saved, gone when you close the tab">what-if</span></span>`;
  } else {
    meTeHtml=`${meVal} / ${teVal} <button class="ind-sim-btn" title="Try different Material/Time Efficiency without owning the researched blueprint — session-only, not saved">⚗ Simulate</button>`;
  }
  // Payback shown regardless of ownership: how many runs of profit recoup the
  // BPO's market price (informational even if you already own it).
  let payback;
  if(d.payback_runs_patient!=null || d.payback_runs_instant!=null){
    const pl=d.payback_runs_patient!=null ? `${fmtNum(d.payback_runs_patient)} list` : "never (list)";
    const pi=d.payback_runs_instant!=null ? `${fmtNum(d.payback_runs_instant)} instant` : "never (instant)";
    payback=`${pl} / ${pi}`+(d.bp_market?` (BPO ${isk(d.bp_market.price)})`:"");
  } else if(d.bp_source==="invention") payback="n/a — invented per run";
  else if(d.bp_market) payback="never at current profit";
  else payback="—";
  // Industry job timer — read-only, driven by the character's running jobs (ESI).
  const tEnd=IND.timers[d.blueprint_id], nowMs=Date.now();
  const job=(AUTH.loggedIn && AUTH.data && AUTH.data.jobs)
    ? AUTH.data.jobs.find(j=>j.blueprint_type_id===d.blueprint_id && j.activity_id===1) : null;
  const jobRuns=job&&job.runs?` · ${job.runs} run(s)`:"";
  let timerHtml;
  if(tEnd && tEnd>nowMs){
    timerHtml=`<div class="ind-timer">
        <span class="ind-timer-remaining ind-live-timer" data-end="${tEnd}">${fmtCountdown(tEnd-nowMs)}</span>
        <span class="ind-timer-eta">ETA ${new Date(tEnd).toLocaleString([],{hour:'2-digit',minute:'2-digit',day:'2-digit',month:'short'})}${jobRuns}</span>
      </div>`;
  } else if(tEnd){
    timerHtml=`<div class="ind-timer done">
        <span class="ind-timer-remaining">✓ Ready — finished ${new Date(tEnd).toLocaleTimeString([],{hour:'2-digit',minute:'2-digit'})}</span>
      </div>`;
  } else {
    timerHtml=`<div class="ind-timer-none">${AUTH.loggedIn
        ? "No active manufacturing job for this blueprint."
        : "Log in with EVE to see your running industry jobs here."}</div>`;
  }
  // Busy-being-researched note: the blueprint is tied up in a ME/TE research or
  // copy job, so it can't be used for manufacturing until that job finishes.
  const rz=IND.research[d.blueprint_id];
  let researchHtml="";
  if(rz){
    const who=rz.character_name?` (${rz.character_name})`:"";
    let when="";
    if(rz.end>0){
      const rem=rz.end-nowMs;
      when=rem>0
        ? ` — frees up in <b>${fmtCountdownShort(rem)}</b>, ETA ${new Date(rz.end).toLocaleString([],{hour:'2-digit',minute:'2-digit',day:'2-digit',month:'short'})}`
        : " — <b>job ready</b> to deliver";
    }
    researchHtml=`<div class="ind-busy-warn">🔬 This blueprint is busy: <b>${rz.activity||"research"}</b>${who}${when}.</div>`;
  }
  let invHtml="";
  if(d.invention){
    const iv=d.invention;
    const dcs=iv.datacores.map(c=>
      `<tr><td>${c.name}</td><td class="num">${fmtNum(c.quantity)}</td>`
      +`<td class="num">${isk(c.unit_price)}</td><td class="num">${isk(c.line_cost)}</td></tr>`).join("");
    invHtml=`
      <div class="ind-d-sub" style="margin-top:14px">Invention (T2)</div>
      <div class="ind-d-grid" style="max-width:none">
        <span>Success probability</span><b>${(iv.probability*100).toFixed(1)}% (base ${(iv.base_probability*100).toFixed(1)}%)</b>
        <span>Runs per invented BPC</span><b>${fmtNum(iv.runs_per_bpc)}</b>
        <span>Invention cost / T2 run</span><b>${isk(iv.cost_per_run)}</b>
      </div>
      <table class="ind-d-mats"><thead><tr><th>Datacore</th><th class="num">Qty</th>
        <th class="num">Unit</th><th class="num">Line</th></tr></thead><tbody>${dcs}</tbody></table>`;
  }
  // ── Sell decision (signature) ─────────────────────────────────────────────
  // The panel's one loud element. Selling in EVE is a real fork: list patiently
  // at the ask, or dump instantly into standing buy orders. So the hero IS that
  // fork — two paths side by side, each answering "sell this way → you net X, at
  // price Y, for Z% margin." The winning path gets an accent so the eye lands on
  // the answer, not on a column of fees. Fees/materials move out of the hero into
  // the tables below; the composition question is answered there, the *decision*
  // here. A market strip underneath says whether the market can actually take it.
  const marginL=(batchProfitL!=null && batchCost)?batchProfitL/batchCost:null;
  const marginI=(batchProfitI!=null && batchCost)?batchProfitI/batchCost:null;
  // Signed ISK for a hero figure: "+1.2M" / "−800K", sign explicit so profit vs
  // loss reads at a glance without hunting for a colour.
  const sProfit=v=> v==null?"—":(v>0?"+":v<0?"−":"")+fmtISK(Math.abs(v));
  const sPct=v=> v==null?"—":(v>0?"+":v<0?"−":"")+(Math.abs(v)*100).toFixed(1)+"%";
  // Which path pays more (defined side beats a null side) — but only accent it as
  // "best" when it's actually profitable. Accenting a loss-making path would
  // celebrate the lesser loss; when both lose, the red figures carry the message.
  const listBetter=batchProfitL!=null && (batchProfitI==null || batchProfitL>=batchProfitI);
  const instBetter=batchProfitI!=null && (batchProfitL==null || batchProfitI>batchProfitL);
  const listWins=listBetter && batchProfitL>0;
  const instWins=instBetter && batchProfitI>0;
  const pnCls=v=> v==null?"na":(v>0?"pos":v<0?"neg":"na");
  // One sell path: how you sell · the per-unit price it uses · the batch net +
  // margin. `note` is the fee mix folded into that net, in plain language.
  const sellPath=(cls,tag,label,price,priceLbl,profit,margin,note,win,extra)=>`
    <div class="ind-sell-path ${cls}${win?" win":""}">
      <div class="ind-sell-path-top">
        <span class="ind-sell-tag">${tag}</span>
        ${win?'<span class="ind-sell-best" title="The more profitable of the two sell methods at these prices">▲ best</span>':""}
      </div>
      <div class="ind-sell-way">${label}</div>
      <div class="ind-sell-net ${pnCls(profit)}">${sProfit(profit)}</div>
      <div class="ind-sell-sub"><span class="ind-sell-margin ${pnCls(margin)}">${sPct(margin)} margin</span></div>
      <div class="ind-sell-price"><span>${priceLbl}</span><b>${price==null?"—":isk(price)}</b>${extra||""}</div>
      <div class="ind-sell-note">${note}</div>
    </div>`;
  // Batch capacity of the instant path: standing buy orders can only absorb so
  // many units — and only orders whose min_volume the batch can meet count at all.
  // fillQty is what the reachable buy book actually takes (from walkBook above);
  // null = we don't know the depth (no book / aggregate unknown).
  const instantDepthNote=(fillQty==null)?"paid to buy orders, sales tax only — no broker fee"
    : fillQty>=qtyBatchTot ? `buy orders can take all ${fmtNum(qtyBatchTot)} — sales tax only`
    : fillQty>0 ? `only ${fmtNum(fillQty)} wanted on buy orders vs ${fmtNum(qtyBatchTot)} made — the rest needs listing`
    : `no buy order will take ${fmtNum(qtyBatchTot)} units (all demand a larger minimum) — you must list`;
  // Market health strip — can the market actually absorb this batch? Units traded
  // per day, days to offload the whole batch at that rate, competing sell orders
  // already listed, and buy-side depth for an instant exit.
  const daily=d.daily_units;
  const daysToOffload=(daily!=null && daily>0)?qtyBatchTot/daily:null;
  const tradeCls=d.tradeability==null?"na":d.tradeability>=70?"pos":d.tradeability>=40?"warn":"neg";
  const fmtDaysOff=v=> v==null?"—":v<1?"< 1 day":v<10?v.toFixed(1)+" days":Math.round(v)+" days";
  // One market cell: value + label, with an optional colour class.
  const mkt=(val,lbl,cls,tip)=>`<div class="ind-mkt-cell"${tip?` title="${tip}"`:""}>`
    +`<b class="${cls||""}">${val}</b><span>${lbl}</span></div>`;
  const ownStr=d.owned_me_te
      ? `<span class="ind-yours">✓ You own this blueprint${isBpo?" (Original — infinite runs)":" (Copy)"}</span>`
      : (d.other_owners&&d.other_owners.length
        ? `<span class="ind-alt-owns">✓ Owned by ${d.other_owners.map(o=>`${o.name} (${o.is_bpo?"BPO":"BPC"}${o.is_bpo?"":", "+o.max_runs+" runs"} · ME ${o.me} / TE ${o.te})`).join(", ")}</span>`
        : `<span class="ind-not-yours">✗ Not in your blueprints</span>`);
  box.innerHTML=`
    <div class="ind-d-head">
      <span class="ind-d-title">${tier?`<span class="ind-d-tier">${tier}</span>`:""}<b>${d.product.name}</b></span>
      <span class="ind-d-acts">
        <button class="ind-fav-btn${IND.favorites.has(d.blueprint_id)?" on":""}" title="${esiOwned?"Owned blueprints appear in My Blueprints automatically":"Add to Watchlist — track blueprints you don't own yet"}">${IND.favorites.has(d.blueprint_id)?"★ Watchlist":"☆ Watchlist"}</button>
        <button class="ind-copy" title="Copy item name to clipboard">⧉ Copy name</button>
        <button class="ind-pull-prices${d.esi_prices?" on":""}" title="Fetch live prices directly from ESI (more accurate than Fuzzwork aggregate)">${d.esi_prices?"✓ ESI prices":"⟳ Pull live prices"}</button>
        ${(()=>{
          // Persistent "already tracking" state: most players own one blueprint,
          // so an in-progress build for it should read as already-tracked on
          // reopen — not a fresh "＋ Track" every time. Only builds still on the
          // blueprint count (planned/building); once built, listed or sold the
          // blueprint is free again, so the button reverts to "＋ Track".
          const active=IND.builds.filter(b=>b.blueprint_id===d.blueprint_id && _isInProgressStage(_buildStage(b)));
          if(active.length){
            const runsList=active.map(b=>Math.max(1,b.runs||1));
            const stg=_STAGE_LABEL[_buildStage(active[0])]||"tracked";
            const tip=active.length===1
              ? `Already tracking a build of ${d.product.name} — ${runsList[0].toLocaleString()} run${runsList[0]===1?"":"s"}, ${stg.toLowerCase()}. Click to track another (you'll be asked to confirm).`
              : `Already tracking ${active.length} builds of ${d.product.name} (${runsList.map(r=>r.toLocaleString()).join(", ")} runs). Click to track another (you'll be asked to confirm).`;
            return `<button class="ind-track-btn on" title="${tip}">✓ Tracking${active.length>1?` (${active.length})`:""}</button>`;
          }
          return `<button class="ind-track-btn" title="Freeze these stats for the current run count so you can revisit them after the batch finishes — the numbers stay put even as market prices move. Appears under 'Tracked builds' up top.">＋ Track this build</button>`;
        })()}
      </span>
      <span class="ind-d-close" title="Close">✕</span>
    </div>
    <div class="ind-d-controls">
      <span class="ind-d-runs-wrap"><span class="ind-d-ctl-lbl">Batch</span><input class="ind-d-runs" type="text" inputmode="numeric" pattern="[0-9]*" value="${n}" style="width:68px"><span class="ind-d-runs-step"><button class="ind-d-runs-inc" title="Increase runs" tabindex="-1">▲</button><button class="ind-d-runs-dec" title="Decrease runs" tabindex="-1">▼</button></span><button class="ind-d-runs-add" data-n="10" title="Add 10 runs">+10</button><button class="ind-d-runs-add" data-n="100" title="Add 100 runs">+100</button><button class="ind-d-runs-add" data-n="1000" title="Add 1000 runs">+1000</button><button class="ind-d-runs-mul" data-m="2" title="Double the runs">×2</button><button class="ind-d-runs-mul" data-m="5" title="5× the runs">×5</button><button class="ind-d-runs-mul" data-m="10" title="10× the runs">×10</button></span>
      <span class="ind-d-maxwrap">${maxIskRuns!=null?`<button class="ind-d-max-isk" title="Set runs to the most this batch's wallet can afford — materials + job install + broker fee + sales tax at the suggested list price (${isk(costPerRun)}/run against ${isk(walletBal)} in ${maxWho}'s wallet)">💰 Max wallet (${fmtNum(maxIskRuns)})</button>`:""}<span class="ind-d-cargo-box" title="Set runs to the most whose input materials fit this cargo hold. Your m³ is saved across sessions."><span class="ind-d-cargo-ico">📦</span><input class="ind-d-cargo-cap" type="text" inputmode="decimal" placeholder="m³" value="${cargoCap!=null?cargoCap:""}"><button class="ind-d-max-cargo"${inVolRun>0?"":" disabled"}>Max cargo${(()=>{const r=maxCargoRuns(cargoCap);return r!=null?` (${fmtNum(r)})`:"";})()}</button></span></span>
      <span class="ind-d-source" title="Trade hub these prices come from">${d.station_name}</span>
    </div>
    ${researchHtml}
    ${esiOwned && !isBpo ? `<div class="ind-bpc-warn">
      ⚠ You only have a <b>Blueprint Copy</b> with <b>${bpcRuns} run${bpcRuns===1?"":"s"}</b> remaining — it will be consumed.
      ${d.bp_market
        ? `<span class="ind-bpc-buy">Buy permanent BPO: ${isk(d.bp_market.price)} at ${d.bp_market.station} (${fmtNum(d.bp_market.orders)} on sale in ${d.bp_market.region})</span>`
        : `<span class="ind-bpc-buy">No BPO on the market in ${d.region_name}. <button class="ind-bpo-expand" data-bp="${d.blueprint_id}">Search other regions</button></span>`}
    </div>` : ""}
    <div class="ind-d-body">
    <div class="ind-sell">
      <div class="ind-sell-head">
        <span class="ind-sell-title">Sell this batch</span>
        <span class="ind-sell-scope">${n.toLocaleString()} run${n===1?"":"s"} → ${fmtNum(qtyBatchTot)}× ${d.product.name}</span>
      </div>
      <div class="ind-sell-paths">
        ${sellPath("list", "① List", "List &amp; wait", d.ask, "at ask", batchProfitL, marginL,
          `${fmtNum(qtyBatchTot)} × ${isk(d.ask)}, less broker ${fmtPct1(d.broker_fee)} + tax ${fmtPct1(d.sales_tax)}`,
          listWins)}
        ${sellPath("instant", "② Instant", "Dump now", effBid, "at bid", batchProfitI, marginI,
          instantDepthNote, instWins)}
      </div>
      <div class="ind-sell-rail">
        ${minPriceList!=null?`<span class="ind-rail-cell"><i>Break-even</i><b class="warn">${isk(minPriceList)}</b><em>/unit to list</em></span>`:""}
        <span class="ind-rail-cell"><i>Build cost</i><b>${isk(batchCost)}</b><em>${fmtNum(qtyBatchTot)} units</em></span>
        <span class="ind-rail-cell"><i>Build time</i><b>${fmtDur(batchTime)}</b></span>
      </div>
      <div class="ind-sell-market">
        <span class="ind-mkt-lbl" title="Whether the market can actually absorb what you'd make — history and the live order book at ${d.station_name}.">Market</span>
        ${mkt(daily==null?"—":fmtNum(daily)+"/d", "traded", "",
          "Units traded per day (~30-day median). The market's daily appetite for this item.")}
        ${mkt(fmtDaysOff(daysToOffload), "to offload batch",
          daysToOffload==null?"":daysToOffload>14?"neg":daysToOffload>4?"warn":"pos",
          "How long your whole batch would take to clear at the recent daily volume — lower is more liquid.")}
        ${mkt(d.sell_volume==null?"—":fmtNum(d.sell_volume), "listed vs you",
          "", "Units already on sell orders here — your competition. Fewer means less undercutting.")}
        ${mkt(fillQty==null?"—":fmtNum(fillQty), "wanted now",
          "", "Units on buy orders this batch could actually fill — big buyers whose minimum volume exceeds your batch are excluded (you can't sell them what they demand).")}
        ${mkt(d.tradeability==null?"—":d.tradeability, "liquidity", tradeCls,
          "0–100 liquidity score from daily volume vs your Volume preset, gated on the live book. Higher sells more reliably.")}
      </div>
    </div>
    <div class="ind-d-note">
      <label class="ind-d-note-lbl">📝 Note</label>
      <textarea class="ind-d-note-box" rows="2" placeholder="Add a note for this blueprint…">${(indNote(d.blueprint_id)||"").replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;")}</textarea>
    </div>
    <div class="ind-d-timer-card">${timerHtml}</div>
    <div class="ind-d-specs">
      <div class="ind-spec-col">
        <div class="ind-d-sub">Blueprint</div>
        <div class="ind-d-grid">
          <span>Source</span><b class="bp-buy">${bpSrc}</b>
          <span>ME / TE used</span><b class="ind-sim-cell">${meTeHtml}</b>
          <span>Ownership</span><b>${ownStr}</b>
          <span>Payback</span><b>${payback}</b>
        </div>
      </div>
      <div class="ind-spec-col">
        <div class="ind-d-sub">Logistics — ${n.toLocaleString()} run${n===1?"":"s"}</div>
        <div class="ind-d-grid">
          <span>Cargo in</span><b>${inputBatch?fmtVol(inputBatch):"—"}</b>
          <span>Cargo out</span><b>${outputBatch?fmtVol(outputBatch):"—"}</b>
          <span title="Cumulative runs delivered for this item since the app started watching. Log in with EVE to track.">Runs delivered</span><b>${prodTrack?`${prodTrack.runs.toLocaleString()} <span class="ind-spec-note">${prodTrack.jobs.toLocaleString()} job${prodTrack.jobs===1?"":"s"}</span>`:(AUTH.loggedIn?"0":"—")}</b>
        </div>
      </div>
    </div>
    ${d.missing_skills&&d.missing_skills.length?`
    <div class="ind-d-skillbox">
    <div class="ind-d-sub ind-skills-warn">Missing skills — ${d.missing_skills.length} needed</div>
    <table class="ind-d-mats ind-d-skills"><thead><tr><th>Skill</th><th class="num">Have</th><th class="num">Need</th><th class="num">Train time</th></tr></thead><tbody>${d.missing_skills.map(s=>`<tr><td>${s.name}${s.prereq?' <span class="ind-prereq">(prereq)</span>':''}</td><td class="num">${s.current}</td><td class="num">${s.required}</td><td class="num">${s.train_hours<1?(Math.round(s.train_hours*60)+"m"):(s.train_hours<24?s.train_hours.toFixed(1)+"h":(s.train_hours/24).toFixed(1)+"d")}</td></tr>`).join("")}</tbody>
    <tfoot><tr class="ind-d-total"><td>Total training</td><td></td><td></td><td class="num">${(()=>{const h=d.missing_skills.reduce((s,sk)=>s+sk.train_hours,0);return h<1?(Math.round(h*60)+"m"):(h<24?h.toFixed(1)+"h":(h/24).toFixed(1)+"d");})()}</td></tr></tfoot></table>
    </div>`:""}
    </div>
    <div class="ind-d-sub">Materials to buy — ${n.toLocaleString()} run(s)</div>
    <table class="ind-d-mats"><thead><tr><th>Material</th><th class="num">Qty needed</th>
      <th class="num">Unit price</th><th class="num">Total cost</th>
      <th class="num">Cargo m³</th></tr></thead><tbody>${mats}${matTotal}</tbody></table>
    ${invHtml}`;
  // Wire copy + close + ownership via listeners (inline onclick can't see $).
  box.querySelector(".ind-d-close").onclick=closeIndDetail;
  // Clicking the header bar itself (not its buttons) collapses the detail view.
  const head=box.querySelector(".ind-d-head");
  let headDownInInteractive=false;
  head.onmousedown=ev=>{ headDownInInteractive=!!ev.target.closest("button,input,.ind-d-runs-wrap,.ind-d-cargo-box"); };
  head.onclick=ev=>{ if(!ev.target.closest("button,input,.ind-d-runs-wrap,.ind-d-cargo-box") && !headDownInInteractive) closeIndDetail(); };
  box.querySelector(".ind-fav-btn").onclick=()=>toggleFavorite(d.blueprint_id);
  // Note editor — autosaves as you type (debounced by setPref). Re-render the
  // table on blur so the 📝 marker appears/disappears without disrupting typing.
  const noteBox=box.querySelector(".ind-d-note-box");
  if(noteBox){
    let last=indNote(d.blueprint_id);
    noteBox.addEventListener("input", ()=>setIndNote(d.blueprint_id, noteBox.value));
    noteBox.addEventListener("blur", ()=>{
      const now=indNote(d.blueprint_id);
      if((!!now)!==(!!last)){ last=now; renderIndTable(); }
    });
  }
  const trackBtn=box.querySelector(".ind-track-btn");
  if(trackBtn) trackBtn.onclick=()=>trackThisBuild(d, Math.max(1, IND.detailRuns||1), trackBtn);
  const copyBtn=box.querySelector(".ind-copy");
  copyBtn.onclick=()=>{
    const done=()=>{ copyBtn.textContent="✓ Copied"; setTimeout(()=>{copyBtn.textContent="⧉ Copy";},1200); };
    if(navigator.clipboard&&navigator.clipboard.writeText){
      navigator.clipboard.writeText(d.product.name).then(done).catch(()=>fallbackCopy(d.product.name,done));
    } else fallbackCopy(d.product.name, done);
  };
  const pullBtn=box.querySelector(".ind-pull-prices");
  pullBtn.onclick=()=>{
    pullBtn.disabled=true; pullBtn.textContent="Fetching…";
    const p=indParams(indSimParams(d.blueprint_id, {blueprint_id:d.blueprint_id, refresh_prices:"1"}));
    fetch("/api/ind/detail?"+p).then(r=>r.json()).then(fresh=>{
      if(fresh.error){ pullBtn.textContent="⚠ "+fresh.error; return; }
      renderIndDetail(fresh);
    }).catch(()=>{ pullBtn.disabled=false; pullBtn.textContent="⟳ Pull live prices"; });
  };
  const bpoExpBtn=box.querySelector(".ind-bpo-expand");
  if(bpoExpBtn) bpoExpBtn.onclick=()=>{
    bpoExpBtn.disabled=true; bpoExpBtn.textContent="Searching…";
    const p=new URLSearchParams({blueprint_id:bpoExpBtn.dataset.bp, station:$("#ind-station").value});
    fetch("/api/ind/bpo-search?"+p).then(r=>r.json()).then(res=>{
      if(res.bp_market){
        const m=res.bp_market;
        const jmp=m.jumps!=null?` · ${m.jumps} jump${m.jumps===1?"":"s"}`:"";
        bpoExpBtn.parentElement.innerHTML=`Buy permanent BPO: ${isk(m.price)} at ${m.station} (${m.region}${jmp})`;
      } else {
        bpoExpBtn.textContent="Not sold anywhere — LP store / event only";
      }
    }).catch(()=>{ bpoExpBtn.disabled=false; bpoExpBtn.textContent="Search other regions"; });
  };
  const runsInput=box.querySelector(".ind-d-runs");
  const setRuns=v=>{ IND.detailRuns=Math.max(1,v); renderIndDetail(d); };
  // Re-rendering rebuilds box.innerHTML, which destroys this very input and
  // drops keyboard focus. So on each keystroke: keep only the digits the user
  // typed, remember the caret offset, re-render, then re-focus the fresh input
  // and put the caret back where it was. It's a text field (not type=number) so
  // selectionStart/setSelectionRange actually work — number inputs return null
  // for the caret, which is why the cursor kept snapping to the end.
  runsInput.addEventListener("input", ()=>{
    const raw=runsInput.value;
    const digits=raw.replace(/[^0-9]/g,"");
    // How many digits sit left of the caret — the caret position that survives
    // stripping non-digits and re-rendering the (possibly clamped) value.
    const caretDigits=raw.slice(0, runsInput.selectionStart ?? raw.length).replace(/[^0-9]/g,"").length;
    setRuns(parseInt(digits,10)||1);
    const fresh=box.querySelector(".ind-d-runs");
    if(fresh){
      fresh.focus();
      const pos=Math.min(caretDigits, fresh.value.length);
      try{ fresh.setSelectionRange(pos,pos); }catch(e){}
    }
  });
  const incBtn=box.querySelector(".ind-d-runs-inc");
  if(incBtn) incBtn.onclick=()=>setRuns((IND.detailRuns||1)+1);
  const decBtn=box.querySelector(".ind-d-runs-dec");
  if(decBtn) decBtn.onclick=()=>setRuns((IND.detailRuns||1)-1);
  box.querySelectorAll(".ind-d-runs-add").forEach(b=>{
    b.onclick=()=>setRuns((IND.detailRuns||0)+(+b.dataset.n));
  });
  box.querySelectorAll(".ind-d-runs-mul").forEach(b=>{
    b.onclick=()=>setRuns(IND.detailRuns*(+b.dataset.m));
  });
  const maxIskBtn=box.querySelector(".ind-d-max-isk");
  if(maxIskBtn && maxIskRuns!=null) maxIskBtn.onclick=()=>setRuns(maxIskRuns);
  const cargoInput=box.querySelector(".ind-d-cargo-cap");
  const maxCargoBtn=box.querySelector(".ind-d-max-cargo");
  // Parse the cargo box, persist it (across sessions), and fit runs to it. The
  // box is the source of truth; the button just applies whatever it holds.
  const applyCargo=()=>{
    if(inVolRun<=0) return;
    const cap=parseFloat((cargoInput.value||"").replace(/[, ]/g,""));
    if(!isFinite(cap)||cap<=0){ cargoInput.focus(); return; }
    if(typeof setPref==="function") setPref("ind.cargo_cap", cap);
    const runs=maxCargoRuns(cap);
    if(runs!=null) setRuns(runs);   // re-renders; the box keeps its value via the pref
  };
  if(cargoInput){
    // Persist on change without re-rendering (so typing isn't interrupted); the
    // stored value is what a later reopen / Max click reads.
    cargoInput.addEventListener("input", ()=>{
      const cap=parseFloat((cargoInput.value||"").replace(/[, ]/g,""));
      if(typeof setPref==="function") setPref("ind.cargo_cap", (isFinite(cap)&&cap>0)?cap:null);
      // Live-update the button's parenthetical run count as they type, without
      // re-rendering (which would interrupt typing) — mirrors "Max wallet (N)".
      if(maxCargoBtn){
        const runs=maxCargoRuns(cap);
        maxCargoBtn.textContent=`Max cargo${runs!=null?` (${fmtNum(runs)})`:""}`;
      }
    });
    cargoInput.addEventListener("keydown", ev=>{ if(ev.key==="Enter"){ ev.preventDefault(); applyCargo(); } });
  }
  if(maxCargoBtn && inVolRun>0) maxCargoBtn.onclick=applyCargo;

  // ── Simulate ME/TE ──────────────────────────────────────────────────────
  // Enter what-if mode: seed the override with the current values and re-render
  // so the inline ME/TE inputs appear (no re-fetch — values are unchanged yet).
  const simBtn=box.querySelector(".ind-sim-btn");
  if(simBtn) simBtn.onclick=()=>{
    // d.me_used/te_used ARE the real values right now (no sim applied yet), so
    // stash them as the baseline for the "real → sim" display.
    IND.sim[d.blueprint_id]={me:d.me_used, te:d.te_used, realMe:d.me_used, realTe:d.te_used};
    renderIndDetail(d);
    const fresh=box.querySelector(".ind-sim-me"); if(fresh){ fresh.focus(); fresh.select(); }
  };
  // Leave what-if mode and re-fetch the real values.
  const simReset=box.querySelector(".ind-sim-reset");
  if(simReset) simReset.onclick=()=>{
    delete IND.sim[d.blueprint_id];
    reloadIndDetail(d.blueprint_id);
  };
  // Apply the typed ME/TE. Only re-fetch when a value actually changed (clamped
  // to EVE's ranges); Enter/blur commit, so mid-typing keystrokes don't spam.
  const meInput=box.querySelector(".ind-sim-me"), teInput=box.querySelector(".ind-sim-te");
  const clamp1=(v,hi)=>isNaN(v)?0:Math.max(0,Math.min(hi,v));
  // Commit whatever's typed. Keeps the stashed baseline (realMe/realTe) so the
  // "real → sim" display survives; only re-fetches when a value actually moved.
  const commitSim=()=>{
    const read=(el,hi)=>clamp1(parseInt((el.value||"").replace(/[^0-9]/g,""),10),hi);
    const me=read(meInput,10), te=read(teInput,20);
    const cur=IND.sim[d.blueprint_id]||{};
    if(cur.me===me && cur.te===te) return;
    IND.sim[d.blueprint_id]={me, te, realMe:cur.realMe, realTe:cur.realTe};
    reloadIndDetail(d.blueprint_id);
  };
  // Bump a field by ±1 (arrow keys / mouse wheel) within its range and commit.
  const step=(el,hi,delta)=>{
    const now=clamp1(parseInt((el.value||"").replace(/[^0-9]/g,""),10),hi);
    el.value=clamp1(now+delta,hi);
    commitSim();
  };
  [[meInput,10],[teInput,20]].forEach(([el,hi])=>{
    if(!el) return;
    el.addEventListener("keydown", ev=>{
      if(ev.key==="Enter"){ ev.preventDefault(); commitSim(); }
      else if(ev.key==="ArrowUp"){ ev.preventDefault(); step(el,hi,1); }
      else if(ev.key==="ArrowDown"){ ev.preventDefault(); step(el,hi,-1); }
    });
    el.addEventListener("wheel", ev=>{ ev.preventDefault(); step(el,hi,ev.deltaY<0?1:-1); }, {passive:false});
    el.addEventListener("blur", commitSim);
  });
}

function fmtCountdown(ms){
  let s=Math.max(0,Math.floor(ms/1000));
  const d=Math.floor(s/86400); s-=d*86400;
  const h=Math.floor(s/3600); s-=h*3600;
  const m=Math.floor(s/60); s-=m*60;
  if(d>0) return `${d}d ${h}h left`;
  return (h?h+"h ":"")+(h||m?m+"m ":"")+s+"s left";
}
// Compact H:MM:SS / M:SS form for the narrow table column (Dd Hh past 24h).
function fmtCountdownShort(ms){
  let s=Math.max(0,Math.floor(ms/1000));
  const d=Math.floor(s/86400); s-=d*86400;
  const h=Math.floor(s/3600); s-=h*3600;
  const m=Math.floor(s/60); s-=m*60;
  if(d>0) return `${d}d ${h}h`;
  return h>0 ? `${h}:${String(m).padStart(2,"0")}:${String(s).padStart(2,"0")}`
             : `${m}:${String(s).padStart(2,"0")}`;
}
// Tick every live countdown once a second — the open detail panel's span and
// any "_timer" cells in the main table — without a full table re-render.
setInterval(()=>{
  document.querySelectorAll(".ind-live-timer[data-end]").forEach(el=>{
    const rem=(+el.dataset.end)-Date.now();
    const isCell=el.classList.contains("timer-cell");
    const isTile=el.classList.contains("ind-tile-live");   // pipeline board tile
    const inBuildCard=!!el.closest(".ind-build-card");
    if(rem<=0){
      if(isCell){ el.textContent="✓ Ready"; el.classList.add("done"); el.removeAttribute("data-end"); }
      else if(isTile){
        el.textContent="✓ ready to deliver"; el.removeAttribute("data-end");
        // Promote the countdown span to the lit "ready" style and light the
        // whole tile, matching a freshly-rendered ready tile — and drop the
        // now-full progress bar so the tile reads as done, not mid-build.
        el.classList.remove("ind-tile-live"); el.classList.add("ind-tile-ready");
        const tl=el.closest(".ind-tile"); if(tl) tl.classList.add("ready");
        const bar=tl&&tl.querySelector(".ind-tile-bar"); if(bar) bar.remove();
      }
      else if(inBuildCard){ el.textContent="ready for delivery"; el.removeAttribute("data-end"); }
      else if(IND.openDetail) renderIndDetail(IND.openDetail);
    } else {
      el.textContent=(isCell||isTile)?fmtCountdownShort(rem):fmtCountdown(rem);
    }
  });
  tickCharRefreshTimer();
}, 1000);

// ══════════════════════════════════════════════════════════════════════════
// TRACKED BUILDS
// ──────────────────────────────────────────────────────────────────────────
// "Track this build" freezes the detail panel's stats for the current run count
// so the exact economics you committed to stay visible days later, even as
// market prices drift. Each tracked build is matched — client-side, from the
// same live ESI jobs that drive the timers — to an actual in-game manufacturing
// job of the same blueprint + run count. Lifecycle, derived (never guessed):
//   • awaiting — no matching active job yet → a warning (you haven't started it,
//     or ESI hasn't caught up). Clears the moment a matching job appears.
//   • building — linked to an active job; shows its live countdown.
//   • done — the linked job has left ESI's active list (delivered).
// Only a build that was actually linked can become "done", so a freshly-tracked
// build never jumps straight to done.

// Guard against tracking the same blueprint twice. The premise is one blueprint
// per player, so a build that is still *in progress* (planned or building) is
// treated as an existing track the user should be warned about up front. Once
// the batch is built (delivered), listed or sold it no longer occupies the
// blueprint — the player can start a fresh batch — so those stages are not a
// clash. An exact match (same blueprint AND run count) is the hard warning; a
// different run count is the softer "already tracking this, separate batch?"
// nudge. Returns true if the user chose to go ahead (or there was no clash).
function _confirmTrackNotDuplicate(d, runs){
  const bp=d.blueprint_id, name=(d.product||{}).name||"this blueprint";
  const active=IND.builds.filter(b=>b.blueprint_id===bp && _isInProgressStage(_buildStage(b)));
  if(!active.length) return true;
  const stageOf=b=>(_STAGE_LABEL[_buildStage(b)]||"tracked").toLowerCase();
  const exact=active.find(b=>Math.max(1,b.runs||1)===runs);
  if(exact){
    return confirm(
      `⚠ Already tracking\n\n`
      +`You're already tracking a build of ${name} at exactly ${runs} run${runs===1?"":"s"} `
      +`(${stageOf(exact)}). Tracking it again creates a duplicate.\n\n`
      +`Track a second identical build anyway?`);
  }
  const others=active.map(b=>`${Math.max(1,b.runs||1).toLocaleString()} run${Math.max(1,b.runs||1)===1?"":"s"} (${stageOf(b)})`);
  const runList=others.length===1?others[0]
    :`${others.slice(0,-1).join(", ")} and ${others[others.length-1]}`;
  return confirm(
    `Already tracking ${name}\n\n`
    +`You're already tracking ${others.length===1?"a build":others.length+" builds"} of ${name}: ${runList}. `
    +`This one is ${runs} run${runs===1?"":"s"}. If it's a separate batch, go ahead — otherwise you may be double-tracking.\n\n`
    +`Track this ${runs}-run build too?`);
}

// Freeze the currently-open detail blob for N runs and persist it. The snapshot
// is the exact /api/ind/detail response the panel is rendering, so reopening it
// reproduces the numbers verbatim regardless of later price moves.
function trackThisBuild(d, runs, btn){
  if(!_confirmTrackNotDuplicate(d, runs)) return;
  const snap=JSON.parse(JSON.stringify(d));
  const body={runs:String(runs), snapshot:JSON.stringify(snap)};
  if(btn){ btn.disabled=true; btn.textContent="Tracking…"; }
  fetch("/api/ind/builds/save",{method:"POST",headers:{"Content-Type":"application/json"},
    body:JSON.stringify(body)})
    .then(r=>r.json()).then(res=>{
      if(res && res.build){
        IND.builds=IND.builds.filter(b=>b.id!==res.build.id);
        IND.builds.unshift(res.build);
        renderIndBuilds();
        // Re-render the detail so the action button flips to its persistent
        // "✓ Tracking" state (survives reopening the panel) rather than a fleeting
        // toast that reverts to "＋ Track this build".
        if(IND.openDetail && IND.openDetail.blueprint_id===d.blueprint_id) renderIndDetail(d);
      } else if(btn){
        btn.textContent=res && res.error?("⚠ "+res.error):"⚠ Failed"; btn.disabled=false;
      }
    }).catch(()=>{ if(btn){ btn.textContent="⚠ Failed"; btn.disabled=false; } });
}

function loadIndBuilds(){
  if(!AUTH.loggedIn){ IND.builds=[]; IND.buildsLoaded=true; renderIndBuilds(); return; }
  fetch("/api/ind/builds").then(r=>r.json()).then(res=>{
    IND.builds=(res && res.builds)||[];
    IND.buildsLoaded=true;
    // If jobs are already loaded, reconcile now (links jobs, marks done);
    // otherwise just render — the next char-data refresh will reconcile.
    if(AUTH.data && AUTH.data.jobs) reconcileBuilds(); else renderIndBuilds();
    // Pull the derived per-build state (stage / realized profit / abandoned)
    // from the summary and fold it in, so the board's sell state is populated
    // even before the user opens the Tracker.
    if(typeof loadSummary==="function") loadSummary();
  }).catch(()=>{ IND.buildsLoaded=true; });
}

// Fold the server's derived per-build fields from a /api/ind/summary payload
// into IND.builds: the FIFO-allocated realized profit, the lifecycle stage
// (built/listed/sold — the ledger-driven part the client can't compute), the
// abandoned flag, and the frozen units/cost. Job-linkage fields (job_id,
// done_at, job_end) stay client-authoritative — the client tracks live jobs the
// server can't see, so we never let a lagging server snapshot regress them.
// Returns true if anything changed (so the caller can re-render).
function mergeSummaryBuilds(summary){
  const rows=(summary&&summary.builds)||[];
  if(!rows.length && !IND.builds.length) return false;
  const byId=Object.fromEntries(rows.map(r=>[r.id, r]));
  let changed=false;
  IND.builds.forEach(b=>{
    const r=byId[b.id];
    if(!r) return;
    const before=JSON.stringify([b.stage, b.abandoned, b.realized,
                                 b.units_produced, b.cost_per_unit,
                                 b.is_listed_anchor, b.listed_units, b.held_units,
                                 b.sold_units, b.settling]);
    b.stage=r.stage;
    b.abandoned=r.abandoned;
    b.realized=r.realized;
    b.units_produced=r.units_produced;
    b.cost_per_unit=r.cost_per_unit;
    // The server's single reconcile pass decides which lot a live sell order's 🔗
    // links to (is_listed_anchor) — the client badge reads this, never a second
    // "is it listed?" rule, so the order badge and the tracker card always agree.
    b.is_listed_anchor=r.is_listed_anchor;
    b.listed_units=r.listed_units;
    b.held_units=r.held_units;
    // Physical units gone vs wallet-confirmed profit (realized.units): settling
    // means the card reads "sold" while ESI's wallet feed still trails the sale.
    b.sold_units=r.sold_units;
    b.settling=r.settling;
    if(JSON.stringify([b.stage, b.abandoned, b.realized,
                       b.units_produced, b.cost_per_unit,
                       b.is_listed_anchor, b.listed_units, b.held_units,
                       b.sold_units, b.settling])!==before) changed=true;
  });
  return changed;
}

// Match a tracked build to one of the character's live manufacturing jobs, by
// blueprint + run count. Prefers an as-yet-unclaimed job so several concurrent
// batches of the same blueprint each grab a distinct job.
function _findJobForBuild(b, claimed){
  const jobs=(AUTH.data&&AUTH.data.jobs)||[];
  const cands=jobs.filter(j=>j.activity_id===1 && j.blueprint_type_id===b.blueprint_id
    && (j.runs==null || j.runs===b.runs));
  return cands.find(j=>!claimed.has(String(j.job_id))) || null;
}

// Same blueprint but a DIFFERENT run count than tracked (e.g. tracked 30×,
// started 32× in EVE). Used to suggest a close match the user can accept with
// one click. Prefers the unclaimed job whose run count is nearest to the
// tracked value so several concurrent batches each pick the sensible neighbour.
function _findCloseJobForBuild(b, claimed){
  const jobs=(AUTH.data&&AUTH.data.jobs)||[];
  const cands=jobs.filter(j=>j.activity_id===1 && j.blueprint_type_id===b.blueprint_id
    && j.runs!=null && j.runs!==b.runs && !claimed.has(String(j.job_id)));
  if(!cands.length) return null;
  return cands.slice().sort((x,y)=>
    Math.abs(x.runs-b.runs)-Math.abs(y.runs-b.runs))[0];
}

// Set of active manufacturing job ids, as STRINGS. job_id round-trips through
// the server as a string (JSON→str), but ESI hands it back as a number in the
// same session — so comparisons must normalise to one type or a reloaded build
// never matches its live job and wrongly flips to "done". Always compare via
// String().
function _activeJobIdSet(){
  return new Set(((AUTH.data&&AUTH.data.jobs)||[])
    .filter(j=>j.activity_id===1 && j.job_id!=null).map(j=>String(j.job_id)));
}

// Resolved station/structure name of the live job a build is linked to, or ""
// if that job isn't in the current fetch. The server resolves facility_id to a
// name (falling back to "Structure" for unnamed citadels) on each job.
function _buildJobLocation(b){
  // Prefer the live job's resolved location; fall back to the location persisted
  // when the build first linked, so built/listed/sold builds (whose job has left
  // ESI's active list) still show where they were made.
  if(b.job_id!=null){
    const jobs=(AUTH.data&&AUTH.data.jobs)||[];
    const j=jobs.find(j=>String(j.job_id)===String(b.job_id));
    if(j&&j.location) return j.location;
  }
  return b.job_location||"";
}

// Recompute each build's status from live jobs and persist the transitions that
// must survive a reload (first link to a job, and completion). Returns nothing;
// mutates IND.builds in place and re-renders.
function reconcileBuilds(){
  if(!IND.builds.length){ renderIndBuilds(); return; }
  // Guard: if jobs haven't loaded yet (AUTH.data absent, or no jobs array),
  // we can't tell "job finished" from "not fetched" — don't mark anything done.
  const jobsKnown = !!(AUTH.data && Array.isArray(AUTH.data.jobs));
  const activeJobIds=_activeJobIdSet();
  const claimed=new Set();
  // Pre-seed with EVERY already-linked, still-active job id before the loop.
  // Otherwise an older unlinked build (processed first in created_at order) can
  // adopt a job that a newer, already-linked build owns, because that newer
  // build hasn't added its id to `claimed` yet — leaving two builds on one job.
  IND.builds.forEach(b=>{
    // Only pre-seed links held by builds that could legitimately still be
    // building. A built/listed/sold build holding an active job has a *stolen*
    // link (its own job was delivered long ago) — the main loop releases it, so
    // don't reserve that job here or the real batch can never claim it.
    if(b.job_id!=null && !b.done_at && activeJobIds.has(String(b.job_id))
       && _buildStage(b)!=="sold" && _buildStage(b)!=="listed")
      claimed.add(String(b.job_id));
  });
  let changed=false;
  // Order by created_at so the oldest batch claims the oldest matching job.
  const ordered=[...IND.builds].sort((a,b)=>(a.created_at||0)-(b.created_at||0));
  ordered.forEach(b=>{
    // A FINISHED build (done_at set — built/listed/sold) is finished forever:
    // done_at is monotonic and is NEVER cleared here. Its own manufacturing job
    // was delivered long ago, so if it still holds an ACTIVE job_id that link
    // points at a *different* live batch (same blueprint/runs) that got wrongly
    // adopted — release the link so it never shows on a finished card and the real
    // batch can claim the job, but leave done_at untouched.
    //
    // (Wiping done_at here was the "Listed→Built regression" bug: a delivered lot
    // briefly un-marked would re-stamp done_at to *now* on the next sweep, and the
    // server's FIFO delivery gate — a sale only counts if done_at ≤ sale time —
    // then rejected every earlier fill, collapsing the sold count to 0 and dropping
    // the stage back to Built. A finished build cannot be unfinished.)
    if(b.done_at){
      if(b.job_id!=null && activeJobIds.has(String(b.job_id))){
        b.job_id=null; b.job_end=null; changed=true;
        _patchBuildLink(b, {job_id:null, job_end:null});
      }
      return;
    }
    if(b.job_id!=null && activeJobIds.has(String(b.job_id))){
      claimed.add(String(b.job_id)); return;    // still running under its link
    }
    if(b.job_id!=null && jobsKnown && !activeJobIds.has(String(b.job_id))){
      // Its linked job left the active list → delivered.
      b.done_at=Date.now()/1000; changed=true;
      _patchBuildLink(b, {done_at:b.done_at});
      return;
    }
    if(b.job_id!=null) return;                  // linked but jobs unknown — hold
    // A build that already reached built/listed/sold must NOT adopt a fresh job:
    // a new in-game batch of the same blueprint (same runs) is a *different*
    // build. Without this guard an old sold build with no job link would grab the
    // new job, steal its ETA countdown, and leave the real batch card-less.
    if(_buildStage(b)!=="planned") return;
    // Not yet linked — try to adopt a matching active job.
    const job=_findJobForBuild(b, claimed);
    if(job){
      claimed.add(String(job.job_id));
      b.job_id=job.job_id; b.job_end=job.end; b.char_name=job.character_name;
      b.job_location=job.location||b.job_location; changed=true;
      _patchBuildLink(b, {job_id:job.job_id, job_end:job.end, char_name:job.character_name, job_location:b.job_location});
    }
  });
  renderIndBuilds();
  // Sales accrue server-side from wallet transactions (per-product FIFO ledger),
  // and a delivered build's stage (built → listed → sold) is derived there too.
  // Re-pull the summary so the merged per-build realized profit + stage reflect
  // the latest sweep. Any delivered build could have just gained a fill, so we
  // refresh whenever anything is past planning.
  const needsSellPull=IND.builds.some(b=>b.done_at);
  if(needsSellPull && typeof loadSummary==="function") loadSummary();
  return changed;
}

// ── Industry Planner ⇄ Tracker mode ──────────────────────────────────────────
// The Industry tab has two modes sharing one tablewrap: the Planner (the
// blueprint catalogue — what to build) and the Tracker (the portfolio P&L
// dashboard from summary.js plus the tracked-build cards for everything crafted
// and sold). A build leaves the Planner and lives in the Tracker the moment it's
// tracked. data-mode stays "summary" internally so saved prefs keep working.
// The last-used mode is server-authoritative.
function indSetMode(mode){
  IND.mode = (mode==="summary") ? "summary" : "planner";
  if(typeof setPref==="function") setPref('ind_mode', IND.mode);
  indApplyMode();
}

// Reflect IND.mode into the DOM: toggle the two views + the mode buttons, hide
// the scan-filter controls bar in Tracker mode, and (re)load the roll-up +
// build cards on entry. Safe to call whenever the Industry tab or auth changes.
function indApplyMode(){
  const tracker = IND.mode==="summary";
  document.querySelectorAll(".ind-mode-btn").forEach(b=>
    b.classList.toggle("active", b.dataset.mode===IND.mode));
  const planV=$("#ind-planner-view"), sumV=$("#ind-summary-view");
  if(planV) planV.classList.toggle("hidden", tracker);
  if(sumV) sumV.classList.toggle("hidden", !tracker);
  // The scan-filter controls belong to the Planner only.
  const ctrls=$("#ind-controls");
  if(ctrls && ACTIVE_TAB==="ind" && AUTH.loggedIn) ctrls.classList.toggle("hidden", tracker);
  _updateTrackCount();
  if(tracker){
    renderIndBuilds();                                  // cards live in the Tracker now
    if(typeof loadSummary==="function") loadSummary();  // dashboard above them
  }
}

// Keep the Tracker button's (N) badge in sync with the count of ACTIVE builds.
// Dead builds (archived/stopped) are hidden in the collapsed drawer and
// shouldn't inflate the badge — it reflects what's live, not the full history.
function _updateTrackCount(){
  const n=IND.builds.filter(b=>!b.archived&&!b.stopped).length;
  const badge=$("#ind-track-count");
  if(badge){
    badge.textContent = n?`(${n})`:"";
    badge.classList.toggle("hidden", !n);
  }
}

(function(){
  if(!document.querySelectorAll) return;
  document.querySelectorAll(".ind-mode-btn").forEach(b=>{
    b.onclick=()=>indSetMode(b.dataset.mode);
  });
})();

function _patchBuildLink(b, fields){
  const body=Object.assign({id:b.id}, fields);
  Object.keys(body).forEach(k=>{ if(body[k]==null) body[k]="null"; else body[k]=String(body[k]); });
  fetch("/api/ind/builds/link",{method:"POST",headers:{"Content-Type":"application/json"},
    body:JSON.stringify(body)}).catch(()=>{});
}

// Re-pull the portfolio dashboard (totals + by-item table) after a mutation that
// changes the stats — delete/edit/close/cancel/archive. Without this the summary
// above the cards keeps showing pre-mutation figures until a manual refresh.
function _refreshSummary(){
  if(IND.mode==="summary" && typeof loadSummary==="function") loadSummary();
}

function deleteBuild(id){
  IND.builds=IND.builds.filter(b=>b.id!==id);
  IND.buildsExpanded.delete(id);
  renderIndBuilds();
  fetch("/api/ind/builds/delete",{method:"POST",headers:{"Content-Type":"application/json"},
    body:JSON.stringify({id})}).then(_refreshSummary).catch(_refreshSummary);
}

// Archive (file away a FULLY-SOLD build) or unarchive. The server refuses to
// archive a build that still holds unsold stock — that's a "stop tracking" case
// — so this is server-authoritative: we apply the flag only if the call is ok,
// and surface the reason otherwise. Archiving freezes the build's realized
// profit; it stays in the portfolio total but leaves the board's lanes.
function archiveBuild(b, archived){
  fetch("/api/ind/builds/archive",{method:"POST",headers:{"Content-Type":"application/json"},
    body:JSON.stringify({id:b.id, archived:archived?"1":"0"})}).then(r=>r.json()).then(res=>{
    if(res && res.ok){
      b.archived=!!(res.build&&res.build.archived);
      if(res.build) b.frozen=res.build.frozen;
      renderIndBuilds();
      if(typeof loadSummary==="function") loadSummary(); else _refreshSummary();
    } else if(res && res.error){ alert(res.error); }
  }).catch(()=>{});
}

// Stop tracking a build that still holds unsold stock: what already sold is
// frozen (kept in the portfolio total), and the held remainder is orphaned —
// left on the market untracked and flagged with a per-product badge. The build
// goes dead: reconcile never touches it again, so later sales of the same item
// flow to your live builds. Pass stopped=false to resume tracking.
function stopBuild(b, stopped){
  fetch("/api/ind/builds/stop",{method:"POST",headers:{"Content-Type":"application/json"},
    body:JSON.stringify({id:b.id, stopped:stopped?"1":"0"})}).then(r=>r.json()).then(res=>{
    if(res && res.ok){
      if(res.build){ b.stopped=!!res.build.stopped;
        b.stopped_held=res.build.stopped_held||0; b.frozen=res.build.frozen; }
      renderIndBuilds();
      if(typeof loadSummary==="function") loadSummary(); else _refreshSummary();
    } else if(res && res.error){ alert(res.error); }
  }).catch(()=>{});
}

// Correct a tracked build's run count (the only editable field — it re-scales
// produced units, so cost and the sold/held split re-derive on reconcile).
// Refused on a dead (archived/stopped) build.
function editBuildRuns(b, runs){
  fetch("/api/ind/builds/edit",{method:"POST",headers:{"Content-Type":"application/json"},
    body:JSON.stringify({id:b.id, runs:String(runs)})}).then(r=>r.json()).then(res=>{
    if(res && res.ok){
      if(res.build){ b.runs=res.build.runs; b.abandoned=!!res.build.abandoned; }
      renderIndBuilds();
      if(typeof loadSummary==="function") loadSummary(); else _refreshSummary();
    } else if(res && res.error){ alert(res.error); }
  }).catch(()=>{});
}

// Job status of a build, derived from its stored link fields + live jobs. This
// tracks only the *manufacturing job* (awaiting → building → done); the card's
// badge uses _buildBadge below, which reflects the whole sell lifecycle so a
// built-but-unsold batch doesn't read as finished.
function _buildStatus(b){
  if(b.done_at) return {key:"done", label:"✓ Done"};
  if(b.job_id!=null && _activeJobIdSet().has(String(b.job_id))) return {key:"building", label:"⏳ Building"};
  return {key:"awaiting", label:"⚠ No matching job"};
}

// The card's colored badge, driven by the full lifecycle stage — not just the
// job. A batch that's built but hasn't sold anything stays amber ("Built · not
// sold"), an in-progress sale is cyan, and only a completed/closed sale turns
// green. This is what stops "✓ Done / Finished" from claiming a batch is over
// before a single unit has actually sold.
function _buildBadge(b, stage){
  if(stage==="planned") return {key:"awaiting", label:_buildStatus(b).label};
  if(stage==="building") return {key:"building", label:"⏳ Building"};
  if(stage==="built")    return {key:"built", label:"🔨 Built · not sold"};
  if(stage==="listed"){
    const rz=_buildRealized(b);
    return {key:"listed", label:rz.units>0?"◑ Selling":"◔ Listed"};
  }
  // sold
  return {key:"sold", label:b.abandoned?"✓ Closed early":"✓ Sold"};
}

// Explicit lifecycle stage for the stepper: planned → building → built →
// listed → sold. The server derives built/listed/sold from the wallet sell
// ledger + live open-order volume (things the client can't see) and returns it
// as `b.stage`; the client refines only the pre-delivery split (planned vs
// building) against live jobs, which the server can't observe. Until the
// summary has merged its derived stage in, an undelivered build reads planned/
// building and a delivered one falls back to "built".
const _BUILD_STAGES=["planned","building","built","listed","sold"];
const _STAGE_LABEL={planned:"Planned",building:"Building",built:"Built",listed:"Listed",sold:"Sold"};
// A build is "in progress" only while it still occupies the blueprint — planned
// or building. Once it's built (delivered), listed or sold the blueprint is free
// for a new batch, so those stages don't count as a duplicate-tracking clash.
function _isInProgressStage(stage){ return stage==="planned" || stage==="building"; }
function _buildStage(b){
  // Not yet delivered → the live-job view (client-authoritative) decides.
  if(!b.done_at){
    return _buildStatus(b).key==="building" ? "building" : "planned";
  }
  // Delivered → trust the server's ledger-derived stage (built/listed/sold).
  return b.stage || "built";
}

// Total units this batch yields (product qty per run × runs) and the frozen
// cost basis per produced unit — used by the sell panel + realized math.
function _buildUnits(b){
  const per=((b.snapshot||{}).product||{}).quantity;
  return per==null?null:per*Math.max(1,b.runs||1);
}
function _buildCostPerUnit(b){
  const cost=_batchEconomics(b.snapshot||{}, b.runs||1).cost;
  const units=_buildUnits(b);
  return (cost==null||!units)?null:cost/units;
}

// Realized sale totals accrued server-side (FIFO allocation of the wallet sell
// ledger onto this build's lot): units sold, net revenue after sales tax, frozen
// cost of those units, the abandoned-remainder write-off, and profit. Merged
// onto the build as `b.realized` by the summary pull; zero-filled until then.
function _buildRealized(b){
  const r=b.realized||{};
  return {units:r.units||0, net:r.net||0,
          cost:(r.cost_of_sold!=null)?r.cost_of_sold:null,
          writeoff:r.writeoff||0,
          profit:(r.profit!=null)?r.profit:null};
}

// Break-even sell price per unit (revenue exactly covers total batch cost).
// Instant sale pays sales tax only; a list order also pays the broker fee.
function _buildBreakEven(b){
  const d=b.snapshot||{};
  const cost=_batchEconomics(d, b.runs||1).cost;
  const units=_buildUnits(b);
  const stax=d.sales_tax||0, bfee=d.broker_fee||0;
  if(cost==null||!units) return {list:null, instant:null};
  return {
    instant:(1-stax)>0?cost/(units*(1-stax)):null,
    list:(1-stax-bfee)>0?cost/(units*(1-stax-bfee)):null,
  };
}

// Render the tracked builds as a PIPELINE BOARD: one lane per lifecycle stage
// (planned → building → built → listed → sold), each holding compact tiles that
// flow top-to-bottom. Clicking a tile expands it into a focus panel below the
// board — that panel reuses the full card (_buildCardHtml/_wireBuildCard) so
// every sell / close / edit / compare action stays exactly where it was. The
// board answers "what's in here?" at a glance; the focus panel answers "what
// about this one?" on demand. Only rendered while the Tracker is active; the
// Planner shows a link-across hint instead. If the Character overview is the
// active tab, refresh it too so its 🔗 tracked-job markers stay in sync.
//
// Lane order follows the pipeline left→right so work reads like a flow: the
// stages that need you (planned/building/built) sit up front, finished sales at
// the end. Keyed by _buildStage()'s output.
const _BUILD_GROUP_ORDER=["planned","building","built","listed","sold"];
const _BUILD_GROUP_LABEL={
  planned:"⚠ Planned", building:"⏳ Building", built:"✓ Built — ready to list",
  listed:"🏷 Listed for sale", sold:"💰 Sold",
};
// Terse lane headers for the board — the full labels above are used elsewhere
// (e.g. the peek). A lane header is a stage name + tile count, nothing more.
const _LANE_LABEL={
  planned:"Planned", building:"Building", built:"Built",
  listed:"Listed", sold:"Sold",
};
// A representative timestamp for ordering builds *within* a status group, newest
// first. Uses the stage-relevant moment (finish/list/sale) when known so the most
// recently-progressed build leads, falling back to when it was first tracked.
function _buildSortTs(b){
  return b.done_at
      || (b.job_end?Date.parse(b.job_end)/1000:0) || b.created_at || 0;
}
// Finish time (epoch seconds) of a building job, for soonest-first ordering.
// Builds with no known end (unlinked / awaiting) sort last (+Infinity).
function _buildFinishTs(b){
  const end=b.job_end?Date.parse(b.job_end):NaN;
  return isFinite(end)?end/1000:Infinity;
}

function renderIndBuilds(){
  _updateTrackCount();
  const box=$("#ind-builds");
  if(box){
    if(IND.mode!=="summary" || !IND.builds.length){
      box.classList.add("hidden"); box.innerHTML="";
      IND.focusedBuild=null;
    } else {
      box.classList.remove("hidden");
      // Jobs already linked to a build must not be offered as a close match to
      // an awaiting one. Collect the linked ids (as strings) up front.
      const linked=new Set(IND.builds.filter(b=>b.job_id!=null).map(b=>String(b.job_id)));
      // Dead builds — archived (filed away fully sold) and stopped (untracked
      // with a remainder) — sit in their own collapsed drawer below the board,
      // out of the lane buckets. They still carry their frozen realized profit in
      // the portfolio stats; they're just closed out of the active pipeline.
      const archived=IND.builds.filter(b=>b.archived||b.stopped);
      const active=IND.builds.filter(b=>!b.archived&&!b.stopped);
      // A dropped/deleted build could still be the focused one — forget it so the
      // panel doesn't try to render a card that no longer exists.
      if(IND.focusedBuild && !IND.builds.some(b=>b.id===IND.focusedBuild))
        IND.focusedBuild=null;
      // Bucket every active build by lifecycle stage.
      const buckets={};
      active.forEach(b=>{ (buckets[_buildStage(b)]||(buckets[_buildStage(b)]=[])).push(b); });

      // ── Lanes ────────────────────────────────────────────────────────────
      // One column per stage. An empty lane still shows (as a thin "—" rail) so
      // the pipeline's shape is constant and a build visibly moves rightward as
      // it progresses, rather than lanes appearing and vanishing.
      const laneHtml=_BUILD_GROUP_ORDER.map(key=>{
        const list=buckets[key]||[];
        // Building lane: soonest-to-finish leads. Every other: newest progress first.
        if(key==="building") list.sort((a,b)=>_buildFinishTs(a)-_buildFinishTs(b));
        else list.sort((a,b)=>_buildSortTs(b)-_buildSortTs(a));
        const tiles=list.length
          ? list.map(b=>_buildTileHtml(b, linked)).join("")
          : `<div class="ind-lane-empty">—</div>`;
        return `<section class="ind-lane stage-${key}" data-stage="${key}">
          <header class="ind-lane-head">
            <span class="ind-lane-name">${_LANE_LABEL[key]||key}</span>
            <span class="ind-lane-count">${list.length}</span>
          </header>
          <div class="ind-lane-tiles">${tiles}</div>
        </section>`;
      }).join("");
      // ── Untracked-stock warning ──────────────────────────────────────────
      // A stopped build can leave held units on the market untracked. Surface
      // that per product as a persistent banner so those orphaned units aren't
      // silently forgotten — the sale of them won't accrue to any tracked build.
      const orphanByProduct={};
      IND.builds.filter(b=>b.stopped && (b.stopped_held||0)>0).forEach(b=>{
        const k=b.product_type_id;
        if(!orphanByProduct[k]) orphanByProduct[k]={name:b.product_name||"?", units:0};
        orphanByProduct[k].units+=b.stopped_held||0;
      });
      const orphans=Object.values(orphanByProduct);
      let warnHtml="";
      if(orphans.length){
        const items=orphans.sort((a,b)=>b.units-a.units)
          .map(o=>`<li><b>${o.units.toLocaleString()}×</b> ${o.name}</li>`).join("");
        warnHtml=`<div class="ind-orphan-warn" role="status">
          <div class="ind-orphan-head">⚠ Untracked stock on the market</div>
          <div class="ind-orphan-sub">You stopped tracking builds that still had unsold units. Their sales won't accrue to any build — check these:</div>
          <ul class="ind-orphan-list">${items}</ul></div>`;
      }
      let html=warnHtml+`<div class="ind-board" role="list">${laneHtml}</div>`;

      // ── Focus panel ──────────────────────────────────────────────────────
      // The full card for whichever tile is focused, docked under the board.
      const fb=IND.focusedBuild ? IND.builds.find(b=>b.id===IND.focusedBuild) : null;
      if(fb){
        html+=`<div class="ind-focus" data-id="${fb.id}">
          <div class="ind-focus-head">
            <span class="ind-focus-lbl">Build detail</span>
            <button class="ind-focus-close" title="Close (Esc)">✕ Close</button>
          </div>
          ${_buildCardHtml(fb, linked)}
        </div>`;
      }

      // ── Archived drawer ──────────────────────────────────────────────────
      if(archived.length){
        archived.sort((a,b)=>_buildSortTs(b)-_buildSortTs(a));
        if(IND.buildGroups.archived===undefined) IND.buildGroups.archived=true;
        const collapsed=IND.buildGroups.archived===true;
        const tiles=archived.map(b=>_buildTileHtml(b, linked)).join("");
        html+=`<div class="ind-archive-drawer${collapsed?" collapsed":""}" data-grp="archived">
          <div class="ind-archive-head" data-grp="archived">
            <span class="grp-arrow">▾</span>📦 Archived &amp; stopped
            <span class="chip-count">(${archived.length})</span></div>
          <div class="ind-archive-tiles">${tiles}</div></div>`;
      }
      box.innerHTML=html;

      // Tile click → focus that build (toggle off if it's already focused).
      box.querySelectorAll(".ind-tile").forEach(tile=>{
        tile.onclick=ev=>{
          if(ev.target.closest("button,input,a")) return;
          const id=tile.dataset.id;
          IND.focusedBuild = (IND.focusedBuild===id) ? null : id;
          // The focus panel leads with the stage-specific decision view; the full
          // frozen breakdown (cost basis, materials) stays behind "Full detail" so
          // the panel opens uncluttered — the user reveals the numbers on demand.
          renderIndBuilds();
          if(IND.focusedBuild){
            const panel=box.querySelector(".ind-focus");
            if(panel) panel.scrollIntoView({block:"nearest", behavior:"smooth"});
          }
        };
        tile.onkeydown=ev=>{
          if(ev.key==="Enter"||ev.key===" "){ ev.preventDefault(); tile.click(); }
        };
        // Per-tile archive (sold tiles only) — file the build away without
        // opening it. Reuses the same archiveBuild() the full card calls.
        const arch=tile.querySelector(".ind-tile-archive");
        if(arch) arch.onclick=ev=>{
          ev.stopPropagation();
          const b=IND.builds.find(x=>x.id===tile.dataset.id);
          if(b) archiveBuild(b, true);
        };
      });
      // Focus panel: close button + the full card's own wiring.
      const closeBtn=box.querySelector(".ind-focus-close");
      if(closeBtn) closeBtn.onclick=()=>{ IND.focusedBuild=null; renderIndBuilds(); };
      if(IND.focusedBuild){
        const fbid=IND.focusedBuild;
        const fbuild=IND.builds.find(b=>b.id===fbid);
        if(fbuild) _wireBuildCard(box, fbuild);
      }
      // Archived drawer collapse toggle (persisted server-side).
      const ah=box.querySelector(".ind-archive-head");
      if(ah) ah.onclick=()=>{
        IND.buildGroups.archived=!IND.buildGroups.archived;
        setPref('ind.build_groups', IND.buildGroups);
        renderIndBuilds();
      };
      // Prefetch the market for LISTED builds so their tiles can flag a needed
      // re-price/dump without the user opening each one. Uses the shared decider
      // cache + fetch path (so opening the card later reuses it), and re-paints
      // the tiles once the data lands.
      _prefetchListedFlags(box, (buckets.listed||[]));
    }
  }
  // Keep the overview's job 🔗 markers in sync (only when it's showing).
  if(ACTIVE_TAB==="char" && AUTH.data && typeof renderCharData==="function") renderCharData();
}

// ── Pipeline tile ────────────────────────────────────────────────────────────
// The compact face of a build in its lane. Shows only what tells you the build's
// state at a glance: its name, run count, and one stage-appropriate readout —
// a "start it" nudge (planned), a live countdown + progress bar (building),
// the ready profit (built), a sold/target progress bar (listed), or the realized
// profit (sold). Clicking opens the full card in the focus panel below.
function _buildTileHtml(b, linked){
  const s=b.snapshot||{}, n=Math.max(1, b.runs||1);
  const isk=v=>v===null||v===undefined?"—":fmtISK(v);
  const pn=v=>v==null?"":(v>0?"pos":(v<0?"neg":""));
  const stage=_buildStage(b);
  const be=_batchEconomics(s, n);
  const focused=IND.focusedBuild===b.id;

  // The one stage-specific readout line + optional bar + optional footer.
  let line="", bar="", foot="";
  // A building job whose countdown has already elapsed is finished in-game and
  // waiting to be delivered — the moment the player most wants to spot. Flag it
  // so the tile lights up (mirrored live by the 1s timer loop when it hits zero).
  let ready=false;
  if(stage==="planned"){
    const st=_buildStatus(b);
    line = st.key==="awaiting"
      ? `<span class="ind-tile-warn">⚠ Start it in EVE</span>`
      : `<span class="ind-tile-dim">${st.label}</span>`;
  } else if(stage==="building"){
    const end=b.job_end?Date.parse(b.job_end):null;
    if(end && isFinite(end)){
      ready = end<=Date.now();
      if(ready){
        line=`<span class="ind-tile-ready">✓ ready to deliver</span>`;
      } else {
        // Progress bar from tracked-at → job end; fraction of the build elapsed.
        const total=end-((b.created_at||0)*1000);
        const done=Date.now()-((b.created_at||0)*1000);
        const pct=(total>0)?Math.max(0,Math.min(100,done/total*100)):0;
        line=`<span class="ind-tile-live ind-live-timer" data-end="${end}">${fmtCountdownShort(end-Date.now())}</span>`;
        bar=`<div class="ind-tile-bar"><span class="ind-tile-bar-fill building" style="width:${pct.toFixed(1)}%"></span></div>`;
      }
    } else {
      line=`<span class="ind-tile-live">running</span>`;
    }
  } else if(stage==="built"){
    line=`<span class="ind-tile-dim">ready ·</span> <b class="${pn(be.profitL)}">${isk(be.profitL)}</b>`;
  } else if(stage==="listed"){
    const rz=_buildRealized(b);
    const target=_buildUnits(b)||0;
    const pct=target>0?Math.min(100,rz.units/target*100):0;
    // Surface the decider's Call as a tiny flag on the tile itself, so a lot that
    // needs a decision (re-price / dump) is spottable across the board without
    // opening each one. Computed from the prefetched market (see _prefetchListed);
    // null until that lands or when the verdict is "hold" — the flag means action.
    const flag=_tileActionFlag(b);
    line=`<span class="ind-tile-dim">${rz.units.toLocaleString()} / ${target.toLocaleString()} sold</span>`;
    bar=`<div class="ind-tile-bar"><span class="ind-tile-bar-fill listed" style="width:${pct.toFixed(1)}%"></span></div>`;
    if(flag) foot=`<div class="ind-tile-action ${flag.action}" title="Suggested action: ${flag.tip.replace(/"/g,'&quot;')}">⚠ ${flag.action==="dump"?"Dump":"Re-price"}</div>`;
  } else if(stage==="stopped"){
    const rz=_buildRealized(b);
    const orphan=b.stopped_held||0;
    line=`<span class="ind-tile-dim">stopped ·</span> <b class="${pn(rz.profit)}">${isk(rz.profit)}</b>`
      +(orphan>0?` <span class="ind-tile-warn" title="${orphan.toLocaleString()} unsold unit(s) left on the market, no longer tracked">⚠ ${orphan.toLocaleString()} untracked</span>`:"");
  } else { // sold
    const rz=_buildRealized(b);
    const early=b.abandoned;
    line=`<span class="ind-tile-dim">${early?"closed":"sold"} ·</span> <b class="${pn(rz.profit)}">${isk(rz.profit)}</b>`;
    // The card can read "sold" (order-diff saw the order empty) before ESI's
    // laggy wallet feed has priced every unit. Until it catches up the profit
    // shown covers only the wallet-confirmed units — flag it as provisional so
    // a trailing number doesn't look like the final tally.
    if(b.settling) line+=` <span class="ind-tile-warn" title="Sold, but ESI's wallet feed hasn't priced every unit yet — the profit shown is still settling and will finish updating within a few minutes.">⏳ settling</span>`;
    // Finished builds can be archived straight from the board — the same
    // declutter action the full card offers, brought up to the tile so a sold
    // batch can be filed away without opening it. Dead builds (archived/stopped)
    // already live in the drawer and offer their own resume/unarchive actions.
    if(!b.archived&&!b.stopped) foot=`<div class="ind-tile-foot">`
      +`<button class="ind-tile-archive" title="Archive this sold build — hides it in the collapsed Archived section below. It still counts in your portfolio stats.">📦 Archive</button></div>`;
  }

  return `<div class="ind-tile stage-${stage}${focused?" focused":""}${ready?" ready":""}" role="listitem"
      tabindex="0" data-id="${b.id}" data-stage="${stage}" title="${(b.product_name||"").replace(/"/g,'&quot;')} — click for full detail">
    <div class="ind-tile-name">${b.product_name||"?"}</div>
    <div class="ind-tile-runs">${n.toLocaleString()} run${n===1?"":"s"}</div>
    <div class="ind-tile-line">${line}</div>
    ${bar}
    ${foot}
  </div>`;
}

// Prefetch the live quote + sell-analysis for every LISTED build on the board so
// its tile can show the re-price/dump flag without the user opening the card. Kicks
// the same cached fetch path the decider uses (so opening the card later is free);
// the fetches call _renderTileFlag on completion, which re-paints the tile in place.
function _prefetchListedFlags(box, listed){
  if(!box || !listed || !listed.length) return;
  listed.forEach(b=>{
    const st=_deciderState(b);
    if(st.marketState==="done") _renderTileFlag(b);       // already cached: paint now
    else if(st.marketState==="idle"){ st.marketState="loading"; _fetchDeciderMarket(b); }
    if(st.liveState==="idle"){ st.liveState="loading"; _fetchDeciderLive(b); }
  });
}
// Paint (or clear) a listed tile's action flag from the current decider cache. A
// no-op when the tile isn't on screen, so the decider fetches can call it blindly
// (like they call _renderDeciderBody) without knowing whether the board's showing.
function _renderTileFlag(b){
  const tile=document.querySelector(`#ind-builds .ind-tile[data-id="${CSS.escape(b.id)}"]`);
  if(!tile || tile.dataset.stage!=="listed") return;
  const old=tile.querySelector(".ind-tile-action"); if(old) old.remove();
  const flag=_tileActionFlag(b);
  if(!flag) return;
  const el=document.createElement("div");
  el.className=`ind-tile-action ${flag.action}`;
  el.title=`Suggested action: ${flag.tip}`;
  el.textContent=`⚠ ${flag.action==="dump"?"Dump":"Re-price"}`;
  tile.appendChild(el);
}

// Expand a tracked build's detailed view and scroll to it. Used when arriving
// from a clicked industry-job row in the Character overview. The cards live in
// the Tracker now, so switch into it first.
function openTrackedBuild(id){
  const b=IND.builds.find(x=>x.id===id);
  if(!b) return;
  // Focus its tile so the full card opens in the panel below the board. An
  // archived build's tile lives in the (default-collapsed) drawer — open it so
  // the tile we're about to scroll to is visible.
  IND.focusedBuild=id;
  IND.buildsExpanded.add(id);
  if(b.archived||b.stopped) IND.buildGroups.archived=false;
  if(IND.mode!=="summary") indSetMode("summary"); else renderIndBuilds();
  const box=$("#ind-builds");
  const panel=box&&box.querySelector(".ind-focus");
  if(panel) panel.scrollIntoView({block:"center", behavior:"smooth"});
  else {
    const tile=box&&box.querySelector(`.ind-tile[data-id="${CSS.escape(id)}"]`);
    if(tile) tile.scrollIntoView({block:"center", behavior:"smooth"});
  }
}

// Batch economics for a detail blob `d` at run count `n`, applying EVE's
// job-level Material Efficiency rounding to the material cost (not per-run × N).
// Works for live, frozen and re-based (close-match) builds, and degrades to the
// old per-run × N behaviour for snapshots saved before base_qty was recorded.
function _batchEconomics(d, n){
  n=Math.max(1, n||1);
  const me=d.me_used||0;
  let matCost=null;
  if(Array.isArray(d.required_items) && d.required_items.some(m=>m.base_qty!=null)){
    matCost=0;
    for(const m of d.required_items){
      if(m.unit_price==null) continue;
      const q=(m.base_qty!=null)?effectiveQty(m.base_qty, me, n):m.eff_qty*n;
      matCost+=q*m.unit_price;
    }
  }
  const jobPlusInvRun=(d.job_cost||0)+(d.invention?(d.invention_cost||0):0);
  // matCost known → rebuild total from parts; else fall back to per-run × N.
  const cost=(matCost!=null)?matCost+jobPlusInvRun*n
           :(d.total_cost!=null?d.total_cost*n:null);
  const revL=d.revenue_patient!=null?d.revenue_patient*n:null;
  // Instant revenue walks the live buy book for the whole batch, skipping orders
  // whose min_volume the batch can't meet (see walkBook) — a 60k-min buyer can't
  // take a 4.2k batch, so it mustn't inflate the dump-now figure. Falls back to
  // the raw aggregate only when no book shipped.
  const qtyTot=(d.product&&d.product.quantity?d.product.quantity:1)*n;
  const bw=(d.buy_book&&d.buy_book.length)?walkBook(d.buy_book,qtyTot):null;
  const revI=(bw&&bw.filled>0)?bw.avg*bw.filled*(1-(d.sales_tax||0))
           :(d.buy_book?null:(d.revenue_instant!=null?d.revenue_instant*n:null));
  const profitL=(revL!=null&&cost!=null)?revL-cost
              :(d.profit_patient!=null?d.profit_patient*n:null);
  const profitI=(revI!=null&&cost!=null)?revI-cost:null;
  return {cost, profitL, profitI, matCost, time:d.build_time?d.build_time*n:null};
}

function _buildCardHtml(b, linked){
  const n=Math.max(1, b.runs||1);
  const st=_buildStatus(b);
  const when=b.created_at?new Date(b.created_at*1000).toLocaleString([],{day:'2-digit',month:'short',hour:'2-digit',minute:'2-digit'}):"";
  // Status line: warning if no job yet, live countdown + ETA while building.
  // Once built/listed/sold there's no line — the stepper hover carries the state.
  // Gate on the lifecycle `stage`, not the raw job status: a linked job can still
  // sit in ESI's active list after the batch has been listed and fully sold, so
  // _buildStatus would say "building" and render a live countdown on a Sold card.
  const stage=_buildStage(b);
  let statusLine="";
  if(stage==="planned" && st.key==="awaiting"){
    // If a running job for this blueprint exists with a different run count,
    // offer it as a one-click close match instead of only nagging. `linked` is
    // shared across cards this render, and includes jobs already suggested to an
    // earlier awaiting card — so two awaiting builds of the same blueprint never
    // both point at the same job (which would double-link on accept).
    const claimedJobs=linked||new Set();
    const close=AUTH.loggedIn ? _findCloseJobForBuild(b, claimedJobs) : null;
    if(close){
      claimedJobs.add(String(close.job_id));   // reserve it for this card
      const cn=close.runs;
      statusLine=`<span class="ind-build-warn">No exact match — but a running `
        +`<b>${cn.toLocaleString()}×</b> job of this blueprint`
        +`${close.character_name?" ("+close.character_name+")":""} is in progress `
        +`(you tracked ${n.toLocaleString()}×).</span> `
        +`<button class="ind-build-linkclose" data-job="${close.job_id}" `
        +`data-runs="${cn}" title="Link this build to that job and re-base it onto ${cn.toLocaleString()} run(s)">`
        +`Link to ${cn.toLocaleString()}× job</button>`;
    } else {
      statusLine=`<span class="ind-build-warn">No matching in-game job yet — start ${n.toLocaleString()}× run(s) of this blueprint in EVE${AUTH.loggedIn?" and it'll link automatically":"; log in with EVE to link"}.</span>`;
    }
  } else if(stage==="building"){
    const end=b.job_end?Date.parse(b.job_end):null;
    // The linked live job carries a resolved station/structure name — show where
    // the batch is being built so a multi-location industrialist knows where to
    // pick it up.
    const loc=_buildJobLocation(b);
    const meta=(b.char_name?" · "+b.char_name:"")+(loc?" · 📍 "+loc:"");
    statusLine=end && isFinite(end)
      ? `<span class="ind-build-live ind-live-timer" data-end="${end}">${fmtCountdown(end-Date.now())}</span> <span class="ind-build-eta">ETA ${new Date(end).toLocaleString([],{day:'2-digit',month:'short',hour:'2-digit',minute:'2-digit'})}${meta}</span>`
      : `<span class="ind-build-live">running${meta}</span>`;
  }
  // Once the build is finished (built/listed/sold) the stepper + sell block carry
  // the state; the old "Build finished <date>" line is gone — its timestamp now
  // lives in the stepper's hover tooltip.
  const expanded=IND.buildsExpanded.has(b.id);
  const detail=expanded?_buildDetailHtml(b):"";
  const badge=_buildBadge(b, stage);
  const stepper=_buildStepperHtml(b, stage);
  const sellBlock=_buildSellHtml(b, stage);
  // Header is pure identity now — name, run count, when it was frozen. The
  // economics (cost, both exit strategies, build time) used to crowd this row as
  // eight dim chips AND repeat in the expanded detail below; they now live in one
  // place, the detail's readout, so the panel reads top-to-bottom without saying
  // the same numbers twice.
  return `<div class="ind-build-card ${badge.key} stage-${stage}" data-id="${b.id}">
    <div class="ind-build-row">
      <span class="ind-build-status ${badge.key}">${badge.label}</span>
      <span class="ind-build-name">${b.product_name||"?"}</span>
      <span class="ind-build-runs">${n.toLocaleString()} run(s)</span>
      <span class="ind-build-when">frozen ${when}</span>
      <button class="ind-build-toggle" title="Show or hide the full frozen breakdown — cost basis, both sell strategies and the material list">${expanded?"▲ Hide detail":"▼ Full detail"}</button>
      ${(stage==="listed"||stage==="sold")?"":`<button class="ind-build-del" title="Stop tracking this build">✕</button>`}
    </div>
    ${stepper}
    ${statusLine?`<div class="ind-build-substatus">${statusLine}</div>`:""}
    ${sellBlock}
    ${detail}
  </div>`;
}

// The lifecycle stepper: planned → building → built → listed → sold, with the
// current stage highlighted and everything up to it marked done. The stage that
// needs the user (built → "list it in game") is styled as "active" so the card
// reads as a guided flow, not just a status label.
// Each step carries a data-tip: hovering a stage shows what it means and when it
// happened (completed / started / ETA), so the timestamps live in a popup rather
// than cluttering the card.
function _buildStepperHtml(b, stage){
  const idx=_BUILD_STAGES.indexOf(stage);
  const dots=_BUILD_STAGES.map((s,i)=>{
    const cls=i<idx?"done":(i===idx?"active":"todo");
    const tip=_stageTip(b, s, cls).replace(/"/g,"&quot;");
    return `<span class="ind-step ${cls}" data-tip="${tip}"><i class="ind-step-dot"></i>${_STAGE_LABEL[s]}</span>`;
  }).join(`<i class="ind-step-sep"></i>`);
  return `<div class="ind-build-stepper">${dots}</div>`;
}

// Short local timestamp for a unix-seconds value (or "" if absent).
function _stageTs(ts){
  return ts?new Date(ts*1000).toLocaleString([],{day:'2-digit',month:'short',hour:'2-digit',minute:'2-digit'}):"";
}

// The hover text for one stepper stage: a one-line "what this step is" plus its
// timing — when it completed (done), when it started / its ETA (active/ongoing),
// or that it hasn't happened yet (todo).
function _stageTip(b, s, cls){
  const done=cls==="done", active=cls==="active";
  if(s==="planned"){
    const when=_stageTs(b.created_at);
    return `Planned — build tracked${when?" "+when:""}.`;
  }
  if(s==="building"){
    if(active){
      const end=b.job_end?Date.parse(b.job_end):null;
      const loc=_buildJobLocation(b);
      const meta=(b.char_name?" · "+b.char_name:"")+(loc?" · 📍 "+loc:"");
      return end&&isFinite(end)
        ? `Building — manufacturing job running, ETA ${_stageTs(end/1000)}${meta}.`
        : (b.job_id!=null?`Building — job running${meta}.`
                        :"Building — no in-game job linked yet; start the runs in EVE.");
    }
    if(done) return `Building — job delivered${b.done_at?" "+_stageTs(b.done_at):""}.`;
    return "Building — manufacturing job not started yet.";
  }
  if(s==="built"){
    if(active) return `Built — job delivered${b.done_at?" "+_stageTs(b.done_at):""}; ready to list for sale.`;
    if(done) return `Built — completed${b.done_at?" "+_stageTs(b.done_at):""}, now selling.`;
    return "Built — waiting on the manufacturing job.";
  }
  if(s==="listed"){
    const rz=_buildRealized(b);
    const target=_buildUnits(b)||0;
    if(active){
      const sold=rz.units>0?` · ${rz.units.toLocaleString()}/${target.toLocaleString()} sold`:"";
      return `Listed — units of this item are on the market${sold}. Sales accrue from your wallet automatically.`;
    }
    if(done) return "Listed — all units sold.";
    return "Listed — not on the market yet.";
  }
  // sold
  if(active){
    return b.abandoned
      ? "Sold — closed early; unsold remainder written off."
      : "Sold — every produced unit has sold.";
  }
  return "Sold — sale not finished yet.";
}

// Proposed list price for a built batch: the current ask if we have one, else
// the frozen ask, floored at break-even so a nudge never proposes a loss.
function _buildProposedPrice(b){
  const d=b.snapshot||{};
  const be=_buildBreakEven(b).list;
  const ask=d.ask;
  if(ask==null) return be;
  return (be!=null)?Math.max(ask, be):ask;
}

// The sell section of a card — the stage's actionable heart. Each lifecycle
// stage answers ONE question, and the panel shows only what serves that answer;
// everything else lives behind the "Full detail" toggle. Sales are FULLY
// AUTOMATIC in the pooled model (money accrues from wallet transactions, no order
// linking), so there are no start/link/close/edit buttons.
//  • planned  → "what do I buy, and is it worth it?" — the batch shopping bill
//               plus a list-vs-instant profit hint.
//  • building → "where is it and when's it done?" — nothing to act on but a look-
//               ahead at the market (the price decider works pre-delivery too).
//  • built    → "what do I list it at?" — the inline price decider: predicted-vs-
//               now drift, a break-even-aware price slider, live sell-through odds
//               and a copy-to-the-cent price.
//  • listed   → "should I re-price to move it?" — sold-so-far + the same decider,
//               scoped to the unsold remainder.
//  • sold      → "how did I do vs the plan?" — real profit against the prediction,
//               with a what-if for dumping into buy orders instead.
function _buildSellHtml(b, stage){
  const isk=v=>v===null||v===undefined?"—":fmtISK(v);
  const pn=v=>v==null?"":(v>0?"pos":(v<0?"neg":""));
  const s=b.snapshot||{}, n=Math.max(1, b.runs||1);
  const econ=_batchEconomics(s, n);

  if(stage==="planned"){
    // Pre-commitment view: "is this build still worth starting?" The two exit
    // routes are the anchor — but framed as a FORECAST, priced at today's market,
    // that will drift by the time the lot is actually in hand. The shopping bill
    // sits above as the stake; a forecast rail below foreshadows the time delta
    // (roughly the build time) so the user starts expecting predicted≠reality.
    const matCost=(econ.matCost!=null)?econ.matCost
      :(s.material_cost!=null?s.material_cost*n:null);
    const nMats=(s.required_items||[]).length;
    const units=_buildUnits(b);
    const horizon=econ.time!=null?fmtDur(econ.time):null;
    return `<div class="ind-sell ind-plan" data-id="${b.id}">
      <div class="ind-plan-buy">
        <span class="ind-plan-lbl">Shopping bill</span>
        <span class="ind-plan-cost">${isk(matCost)}</span>
        <span class="ind-plan-sub">${nMats?`${nMats.toLocaleString()} material${nMats===1?"":"s"} · `:""}${units!=null?`${units.toLocaleString()} unit${units===1?"":"s"} out`:""} · full list below</span>
      </div>
      <div class="ind-plan-out" data-role="routes">
        <div class="ind-plan-way list">
          <span class="ind-plan-way-lbl">Sell &amp; wait</span>
          <b class="${pn(econ.profitL)}">${_signIsk(econ.profitL)}</b>
          <span class="ind-plan-way-sub">list &amp; be patient — if it sells</span>
        </div>
        <div class="ind-plan-way instant">
          <span class="ind-plan-way-lbl">or dump now</span>
          <b class="${pn(econ.profitI)}">${_signIsk(econ.profitI)}</b>
          <span class="ind-plan-way-sub">straight into buy orders — sure thing</span>
        </div>
      </div>
      <div class="ind-plan-forecast">⌛ <b>Forecast</b> at today's prices${horizon?` — this lands in about <b>${horizon}</b> of build time`:""}. The market can move by delivery, so re-check the real spread once it's built.</div>
    </div>`;
  }

  if(stage==="building"){
    // Dead time — nothing to act on yet, but the delta accrues here. The ETA +
    // location sit in the status line above; this panel gives the user a REASON
    // to check: has the market moved under the frozen plan since it started? The
    // watch (wired async) shows frozen-ask → live-ask drift and what that does to
    // the projected list profit — early warning that the forecast is drifting,
    // without pretending they can list yet.
    return `<div class="ind-sell ind-sell-peek" data-id="${b.id}">
      <div class="ind-watch" data-id="${b.id}" data-role="watch">
        <div class="ind-watch-load">Checking how the market's moved since you started this build…</div>
      </div>
      <button class="ind-sell-analyze" title="Open the price decision tool: market trend + the odds this batch sells within a day at a given price — look ahead before it's delivered">📊 See the full market ▸</button>
    </div>`;
  }

  if(stage==="built"){
    // THE decision stage: what to list at. The inline decider carries it — a
    // live drift vs the frozen prediction, a price slider with sell-through odds,
    // and BOTH exit routes side by side: list at your chosen price (patient) vs
    // dump into buy orders now (instant). Each shows its own profit so the trade
    // — more ISK later vs. cash today — is a direct comparison, not a guess.
    return `<div class="ind-sell ind-sell-nudge" data-id="${b.id}">
      <div class="ind-sell-headrow">
        <span class="ind-sell-head">It's built — list or dump?</span>
        <span class="ind-sell-subhead">the plan meets the market</span>
      </div>
      ${_buildDeciderHtml(b, stage)}
      <div class="ind-sell-hint">List it in EVE at your chosen price, or dump into buy orders — sales track themselves from your wallet, oldest batch first. Nothing to link.</div>
      <div class="ind-sell-foot">
        <button class="ind-sell-edit" title="Correct this build's run count if it didn't match reality. Re-scales produced units; sold units are untouched.">Edit runs</button>
        <button class="ind-sell-stop" title="Stop tracking this build. Anything already sold stays in your stats (frozen); the unsold units are left untracked and flagged. Reversible.">Stop tracking ▸</button>
        <button class="ind-sell-delete" title="Delete this build — its share of the tracked realized profit is removed from your stats. Can't be undone.">Delete</button>
      </div>
    </div>`;
  }

  if(stage==="stopped"){
    // A dead, frozen build the user stopped following with stock still unsold.
    // What sold keeps its real profit; the held remainder was orphaned onto the
    // market untracked (surfaced by the per-product badge elsewhere). Only action
    // is to resume tracking — which hands its lots back to reconcile live.
    const rz=_buildRealized(b);
    const orphan=b.stopped_held||0;
    return `<div class="ind-sell ind-sell-stopped-panel" data-id="${b.id}">
      <div class="ind-done-hero">
        <div class="ind-done-lbl">Stopped tracking · realized</div>
        <div class="ind-done-val ${pn(rz.profit)}">${_signIsk(rz.profit)}</div>
        <div class="ind-done-sub">${rz.units.toLocaleString()} sold${orphan>0?` · ${orphan.toLocaleString()} left on the market, untracked`:""}</div>
      </div>
      <div class="ind-sell-foot">
        <button class="ind-sell-resume" title="Resume tracking this build — reconcile picks its lots back up and later sales of this item can accrue to it again.">Resume tracking</button>
        <button class="ind-sell-delete" title="Delete this build — its share of the tracked realized profit is removed from your stats. Can't be undone.">Delete</button>
      </div>
    </div>`;
  }

  if(stage==="listed"||stage==="sold"){
    const rz=_buildRealized(b);
    const target=_buildUnits(b)||0;
    const cpu=b.cost_per_unit;
    const remain=Math.max(0, target-rz.units);
    const closed=stage==="sold";
    const closedEarly=closed&&b.abandoned;

    if(closed){
      // Game over — compare the plan against reality. The hero is the real
      // realized profit; beside it, what we predicted when the build was tracked
      // (frozen list estimate) and the delta, plus a what-if: had you dumped the
      // whole lot into buy orders at the frozen bid instead.
      const predicted=econ.profitL;               // the plan: patient list
      const actual=rz.profit;
      const delta=(predicted!=null&&actual!=null)?actual-predicted:null;
      const whatIfInstant=econ.profitI;            // if you'd dumped at the frozen bid
      const costSub=closedEarly&&rz.writeoff>0
        ? `net ${isk(rz.net)} − sold cost ${isk(rz.cost)} − write-off ${isk(rz.writeoff)}`
        : `net ${isk(rz.net)} − cost ${isk(rz.cost)}`;
      // The verdict against the plan: did waiting-and-listing beat dumping? Compare
      // what actually landed to the frozen dump-now counterfactual — "patience paid
      // off" only if the real sale cleared what an instant dump would have.
      const patience=(actual!=null&&whatIfInstant!=null)?actual-whatIfInstant:null;
      const beatPlan=delta!=null&&delta>=0;
      const paid=patience!=null&&patience>=0;
      const verdictCls=delta==null?"":(beatPlan?"pos":"neg");
      const verdict=delta==null?"How did it do?"
        :(beatPlan?"✓ Beat the plan":"Missed the plan");
      return `<div class="ind-sell ind-sell-done-panel" data-id="${b.id}">
        <div class="ind-done-verdict ${verdictCls}">${verdict}${delta!=null?` by <b>${isk(Math.abs(delta))}</b>`:""}</div>
        <div class="ind-done-hero">
          <div class="ind-done-lbl">Real profit${closedEarly?" (closed early)":""}</div>
          <div class="ind-done-val ${pn(actual)}">${_signIsk(actual)}</div>
          <div class="ind-done-sub">${rz.units.toLocaleString()} / ${target.toLocaleString()} sold · ${costSub}</div>
        </div>
        <div class="ind-done-scenarios">
          <div class="ind-done-scn plan">
            <span class="ind-done-scn-lbl">You predicted</span>
            <span class="ind-done-scn-v">${_signIsk(predicted)}</span>
            <span class="ind-done-scn-sub">the patient-list plan</span>
          </div>
          <div class="ind-done-scn actual">
            <span class="ind-done-scn-lbl">What happened</span>
            <span class="ind-done-scn-v ${pn(actual)}">${_signIsk(actual)}</span>
            <span class="ind-done-scn-sub ${pn(delta)}" title="How the real sale landed against the list profit you projected when tracking this build">${delta==null?"—":(delta>=0?"▲ beat plan by ":"▼ missed plan by ")+isk(Math.abs(delta))}</span>
          </div>
        </div>
        <div class="ind-done-compare">
          <div class="ind-done-row whatif">
            <span class="ind-done-k">If you'd dumped at the frozen bid instead</span>
            <span class="ind-done-v ${pn(whatIfInstant)}" title="Profit if you'd instant-sold the whole batch into buy orders at the bid frozen when tracked, instead of listing">${_signIsk(whatIfInstant)}</span>
          </div>
          ${patience!=null?`<div class="ind-done-row patience">
            <span class="ind-done-k">${paid?"Patience paid off":"Patience cost you"}</span>
            <span class="ind-done-v ${paid?"pos":"neg"}" title="What listing-and-waiting earned over dumping the whole lot at the frozen bid">${paid?"+":"−"}${isk(Math.abs(patience))}</span>
          </div>`:""}
        </div>
        <div class="ind-sell-foot">
          <span class="ind-sell-done">${closedEarly?`✓ Closed early · ${rz.units.toLocaleString()} of ${target.toLocaleString()} sold`:`✓ Fully sold`}</span>
          ${closedEarly?`<button class="ind-sell-abandon" data-undo="1" title="Undo the write-off: restore the unsold remainder as held stock.">Undo abandon</button>`:""}
          <button class="ind-sell-archive" title="${b.archived?"Move this build back into the active tracker":"Hide this finished build in the collapsed Archived section. It still counts in your portfolio stats."}">${b.archived?"Unarchive":"Archive"}</button>
          <button class="ind-sell-delete" title="Delete this build — its share of the tracked realized profit is removed from your stats. Can't be undone.">Delete</button>
        </div>
      </div>`;
    }

    // Listed: on the market, selling. The job here is fine-tuning the price to
    // move the remainder — so the decider leads, with a compact sold-so-far line
    // above it. Full remainder projection stays in Full detail.
    const watchMsg=rz.units>0
      ? `${rz.units.toLocaleString()} of ${target.toLocaleString()} sold — sales accrue from your wallet automatically${remain>0?` · ${remain.toLocaleString()} left`:""}.`
      : `On the market — sales will accrue here from your wallet as units sell.`;
    return `<div class="ind-sell ind-sell-live" data-id="${b.id}">
      <div class="ind-listed-progress">
        <div class="ind-listed-bar"><i class="${pn(rz.profit)==="neg"?"neg":""}" style="width:${target>0?Math.min(100,rz.units/target*100).toFixed(1):0}%"></i></div>
        <div class="ind-listed-line"><span>${watchMsg}</span>
          <b class="${pn(rz.profit)}">${_signIsk(rz.profit)} <small>realized</small></b></div>
      </div>
      <div class="ind-sell-headrow">
        <span class="ind-sell-head">Keep waiting, or re-price?</span>
        <span class="ind-sell-subhead">${remain.toLocaleString()} unit${remain===1?"":"s"} still to move</span>
      </div>
      ${_buildDeciderHtml(b, stage)}
      <div class="ind-sell-foot">
        ${remain>0?`<button class="ind-sell-abandon" title="Give up on the ${remain.toLocaleString()} unsold unit(s): write off their frozen cost as a loss so capital-in-flight clears and later sales of this item flow to your next batch. Reversible.">Abandon remainder ▸</button>`:""}
        <button class="ind-sell-edit" title="Correct this build's run count if it didn't match reality. Re-scales produced units; sold units are untouched.">Edit runs</button>
        <button class="ind-sell-stop" title="Stop tracking this build. What's already sold stays in your stats (frozen); the ${remain.toLocaleString()} unsold unit(s) are left on the market untracked and flagged. Reversible.">Stop tracking ▸</button>
        <button class="ind-sell-delete" title="Delete this build — its share of the tracked realized profit is removed from your stats. Can't be undone.">Delete</button>
      </div>
    </div>`;
  }
  return "";
}

// A signed ISK string: "+1.2M" / "−0.4M" / "—", keeping the sign explicit so a
// profit/loss reads at a glance (fmtISK alone drops the leading + on gains).
function _signIsk(v){
  if(v==null) return "—";
  return (v>0?"+":v<0?"−":"")+fmtISK(Math.abs(v));
}

// ── Inline price decider ──────────────────────────────────────────────────────
// The pricing decision, lifted OUT of the peek modal and docked straight in the
// Built/Listed panel so "what do I sell this at?" is answered where you're
// looking — no modal hop. It reuses the modal's pure market math (owner fees,
// price-conditioned demand rate, sell-through survival, the break-even rail) but
// draws its own compact skeleton: a predicted→now drift line, a break-even-aware
// slider with snap chips, a one-line sell-through read for the chosen price, and
// a copy-to-the-cent button. Two async fetches fill it: /api/ind/detail for the
// live best ask/bid, /api/ind/sell-analysis for the order book + history. Their
// results are cached on IND.decider[id] so a board re-render restores the panel
// without refetching (and without losing a price the user has dialled in). The
// full 90-day chart still lives in the modal — one "See the full market" link
// opens it for anyone who wants to go deeper.
function _deciderState(b){
  let st=IND.decider[b.id];
  if(!st){ st={live:null, liveState:"idle", market:null, marketState:"idle", price:null};
    IND.decider[b.id]=st; }
  return st;
}
// The reachable instant-sell bid + fillable units for dumping `qty` into buy orders
// RIGHT NOW, honouring each order's min_volume. A buyer demanding more units than
// you have (e.g. 60k min vs a 4.2k batch) can't be filled, so its bid must not set
// the dump price — walkBook skips it. Prefers the live buy book; when none shipped
// (old state / fetch failed) falls back to the raw top bid (live, else frozen).
//   bid     — proceeds-weighted reachable bid (null = no order can take the batch)
//   fillQty — units the reachable buy orders actually absorb (≤ qty)
function _dumpQuote(st, frozenBid, qty){
  const book=(st&&st.live&&st.live.buy_book)?st.live.buy_book:null;
  const bw=(book&&book.length&&typeof walkBook==="function")?walkBook(book,qty):null;
  if(bw) return {bid:bw.filled>0?bw.avg:null, fillQty:bw.filled};
  if(book) return {bid:null, fillQty:0};   // book shipped but empty → nobody buying
  const raw=(st&&st.live&&st.live.bid!=null)?st.live.bid:frozenBid;
  return {bid:raw!=null?raw:null, fillQty:qty};
}
// Everything the decider math needs, resolved once per render: owner fees (live
// skills, falling back to the snapshot), per-unit cost basis, break-even, and how
// many units the slider prices (the unsold remainder, or the whole lot pre-sale).
function _deciderCtx(b){
  const s=b.snapshot||{}, n=Math.max(1, b.runs||1);
  const fees=(typeof _peekOwnerFees==="function")?_peekOwnerFees(b)
    :{stax:s.sales_tax||0, bfee:s.broker_fee||0, live:false, who:null};
  const be=_buildBreakEven(b);
  const cpu=(b.cost_per_unit!=null)?b.cost_per_unit:_buildCostPerUnit(b);
  const rz=_buildRealized(b);
  const target=_buildUnits(b)||0;
  const remaining=Math.max(1, (rz.units>0?target-rz.units:target)||1);
  return {s, n, fees, be, cpu, rz, target, remaining,
          proposed:_buildProposedPrice(b)};
}
function _buildDeciderHtml(b, stage){
  return `<div class="ind-decider" data-id="${b.id}" data-stage="${stage}">
    <div class="ind-dec-drift" data-role="drift"></div>
    <div class="ind-dec-body" data-role="body">
      <div class="ind-dec-loading">Fetching the current market…</div>
    </div>
    <button class="ind-dec-full ind-sell-analyze" title="Open the full market view: 90-day price trend chart + the odds table across every listing duration">See the full market ▸</button>
  </div>`;
}
// Wire one decider: paint whatever's already cached, then kick the fetches that
// aren't in flight yet. Guarded so a re-render mid-fetch doesn't double-request.
function _wireBuildDecider(card, b){
  const root=card.querySelector(`.ind-decider[data-id="${CSS.escape(b.id)}"]`);
  if(!root) return;
  const st=_deciderState(b);
  // The "full market" link reuses the tested modal (opens on its Market tab).
  const full=root.querySelector(".ind-dec-full");
  if(full) full.onclick=()=>{
    if(typeof openBuildPeek==="function") openBuildPeek(b.id, "market");
    else if(typeof openTrackedBuild==="function") openTrackedBuild(b.id);
  };
  // Slider + chip interaction (delegated, attached once per render).
  root.addEventListener("input", e=>{
    if(e.target && e.target.classList.contains("ind-dec-slider"))
      _updateBuildDecider(b, +e.target.value);
  });
  root.addEventListener("click", e=>{
    const chip=e.target.closest && e.target.closest(".bp-chip");
    if(chip){ e.preventDefault(); _updateBuildDecider(b, +chip.dataset.price); }
    const copy=e.target.closest && e.target.closest(".ind-dec-copy");
    if(copy){ e.preventDefault(); _deciderCopy(b, copy); }
    // Instant-route copy grabs the REACHABLE bid (what a buyer who can take the
    // batch actually pays, honouring min_volume), not the slider or an unfillable
    // top bid.
    const copyInst=e.target.closest && e.target.closest(".ind-dec-copy-inst");
    if(copyInst){ e.preventDefault();
      const st2=_deciderState(b), ctx2=_deciderCtx(b);
      _deciderCopyValue(_dumpQuote(st2, ctx2.s.bid, ctx2.remaining).bid, copyInst); }
  });
  _renderDeciderDrift(b);
  _renderDeciderBody(b);
  if(st.liveState==="idle"){ st.liveState="loading"; _fetchDeciderLive(b); }
  if(st.marketState==="idle"){ st.marketState="loading"; _fetchDeciderMarket(b); }
}
// Live best ask/bid — same endpoint + params the modal uses, replaying the frozen
// job rate/taxes so only market price moves. Cached; stale responses dropped when
// the build has left the board.
function _fetchDeciderLive(b){
  const s=b.snapshot||{};
  const p=new URLSearchParams({
    blueprint_id:String(s.blueprint_id||""), station:String(s.station_id||""),
    job_rate:String(((s.job_rate||0)*100)), sales_tax:String(((s.sales_tax||0)*100)),
    broker:String(((s.broker_fee||0)*100)), runs:"1", refresh_prices:"1"});
  fetch("/api/ind/detail?"+p).then(r=>r.json()).then(fresh=>{
    const st=IND.decider[b.id]; if(!st) return;
    // buy_book comes along so "Dump now" can honour each buy order's min_volume:
    // a 60k-min buyer can't take a 4.2k batch, so its bid mustn't set the dump
    // price/profit. The decider gates against it in _updateBuildDecider.
    st.live=(fresh&&!fresh.error)?{ask:fresh.ask, bid:fresh.bid, buy_book:fresh.buy_book}:null;
    st.liveState="done";
    _renderDeciderDrift(b); _renderDeciderBody(b); _renderBuildWatch(b); _renderTileFlag(b);
  }).catch(()=>{ const st=IND.decider[b.id]; if(!st) return;
    st.live=null; st.liveState="error"; _renderDeciderDrift(b); _renderDeciderBody(b); _renderBuildWatch(b); });
}
// Order book + recent history for the sell-through odds. Cached; the slider then
// recomputes the odds locally (price-conditioned) with no refetch.
function _fetchDeciderMarket(b){
  const s=b.snapshot||{}, ctx=_deciderCtx(b);
  const price=ctx.proposed;
  const p=new URLSearchParams({type_id:String(b.product_type_id||""),
    station:String(s.station_id||""), qty:String(ctx.remaining||1)});
  if(price!=null) p.set("price", String(price));
  fetch("/api/ind/sell-analysis?"+p).then(r=>r.json()).then(m=>{
    const st=IND.decider[b.id]; if(!st) return;
    st.market=(m&&!m.error)?m:null;
    st.marketState=(m&&!m.error)?"done":"error";
    _renderDeciderBody(b); _renderTileFlag(b);
  }).catch(()=>{ const st=IND.decider[b.id]; if(!st) return;
    st.market=null; st.marketState="error"; _renderDeciderBody(b); });
}
// The predicted→now line: the list price you froze when tracking vs. the live
// best ask, with the drift %. This is the "market moved under me" signal the
// Built/Listed user most wants before committing to a price.
function _renderDeciderDrift(b){
  const root=document.querySelector(`.ind-decider[data-id="${CSS.escape(b.id)}"]`);
  if(!root) return;
  const slot=root.querySelector('[data-role="drift"]'); if(!slot) return;
  const st=_deciderState(b), isk=v=>v==null?"—":fmtISKFull(v);
  const stage=root.dataset.stage||"";
  const frozen=(b.snapshot||{}).ask;
  const now=st.live?st.live.ask:null;
  let deltaHtml="", verdict="";
  if(st.liveState==="loading") deltaHtml=`<span class="ind-dec-drift-load">checking market…</span>`;
  else if(now!=null){
    const diff=(frozen!=null)?now-frozen:null;
    const cls=diff>0?"pos":(diff<0?"neg":"");
    const arrow=diff>0?"▲":(diff<0?"▼":"");
    const pctN=(frozen)?Math.abs(diff/frozen*100):null;
    const pct=(pctN!=null)?` ${pctN.toFixed(1)}%`:"";
    deltaHtml=`<span class="ind-dec-now">now <b>${isk(now)}</b></span>`
      +(diff!=null&&diff!==0?` <span class="ind-dec-delta ${cls}">${arrow}${pct}</span>`:"");
    // The plain-language surprise — a rising ask is good news for a seller (you
    // can list higher than you planned), a falling ask bad. Only spoken when the
    // move is worth noticing (>1%); at Built this is the plan-meets-reality line.
    if(diff!=null && pctN!=null && pctN>=1){
      const up=diff>0;
      const framed=(stage==="built")
        ? (up?`You planned to list at ${isk(frozen)} — the market now bears more. A pleasant surprise.`
             :`You planned to list at ${isk(frozen)} — the market softened since. Mind the routes below.`)
        : (up?`The market's climbed above your frozen plan — room to ask more.`
             :`The market's slipped below your frozen plan — your price may be optimistic.`);
      verdict=`<div class="ind-dec-drift-verdict ${up?"pos":"neg"}">${up?"↑":"↓"} ${framed}</div>`;
    }
  }
  slot.innerHTML=`<div class="ind-dec-drift-line">`
    +`<span class="ind-dec-drift-k">Planned ask <b>${isk(frozen)}</b></span>`
    +`<span class="ind-dec-arrow">→</span>${deltaHtml}</div>${verdict}`;
}
// Building-stage market watch: the delta accrues during the build's dead time, so
// this surfaces "has the market moved under my frozen plan?" — the same frozen-ask
// → live-ask drift the decider draws, plus what that move does to the projected
// list profit. It reuses the decider's cached live quote (no extra fetch path) so
// a board re-render restores it, and it gives the waiting user a reason to check
// without pretending they can list yet. Wired by _wireBuildWatch.
function _renderBuildWatch(b){
  const slot=document.querySelector(`.ind-watch[data-id="${CSS.escape(b.id)}"]`);
  if(!slot) return;
  const st=_deciderState(b), ctx=_deciderCtx(b), isk=v=>v==null?"—":fmtISKFull(v);
  const frozen=(b.snapshot||{}).ask;
  const now=st.live?st.live.ask:null;
  if(st.liveState==="loading"||st.liveState==="idle"){
    slot.innerHTML=`<div class="ind-watch-load">Checking how the market's moved since you started this build…</div>`;
    return;
  }
  if(now==null){
    slot.innerHTML=`<div class="ind-watch-load">Market read unavailable right now — nothing to act on yet anyway; it's still in production.</div>`;
    return;
  }
  const diff=(frozen!=null)?now-frozen:null;
  const pctN=(frozen)?Math.abs(diff/frozen*100):null;
  const up=diff>0;
  const cls=diff>0?"pos":(diff<0?"neg":"");
  const arrow=diff>0?"▲":(diff<0?"▼":"");
  // What the move does to the projected list profit — re-price the whole lot at
  // the live ask (frozen fees + cost) vs. the frozen plan, so drift reads in ISK.
  const {stax, bfee}=ctx.fees, cpu=ctx.cpu, qty=ctx.target||ctx.remaining;
  const planProfit=(frozen!=null&&cpu!=null)?(frozen*(1-stax-bfee)-cpu)*qty:null;
  const nowProfit=(cpu!=null)?(now*(1-stax-bfee)-cpu)*qty:null;
  const pdiff=(planProfit!=null&&nowProfit!=null)?nowProfit-planProfit:null;
  const still=(diff==null||pctN==null||pctN<1);
  const head=still
    ? `Market's holding near your plan`
    : (up?`Market's up since you started` : `Market's down since you started`);
  slot.innerHTML=`
    <div class="ind-watch-head ${still?"":cls}">${still?"◆":arrow} ${head}</div>
    <div class="ind-watch-drift">
      <span class="ind-watch-k">Planned ask <b>${isk(frozen)}</b></span>
      <span class="ind-dec-arrow">→</span>
      <span class="ind-watch-now">now <b>${isk(now)}</b></span>
      ${diff!=null&&diff!==0?`<span class="ind-dec-delta ${cls}">${arrow} ${pctN.toFixed(1)}%</span>`:""}
    </div>
    ${pdiff!=null&&!still?`<div class="ind-watch-note">At today's ask the lot would clear <b class="${pdiff>=0?"pos":"neg"}">${pdiff>=0?"+":"−"}${isk(Math.abs(pdiff))}</b> ${pdiff>=0?"more":"less"} than planned — nothing to do yet, but worth knowing when it lands.</div>`
      :`<div class="ind-watch-note">Still in production — nothing to act on. You'll set the real price once it's built.</div>`}`;
}
// Wire the building-stage watch: kick the shared live-quote fetch (cached on the
// decider state) if it hasn't run, then paint whatever's cached.
function _wireBuildWatch(card, b){
  const slot=card.querySelector(`.ind-watch[data-id="${CSS.escape(b.id)}"]`);
  if(!slot) return;
  const st=_deciderState(b);
  _renderBuildWatch(b);
  if(st.liveState==="idle"){ st.liveState="loading"; _fetchDeciderLive(b); }
}
// The slider body — built once the live quote lands so its window can bracket the
// live best ask. Reuses the modal's rail tint (red below break-even, green above)
// and snap chips. When live is unavailable it degrades to a static advice line.
function _renderDeciderBody(b){
  const root=document.querySelector(`.ind-decider[data-id="${CSS.escape(b.id)}"]`);
  if(!root) return;
  const slot=root.querySelector('[data-role="body"]'); if(!slot) return;
  const st=_deciderState(b), ctx=_deciderCtx(b), isk=v=>v==null?"—":fmtISKFull(v);
  if(st.liveState==="loading"){
    slot.innerHTML=`<div class="ind-dec-loading">Fetching the current market…</div>`; return;
  }
  const be=ctx.be.list, frozen=ctx.s.ask;
  const bestAsk=st.live?st.live.ask:null;
  const undercut=bestAsk!=null?bestAsk*0.9999:null;
  const refs=[be,frozen,bestAsk,undercut].filter(v=>v!=null);
  if(!refs.length){
    slot.innerHTML=`<div class="ind-dec-loading">Live market unavailable — can't suggest a price right now.</div>`;
    return;
  }
  const lo=Math.min(...refs)*0.9, hi=Math.max(...refs)*1.1;
  // Keep a price the user already dialled in across a re-render; else start at the
  // undercut-best-ask (the "climb the queue" default), falling back to frozen/BE.
  if(st.price==null || st.price<lo || st.price>hi)
    st.price=(undercut!=null)?undercut:(bestAsk!=null?bestAsk:(frozen!=null?frozen:be));
  const step=Math.max(0.01, (hi-lo)/1000);
  const railStyle=(be!=null && typeof _peekRailStyle==="function")?` style="${_peekRailStyle(lo,hi,be)}"`:"";
  const chip=(label,val)=> val==null?"" :
    `<button class="bp-chip" data-price="${val}" title="Set price to ${isk(val)}">${label}<b>${isk(val)}</b></button>`;
  slot.innerHTML=`
    <div class="ind-dec-price"><span class="ind-dec-price-v" data-role="price">${isk(st.price)}</span>
      <span class="ind-dec-price-u">/ unit</span>
      <button class="ind-dec-copy" title="Copy this price to the cent, ready to paste into EVE's sell order">⧉ Copy</button></div>
    <input class="ind-dec-slider bp-sim-slider" type="range" min="${lo}" max="${hi}" step="${step}" value="${st.price}"${railStyle}>
    <div class="bp-sim-chips">
      ${chip("Undercut ",undercut)}
      ${chip("Best ask ",bestAsk)}
      ${chip("Break-even ",be)}
      ${chip("Predicted ",frozen)}
    </div>
    <div class="ind-dec-out" data-role="out"></div>`;
  _updateBuildDecider(b, st.price);
}
// Recompute the read-out for a chosen price. The lead answer is "will it sell,
// and for what?" — the odds the whole remainder clears within a day / week at
// this price (plus an ETA) and the profit it books. Break-even is NOT a headline
// here: it only surfaces as a quiet ⚠ warning when the chosen price is actually
// underwater, so the eye stays on price-vs-demand, not on a margin readout.
function _updateBuildDecider(b, price){
  const root=document.querySelector(`.ind-decider[data-id="${CSS.escape(b.id)}"]`);
  if(!root) return;
  const st=_deciderState(b), ctx=_deciderCtx(b), isk=v=>v==null?"—":fmtISKFull(v);
  const stage=root.dataset.stage||"";
  st.price=price;
  const priceEl=root.querySelector('[data-role="price"]'); if(priceEl) priceEl.textContent=isk(price);
  const slider=root.querySelector(".ind-dec-slider"); if(slider && +slider.value!==price) slider.value=price;
  const out=root.querySelector('[data-role="out"]'); if(!out) return;

  const {stax, bfee}=ctx.fees, cpu=ctx.cpu, qty=ctx.remaining;
  const pn=v=>v==null?"":(v>=0?"pos":"neg");

  // LIST route — sell at the chosen price; pays sales tax + a fresh broker fee.
  const listNetUnit=price*(1-stax-bfee);
  const listProfit=(cpu!=null)?(listNetUnit-cpu)*qty:null;
  // INSTANT route — dump the lot into buy orders; pays sales tax only (no broker
  // on an immediate sell). _dumpQuote walks the live buy book for the `qty` unsold
  // units honouring each order's min_volume, so a buyer wanting more than the batch
  // (60k min vs 4.2k) can't set the price. fillQty is what actually fits.
  const dq=_dumpQuote(st, ctx.s.bid, qty), bid=dq.bid, fillQty=dq.fillQty;
  const buyBook=(st.live&&st.live.buy_book)?st.live.buy_book:null;   // for the note below
  const instNetUnit=(bid!=null)?bid*(1-stax):null;
  const instProfit=(instNetUnit!=null&&cpu!=null)?(instNetUnit-cpu)*fillQty:null;
  // How much patience buys you — the extra ISK the list route earns over dumping.
  const gain=(listProfit!=null&&instProfit!=null)?listProfit-instProfit:null;

  // Break-even is a quiet flag only: shown when the chosen list price is under it.
  const underBE=(ctx.be.list!=null)?ctx.be.list-price:null;
  const beLine=(underBE!=null && underBE>0)
    ? `<span class="ind-dec-be bad">⚠ Below break-even (${isk(ctx.be.list)}) — you'd lose money</span>`
    : "";

  // Sell-through odds + the raw market signals behind them (queue depth, the
  // price-conditioned demand rate, and the UNconditioned rate — the market's
  // full pace ignoring price). Kept around the whole block so the Listed-stage
  // decision support (queue line, slow-vs-overpriced diagnosis, recommendation)
  // can reason from the same numbers the odds line reports.
  let oddsLine=`<span class="ind-dec-odds-load">estimating…</span>`;
  let ahead=null, rate=null, baseRate=null, dayAll=null, weekAll=null, eta=null, haveOdds=false;
  if(st.marketState==="done" && st.market && st.market.series && st.market.series.length
     && typeof _priceConditionedDailyRate==="function"){
    const m=st.market;
    ahead=_unitsAheadInQueue(m.sell_book, price);
    rate=_priceConditionedDailyRate(m.series, price);
    baseRate=_priceConditionedDailyRate(m.series, null);   // full pace, price aside
    if(rate!=null){
      haveOdds=true;
      const day=_sellThroughProb(ahead, rate, qty, 1);
      weekAll=_sellThroughProb(ahead, rate, qty, 7).all;
      dayAll=day.all;
      const pct=v=>v==null?"—":(v*100).toFixed(0)+"%";
      const cls=v=>v>=0.66?"good":(v>=0.33?"warn":"bad");
      eta=day.eta;
      const etaTxt=(eta==null||!isFinite(eta))?"—":(eta<1?`~${Math.round(eta*24)}h`:(eta<60?`~${eta.toFixed(eta<10?1:0)}d`:"months+"));
      oddsLine=`<b class="${cls(dayAll)}">${pct(dayAll)}</b> in a day · <b class="${cls(weekAll)}">${pct(weekAll)}</b> in a week · clears ${etaTxt}`;
    } else oddsLine=`<span class="ind-dec-odds-load">not enough history for odds</span>`;
  } else if(st.marketState==="error") oddsLine=`<span class="ind-dec-odds-load">odds unavailable</span>`;

  // Two exit routes side by side, each with its own profit, so "more ISK later
  // vs. cash now" is a direct comparison. List carries the odds; instant carries
  // its live bid + a one-click copy (the slider only drives the list price).
  const routes=`
    <div class="ind-dec-routes">
      <div class="ind-dec-route list">
        <div class="ind-dec-route-top"><span class="ind-dec-route-lbl">List &amp; wait</span>
          <span class="ind-dec-route-p">${isk(price)}/u</span></div>
        <div class="ind-dec-route-profit ${pn(listProfit)}">${_signIsk(listProfit)}</div>
        <div class="ind-dec-route-note">${oddsLine}</div>
      </div>
      <div class="ind-dec-route instant">
        <div class="ind-dec-route-top"><span class="ind-dec-route-lbl">Dump now</span>
          <span class="ind-dec-route-p">${bid!=null?isk(bid)+"/u":"no bid"}</span>
          ${bid!=null?`<button class="ind-dec-copy-inst" title="Copy the instant-sell price (the live buy-order bid) to paste into EVE">⧉</button>`:""}</div>
        <div class="ind-dec-route-profit ${pn(instProfit)}">${_signIsk(instProfit)}</div>
        <div class="ind-dec-route-note">${bid==null?(buyBook?`no buy order will take ${qty.toLocaleString()} units (all demand a larger minimum)`:"nobody's buying right now"):fillQty<qty?`only ${fillQty.toLocaleString()} of ${qty.toLocaleString()} fit buy orders — rest unsold`:"into buy orders, immediate"}</div>
      </div>
    </div>`;

  // ── Listed-stage waiting support ─────────────────────────────────────────
  // "Should I keep waiting, or is my price wrong?" gets its own block: how many
  // units sit ahead in the queue at/below this price (the hidden reason nothing
  // sells), a slow-vs-overpriced diagnosis, and a hold / re-price / dump call.
  let waitBlock="";
  let rec=null, recCls=null;
  if(stage==="listed" && haveOdds){
    // The waiting support answers "how's MY listing doing?" — so it reasons about
    // the price you're ACTUALLY listed at (your live sell order), NOT the slider's
    // exploratory price. The slider is a what-if for re-pricing; using it here made
    // the panel claim "you're at the front" (true at the undercut default) while
    // your real order sat at #6. Fall back to the slider price only when no live
    // order is found (not yet listed / order cache cold).
    const listedPrice=_buildListedOrderPrice(b);
    const curPrice=(listedPrice!=null)?listedPrice:price;
    const haveReal=listedPrice!=null;
    // Queue depth + demand recomputed AT YOUR LISTED PRICE (ahead/rate above were
    // at the slider price, for the odds read; these are your true standing).
    const curAhead=_unitsAheadInQueue(st.market.sell_book, curPrice);
    const curRate=_priceConditionedDailyRate(st.market.series, curPrice);
    const curWeekAll=(curRate!=null)?_sellThroughProb(curAhead, curRate, qty, 7).all:weekAll;
    // Queue depth — units listed at or under YOUR price that clear before yours.
    const behind=(curAhead!=null)?Math.round(curAhead):null;
    const atSub=haveReal?` (listed at ${isk(curPrice)})`:"";
    const queueLine=(behind!=null)
      ? (behind<=0
          ? `<span class="ind-wait-queue-v good">You're at the front</span> — nothing's listed below your price${atSub}.`
          : `<span class="ind-wait-queue-v ${behind>=qty*4?"bad":"warn"}">Behind ${behind.toLocaleString()} unit${behind===1?"":"s"}</span> at or under your price${atSub} — those clear before yours.`)
      : "";
    // Slow-vs-overpriced: if the market trades briskly overall (baseRate) but
    // barely at YOUR price (curRate), you're priced above market; if it's slow at
    // ANY price, it's just a quiet market. This is the honest read that replaces
    // an invented weekday signal (history carries no dates).
    let diag="";
    if(baseRate!=null && baseRate>0 && curRate!=null){
      const share=curRate/baseRate;                 // how much of the pace your price captures
      if(baseRate<qty/14){                          // <~half the lot a week even wide open
        diag=`<span class="ind-wait-diag slow">Quiet market — it trades slowly at any price. Waiting is about patience, not your price.</span>`;
      } else if(share<0.5){
        diag=`<span class="ind-wait-diag over">The market's active, but little of it trades at your price — you're likely <b>priced above market</b>. Undercut to join the flow.</span>`;
      } else {
        diag=`<span class="ind-wait-diag fair">Your price is in the market's flow — it's competing. Mostly a matter of waiting your turn in the queue.</span>`;
      }
    }
    // The call — reasons about YOUR listed price too. list/instant profit at the
    // real price so "dump beats waiting" compares against what you're actually
    // asking, not the slider. Factored into _callVerdict so the board tile agrees.
    const curListProfit=(cpu!=null)?(curPrice*(1-stax-bfee)-cpu)*qty:null;
    const curGain=(curListProfit!=null&&instProfit!=null)?curListProfit-instProfit:null;
    const curUnderBE=(ctx.be.list!=null)?ctx.be.list-curPrice:null;
    // Fee-aware re-price gate: undercutting burns a fresh broker fee and books less
    // per unit, so it must beat holding in EXPECTED value (odds × profit) over the
    // same 1-week horizon — a transient dip won't clear the bar, a stop-loss will.
    const bestAskNow=(st.live&&st.live.ask!=null)?st.live.ask:ctx.s.ask;
    const reprice=_repricePaysOff({curPrice, curOdds:curWeekAll, cpu, stax, bfee,
      bestAsk:bestAskNow, series:st.market.series, sell_book:st.market.sell_book,
      qty, horizon:7});
    const v=_callVerdict({underBE:curUnderBE, instProfit, listProfit:curListProfit,
                          baseRate, rate:curRate, qty, weekAll:curWeekAll, gain:curGain,
                          repriceWorthIt:reprice.worth});
    rec=v.rec; recCls=v.recCls;
    waitBlock=`
      <div class="ind-wait">
        <div class="ind-wait-rec ${recCls}"><span class="ind-wait-rec-lbl">Call</span><b>${rec}</b></div>
        ${queueLine?`<div class="ind-wait-queue">${queueLine}</div>`:""}
        ${diag?`<div class="ind-wait-diags">${diag}</div>`:""}
      </div>`;
  }

  out.innerHTML=routes
    +waitBlock
    +(gain!=null&&gain>0?`<div class="ind-dec-gain">Listing earns <b>${isk(gain)}</b> more than dumping — if it sells.</div>`:"")
    +(beLine?`<div class="ind-dec-belines">${beLine}</div>`:"");
}
// The price the Listed-stage queue position, diagnosis and Call must reason about:
// the user's ACTUAL open sell order for this build's product, not a hypothetical.
// Your standing in the queue ("you're #6" vs "at the front") is a fact about the
// price you're really listed at — the slider is only a what-if for re-pricing, so
// answering "keep waiting?" at the slider's default (undercut-best-ask) claimed you
// were at the front when your real 554 order was #6. Falls back to null when no
// live order is found (not yet listed / order cache cold) — callers then have no
// authoritative current price and must not assert a queue position.
function _buildListedOrderPrice(b){
  if(typeof _peekLinkedOrder!=="function") return null;
  const o=_peekLinkedOrder(b);
  return (o && o.price!=null) ? o.price : null;
}
// Does re-pricing actually PAY, once its cost is counted? Re-pricing is NOT free:
// relisting the remainder burns a fresh broker fee AND books less per unit (you
// undercut to a lower price). So the tilt to "re-price" must clear an expected-
// value bar, not just "you're priced above market":
//
//   E[re-price] = P(sells at the lower price) × profit-after-a-fresh-broker-fee
//   E[hold]     = P(sells at your current price) × profit-with-NO-new-fee
//
// Re-price only when E[re-price] > E[hold] by a margin. This encodes the "grano
// salis": a *transient* dip keeps hold-odds high, so eating the fee to chase a
// lower price loses — hold. A *permanent* shift (the stop-loss case) collapses the
// odds of ever selling at your current price, so E[hold] craters and re-pricing
// wins despite the fee. `ctx` carries {curPrice, curOdds, cpu, stax, bfee,
// bestAsk, series, sell_book, qty, horizon} — everything to price both sides; null
// when the caller lacks a real current price (then re-price never fires).
function _repricePaysOff(ctx){
  if(!ctx) return {worth:false, gain:null, target:null};
  const {curPrice, curOdds, cpu, stax, bfee, bestAsk, series, sell_book, qty, horizon}=ctx;
  if(curPrice==null || cpu==null || curOdds==null || bestAsk==null) return {worth:false, gain:null, target:null};
  // The re-price target: undercut the best competing ask to join the flow. Only a
  // move DOWN is a re-price; if you're already at/under the best ask you're already
  // competitive and nothing here should push you lower.
  const target=bestAsk*0.9999;
  if(!(target<curPrice)) return {worth:false, gain:null, target:null};
  const repRate=_priceConditionedDailyRate(series, target);
  if(repRate==null) return {worth:false, gain:null, target};
  const repOdds=_sellThroughProb(_unitsAheadInQueue(sell_book, target), repRate, qty, horizon).all;
  // Hold pays NO new broker fee (the fee's already sunk); re-pricing pays a fresh
  // one on the relisted remainder and clears at the lower target price.
  const holdNet=(curPrice*(1-stax)-cpu)*qty;
  const repNet =(target*(1-stax-bfee)-cpu)*qty;
  const holdEV=(curOdds!=null?curOdds:0)*holdNet;
  const repEV =(repOdds!=null?repOdds:0)*repNet;
  const gain=repEV-holdEV;
  // A margin so a wash never nudges you into paying a fee for nothing; require the
  // relisted lot to at least still book a profit (never "re-price into a loss").
  return {worth:(repNet>0 && gain>0), gain, target};
}
// The Listed-stage "Call" — the ONE recommendation the decider makes about a lot
// still on the market: dump / re-price / hold. Factored out (from the numbers the
// decider already has) so the board tile can flag the same verdict WITHOUT drawing
// the whole decider. Returns {rec, recCls, action}: `action` is the two act-now
// verdicts only — "dump" or "reprice" — and null for either hold, so a caller can
// cheaply ask "does this need me?" A slow-going hold is still a hold: no action.
// `repriceWorthIt` gates the re-price branch on the fee-aware EV test above — the
// demand-share signal only says you're overpriced; whether ACTING on it pays (once
// the fresh broker fee + lower price are counted) is what actually decides it.
function _callVerdict({underBE, instProfit, listProfit, baseRate, rate, qty, weekAll, gain, repriceWorthIt}){
  let rec, recCls, action=null;
  const overpriced=(baseRate!=null && baseRate>=qty/14 && rate!=null && baseRate>0 && rate/baseRate<0.5);
  if(underBE!=null && underBE>0 && instProfit!=null && instProfit>=listProfit){
    rec="Dump the remainder"; recCls="bad"; action="dump";
  } else if(overpriced && repriceWorthIt){
    rec="Re-price to move it"; recCls="warn"; action="reprice";
  } else if(overpriced){
    // Priced above the market, but undercutting wouldn't recover its own cost — a
    // fresh broker fee + the lower price eat the gain. Sit tight rather than pay to
    // chase a dip that may lift.
    rec="Hold — re-pricing won't pay"; recCls="warn";
  } else if(weekAll!=null && weekAll<0.33 && gain!=null && gain>0){
    rec="Hold — but slow going"; recCls="warn";
  } else {
    rec="Hold — waiting pays"; recCls="good";
  }
  return {rec, recCls, action};
}
// The board tile's action flag: reach the same Call the decider would, from the
// cached live quote + sell-analysis (IND.decider[id]) if the build has been opened
// or its market prefetched. Returns null when we don't yet have the market data to
// decide (the tile then shows no flag — better silent than wrong). Only the two
// act-now verdicts surface; a hold returns null so the flag means "do something".
function _tileActionFlag(b){
  const st=IND.decider[b.id];
  if(!st || st.marketState!=="done" || !st.market || !st.market.series || !st.market.series.length) return null;
  if(typeof _priceConditionedDailyRate!=="function" || typeof _unitsAheadInQueue!=="function"
     || typeof _sellThroughProb!=="function") return null;
  const ctx=_deciderCtx(b);
  const {stax, bfee}=ctx.fees, cpu=ctx.cpu, qty=ctx.remaining;
  // Reason about YOUR ACTUAL listed price — the flag is a statement about your
  // current standing, so it must use the price you're really listed at (matching
  // the decider's waiting support). Fall back to the undercut-best-ask default
  // only when no live order is found (order cache cold / not yet listed).
  const bestAsk=(st.live&&st.live.ask!=null)?st.live.ask:null;
  const frozen=ctx.s.ask;
  const listedPrice=_buildListedOrderPrice(b);
  const price=(listedPrice!=null)?listedPrice
             :(bestAsk!=null)?bestAsk*0.9999:(frozen!=null?frozen:ctx.be.list);
  if(price==null) return null;
  const listProfit=(cpu!=null)?(price*(1-stax-bfee)-cpu)*qty:null;
  // Dump profit honours min_volume (see _dumpQuote): the board flag must not say
  // "dump" on a bid from a buyer who can't take the batch.
  const dq=_dumpQuote(st, ctx.s.bid, qty), bid=dq.bid;
  const instProfit=(bid!=null&&cpu!=null)?(bid*(1-stax)-cpu)*dq.fillQty:null;
  const gain=(listProfit!=null&&instProfit!=null)?listProfit-instProfit:null;
  const underBE=(ctx.be.list!=null)?ctx.be.list-price:null;
  const m=st.market;
  const rate=_priceConditionedDailyRate(m.series, price);
  const baseRate=_priceConditionedDailyRate(m.series, null);
  if(rate==null) return null;
  const weekAll=_sellThroughProb(_unitsAheadInQueue(m.sell_book, price), rate, qty, 7).all;
  // Same fee-aware re-price gate the decider uses, so the board flag never says
  // "re-price" when undercutting wouldn't recover its own broker fee + lower price.
  const reprice=_repricePaysOff({curPrice:price, curOdds:weekAll, cpu, stax, bfee,
    bestAsk, series:m.series, sell_book:m.sell_book, qty, horizon:7});
  const v=_callVerdict({underBE, instProfit, listProfit, baseRate, rate, qty, weekAll,
                        gain, repriceWorthIt:reprice.worth});
  return v.action ? {action:v.action, tip:v.rec} : null;
}
// Copy the currently-dialled list price to the cent (Math.round to 2dp),
// matching the modal's copy behaviour so a listed order pastes straight in.
function _deciderCopy(b, btn){
  const st=_deciderState(b);
  _deciderCopyValue(st.price, btn);
}
// Copy any price value to the cent, with a transient "✓ Copied" on the button.
function _deciderCopyValue(price, btn){
  if(price==null) return;
  const txt=String(Math.round(price*100)/100);
  const done=()=>{ const o=btn.textContent; btn.textContent="✓"; setTimeout(()=>{btn.textContent=o;},1200); };
  if(navigator.clipboard&&navigator.clipboard.writeText)
    navigator.clipboard.writeText(txt).then(done).catch(()=>fallbackCopy(txt,done));
  else fallbackCopy(txt, done);
}

// The full frozen breakdown, mirroring the detail panel's materials + batch math
// but computed only from the snapshot (so prices never move under it).
function _buildDetailHtml(b){
  const d=b.snapshot||{}, n=Math.max(1, b.runs||1);
  const isk=v=>v===null||v===undefined?"—":fmtISK(v);
  const mvol=v=> v==null?"—":(v.toLocaleString(undefined,{maximumFractionDigits:v<10?2:1})+" m³");
  if(!d.required_items) return `<div class="ind-build-detail"><span class="ind-build-warn">Snapshot has no material breakdown.</span></div>`;
  // Job-level ME rounding for the batch shopping list (see shared.js/effectiveQty),
  // with a per-run × N fallback for snapshots saved before base_qty was recorded.
  let matTotCost=0, matTotVol=0, matHasVol=false;
  const me=d.me_used||0;
  const batchQty=m=>(m.base_qty!=null)?effectiveQty(m.base_qty, me, n):m.eff_qty*n;
  const sortedItems=[...d.required_items].sort((a,b)=>a.name.localeCompare(b.name));
  const mats=sortedItems.map(m=>{
    const qtyBatch=batchQty(m);
    const costBatch=m.unit_price==null?null:qtyBatch*m.unit_price;
    const volBatch=(m.volume_each!=null)?qtyBatch*m.volume_each:null;
    if(costBatch!=null) matTotCost+=costBatch;
    if(volBatch!=null){ matTotVol+=volBatch; matHasVol=true; }
    return `<tr><td>${m.name}</td><td class="num">${qtyBatch.toLocaleString()}</td>`
      +`<td class="num">${isk(m.unit_price)}</td><td class="num">${isk(costBatch)}</td>`
      +`<td class="num">${mvol(volBatch)}</td></tr>`;
  }).join("");
  const matTotal=`<tr class="ind-d-total"><td>Total — ${d.required_items.length} material${d.required_items.length===1?"":"s"}</td>`
    +`<td class="num"></td><td class="num"></td><td class="num">${isk(matTotCost)}</td>`
    +`<td class="num">${matHasVol?mvol(matTotVol):"—"}</td></tr>`;
  const pn=v=>v==null?"":(v>0?"pos":(v<0?"neg":""));
  const be=_batchEconomics(d, n);
  const batchCost=be.cost, batchProfitL=be.profitL, batchProfitI=be.profitI;
  const qtyTot=(d.product&&d.product.quantity!=null)?d.product.quantity*n:null;
  const sellL=(d.ask!=null&&qtyTot!=null)?d.ask*qtyTot:null;
  // Instant gross reachable for the batch: walk the frozen buy book honouring
  // min_volume (see walkBook), so revenue and the per-unit bid shown match the
  // batch-aware profit from _batchEconomics. Fall back to raw bid only w/o a book.
  const ibw=(d.buy_book&&d.buy_book.length&&qtyTot!=null)?walkBook(d.buy_book,qtyTot):null;
  const instBid=(ibw&&ibw.filled>0)?ibw.avg:(d.buy_book?null:d.bid);
  const sellI=(ibw&&ibw.filled>0)?ibw.cost:(d.buy_book?null:((d.bid!=null&&qtyTot!=null)?d.bid*qtyTot:null));
  const matCostBatch=(be.matCost!=null)?be.matCost:(d.material_cost!=null?d.material_cost*n:null);
  const jobCostBatch=(d.job_cost!=null)?d.job_cost*n:null;
  const inventCostBatch=d.invention&&d.invention_cost!=null?d.invention_cost*n:null;
  // Break-even sell price per unit — the price at which the sale exactly covers
  // total cost, so anything above it is profit and below it is a loss. Instant
  // sales pay sales tax only; list sales also pay the broker fee.
  const stax=(d.sales_tax!=null)?d.sales_tax:0;
  const bfee=(d.broker_fee!=null)?d.broker_fee:0;
  const beI=(batchCost!=null&&qtyTot>0&&(1-stax)>0)?batchCost/(qtyTot*(1-stax)):null;
  const beL=(batchCost!=null&&qtyTot>0&&(1-stax-bfee)>0)?batchCost/(qtyTot*(1-stax-bfee)):null;
  // ── Cost basis strip ──────────────────────────────────────────────────────
  // What you sank into the batch, read left→right to a total: materials (+ job,
  // + invention) = total cost. One quiet line, so the loud thing below is profit.
  const costPart=(k,v)=>v==null?"":`<span class="ind-bd-cost-part">${k}<b>${isk(v)}</b></span>`;
  const costStrip=`<div class="ind-bd-cost">
      <span class="ind-bd-cost-lbl">Cost basis</span>
      <span class="ind-bd-cost-parts">
        ${costPart("Materials",matCostBatch)}
        ${costPart("Job install",jobCostBatch)}
        ${inventCostBatch!=null?costPart("Invention",inventCostBatch):""}
      </span>
      <span class="ind-bd-cost-total">Total<b>${isk(batchCost)}</b></span>
    </div>`;
  // ── The two ways out ──────────────────────────────────────────────────────
  // One column per exit (patient List vs Instant buy-orders). Profit is the hero
  // of each — one big signed number — with revenue and break-even as quiet subs
  // beneath it. A "now" slot (filled by Compare-to-prices-now) sits at the foot.
  const exit=(label, sub, sell, askLbl, be, profit, kProfit)=>`
      <div class="ind-bd-exit ${pn(profit)}">
        <div class="ind-bd-exit-head"><span class="ind-bd-exit-name">${label}</span>
          <span class="ind-bd-exit-note">${sub}</span></div>
        <div class="ind-bd-exit-profit ${pn(profit)}"><b>${isk(profit)}</b>
          <span class="ind-bd-exit-plbl">profit</span></div>
        <div class="ind-bd-exit-meta">
          <span>Revenue <b>${isk(sell)}</b>${qtyTot!=null?` <i>${qtyTot.toLocaleString()}× @ ${askLbl}</i>`:""}</span>
          ${be!=null?`<span class="ind-bd-exit-be">Break-even <b>${isk(be)}</b>/unit</span>`:""}
        </div>
        <div class="ind-d-card-now" data-k="${kProfit}"></div>
      </div>`;
  return `<div class="ind-build-detail">
    ${costStrip}
    <div class="ind-bd-exits">
      ${exit("List", "patient sell orders", sellL, isk(d.ask), beL, batchProfitL, "profitL")}
      ${exit("Instant", "sell into buy orders", sellI, isk(instBid), beI, batchProfitI, "profitI")}
    </div>
    <div class="ind-build-nowrow">
      <button class="ind-build-now" data-id="${b.id}" title="Fetch the current market prices and compare them against the frozen snapshot">↻ Compare to prices now</button>
      <span class="ind-build-nowout"></span>
    </div>
    <details class="ind-build-mats">
      <summary>Materials — ${n.toLocaleString()} run(s), at frozen prices</summary>
      <table class="ind-d-mats"><thead><tr><th>Material</th><th class="num">Qty</th>
        <th class="num">Unit</th><th class="num">Total</th><th class="num">Cargo m³</th></tr></thead>
        <tbody>${mats}${matTotal}</tbody></table>
    </details>
  </div>`;
}

function _wireBuildCard(box, b){
  const card=box.querySelector(`.ind-build-card[data-id="${CSS.escape(b.id)}"]`);
  if(!card) return;
  const del=card.querySelector(".ind-build-del");
  if(del) del.onclick=()=>{
    const rz=_buildRealized(b);
    const msg=rz.units>0
      ? `Delete this build of ${b.product_name||"?"}? This removes the build and its share of the tracked realized profit. This can't be undone.`
      : `Stop tracking this build of ${b.product_name||"?"}?`;
    if(confirm(msg)) deleteBuild(b.id);
  };
  const tog=card.querySelector(".ind-build-toggle");
  if(tog) tog.onclick=()=>{
    if(IND.buildsExpanded.has(b.id)) IND.buildsExpanded.delete(b.id);
    else IND.buildsExpanded.add(b.id);
    renderIndBuilds();
  };
  const lc=card.querySelector(".ind-build-linkclose");
  if(lc) lc.onclick=()=>acceptCloseJob(b.id, lc.dataset.job, parseInt(lc.dataset.runs,10));
  const now=card.querySelector(".ind-build-now");
  if(now) now.onclick=()=>compareBuildToNow(b, now);
  _wireSellCard(card, b);
}

// Wire the sell-section buttons. Pricing/copy for the Built/Listed stages lives
// entirely inside the inline decider (_wireBuildDecider); this handles the shared
// actions — market look-ahead, abandon, archive, delete.
function _wireSellCard(card, b){
  // "Look at the market" — open the tracked-build modal straight on its Market tab
  // (price trend + odds of selling within a day). Falls back to the Industry
  // detail view if the modal isn't available (e.g. not logged in).
  const analyze=card.querySelector(".ind-sell-analyze");
  if(analyze) analyze.onclick=()=>{
    if(typeof openBuildPeek==="function") openBuildPeek(b.id, "market");
    else if(typeof openTrackedBuild==="function") openTrackedBuild(b.id);
  };
  const abandon=card.querySelector(".ind-sell-abandon");
  if(abandon) abandon.onclick=()=>{
    if(abandon.dataset.undo){ setBuildAbandoned(b, false, abandon); return; }
    const rz=_buildRealized(b);
    const remain=Math.max(0, (_buildUnits(b)||0)-rz.units);
    if(confirm(`Abandon the ${remain.toLocaleString()} unsold unit(s) of ${b.product_name||"this build"}? Their frozen cost is written off as a loss (so capital-in-flight clears), and later sales of this item flow to your next batch. You can undo this.`))
      setBuildAbandoned(b, true, abandon);
  };
  // Inline price decider (Built/Listed): draw + fetch its live market in place, so
  // pricing is decided right here rather than in the modal.
  if(card.querySelector(".ind-decider")) _wireBuildDecider(card, b);
  // Building-stage drift watch: kicks the same live-quote fetch (cached on the
  // decider state) so the "market moved since you started" line can fill in.
  if(card.querySelector(".ind-watch")) _wireBuildWatch(card, b);
  const archive=card.querySelector(".ind-sell-archive");
  if(archive) archive.onclick=()=>archiveBuild(b, !b.archived);
  const edit=card.querySelector(".ind-sell-edit");
  if(edit) edit.onclick=()=>{
    const cur=Math.max(1, b.runs||1);
    const val=prompt(`Correct the run count for this build of ${b.product_name||"?"}.\n`
      +`This re-scales produced units (sold units are untouched).`, String(cur));
    if(val==null) return;
    const runs=parseInt(val,10);
    if(!(runs>=1)){ alert("Enter a run count of 1 or more."); return; }
    if(runs!==cur) editBuildRuns(b, runs);
  };
  const stop=card.querySelector(".ind-sell-stop");
  if(stop) stop.onclick=()=>{
    const rz=_buildRealized(b);
    const remain=Math.max(0, (_buildUnits(b)||0)-rz.units);
    if(confirm(`Stop tracking this build of ${b.product_name||"?"}?\n\n`
      +`• ${rz.units.toLocaleString()} already-sold unit(s) keep their profit (frozen in your stats).\n`
      +`• ${remain.toLocaleString()} unsold unit(s) are left on the market untracked and flagged.\n\n`
      +`Later sales of this item will flow to your live builds instead. Reversible.`))
      stopBuild(b, true);
  };
  const resume=card.querySelector(".ind-sell-resume");
  if(resume) resume.onclick=()=>stopBuild(b, false);
  const sdel=card.querySelector(".ind-sell-delete");
  if(sdel) sdel.onclick=()=>{
    if(confirm(`Delete this build of ${b.product_name||"?"}? This removes the build and its share of the tracked realized profit from your stats. This can't be undone.`))
      deleteBuild(b.id);
  };
}

// Abandon (or un-abandon) a delivered build's unsold remainder. Sale tracking is
// otherwise fully automatic — money accrues from wallet transactions — so this
// is the only sell action: it writes off the frozen cost of the never-sold units
// as a realized loss (clearing capital-in-flight) and stops the lot absorbing
// future fills, so later sales of the same item flow to the next batch. Passing
// abandon=false undoes it. The server returns the updated build; we merge its
// flags in and re-pull the summary so realized/stage recompute.
function setBuildAbandoned(b, abandon, btn){
  if(btn){ btn.disabled=true; btn.textContent=abandon?"Abandoning…":"Restoring…"; }
  fetch("/api/ind/builds/sell/abandon",{method:"POST",headers:{"Content-Type":"application/json"},
    body:JSON.stringify({id:b.id, abandoned:abandon?"1":"0"})}).then(r=>r.json()).then(res=>{
    if(res && res.build){
      b.abandoned=!!res.build.abandoned;
      renderIndBuilds();
      // Realized profit + stage are derived from the ledger + this flag, so pull
      // the summary to refresh them (and the portfolio strip) in one shot.
      if(typeof loadSummary==="function") loadSummary(); else _refreshSummary();
    } else if(btn){ btn.disabled=false; btn.textContent=res&&res.error?("⚠ "+res.error):"⚠ Failed"; }
  }).catch(()=>{ if(btn){ btn.disabled=false; btn.textContent="⚠ Failed"; } });
}

// Fetch current market prices for a tracked build and compare its frozen values
// against what the batch would fetch right now, filling the "now" slot in each
// card (per-unit and total). Only market prices move — the snapshot's frozen job
// rate / taxes / broker are replayed so the delta is purely price movement.
function compareBuildToNow(b, btn){
  const d=b.snapshot||{}, n=Math.max(1, b.runs||1);
  const card=btn.closest(".ind-build-detail");
  const out=btn.parentElement.querySelector(".ind-build-nowout");
  btn.disabled=true; const label=btn.textContent; btn.textContent="Fetching…";
  const isk=v=>v===null||v===undefined?"—":fmtISK(v);
  const p=new URLSearchParams({
    blueprint_id:String(d.blueprint_id),
    station:String(d.station_id||""),
    job_rate:String(((d.job_rate||0)*100)),
    sales_tax:String(((d.sales_tax||0)*100)),
    broker:String(((d.broker_fee||0)*100)),
    runs:"1", refresh_prices:"1",
  });
  fetch("/api/ind/detail?"+p).then(r=>r.json()).then(fresh=>{
    btn.disabled=false; btn.textContent=label;
    if(!fresh||fresh.error){ out.textContent="⚠ "+((fresh&&fresh.error)||"fetch failed"); return; }
    const qtyTot=(d.product&&d.product.quantity!=null)?d.product.quantity*n:null;
    // Frozen cost is held fixed; current profit = current revenue − frozen cost.
    const cost=_batchEconomics(d, n).cost;
    const stax=d.sales_tax||0, bfee=d.broker_fee||0;
    const revL=(fresh.ask!=null&&qtyTot!=null)?fresh.ask*qtyTot*(1-stax-bfee):null;
    // Instant revenue walks the live buy book for the batch, skipping orders whose
    // min_volume it can't meet (see walkBook); falls back to raw bid × qty only if
    // the fetch returned no book.
    const fbw=(fresh.buy_book&&fresh.buy_book.length&&qtyTot!=null)?walkBook(fresh.buy_book,qtyTot):null;
    const revI=(fbw&&fbw.filled>0)?fbw.avg*fbw.filled*(1-stax)
             :(fresh.buy_book?null:((fresh.bid!=null&&qtyTot!=null)?fresh.bid*qtyTot*(1-stax):null));
    const vals={
      sellL:{then:d.ask, now:fresh.ask, tot:qtyTot},
      sellI:{then:d.bid, now:fresh.bid, tot:qtyTot},
      profitL:{thenT:d.profit_patient!=null?d.profit_patient*n:null,
               nowT:(revL!=null&&cost!=null)?revL-cost:null},
      profitI:{thenT:d.profit_instant!=null?d.profit_instant*n:null,
               nowT:(revI!=null&&cost!=null)?revI-cost:null},
    };
    const arrow=diff=>diff>0?"▲":(diff<0?"▼":"");
    const clsOf=diff=>diff>0?"pos":(diff<0?"neg":"");
    Object.keys(vals).forEach(k=>{
      const slot=card.querySelector(`.ind-d-card-now[data-k="${k}"]`);
      if(!slot) return;
      const v=vals[k];
      let nowT, thenT, nowU, thenU;
      if(v.tot!=null){ nowU=v.now; thenU=v.then;
        nowT=(v.now!=null)?v.now*v.tot:null; thenT=(v.then!=null)?v.then*v.tot:null; }
      else { nowT=v.nowT; thenT=v.thenT;
        nowU=(v.nowT!=null&&qtyTot)?v.nowT/qtyTot:null;
        thenU=(v.thenT!=null&&qtyTot)?v.thenT/qtyTot:null; }
      if(nowT==null){ slot.innerHTML=`<span class="ind-build-nowlbl">now</span> —`; return; }
      const diff=(thenT!=null)?nowT-thenT:null;
      const cls=diff!=null?clsOf(diff):"";
      const pct=(thenT)?` ${arrow(diff)}${Math.abs(diff/thenT*100).toFixed(1)}%`:"";
      slot.innerHTML=`<span class="ind-build-nowlbl">now</span> `
        +`<b>${isk(nowT)}</b>${nowU!=null?` <span class="ind-d-card-nowunit">(${isk(nowU)}/u)</span>`:""}`
        +(diff!=null?` <span class="${cls}">${diff>0?"+":""}${isk(diff)}${pct}</span>`:"");
    });
    out.innerHTML=`<span class="ind-build-nowlbl">prices as of now</span>`;
  }).catch(()=>{ btn.disabled=false; btn.textContent=label; out.textContent="⚠ fetch failed"; });
}

// User accepted a close-match suggestion: link the build to the picked job and
// re-base its tracked run count onto the job's real runs, so the batch economics
// (cost/profit/time = per-run × runs) reflect what was actually started.
function acceptCloseJob(buildId, jobId, jobRuns){
  const b=IND.builds.find(x=>x.id===buildId);
  if(!b) return;
  const job=((AUTH.data&&AUTH.data.jobs)||[]).find(j=>String(j.job_id)===String(jobId));
  if(!job) return;   // stale card — job no longer active; a reconcile will refresh
  // Guard against a double-claim: if another build already links this job (e.g.
  // two stale cards clicked in quick succession), don't steal it — just refresh.
  if(IND.builds.some(x=>x.id!==buildId && String(x.job_id)===String(jobId) && !x.done_at)){
    renderIndBuilds(); return;
  }
  b.job_id=job.job_id;
  b.job_end=job.end;
  b.char_name=job.character_name;
  b.job_location=job.location||b.job_location;
  if(jobRuns && jobRuns>0) b.runs=jobRuns;
  _patchBuildLink(b, {job_id:job.job_id, job_end:job.end, char_name:job.character_name, job_location:b.job_location, runs:b.runs});
  renderIndBuilds();
}

