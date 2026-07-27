// Tracker readout strip — the one-line portfolio P&L that sits atop the pipeline
// board (ind.js draws the board + tiles). It reads /api/ind/summary (the server
// rolls up realized profit, capital in flight and a per-product breakdown) and
// distils it to four figures: what you've booked, what's still tied up, what's
// left to earn, and the estimated total. The board below carries the per-build
// detail, so this stays a glanceable ribbon rather than a second dashboard.

let SUMMARY = { data:null, loading:false };

function loadSummary(){
  if(!AUTH.loggedIn){ return; }
  SUMMARY.loading = !SUMMARY.data;   // only show the spinner on the first load
  renderSummary();
  fetch("/api/ind/summary").then(r=>r.json()).then(res=>{
    SUMMARY.data = res || {builds:[], totals:{}, by_product:[]};
    SUMMARY.loading = false;
    // Fold the server-derived per-build state (stage / realized / abandoned)
    // into the board's builds, then re-render both the strip and the board.
    if(typeof mergeSummaryBuilds==="function" && mergeSummaryBuilds(SUMMARY.data)
       && typeof renderIndBuilds==="function") renderIndBuilds();
    renderSummary();
  }).catch(()=>{ SUMMARY.loading=false; renderSummary(); });
}

// Realized profit within the selected time window. The pooled model keeps money
// in a per-product wallet ledger, not a per-build fill list, so the server's
// window-aware breakdown isn't reconstructable per build on the client — the
// range toggle currently reports the all-time totals for every window. (A future
// server-side windowed rollup can fill 7d/30d honestly; until then "all" is the
// only exact figure and the others mirror it rather than under-count.)
function _sumRealizedInRange(){
  const d=SUMMARY.data;
  if(!d) return {profit:0, net:0, units:0};
  return {profit:(d.totals&&d.totals.realized_profit)||0,
          net:(d.totals&&d.totals.realized_net)||0,
          units:d.builds.reduce((s,b)=>s+((b.realized&&b.realized.units)||0),0)};
}

// Portfolio figures distilled from the summary payload: realized (booked),
// capital in flight, "ready" (projected profit still to earn on unsold stock),
// the estimate that sums them, and the margin that estimate rides on. Returned
// as one object so the strip can render without recomputing.
function _sumFigures(){
  const d=SUMMARY.data;
  const t=(d&&d.totals)||{};
  let ready=0;
  (d&&d.builds||[]).forEach(b=>{
    if(b.stage==="sold"||b.stage==="planned"||b.stage==="building") return;
    const cpu=b.cost_per_unit, ask=b.ask;
    if(cpu==null||ask==null) return;
    const sold=(b.realized&&b.realized.units)||0;
    // Unsold stock from this build's lot = produced − FIFO-allocated sold.
    const remain=Math.max(0, (b.units_produced||0)-sold);
    const stax=b.sales_tax||0, bfee=b.broker_fee||0;
    ready += remain*(ask*(1-stax-bfee)-cpu);
  });
  const realizedAll=t.realized_profit||0;
  const estTotal=realizedAll+ready;
  const soldCost=(d&&d.builds||[]).reduce((s,b)=>s+(((b.realized&&b.realized.units)||0)*(b.cost_per_unit||0)),0);
  const capBase=(t.capital_in_flight||0)+soldCost;
  const margin=capBase>0?estTotal/capBase*100:null;
  return {realized:_sumRealizedInRange(), capital:t.capital_in_flight||0,
          ready, estTotal, margin};
}

// The slim readout strip. One horizontal ribbon of four readings, each a small
// uppercase label over a tabular figure — booked / tied up / to earn / estimate.
// The realized reading carries the only control (a compact 7d·30d·All range
// toggle) since it's the only figure a time window changes. Everything else the
// old dashboard showed (capital-by-stage, needs-action, by-item) now lives in
// the board of tiles below, so this stays a glance, not a second screen.
function renderSummary(){
  const body=$("#sum-body");
  if(!body) return;
  if(SUMMARY.loading){ body.innerHTML=`<div class="sum-loading">Loading portfolio…</div>`; return; }
  const d=SUMMARY.data;
  if(!d || !d.builds || !d.builds.length){
    // The pipeline board (#ind-builds, rendered by ind.js) sits below this node
    // and stays empty; here we just explain how to fill the Tracker.
    body.innerHTML=`<div class="sum-empty-note">No tracked builds yet. In the
      <b>Planner</b>, find a blueprint and hit <b>＋ Track this build</b> — it'll
      appear on the board here, and once it's built your sell order links
      automatically so the real profit rolls in.</div>`;
    return;
  }
  const isk=v=>v===null||v===undefined?"—":fmtISK(v);
  const pn=v=>v==null?"":(v>0?"pos":(v<0?"neg":""));
  const f=_sumFigures();
  const rr=f.realized;

  // read(label, value, class, extra) — one reading in the strip.
  const read=(label,val,cls,extra)=>`<div class="sum-read">
    <div class="sum-read-label">${label}</div>
    <div class="sum-read-val ${cls||""}">${val}</div>
    ${extra||""}</div>`;

  body.innerHTML=`<div class="sum-strip">
    ${read("Realized", isk(rr.profit), pn(rr.profit))}
    ${read("Capital in flight", isk(f.capital), "dim")}
    ${read("Ready to realize", isk(f.ready), pn(f.ready))}
    ${read("Est. total", isk(f.estTotal), pn(f.estTotal),
      f.margin!=null?`<div class="sum-read-note"><b class="${pn(f.margin)}">${f.margin>=0?"+":""}${f.margin.toFixed(1)}%</b> on capital</div>`:"")}
    <button class="sum-ledger-btn" onclick="openBuildLedger()" title="Open the full build ledger">
      <span class="slb-ico">▤</span><span class="slb-lbl">Ledger</span></button>
  </div>`;
}

// ── Build ledger — the full history of every tracked build and its outcome ─────
// A sortable financial register: one row per batch (runs, cost, revenue, profit,
// stage), with a totals "bottom line" footer. Reads the same /api/ind/summary
// payload the strip does (SUMMARY.data.builds), so it opens instantly with no
// extra fetch. Rows carry the pipeline stage colour so the register reads as the
// same object the board shows. Clicking a row opens that build's quick-look peek.
let LEDGER = { sort:"done", dir:-1, filter:"all" };

// Stage vocabulary mirrored from ind.js (_STAGE_LABEL / _buildBadge) so the
// ledger reads with the tracker's own words + colours even though it renders its
// own compact chip. Order doubles as the "activity" rank for the default sort.
const _LEDGER_STAGES={
  planned:{label:"Planned", cls:"awaiting", rank:0},
  building:{label:"Building", cls:"building", rank:1},
  built:{label:"Built", cls:"built", rank:2},
  listed:{label:"Listed", cls:"listed", rank:3},
  sold:{label:"Sold", cls:"sold", rank:4},
};

function openBuildLedger(){
  const m=$("#buildLedgerModal"); if(!m) return;
  m.classList.remove("hidden");
  renderBuildLedger();
}
function closeBuildLedger(){ const m=$("#buildLedgerModal"); if(m) m.classList.add("hidden"); }

// One build's ledger figures, all read straight from the summary payload. Cost =
// the batch's full frozen cost; revenue = realized net (proceeds after sales tax)
// on units sold so far; profit = the server's realized profit (already nets cost
// of sold + any abandoned write-off). A closed batch (sold/abandoned) shows final
// numbers; an open one shows what's booked to date, so the register never lies
// about money not yet earned.
function _ledgerRow(b){
  const stg=_LEDGER_STAGES[b.stage]||_LEDGER_STAGES.built;
  const rz=b.realized||{};
  const closed=(b.stage==="sold");
  return {
    b, id:b.id, name:b.product_name||"?",
    runs:b.runs||0,
    produced:b.units_produced||0,
    sold:rz.units||0,
    cost:b.batch_cost,
    revenue:rz.net||0,
    profit:(rz.profit==null?null:rz.profit),
    stage:b.stage||"built", stageLabel:stg.label, stageCls:stg.cls, stageRank:stg.rank,
    closed, abandoned:!!b.abandoned,
    when:b.done_at||b.created_at||0,
  };
}

function renderBuildLedger(){
  const scroll=$("#ledger-scroll"); if(!scroll) return;
  const d=SUMMARY.data;
  const all=(d&&d.builds||[]).map(_ledgerRow);
  const sub=$("#ledger-sub");

  // Filter, then sort. "Open" = anything not fully sold; "Closed" = sold/abandoned.
  let rows=all;
  if(LEDGER.filter==="sold") rows=all.filter(r=>r.closed);
  else if(LEDGER.filter==="active") rows=all.filter(r=>!r.closed);

  const keyOf={
    item:r=>r.name.toLowerCase(), runs:r=>r.runs, sold:r=>r.sold,
    cost:r=>(r.cost==null?-Infinity:r.cost),
    revenue:r=>r.revenue, profit:r=>(r.profit==null?-Infinity:r.profit),
    stage:r=>r.stageRank, done:r=>r.when,
  }[LEDGER.sort]||(r=>r.when);
  rows=rows.slice().sort((a,b)=>{
    const ka=keyOf(a), kb=keyOf(b);
    if(ka<kb) return -1*LEDGER.dir; if(ka>kb) return 1*LEDGER.dir; return 0;
  });

  const isk=v=>v==null?"—":fmtISK(v);
  const pn=v=>v==null?"":(v>0?"pos":(v<0?"neg":""));
  const sign=v=>(v!=null&&v>0)?"+":"";

  // Totals bottom line — the register's whole point. Cost only counts batches
  // that have a known cost; profit sums the booked realized profit across the
  // filtered set (nulls skipped). Margin rides on the summed cost.
  const totCost=rows.reduce((s,r)=>s+(r.cost||0),0);
  const totRev=rows.reduce((s,r)=>s+(r.revenue||0),0);
  const totProfit=rows.reduce((s,r)=>s+(r.profit||0),0);
  const totMargin=totCost>0?totProfit/totCost*100:null;

  if(sub) sub.textContent=all.length
    ? `${all.length.toLocaleString()} batch${all.length===1?"":"es"} tracked · ${rows.length.toLocaleString()} shown`
    : "Every batch you've tracked, and what it earned.";

  if(!all.length){
    scroll.innerHTML=`<div class="ledger-empty">No builds tracked yet. Track a
      blueprint in the <b>Planner</b> and every batch will book its cost, revenue
      and profit here as it moves from build to sale.</div>`;
    return;
  }

  // th(col, label, extraCls) — a sortable header carrying the active-sort caret.
  const th=(col,label,cls)=>{
    const on=LEDGER.sort===col;
    const caret=on?(LEDGER.dir<0?" ▾":" ▴"):"";
    return `<th class="${cls||""} ${on?"sorted":""}" data-sort="${col}">${label}${caret}</th>`;
  };

  const dash=`<span class="lg-dim">—</span>`;
  const body=rows.map(r=>{
    const soldTxt=r.produced?`${r.sold.toLocaleString()}<span class="lg-of">/${r.produced.toLocaleString()}</span>`
                            :dash;
    // Nothing sold → revenue/profit are quiet dashes, not noisy zeros.
    const revTxt=r.sold>0?isk(r.revenue):dash;
    const profTxt=(r.profit==null||r.sold<=0)?dash:`${sign(r.profit)}${isk(r.profit)}`;
    const stageChip=`<span class="lg-stage ${r.stageCls}">${r.stageLabel}${r.abandoned?" ·early":""}</span>`;
    return `<tr data-id="${authEsc(r.id)}">
      <td class="lg-item"><span class="lg-dot ${r.stageCls}"></span>${authEsc(r.name)}</td>
      <td class="num">${r.runs.toLocaleString()}</td>
      <td class="num">${soldTxt}</td>
      <td class="num lg-dimval">${isk(r.cost)}</td>
      <td class="num">${revTxt}</td>
      <td class="num lg-prof ${r.sold>0?pn(r.profit):""}">${profTxt}</td>
      <td class="lg-stage-cell">${stageChip}</td>
      <td class="num lg-when">${r.when?fmtTs(r.when):"—"}</td>
    </tr>`;
  }).join("");

  scroll.innerHTML=`<table class="ledger-table">
    <thead><tr>
      ${th("item","Item","lg-item")}
      ${th("runs","Runs","num")}
      ${th("sold","Sold","num")}
      ${th("cost","Cost","num")}
      ${th("revenue","Revenue","num")}
      ${th("profit","Profit","num")}
      ${th("stage","Stage","")}
      ${th("done","Delivered","num lg-when")}
    </tr></thead>
    <tbody>${body||`<tr><td colspan="8" class="ledger-empty">No batches match this filter.</td></tr>`}</tbody>
    <tfoot><tr class="ledger-total">
      <td class="lg-item">Bottom line</td>
      <td class="num"></td><td class="num"></td>
      <td class="num lg-dimval">${isk(totCost)}</td>
      <td class="num">${isk(totRev)}</td>
      <td class="num lg-prof ${pn(totProfit)}">${sign(totProfit)}${isk(totProfit)}</td>
      <td class="lg-stage-cell">${totMargin!=null?`<span class="lg-margin ${pn(totMargin)}">${totMargin>=0?"+":""}${totMargin.toFixed(1)}%</span>`:""}</td>
      <td class="num lg-when"></td>
    </tr></tfoot>
  </table>`;
}

// ── Ledger wiring — sort headers, filter chips, row → peek, close paths ────────
(function wireBuildLedger(){
  const modal=$("#buildLedgerModal"); if(!modal) return;
  const close=$("#ledger-close"); if(close) close.onclick=closeBuildLedger;
  // Backdrop click closes; a click inside the box doesn't.
  modal.addEventListener("click", e=>{ if(e.target.id==="buildLedgerModal") closeBuildLedger(); });
  document.addEventListener("keydown", e=>{
    if(e.key==="Escape" && !modal.classList.contains("hidden")) closeBuildLedger();
  });
  // Sortable headers: click toggles direction on the active column, else selects
  // it (defaulting to descending — biggest/newest first, what you usually want).
  const scroll=$("#ledger-scroll");
  if(scroll) scroll.addEventListener("click", e=>{
    const th=e.target.closest&&e.target.closest("th[data-sort]");
    if(th){
      const col=th.dataset.sort;
      if(LEDGER.sort===col) LEDGER.dir*=-1; else { LEDGER.sort=col; LEDGER.dir=(col==="item")?1:-1; }
      renderBuildLedger(); return;
    }
    const tr=e.target.closest&&e.target.closest("tbody tr[data-id]");
    if(tr && typeof openBuildPeek==="function"){ closeBuildLedger(); openBuildPeek(tr.dataset.id); }
  });
  const filters=$("#ledger-filters");
  if(filters) filters.addEventListener("click", e=>{
    const btn=e.target.closest&&e.target.closest(".ledger-filter");
    if(!btn) return;
    LEDGER.filter=btn.dataset.filter;
    filters.querySelectorAll(".ledger-filter").forEach(b=>b.classList.toggle("active", b===btn));
    renderBuildLedger();
  });
})();
