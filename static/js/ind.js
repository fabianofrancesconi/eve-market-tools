// ══════════════════════════════════════════════════════════════════════════
// INDUSTRY TAB
// ══════════════════════════════════════════════════════════════════════════
let IND = {rows:[], sort:{key:"isk_per_hour_patient", dir:-1}, lastData:null, es:null,
           groupsLoaded:false, profiles:[], profilesCleared:false,
           favorites:new Set(), hidden:new Set(),
           timers:{}, savedGroup:null, openDetail:null, colOrder:null,
           colw:{}, colVis:{}, detailRuns:1,
           fillTotal:0, fillDone:0, tradeWeight:0.5,
           builds:[], buildsLoaded:false, buildsExpanded:new Set(),
           sections:{fav:true, owned:true, hidden:false, all:true, builds:true}};
// Bumped whenever a scan starts or a new fill begins, so an in-flight background
// tradeability fill from a previous scan knows to abandon itself.
let IND_FILL_TOKEN = 0;

const fmtDur = s => {
  if(s===null||s===undefined) return "—";
  const d=Math.floor(s/86400), h=Math.floor((s%86400)/3600), m=Math.round((s%3600)/60);
  if(d>0) return `${d}d ${h}h`;
  return h>0 ? `${h}h ${m}m` : `${m}m`;
};
const fmtPct1 = v => (v===null||v===undefined) ? "—" : (v*100).toFixed(1)+"%";
const fmtDaysSell = v => (v===null||v===undefined) ? "—" : (v<1 ? "<1 d" : v.toFixed(1)+" d");
const fmtTrainTime = h => { if(h<1) return Math.round(h*60)+"m"; if(h<24) return h.toFixed(1)+"h"; return (h/24).toFixed(1)+"d"; };

function computeIndTradeability(){ _computeTradeability(IND.rows, 'days_to_sell', IND.tradeWeight); }

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
  {k:"bp_price",           t:"BP price",       w:108, tip:"Cheapest BPO sell price in The Forge (open an item to see WHERE it's sold). 'invent' = T2, obtained by invention. 'BPO' = you own the original. 'BPC (N)' = you have a limited-run copy.", f:(v,r)=> r.owned_bp_me_te?((r.owned_is_bpo||r.owned_max_runs===-1)?"BPO":`BPC (${r.owned_max_runs})`):((r.other_owners&&r.other_owners.length)?r.other_owners.map(o=>`${o.name}${o.is_bpo?" BPO":" BPC"}`).join(", "):(v!=null?fmtISK(v):(r.bp_source==="invention"?"invent":"—"))), cls:"bp-buy"},
  {k:"payback_runs",       t:"Payback",        w: 88, tip:"Runs of profit needed to recoup the BPO purchase (T1 you don't own).", f:(v,r)=> r.owned_bp_me_te?"—":(v==null?"—":fmtNum(v)+" runs")},
  {k:"ask",                t:"Sell price",     w: 98, tip:"Item's lowest sell order at the source hub.", f:v=>v===null?"—":fmtISK(v)},
  {k:"in_vol_run",         t:"Cargo in",       w: 85, tip:"m³ of materials to haul in per run.", f:v=>v?fmtVol(v):"—"},
  {k:"out_vol_run",        t:"Cargo out",      w: 85, tip:"m³ of finished items to haul out per run.", f:v=>v?fmtVol(v):"—"},
  {k:"days_to_sell",       t:"Days to sell",   w: 88, tip:"How many days to sell one run's output (output qty ÷ daily volume). Spins while the market history loads in the background.", f:(v,r)=> !r.liq_loaded ? _SPIN : fmtDaysSell(v)},
  {k:"tradeability",       t:"Tradeability",   w: 98, tip:"0–100: how realistically you can sell at your price. Blends liquidity (daily volume) and low competition (days to sell), weighted by the Balance buttons. Higher is better; ranked within this scan.", f:(v,r)=> !r.liq_loaded ? _SPIN : (v==null?"—":`<span style="color:${v>=70?'#4caf76':v>=40?'#c8a040':'#e0655a'};font-weight:600">${v}</span>`)},
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
const _IND_RESIZE_CTX={get resizing(){return IND_RESIZING;},set resizing(v){IND_RESIZING=v;},tblSel:'#ind-tbl',get colw(){return IND.colw;},setCg:indSetColgroup,save(){saveIndPrefs();}};
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
  saveIndPrefs();
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
      cb.onchange=()=>{ IND.colVis[cb.dataset.k]=cb.checked; renderIndTable(); saveIndPrefs(); };
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
      saveIndPrefs();
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
  favs=indSortRows(favs); myBps=indSortRows(myBps);
  hiddenBps=indSortRows(hiddenBps); rest=indSortRows(rest);

  // Render filter chips
  const chips=$("#ind-chips");
  const hasSections=favs.length||myBps.length||hiddenBps.length;
  if(hasSections && IND.rows.length){
    const chip=(key,label,n)=>{
      const on=IND.sections[key];
      return `<span class="ind-chip${on?" active":""}" data-sect="${key}">${label} <span class="chip-count">(${n})</span></span>`;
    };
    let ch="";
    if(favs.length||IND.favorites.size) ch+=chip("fav","★ Favorites",favs.length);
    if(myBps.length||IND.rows.some(isOwned)) ch+=chip("owned","My Blueprints",myBps.length);
    if(hiddenBps.length||IND.hidden.size) ch+=chip("hidden","Hidden",hiddenBps.length);
    if(rest.length) ch+=chip("all","All Items",rest.length);
    chips.innerHTML=ch;
    chips.querySelectorAll(".ind-chip").forEach(el=>{
      el.onclick=()=>{ const k=el.dataset.sect; IND.sections[k]=!IND.sections[k]; renderIndTable(); saveLS(); };
    });
  } else { chips.innerHTML=""; }

  const ncol=vc.length;
  const sect=(key,label,n)=>{
    const col=IND.sections[key]?"":" collapsed";
    return `<tr class="ind-section${col}" data-sect="${key}"><td colspan="${ncol}"><span class="sect-arrow">▾</span>${label} — ${n}</td></tr>`;
  };

  const ordered=[];
  let html="";
  if(favs.length){
    html+=sect("fav","★ Favorites", favs.length);
    if(IND.sections.fav) favs.forEach(r=>{ html+=indRowHtml(r, ordered.length); ordered.push(r); });
  }
  if(myBps.length){
    html+=sect("owned","My Blueprints", myBps.length);
    if(IND.sections.owned) myBps.forEach(r=>{ html+=indRowHtml(r, ordered.length); ordered.push(r); });
  }
  if(hiddenBps.length){
    html+=sect("hidden","Hidden", hiddenBps.length);
    if(IND.sections.hidden) hiddenBps.forEach(r=>{ html+=indRowHtml(r, ordered.length); ordered.push(r); });
  }
  const IND_LAZY_BATCH=60;
  let lazyRest=null, lazyIdx=0;
  if(rest.length){
    if(hasSections) html+=sect("all","All Items", rest.length);
    if(!hasSections || IND.sections.all){
      const show=Math.max(IND_LAZY_BATCH, IND._lazyRendered||0);
      const initial=rest.slice(0, Math.min(show, rest.length));
      initial.forEach(r=>{ html+=indRowHtml(r, ordered.length); ordered.push(r); });
      IND._lazyRendered=initial.length;
      if(rest.length>initial.length){ lazyRest=rest; lazyIdx=initial.length; }
    }
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
    tr.onclick=()=>{ const k=tr.dataset.sect; IND.sections[k]=!IND.sections[k]; renderIndTable(); saveLS(); };
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
  if(IND.favorites.has(bp)) IND.favorites.delete(bp); else IND.favorites.add(bp);
  saveIndPrefs();
  renderIndTable();
}
function toggleHidden(bp){
  if(IND.hidden.has(bp)) IND.hidden.delete(bp); else IND.hidden.add(bp);
  saveIndPrefs();
  renderIndTable();
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
    buildable_only:$("#ind-buildable").checked?"1":"0",
    include_unbuildable:$("#ind-unobtainable").checked?"1":"0",
    hide_t2:      $("#ind-hidet2").checked?"1":"0",
    min_tradeability: $("#ind-mintrade").value||"0",
    favorites:    JSON.stringify([...IND.favorites]),
  };
  return new URLSearchParams(Object.assign(p, extra||{}));
}

function scanInd(refreshSde){
  if(IND.es){ IND.es.close(); IND.es=null; }
  IND_FILL_TOKEN++; IND.fillTotal=0; IND._lazyRendered=0;
  const btn=$("#ind-go"); btn.disabled=true; btn.textContent="Scanning…";
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
      es.close(); IND.es=null; btn.disabled=false; btn.textContent="Scan";
      IND.rows=data.rows; IND.lastData=data;
      computeIndTradeability();
      persistScan("ind", {...IND.lastData, rows:IND.rows});
      hideIndProgress(); renderIndStatus(); renderIndTable();
      fillIndTradeability();   // score the long tail in the background
    } else if(data.type==="error"){
      es.close(); IND.es=null; btn.disabled=false; btn.textContent="Scan";
      hideIndProgress(); setStatus(data.error, true);
    }
  };
  es.onerror=()=>{
    es.close(); IND.es=null; btn.disabled=false; btn.textContent="Scan";
    hideIndProgress(); setStatus("Connection error — server may have stopped.", true);
  };
}

// The scan scores only the top-ranked rows inline (to return fast). This walks
// the rest of the catalogue afterwards in chunks, fetching market history per
// product so EVERY item ends up with a real tradeability — gracefully: pending
// rows spin, a status pill counts progress, and the table fills in as it lands.
// A newer scan/fill cancels this one via IND_FILL_TOKEN.
async function fillIndTradeability(){
  const token=++IND_FILL_TOKEN;
  const station=(IND.lastData && IND.lastData.station_id) || $("#ind-station").value;
  // Group still-pending rows by product type so one history lookup updates every
  // blueprint that builds the same item.
  const byProduct=new Map();
  for(const r of IND.rows){
    if(r.liq_loaded) continue;
    if(!byProduct.has(r.product_id)) byProduct.set(r.product_id, []);
    byProduct.get(r.product_id).push(r);
  }
  const ids=[...byProduct.keys()];
  if(!ids.length){ IND.fillTotal=0; renderIndStatus(); return; }
  IND.fillTotal=ids.length; IND.fillDone=0; renderIndStatus();
  const CHUNK=60;
  for(let i=0;i<ids.length;i+=CHUNK){
    if(token!==IND_FILL_TOKEN) return;   // superseded by a newer scan
    const chunk=ids.slice(i,i+CHUNK);
    let liq=null;
    try{
      const p=new URLSearchParams({station:station, type_ids:chunk.join(",")});
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
      fillIndTradeability();
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
  const p=indParams({blueprint_id:row.blueprint_id});
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
  const batchRevL=d.revenue_patient!=null?d.revenue_patient*n:null;
  const batchRevI=d.revenue_instant!=null?d.revenue_instant*n:null;
  const batchProfitL=batchRevL!=null?batchRevL-batchCost:null;
  const batchProfitI=batchRevI!=null?batchRevI-batchCost:null;
  const batchTime=d.build_time?d.build_time*n:null;
  const pn=v=>v==null?"":(v>0?"pos":(v<0?"neg":""));
  // Fee/tax breakdown — re-derives the ISK amounts folded into revenue_patient
  // / revenue_instant (qty × price × rate) so they can surface as their own card.
  const qty=d.product.quantity, qtyBatchTot=qty*n;
  const brokerIsk=(d.ask!=null && d.broker_fee)?qty*d.ask*d.broker_fee*n:null;
  const taxListIsk=(d.ask!=null && d.sales_tax)?qty*d.ask*d.sales_tax*n:null;
  const taxInstantIsk=(d.bid!=null && d.sales_tax)?qty*d.bid*d.sales_tax*n:null;
  const jobCostBatch=d.job_cost!=null?d.job_cost*n:null;
  const inventionCostBatch=d.invention?d.invention_cost*n:0;
  // Cumulative runs delivered for this exact item, from the same tracker
  // backing the Character tab KPI — broken out per product there.
  const prodTrack=(AUTH.loggedIn && AUTH.data && AUTH.data.runs_tracked)
    ? AUTH.data.runs_tracked.by_product[String(d.product.type_id)] : null;
  // Break-even sell price: instant sale only pays sales tax (no broker fee), so
  // qty*price*(1-sales_tax) = total_cost solved for price. Surfaced only when
  // the instant sale is currently unprofitable.
  const minPriceInstant=(d.profit_instant!=null && d.profit_instant<0
      && d.total_cost!=null && qty>0 && d.sales_tax!=null && d.sales_tax<1)
    ? d.total_cost/(qty*(1-d.sales_tax)) : null;
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
  let invHtml="";
  if(d.invention){
    const iv=d.invention;
    const dcs=iv.datacores.map(c=>
      `<tr><td>${c.name}</td><td class="num">${fmtNum(c.quantity)}</td>`
      +`<td class="num">${isk(c.unit_price)}</td><td class="num">${isk(c.line_cost)}</td></tr>`).join("");
    invHtml=`
      <div class="ind-d-head" style="margin-top:10px">Invention (T2)</div>
      <div class="ind-d-grid">
        <span>Success probability</span><b>${(iv.probability*100).toFixed(1)}% (base ${(iv.base_probability*100).toFixed(1)}%)</b>
        <span>Runs per invented BPC</span><b>${fmtNum(iv.runs_per_bpc)}</b>
        <span>Invention cost / T2 run</span><b>${isk(iv.cost_per_run)}</b>
      </div>
      <table class="ind-d-mats"><thead><tr><th>Datacore</th><th class="num">Qty</th>
        <th class="num">Unit</th><th class="num">Line</th></tr></thead><tbody>${dcs}</tbody></table>`;
  }
  box.innerHTML=`
    <div class="ind-d-head">
      <b>${d.product.name}</b>
      <button class="ind-fav-btn${IND.favorites.has(d.blueprint_id)?" on":""}" title="${esiOwned?"Owned blueprints appear in My Blueprints automatically":"Add to Watchlist — track blueprints you don't own yet"}">${IND.favorites.has(d.blueprint_id)?"★ Watchlist":"☆ Watchlist"}</button>
      <button class="ind-copy" title="Copy item name to clipboard">⧉ Copy</button>
      <button class="ind-pull-prices${d.esi_prices?" on":""}" title="Fetch live prices directly from ESI (more accurate than Fuzzwork aggregate)">${d.esi_prices?"✓ ESI prices":"⟳ Pull live prices"}</button>
      <button class="ind-track-btn" title="Freeze these stats for the current run count so you can revisit them after the batch finishes — the numbers stay put even as market prices move. Appears under 'Tracked builds' up top.">＋ Track this build</button>
      ${tier} · <span class="ind-d-runs-wrap">Runs <input class="ind-d-runs" type="text" inputmode="numeric" pattern="[0-9]*" value="${n}" style="width:68px"><span class="ind-d-runs-step"><button class="ind-d-runs-inc" title="Increase runs" tabindex="-1">▲</button><button class="ind-d-runs-dec" title="Decrease runs" tabindex="-1">▼</button></span><button class="ind-d-runs-pre" data-n="1">1</button><button class="ind-d-runs-pre" data-n="10">10</button><button class="ind-d-runs-pre" data-n="100">100</button><button class="ind-d-runs-pre" data-n="10000">10k</button><button class="ind-d-runs-mul" data-m="10">×10</button></span> · source ${d.station_name}
      <span class="ind-d-close" title="Close">✕</span>
    </div>
    <div class="ind-d-body">
    ${esiOwned && !isBpo ? `<div class="ind-bpc-warn">
      ⚠ You only have a <b>Blueprint Copy</b> with <b>${bpcRuns} run${bpcRuns===1?"":"s"}</b> remaining — it will be consumed.
      ${d.bp_market
        ? `<span class="ind-bpc-buy">Buy permanent BPO: ${isk(d.bp_market.price)} at ${d.bp_market.station} (${fmtNum(d.bp_market.orders)} on sale in ${d.bp_market.region})</span>`
        : `<span class="ind-bpc-buy">No BPO on the market in ${d.region_name}. <button class="ind-bpo-expand" data-bp="${d.blueprint_id}">Search other regions</button></span>`}
    </div>` : ""}
    <div class="ind-d-grid">
      <div class="ind-d-sub">Per unit (sell price)</div>
      <span>Sell @ ask — list</span><b>${isk(d.ask)}</b>
      <span>Sell @ bid — instant</span><b>${isk(d.bid)}</b>

      <div class="ind-d-sub">Per run — ${fmtNum(d.product.quantity)}× ${d.product.name}</div>
      <span>Material cost</span><b>${isk(d.material_cost)}</b>
      <span>Job install (EIV ${isk(d.eiv)} × ${(d.job_rate*100).toFixed(1)}%)</span><b>${isk(d.job_cost)}</b>
      ${d.invention?`<span>Invention cost</span><b>${isk(d.invention_cost)}</b>`:""}
      <span>Total cost</span><b>${isk(d.total_cost)}</b>
      <span>Profit — list</span><b class="${pn(d.profit_patient)}">${isk(d.profit_patient)}</b>
      <span>Profit — instant</span><b class="${pn(d.profit_instant)}">${isk(d.profit_instant)}</b>
      <span>Build time</span><b>${fmtDur(d.build_time)}</b>

      <div class="ind-d-sub">Batch — ${n.toLocaleString()} run(s)</div>
      <span>Total cost</span><b>${isk(batchCost)}</b>
      <span>Profit — list</span><b class="${pn(batchProfitL)}">${isk(batchProfitL)}</b>
      <span>Profit — instant</span><b class="${pn(batchProfitI)}">${isk(batchProfitI)}</b>
      <span>Build time</span><b>${fmtDur(batchTime)}</b>
      <span>Cargo in / out</span><b>${inputBatch?fmtVol(inputBatch):"—"} / ${outputBatch?fmtVol(outputBatch):"—"}</b>

      <div class="ind-d-sub">Blueprint &amp; market</div>
      <span>Blueprint</span><b class="bp-buy">${bpSrc}</b>
      <span>ME / TE used</span><b>${d.me_used} / ${d.te_used}</b>
      <span>Ownership</span><b>${d.owned_me_te
          ? `<span class="ind-yours">✓ You own this blueprint${isBpo?" (Original — infinite runs)":" (Copy)"}</span>`
          : (d.other_owners&&d.other_owners.length
            ? `<span class="ind-alt-owns">✓ Owned by ${d.other_owners.map(o=>`${o.name} (${o.is_bpo?"BPO":"BPC"}${o.is_bpo?"":", "+o.max_runs+" runs"} · ME ${o.me} / TE ${o.te})`).join(", ")}</span>`
            : `<span class="ind-not-yours">✗ Not in your blueprints</span>`)}</b>
      <span>Blueprint payback</span><b>${payback}</b>
      <span>Tradeability</span><b>${d.tradeability==null?"—":d.tradeability+" / 100"}${d.daily_units!=null?` (${fmtNum(d.daily_units)} units/day)`:""}</b>
    </div>
    ${d.missing_skills&&d.missing_skills.length?`
    <div class="ind-d-sub ind-skills-warn">Missing skills — ${d.missing_skills.length} needed</div>
    <table class="ind-d-mats ind-d-skills"><thead><tr><th>Skill</th><th class="num">Have</th><th class="num">Need</th><th class="num">Train time</th></tr></thead><tbody>${d.missing_skills.map(s=>`<tr><td>${s.name}${s.prereq?' <span class="ind-prereq">(prereq)</span>':''}</td><td class="num">${s.current}</td><td class="num">${s.required}</td><td class="num">${s.train_hours<1?(Math.round(s.train_hours*60)+"m"):(s.train_hours<24?s.train_hours.toFixed(1)+"h":(s.train_hours/24).toFixed(1)+"d")}</td></tr>`).join("")}</tbody>
    <tfoot><tr class="ind-d-total"><td>Total training</td><td></td><td></td><td class="num">${(()=>{const h=d.missing_skills.reduce((s,sk)=>s+sk.train_hours,0);return h<1?(Math.round(h*60)+"m"):(h<24?h.toFixed(1)+"h":(h/24).toFixed(1)+"d");})()}</td></tr></tfoot></table>`:""}
    <aside class="ind-d-side">
      <div class="ind-d-section">
        <div class="ind-d-sub">Craft</div>
        <div class="ind-d-timer-card">${timerHtml}</div>
        <div class="ind-d-cards">
          <div class="ind-d-card">
            <div class="ind-d-card-label">Job duration</div>
            <div class="ind-d-card-val">${fmtDur(batchTime)}</div>
            <div class="ind-d-card-sub">${n.toLocaleString()} run(s)</div>
          </div>
          <div class="ind-d-card">
            <div class="ind-d-card-label">Build cost</div>
            <div class="ind-d-card-val">${isk(batchCost)}</div>
            <div class="ind-d-card-sub">mats ${isk(matTotCost)} + job ${isk(jobCostBatch)}${d.invention?` + invent ${isk(inventionCostBatch)}`:""}</div>
          </div>
          <div class="ind-d-card" data-tip="Job installation fee charged by the station/structure when you start the manufacturing job. Calculated as EIV × job cost % (system index × bonuses + facility tax + SCC surcharge).">
            <div class="ind-d-card-label">Job install fee</div>
            <div class="ind-d-card-val">${isk(jobCostBatch)}</div>
            <div class="ind-d-card-sub">EIV ${isk(d.eiv)} × ${(d.job_rate*100).toFixed(2)}% × ${n.toLocaleString()} run(s)</div>
          </div>
          <div class="ind-d-card">
            <div class="ind-d-card-label">Cargo in</div>
            <div class="ind-d-card-val">${inputBatch?fmtVol(inputBatch):"—"}</div>
            <div class="ind-d-card-sub">${n.toLocaleString()} run(s)</div>
          </div>
          <div class="ind-d-card" data-tip="Cumulative runs you've delivered for this item, tracked since the app started watching — it can't see deliveries from before that. Log in with EVE to track.">
            <div class="ind-d-card-label">Runs delivered</div>
            <div class="ind-d-card-val">${prodTrack?prodTrack.runs.toLocaleString():(AUTH.loggedIn?"0":"—")}</div>
            <div class="ind-d-card-sub">${prodTrack?prodTrack.jobs.toLocaleString()+" job(s)":(AUTH.loggedIn?"none yet":"log in to track")}</div>
          </div>
        </div>
      </div>
      <div class="ind-d-section">
        <div class="ind-d-sub">Resell</div>
        <div class="ind-d-cards">
          <div class="ind-d-card">
            <div class="ind-d-card-label">Profit — instant</div>
            <div class="ind-d-card-val ${pn(batchProfitI)}">${isk(batchProfitI)}</div>
            <div class="ind-d-card-sub">${qtyBatchTot.toLocaleString()}× @ bid ${isk(d.bid)} − tax ${fmtPct1(d.sales_tax)} − cost ${isk(batchCost)} = ${isk(batchProfitI)}</div>
            ${minPriceInstant!=null?`<div class="ind-d-card-sub ind-d-card-warn">Break-even sell: ${isk(minPriceInstant)}/unit</div>`:""}
          </div>
          <div class="ind-d-card">
            <div class="ind-d-card-label">Profit — sell (list)</div>
            <div class="ind-d-card-val ${pn(batchProfitL)}">${isk(batchProfitL)}</div>
            <div class="ind-d-card-sub">${qtyBatchTot.toLocaleString()}× @ ask ${isk(d.ask)} − tax ${fmtPct1(d.sales_tax)} − broker ${fmtPct1(d.broker_fee)} − cost ${isk(batchCost)} = ${isk(batchProfitL)}</div>
          </div>
          <div class="ind-d-card">
            <div class="ind-d-card-label">Fees &amp; taxes</div>
            <div class="ind-d-card-grid">
              <span>Broker fee (list)</span><b>${isk(brokerIsk)}</b>
              <span>Sales tax (list)</span><b>${isk(taxListIsk)}</b>
              <span>Sales tax (instant)</span><b>${isk(taxInstantIsk)}</b>
            </div>
          </div>
          <div class="ind-d-card">
            <div class="ind-d-card-label">Cargo out</div>
            <div class="ind-d-card-val">${outputBatch?fmtVol(outputBatch):"—"}</div>
            <div class="ind-d-card-sub">batch of ${n.toLocaleString()} run(s)</div>
          </div>
        </div>
      </div>
    </aside>
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
  head.onmousedown=ev=>{ headDownInInteractive=!!ev.target.closest("button,input,.ind-d-runs-wrap"); };
  head.onclick=ev=>{ if(!ev.target.closest("button,input,.ind-d-runs-wrap") && !headDownInInteractive) closeIndDetail(); };
  box.querySelector(".ind-fav-btn").onclick=()=>toggleFavorite(d.blueprint_id);
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
    const p=indParams({blueprint_id:d.blueprint_id, refresh_prices:"1"});
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
  box.querySelectorAll(".ind-d-runs-pre").forEach(b=>{
    b.onclick=()=>setRuns(+b.dataset.n);
  });
  box.querySelectorAll(".ind-d-runs-mul").forEach(b=>{
    b.onclick=()=>setRuns(IND.detailRuns*(+b.dataset.m));
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
    const inBuildCard=!!el.closest(".ind-build-card");
    if(rem<=0){
      if(isCell){ el.textContent="✓ Ready"; el.classList.add("done"); el.removeAttribute("data-end"); }
      else if(inBuildCard){ el.textContent="finishing…"; el.removeAttribute("data-end"); }
      else if(IND.openDetail) renderIndDetail(IND.openDetail);
    } else {
      el.textContent=isCell?fmtCountdownShort(rem):fmtCountdown(rem);
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

// Freeze the currently-open detail blob for N runs and persist it. The snapshot
// is the exact /api/ind/detail response the panel is rendering, so reopening it
// reproduces the numbers verbatim regardless of later price moves.
function trackThisBuild(d, runs, btn){
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
        if(btn){ btn.textContent="✓ Tracked"; setTimeout(()=>{ btn.textContent="＋ Track this build"; btn.disabled=false; },1400); }
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
  }).catch(()=>{ IND.buildsLoaded=true; });
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
  let changed=false;
  // Order by created_at so the oldest batch claims the oldest matching job.
  const ordered=[...IND.builds].sort((a,b)=>(a.created_at||0)-(b.created_at||0));
  ordered.forEach(b=>{
    if(b.done_at){
      // Self-heal: a build wrongly marked done (e.g. a stale string/number
      // job_id mismatch from an older build) whose linked job is in fact still
      // running gets un-marked and reclaimed. A genuinely finished job stays.
      if(b.job_id!=null && activeJobIds.has(String(b.job_id))){
        b.done_at=null; changed=true; claimed.add(String(b.job_id));
        _patchBuildLink(b, {done_at:null});
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
    // Not yet linked — try to adopt a matching active job.
    const job=_findJobForBuild(b, claimed);
    if(job){
      claimed.add(String(job.job_id));
      b.job_id=job.job_id; b.job_end=job.end; b.char_name=job.character_name; changed=true;
      _patchBuildLink(b, {job_id:job.job_id, job_end:job.end, char_name:job.character_name});
    }
  });
  renderIndBuilds();
  return changed;
}

function _patchBuildLink(b, fields){
  const body=Object.assign({id:b.id}, fields);
  Object.keys(body).forEach(k=>{ if(body[k]==null) body[k]="null"; else body[k]=String(body[k]); });
  fetch("/api/ind/builds/link",{method:"POST",headers:{"Content-Type":"application/json"},
    body:JSON.stringify(body)}).catch(()=>{});
}

function deleteBuild(id){
  IND.builds=IND.builds.filter(b=>b.id!==id);
  IND.buildsExpanded.delete(id);
  renderIndBuilds();
  fetch("/api/ind/builds/delete",{method:"POST",headers:{"Content-Type":"application/json"},
    body:JSON.stringify({id})}).catch(()=>{});
}

// Status of a build for display, derived from its stored link fields + live jobs.
function _buildStatus(b){
  if(b.done_at) return {key:"done", label:"✓ Done"};
  if(b.job_id!=null && _activeJobIdSet().has(String(b.job_id))) return {key:"building", label:"⏳ Building"};
  return {key:"awaiting", label:"⚠ No matching job"};
}

// Render the tracked-builds section in the Industry tab. Always expanded — no
// collapse toggle. If the Character overview is the active tab, refresh it too
// so its 🔗 tracked-job markers reflect the current builds.
function renderIndBuilds(){
  const box=$("#ind-builds");
  if(box){
    if(!IND.builds.length){ box.classList.add("hidden"); box.innerHTML=""; }
    else {
      box.classList.remove("hidden");
      // Jobs already linked to a build must not be offered as a close match to
      // an awaiting one. Collect the linked ids (as strings) up front.
      const linked=new Set(IND.builds.filter(b=>b.job_id!=null).map(b=>String(b.job_id)));
      const rows=IND.builds.map(b=>_buildCardHtml(b, linked)).join("");
      box.innerHTML=`
        <div class="ind-builds-head static">Tracked builds <span class="chip-count">(${IND.builds.length})</span></div>
        <div class="ind-builds-list">${rows}</div>`;
      IND.builds.forEach(b=>_wireBuildCard(box, b));
    }
  }
  // Keep the overview's job 🔗 markers in sync (only when it's showing).
  if(ACTIVE_TAB==="char" && AUTH.data && typeof renderCharData==="function") renderCharData();
}

// Expand a tracked build's detailed view and scroll to it. Used when arriving
// from a clicked industry-job row in the Character overview.
function openTrackedBuild(id){
  if(!IND.builds.some(b=>b.id===id)) return;
  IND.buildsExpanded.add(id);
  renderIndBuilds();
  const box=$("#ind-builds");
  const card=box&&box.querySelector(`.ind-build-card[data-id="${CSS.escape(id)}"]`);
  if(card) card.scrollIntoView({block:"center", behavior:"smooth"});
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
  const revI=d.revenue_instant!=null?d.revenue_instant*n:null;
  const profitL=(revL!=null&&cost!=null)?revL-cost
              :(d.profit_patient!=null?d.profit_patient*n:null);
  const profitI=(revI!=null&&cost!=null)?revI-cost
              :(d.profit_instant!=null?d.profit_instant*n:null);
  return {cost, profitL, profitI, matCost, time:d.build_time?d.build_time*n:null};
}

function _buildCardHtml(b, linked){
  const s=b.snapshot||{}, n=Math.max(1, b.runs||1);
  const isk=v=>v===null||v===undefined?"—":fmtISK(v);
  const st=_buildStatus(b);
  const be=_batchEconomics(s, n);
  const batchCost=be.cost, batchProfitL=be.profitL, batchProfitI=be.profitI, batchTime=be.time;
  const pn=v=>v==null?"":(v>0?"pos":(v<0?"neg":""));
  const when=b.created_at?new Date(b.created_at*1000).toLocaleString([],{day:'2-digit',month:'short',hour:'2-digit',minute:'2-digit'}):"";
  // Status line: warning if no job yet, live countdown while building, ETA/finish otherwise.
  let statusLine="";
  if(st.key==="awaiting"){
    // If a running job for this blueprint exists with a different run count,
    // offer it as a one-click close match instead of only nagging.
    const close=AUTH.loggedIn ? _findCloseJobForBuild(b, linked||new Set()) : null;
    if(close){
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
  } else if(st.key==="building"){
    const end=b.job_end?Date.parse(b.job_end):null;
    statusLine=end && isFinite(end)
      ? `<span class="ind-build-live ind-live-timer" data-end="${end}">${fmtCountdown(end-Date.now())}</span> <span class="ind-build-eta">ETA ${new Date(end).toLocaleString([],{day:'2-digit',month:'short',hour:'2-digit',minute:'2-digit'})}${b.char_name?" · "+b.char_name:""}</span>`
      : `<span class="ind-build-live">running${b.char_name?" · "+b.char_name:""}</span>`;
  } else {
    statusLine=`<span class="ind-build-done">Finished${b.done_at?" "+new Date(b.done_at*1000).toLocaleString([],{day:'2-digit',month:'short',hour:'2-digit',minute:'2-digit'}):""}</span>`;
  }
  const expanded=IND.buildsExpanded.has(b.id);
  const detail=expanded?_buildDetailHtml(b):"";
  return `<div class="ind-build-card ${st.key}" data-id="${b.id}">
    <div class="ind-build-row">
      <span class="ind-build-status ${st.key}">${st.label}</span>
      <span class="ind-build-name">${b.product_name||"?"}</span>
      <span class="ind-build-runs">${n.toLocaleString()} run(s)</span>
      <span class="ind-build-stat" title="Frozen lowest-ask sell price per unit — what a patient list order would have fetched">Sell list <b>${isk(s.ask)}</b></span>
      <span class="ind-build-stat" title="Frozen highest-bid sell price per unit — what an instant sale would have fetched">Sell instant <b>${isk(s.bid)}</b></span>
      <span class="ind-build-stat">Cost <b>${isk(batchCost)}</b></span>
      <span class="ind-build-stat">Profit list <b class="${pn(batchProfitL)}">${isk(batchProfitL)}</b></span>
      <span class="ind-build-stat">Profit instant <b class="${pn(batchProfitI)}">${isk(batchProfitI)}</b></span>
      <span class="ind-build-stat">Build ${fmtDur(batchTime)}</span>
      <span class="ind-build-when">frozen ${when}</span>
      <button class="ind-build-toggle" title="Show the full frozen snapshot">${expanded?"▲ Hide":"▼ Details"}</button>
      <button class="ind-build-del" title="Stop tracking this build">✕</button>
    </div>
    <div class="ind-build-substatus">${statusLine}</div>
    ${detail}
  </div>`;
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
  return `<div class="ind-build-detail">
    <div class="ind-d-grid" style="max-width:none">
      <div class="ind-d-sub">Per run — ${fmtNum(d.product.quantity)}× ${d.product.name} (frozen)</div>
      <span>Sell @ ask — list</span><b>${isk(d.ask)}</b>
      <span>Sell @ bid — instant</span><b>${isk(d.bid)}</b>
      <span>Material cost</span><b>${isk(d.material_cost)}</b>
      <span>Job install</span><b>${isk(d.job_cost)}</b>
      ${d.invention?`<span>Invention cost</span><b>${isk(d.invention_cost)}</b>`:""}
      <span>Total cost</span><b>${isk(d.total_cost)}</b>
      <span>Profit — list</span><b class="${pn(d.profit_patient)}">${isk(d.profit_patient)}</b>
      <span>Profit — instant</span><b class="${pn(d.profit_instant)}">${isk(d.profit_instant)}</b>
      <div class="ind-d-sub">Batch — ${n.toLocaleString()} run(s)</div>
      <span>Total cost</span><b>${isk(batchCost)}</b>
      <span>Profit — list</span><b class="${pn(batchProfitL)}">${isk(batchProfitL)}</b>
      <span>Profit — instant</span><b class="${pn(batchProfitI)}">${isk(batchProfitI)}</b>
    </div>
    <div class="ind-d-sub" style="margin-top:10px">Materials — ${n.toLocaleString()} run(s), at frozen prices</div>
    <table class="ind-d-mats"><thead><tr><th>Material</th><th class="num">Qty</th>
      <th class="num">Unit</th><th class="num">Total</th><th class="num">Cargo m³</th></tr></thead>
      <tbody>${mats}${matTotal}</tbody></table>
  </div>`;
}

function _wireBuildCard(box, b){
  const card=box.querySelector(`.ind-build-card[data-id="${CSS.escape(b.id)}"]`);
  if(!card) return;
  const del=card.querySelector(".ind-build-del");
  if(del) del.onclick=()=>{
    if(confirm(`Stop tracking this build of ${b.product_name||"?"}?`)) deleteBuild(b.id);
  };
  const tog=card.querySelector(".ind-build-toggle");
  if(tog) tog.onclick=()=>{
    if(IND.buildsExpanded.has(b.id)) IND.buildsExpanded.delete(b.id);
    else IND.buildsExpanded.add(b.id);
    renderIndBuilds();
  };
  const lc=card.querySelector(".ind-build-linkclose");
  if(lc) lc.onclick=()=>acceptCloseJob(b.id, lc.dataset.job, parseInt(lc.dataset.runs,10));
}

// User accepted a close-match suggestion: link the build to the picked job and
// re-base its tracked run count onto the job's real runs, so the batch economics
// (cost/profit/time = per-run × runs) reflect what was actually started.
function acceptCloseJob(buildId, jobId, jobRuns){
  const b=IND.builds.find(x=>x.id===buildId);
  if(!b) return;
  const job=((AUTH.data&&AUTH.data.jobs)||[]).find(j=>String(j.job_id)===String(jobId));
  if(!job) return;   // stale card — job no longer active; a reconcile will refresh
  b.job_id=job.job_id;
  b.job_end=job.end;
  b.char_name=job.character_name;
  if(jobRuns && jobRuns>0) b.runs=jobRuns;
  _patchBuildLink(b, {job_id:job.job_id, job_end:job.end, char_name:job.character_name, runs:b.runs});
  renderIndBuilds();
}

