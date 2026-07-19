// Summary tab — industry portfolio P&L across every tracked build.
//
// It reads /api/ind/summary (the server rolls up realized profit, capital in
// flight and a per-product breakdown), then renders: a KPI header with a
// time filter over realized profit, a "needs action" work queue (built-and-
// unlisted, or an order awaiting a pick), and a per-item breakdown table.
// Clicking a row jumps to the build's card in the Industry tab.

let SUMMARY = { data:null, range:"all", loading:false };

const _SUM_STAGE_LABEL = {planned:"Planned", building:"Building", built:"Built",
                          listed:"Listed", sold:"Sold"};

function loadSummary(){
  if(!AUTH.loggedIn){ return; }
  SUMMARY.loading = !SUMMARY.data;   // only show the spinner on the first load
  renderSummary();
  fetch("/api/ind/summary").then(r=>r.json()).then(res=>{
    SUMMARY.data = res || {builds:[], totals:{}, by_product:[]};
    SUMMARY.loading = false;
    renderSummary();
  }).catch(()=>{ SUMMARY.loading=false; renderSummary(); });
}

// Realized profit within the selected time window, recomputed from raw fills
// (ts/net) and each build's frozen cost basis. "all" uses the server totals.
function _sumRealizedInRange(){
  const d=SUMMARY.data;
  if(!d) return {profit:0, net:0, units:0};
  if(SUMMARY.range==="all"){
    return {profit:(d.totals&&d.totals.realized_profit)||0,
            net:(d.totals&&d.totals.realized_net)||0,
            units:d.builds.reduce((s,b)=>s+((b.realized&&b.realized.units)||0),0)};
  }
  const now=Date.now()/1000;
  const cutoff = now - (SUMMARY.range==="week"?7*86400:30*86400);
  let profit=0, net=0, units=0;
  d.builds.forEach(b=>{
    const sell=b.sell; if(!sell||!sell.fills) return;
    const cpu=sell.cost_per_unit;
    sell.fills.forEach(f=>{
      if((f.ts||0)<cutoff) return;
      net+=f.net||0; units+=f.units||0;
      if(cpu!=null) profit+=(f.net||0)-(f.units||0)*cpu;
    });
  });
  return {profit, net, units};
}

// Builds that need the user to do something next — the cross-build work list.
function _sumNeedsAction(){
  const d=SUMMARY.data; if(!d) return [];
  const out=[];
  d.builds.forEach(b=>{
    if(b.stage==="built"){
      out.push({b, kind:"list", msg:"Built — list it for sale in-game"});
    } else if(b.stage==="listed" && b.sell && b.sell.needs_pick){
      out.push({b, kind:"pick", msg:"Several matching orders — pick which to track"});
    }
  });
  return out;
}

function renderSummary(){
  const body=$("#sum-body");
  if(!body) return;
  if(SUMMARY.loading){ body.innerHTML=`<div class="sum-loading">Loading portfolio…</div>`; return; }
  const d=SUMMARY.data;
  if(!d || !d.builds || !d.builds.length){
    // The tracked-build cards (#ind-builds, rendered by ind.js) sit below this
    // node and stay empty; here we just explain how to fill the Tracker.
    body.innerHTML=`<div class="sum-empty-note">No tracked builds yet. In the
      <b>Planner</b>, find a blueprint and hit <b>＋ Track this build</b> — it'll
      appear here, and once it's built your sell order links automatically so the
      real profit rolls in.</div>`;
    return;
  }
  const isk=v=>v===null||v===undefined?"—":fmtISK(v);
  const pn=v=>v==null?"":(v>0?"pos":(v<0?"neg":""));
  const rr=_sumRealizedInRange();
  const t=d.totals||{};
  const rangeLbl=SUMMARY.range==="week"?"last 7 days":SUMMARY.range==="month"?"last 30 days":"all time";
  // Sum of the projected profit on everything still sellable (listed remainder
  // + built, priced at the frozen ask net of fees) — "ready to realize".
  let ready=0;
  d.builds.forEach(b=>{
    if(b.stage==="sold"||b.stage==="planned"||b.stage==="building") return;
    const cpu=b.cost_per_unit, ask=b.ask;
    if(cpu==null||ask==null) return;
    const sold=(b.realized&&b.realized.units)||0;
    const target=(b.sell&&b.sell.qty_target)||b.units_produced||0;
    const remain=Math.max(0, target-sold);
    const stax=b.sales_tax||0, bfee=b.broker_fee||0;
    ready += remain*(ask*(1-stax-bfee)-cpu);
  });
  // Est. total profit = booked (all-time realized) + still-to-come (ready). The
  // margin is that estimate against the capital it's riding on (cost already
  // sunk into unsold builds + cost basis of what's sold).
  const realizedAll=(t.realized_profit)||0;
  const estTotal=realizedAll+ready;
  const soldCost=d.builds.reduce((s,b)=>s+(((b.realized&&b.realized.units)||0)*(b.cost_per_unit||0)),0);
  const capBase=(t.capital_in_flight||0)+soldCost;
  const margin=capBase>0?estTotal/capBase*100:null;

  const kpis=`<div class="sum-kpis">
    <div class="sum-kpi">
      <div class="sum-kpi-label">Realized profit <span class="sum-range-lbl">· ${rangeLbl}</span></div>
      <div class="sum-kpi-val ${pn(rr.profit)}">${isk(rr.profit)}</div>
      <div class="sum-kpi-sub">${rr.units.toLocaleString()} unit(s) sold · net ${isk(rr.net)}</div>
      <div class="sum-range">
        <button class="sum-range-btn${SUMMARY.range==="week"?" on":""}" data-range="week">7d</button>
        <button class="sum-range-btn${SUMMARY.range==="month"?" on":""}" data-range="month">30d</button>
        <button class="sum-range-btn${SUMMARY.range==="all"?" on":""}" data-range="all">All</button>
      </div>
    </div>
    <div class="sum-kpi">
      <div class="sum-kpi-label">Ready to realize</div>
      <div class="sum-kpi-val ${pn(ready)}">${isk(ready)}</div>
      <div class="sum-kpi-sub">unsold stock projected at frozen ask, net of fees</div>
    </div>
    <div class="sum-kpi">
      <div class="sum-kpi-label">Capital in flight</div>
      <div class="sum-kpi-val">${isk(t.capital_in_flight)}</div>
      <div class="sum-kpi-sub">frozen cost of unsold builds</div>
    </div>
    <div class="sum-kpi sum-kpi-accent">
      <div class="sum-kpi-label">Est. total profit</div>
      <div class="sum-kpi-val ${pn(estTotal)}">${isk(estTotal)}</div>
      <div class="sum-kpi-sub">realized + ready${margin!=null?` · <b class="${pn(margin)}">${margin>=0?"+":""}${margin.toFixed(1)}%</b> on capital`:""}</div>
    </div>
  </div>`;

  // "Where your capital sits" — a stacked bar of frozen cost by pipeline stage,
  // so the big Capital-in-flight number becomes legible at a glance.
  const capByStage={building:0,built:0,listed:0,planned:0};
  d.builds.forEach(b=>{
    if(!(b.stage in capByStage)) return;
    const cpu=b.cost_per_unit;
    const sold=(b.realized&&b.realized.units)||0;
    let cost=b.batch_cost||0;
    if(cpu!=null&&sold) cost=Math.max(0, cost-sold*cpu);   // unsold remainder only
    capByStage[b.stage]+=cost;
  });
  const capTot=Object.values(capByStage).reduce((s,v)=>s+v,0);
  const capOrder=["planned","building","built","listed"];
  const capBar=capTot>0?`<div class="sum-capbar-wrap">
    <div class="sum-capbar">${capOrder.filter(s=>capByStage[s]>0).map(s=>
      `<span class="sum-capseg stage-${s}" style="width:${(capByStage[s]/capTot*100).toFixed(2)}%"
        title="${_SUM_STAGE_LABEL[s]}: ${isk(capByStage[s])}"></span>`).join("")}</div>
    <div class="sum-caplegend">${capOrder.filter(s=>capByStage[s]>0).map(s=>
      `<span class="sum-caplbl stage-${s}">${_SUM_STAGE_LABEL[s]} <b>${isk(capByStage[s])}</b></span>`).join("")}</div>
  </div>`:"";

  // Needs-action queue.
  const acts=_sumNeedsAction();
  const queue=acts.length?`<div class="sum-section">
    <div class="sum-section-head">Needs action <span class="chip-count">(${acts.length})</span></div>
    <div class="sum-queue">${acts.map(a=>`
      <div class="sum-queue-row" data-build="${a.b.id}">
        <span class="sum-q-badge ${a.kind}">${a.kind==="list"?"List it":"Pick order"}</span>
        <span class="sum-q-name">${a.b.product_name||"?"}</span>
        <span class="sum-q-runs">${(a.b.runs||1).toLocaleString()} run(s)</span>
        <span class="sum-q-msg">${a.msg}</span>
        <button class="sum-q-open" data-build="${a.b.id}">Open ▸</button>
      </div>`).join("")}</div>
  </div>`:"";

  // Pipeline stage counts (a compact strip).
  const counts={planned:0,building:0,built:0,listed:0,sold:0};
  d.builds.forEach(b=>{ counts[b.stage]=(counts[b.stage]||0)+1; });
  const strip=`<div class="sum-stagestrip">${["planned","building","built","listed","sold"].map(s=>
    `<span class="sum-stagepill stage-${s}"><b>${counts[s]}</b> ${_SUM_STAGE_LABEL[s]}</span>`).join("")}</div>`;

  // Per-item breakdown, most profitable first.
  const rows=(d.by_product||[]).map(p=>`
    <tr>
      <td>${p.name}</td>
      <td class="num">${p.builds.toLocaleString()}</td>
      <td class="num">${p.units_sold.toLocaleString()}</td>
      <td class="num ${pn(p.realized_profit)}">${isk(p.realized_profit)}</td>
    </tr>`).join("");
  const breakdown=`<div class="sum-section">
    <div class="sum-section-head">By item — realized profit</div>
    <table class="sum-table"><thead><tr>
      <th>Item</th><th class="num">Builds</th><th class="num">Units sold</th>
      <th class="num">Realized profit</th></tr></thead>
      <tbody>${rows||`<tr><td colspan="4" class="sum-none">Nothing sold yet.</td></tr>`}</tbody></table>
  </div>`;

  body.innerHTML=`<div class="sum-wrap">${kpis}${capBar}${strip}${queue}${breakdown}</div>`;
  _wireSummary(body);
}

function _wireSummary(body){
  body.querySelectorAll(".sum-range-btn").forEach(btn=>{
    btn.onclick=()=>{ SUMMARY.range=btn.dataset.range; renderSummary(); };
  });
  const openBuild=id=>{
    if(typeof switchTab==="function") switchTab("ind");
    if(typeof openTrackedBuild==="function") setTimeout(()=>openTrackedBuild(id), 60);
  };
  body.querySelectorAll(".sum-q-open").forEach(btn=>{
    btn.onclick=()=>openBuild(btn.dataset.build);
  });
  body.querySelectorAll(".sum-queue-row").forEach(row=>{
    row.onclick=ev=>{ if(!ev.target.closest("button")) openBuild(row.dataset.build); };
  });
}
