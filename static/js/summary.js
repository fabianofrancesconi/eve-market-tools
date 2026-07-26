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
  </div>`;
}
