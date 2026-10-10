// Industry → Statistics. The Tracker answers "where is each batch now?"; this
// answers "what have I sold, when, and which blueprints actually pay?". It reads
// /api/ind/stats, which replays the same wallet money the Tracker books (live
// builds exactly, archived builds pinned to their frozen totals), so the
// all-time figures here always equal the Tracker's realized profit.
//
// Layout: a range filter (30d · 90d · 1y · All) that drives everything, a KPI
// strip, a sales-over-time bar chart (metric × period toggles), a margin-by-
// blueprint ranking, and the per-blueprint table that backs both charts.

let STATS = { data:null, loading:false, range:"all", period:"week",
              metric:"blueprints", marginView:"pct", sort:"profit", dir:-1, seq:0, sig:null };
let _statsTimeChart=null, _statsMarginChart=null, _statsChartRetry=0;

const _STATS_RANGES=[["30","30d"],["90","90d"],["365","1y"],["all","All"]];
const _STATS_PERIODS=[["day","Day"],["week","Week"],["month","Month"]];
// The by-blueprint ranking reads either as a margin (%) or as ISK profit — a
// tiny item can top the % list while earning almost nothing, so both matter.
const _STATS_MARGIN_VIEWS={
  pct:{label:"%", title:"Margin by blueprint", note:"profit ÷ what the sold units cost to build",
       val:p=>p.margin, fmt:v=>_statsPct(v), axis:v=>`${Math.round(v)}%`},
  isk:{label:"ISK", title:"Profit by blueprint", note:"realized profit, after sales tax and build cost",
       val:p=>p.profit, fmt:v=>`${v>0?"+":""}${fmtISK(v)}`, axis:v=>fmtISK(v)},
};
// Each over-time metric: label, how to read it off a bucket, and its formatter.
const _STATS_METRICS={
  blueprints:{label:"Blueprints", title:"Different blueprints sold",
              val:b=>b.blueprints, fmt:v=>Math.round(v).toLocaleString()},
  profit:{label:"Profit", title:"Realized profit", val:b=>b.profit, fmt:v=>fmtISK(v)+" ISK"},
  revenue:{label:"Revenue", title:"Revenue (after sales tax)", val:b=>b.revenue, fmt:v=>fmtISK(v)+" ISK"},
  units:{label:"Units", title:"Units sold", val:b=>b.units, fmt:v=>Math.round(v).toLocaleString()},
};
// Chart ink, mirrored from the :root tokens (ApexCharts needs literal colours).
const _STATS_INK={bar:"#4fc3f7", pos:"#66bb6a", neg:"#e05555",
                  axis:"#5a7a95", grid:"#1f3044", fg:"#c8d8e8"};

function loadStats(){
  if(!AUTH.loggedIn) return;
  const seq=++STATS.seq;
  STATS.loading=!STATS.data;
  if(STATS.loading) renderStats();
  fetch(`/api/ind/stats?range=${encodeURIComponent(STATS.range)}`).then(r=>r.json()).then(res=>{
    if(seq!==STATS.seq) return;          // a newer range was picked meanwhile
    const next=(res && !res.error) ? res : null;
    // Background sweeps re-pull every few minutes; skip the rebuild (and the
    // chart redraw under the user's cursor) when nothing actually moved.
    const sig=JSON.stringify(next);
    const wasLoading=STATS.loading;
    STATS.loading=false;
    if(!wasLoading && sig===STATS.sig && $("#stats-body .stats-head")) return;
    STATS.sig=sig;
    STATS.data=next;
    renderStats();
  }).catch(()=>{ if(seq===STATS.seq){ STATS.loading=false; renderStats(); } });
}

function _statsDays(d){
  if(d==null) return "—";
  if(d<1) return `${Math.max(1,Math.round(d*24))}h`;
  return `${d<10?d.toFixed(1):Math.round(d)}d`;
}
function _statsPct(m){ return m==null?"—":`${m>=0?"+":""}${m.toFixed(1)}%`; }
function _statsPn(v){ return v==null?"":(v>0?"pos":(v<0?"neg":"")); }
function _statsSeg(group, opts, cur){
  return `<div class="stats-seg" data-group="${group}">${opts.map(([k,l])=>
    `<button class="stats-seg-btn${k===cur?" active":""}" data-k="${k}">${l}</button>`).join("")}</div>`;
}

function renderStats(){
  const body=$("#stats-body"); if(!body) return;
  _statsDestroyCharts();
  const head=`<div class="stats-head">
      <div class="stats-head-title">Sales statistics
        <span class="stats-head-sub">from your tracked builds' wallet sales</span></div>
      ${_statsSeg("range", _STATS_RANGES, STATS.range)}
    </div>`;
  if(STATS.loading){ body.innerHTML=head+`<div class="sum-loading">Loading statistics…</div>`; return; }
  const d=STATS.data;
  if(!d){ body.innerHTML=head+`<div class="sum-empty-note">Couldn't load statistics.</div>`; return; }
  const t=d.totals||{};
  if(!t.blueprints){
    body.innerHTML=head+`<div class="sum-empty-note">${d.tracked_builds
      ? (STATS.range==="all" ? "None of your tracked builds has sold anything yet." :
         "Nothing sold in this window — try a longer range.")
      : "No tracked builds yet. Track a blueprint in the <b>Planner</b>; once it sells, its history shows up here."}</div>`;
    return;
  }
  const read=(label,val,cls,note)=>`<div class="sum-read">
    <div class="sum-read-label">${label}</div>
    <div class="sum-read-val ${cls||""}">${val}</div>
    ${note?`<div class="sum-read-note">${note}</div>`:""}</div>`;
  const m=_STATS_METRICS[STATS.metric]||_STATS_METRICS.blueprints;
  body.innerHTML=head+`<div class="sum-strip stats-strip">
      ${read("Blueprints sold", t.blueprints.toLocaleString(), "", `${(t.batches||0).toLocaleString()} batch${t.batches===1?"":"es"}`)}
      ${read("Units sold", fmtNum(t.units))}
      ${read("Revenue", fmtISK(t.revenue), "dim", "after sales tax")}
      ${read("Profit", fmtISK(t.profit), _statsPn(t.profit))}
      ${read("Margin", _statsPct(t.margin), _statsPn(t.margin), "profit ÷ build cost")}
    </div>
    <section class="stats-card">
      <div class="stats-card-head">
        <h3 id="stats-time-title">${m.title}</h3>
        <div class="stats-card-ctrls">
          ${_statsSeg("metric", Object.entries(_STATS_METRICS).map(([k,v])=>[k,v.label]), STATS.metric)}
          ${_statsSeg("period", _STATS_PERIODS, STATS.period)}
        </div>
      </div>
      <div id="stats-time-chart" class="stats-chart"></div>
    </section>
    <section class="stats-card">
      <div class="stats-card-head"><h3 id="stats-margin-title">${_statsMarginView().title}</h3>
        <div class="stats-card-ctrls">
          <span id="stats-margin-note" class="stats-card-note">${_statsMarginView().note}</span>
          ${_statsSeg("marginView", Object.entries(_STATS_MARGIN_VIEWS).map(([k,v])=>[k,v.label]), STATS.marginView)}
        </div></div>
      <div id="stats-margin-chart" class="stats-chart"></div>
    </section>
    <section class="stats-card">
      <div class="stats-card-head"><h3>By blueprint</h3></div>
      <div id="stats-table" class="stats-table-wrap">${_statsTableHtml(d.products||[])}</div>
    </section>`;
  _statsChartRetry=0;
  _statsDrawCharts();
}

function _statsDestroyCharts(){
  if(_statsTimeChart){ _statsTimeChart.destroy(); _statsTimeChart=null; }
  if(_statsMarginChart){ _statsMarginChart.destroy(); _statsMarginChart=null; }
}

// ApexCharts lays out against the container width; a zero-width (hidden / not
// yet laid out) container gives NaN geometry, so wait a few frames for layout.
function _statsDrawCharts(){
  const tc=$("#stats-time-chart"), mc=$("#stats-margin-chart");
  if(!tc || !mc) return;
  if(typeof ApexCharts==="undefined"){
    tc.innerHTML=mc.innerHTML=`<div class="wallet-chart-none">Chart unavailable (no internet) — the table below has every figure.</div>`;
    return;
  }
  if(!tc.clientWidth){
    if(_statsChartRetry++<30) requestAnimationFrame(_statsDrawCharts);
    return;
  }
  _statsDrawTimeChart(tc);
  _statsDrawMarginChart(mc);
}

function _statsBucketLabel(start){
  const d=new Date(start*1000), o={timeZone:"UTC"};
  if(STATS.period==="month") return d.toLocaleDateString(undefined,{...o, month:"long", year:"numeric"});
  const day=d.toLocaleDateString(undefined,{...o, day:"numeric", month:"short", year:"numeric"});
  return STATS.period==="week" ? `Week of ${day}` : day;
}

function _statsDrawTimeChart(el){
  const m=_STATS_METRICS[STATS.metric]||_STATS_METRICS.blueprints;
  const rows=((STATS.data.timeline||{})[STATS.period])||[];
  const signed=STATS.metric==="profit";
  const opts={
    chart:{type:"bar", height:240, background:"transparent", fontFamily:"inherit",
      toolbar:{show:false}, zoom:{enabled:false}, animations:{enabled:false}},
    theme:{mode:"dark"},
    series:[{name:m.label, data:rows.map(b=>[b.start*1000, m.val(b)||0])}],
    colors:[signed?_STATS_INK.pos:_STATS_INK.bar],
    plotOptions:{bar:{columnWidth:rows.length>40?"85%":"60%", borderRadius:3,
      borderRadiusApplication:"end",
      colors:{ranges:signed?[{from:-1e18, to:-1e-9, color:_STATS_INK.neg}]:[]}}},
    xaxis:{type:"datetime", labels:{datetimeUTC:true, style:{colors:_STATS_INK.axis, fontSize:"10px"}},
      axisBorder:{color:_STATS_INK.grid}, axisTicks:{color:_STATS_INK.grid}},
    yaxis:{forceNiceScale:true, labels:{style:{colors:_STATS_INK.axis, fontSize:"10px"},
      formatter:v=>STATS.metric==="blueprints"||STATS.metric==="units"
        ? Math.round(v).toLocaleString() : fmtISK(v)}},
    grid:{borderColor:_STATS_INK.grid, strokeDashArray:3},
    dataLabels:{enabled:false}, legend:{show:false},
    tooltip:{theme:"dark", custom:({dataPointIndex})=>{
      const b=rows[dataPointIndex]; if(!b) return "";
      const top=(b.products||[]).filter(p=>p.units>0).slice(0,8);
      const more=(b.products||[]).filter(p=>p.units>0).length-top.length;
      return `<div class="stats-tip"><div class="stats-tip-h">${_statsBucketLabel(b.start)}</div>
        <div class="stats-tip-v">${m.label}: <b>${m.fmt(m.val(b)||0)}</b></div>
        ${top.length?`<table>${top.map(p=>`<tr><td>${authEsc(p.name)}</td>
          <td class="num">${fmtNum(p.units)}×</td>
          <td class="num ${_statsPn(p.profit)}">${fmtISK(p.profit)}</td></tr>`).join("")}</table>`
          :`<div class="stats-tip-none">No sales</div>`}
        ${more>0?`<div class="stats-tip-none">+${more} more</div>`:""}</div>`;
    }},
  };
  _statsTimeChart=new ApexCharts(el, opts);
  _statsTimeChart.render();
}

function _statsMarginView(){ return _STATS_MARGIN_VIEWS[STATS.marginView]||_STATS_MARGIN_VIEWS.pct; }

function _statsDrawMarginChart(el){
  const v=_statsMarginView();
  const rows=(STATS.data.products||[]).filter(p=>v.val(p)!=null)
    .sort((a,b)=>v.val(b)-v.val(a));
  if(!rows.length){ el.innerHTML=`<div class="wallet-chart-none">No cost basis recorded for these sales.</div>`; return; }
  const opts={
    chart:{type:"bar", height:Math.max(140, rows.length*30+50), background:"transparent",
      fontFamily:"inherit", toolbar:{show:false}, zoom:{enabled:false}, animations:{enabled:false}},
    theme:{mode:"dark"},
    series:[{name:STATS.marginView==="isk"?"Profit":"Margin",
      data:rows.map(p=>STATS.marginView==="isk"?Math.round(p.profit):+p.margin.toFixed(1))}],
    colors:[_STATS_INK.pos],
    plotOptions:{bar:{horizontal:true, barHeight:"62%", borderRadius:3,
      borderRadiusApplication:"end", dataLabels:{position:"top"},
      colors:{ranges:[{from:-1e18, to:-1e-9, color:_STATS_INK.neg}]}}},
    xaxis:{categories:rows.map(p=>p.name),
      labels:{style:{colors:_STATS_INK.axis, fontSize:"10px"}, formatter:v.axis},
      axisBorder:{color:_STATS_INK.grid}, axisTicks:{color:_STATS_INK.grid}},
    yaxis:{labels:{maxWidth:240, style:{colors:_STATS_INK.fg, fontSize:"11.5px"}}},
    grid:{borderColor:_STATS_INK.grid, strokeDashArray:3,
      xaxis:{lines:{show:true}}, yaxis:{lines:{show:false}}},
    dataLabels:{enabled:true, offsetX:STATS.marginView==="isk"?30:26, formatter:v.fmt,
      style:{fontSize:"10.5px", fontWeight:600, colors:[_STATS_INK.fg]}},
    legend:{show:false},
    tooltip:{theme:"dark", custom:({dataPointIndex})=>{
      const p=rows[dataPointIndex]; if(!p) return "";
      return `<div class="stats-tip"><div class="stats-tip-h">${authEsc(p.name)}</div>
        <table>
          <tr><td>Margin</td><td class="num ${_statsPn(p.margin)}">${_statsPct(p.margin)}</td></tr>
          <tr><td>Profit</td><td class="num ${_statsPn(p.profit)}">${fmtISK(p.profit)}</td></tr>
          <tr><td>Revenue</td><td class="num">${fmtISK(p.revenue)}</td></tr>
          <tr><td>Build cost</td><td class="num">${fmtISK(p.cost+(p.writeoff||0))}</td></tr>
          <tr><td>Units · batches</td><td class="num">${fmtNum(p.units)} · ${p.batches}</td></tr>
        </table></div>`;
    }},
  };
  _statsMarginChart=new ApexCharts(el, opts);
  _statsMarginChart.render();
}

// The per-blueprint table: every figure both charts draw from, sortable.
function _statsTableHtml(products){
  const key={
    name:p=>p.name.toLowerCase(), batches:p=>p.batches, units:p=>p.units,
    revenue:p=>p.revenue, cost:p=>p.cost+(p.writeoff||0), profit:p=>p.profit,
    margin:p=>p.margin==null?-Infinity:p.margin,
    ppu:p=>p.profit_per_unit==null?-Infinity:p.profit_per_unit,
    wait:p=>p.avg_days_to_sell==null?Infinity:p.avg_days_to_sell,
    last:p=>p.last_sale||0,
  }[STATS.sort]||(p=>p.profit);
  const rows=products.slice().sort((a,b)=>{
    const ka=key(a), kb=key(b);
    return ka<kb?-STATS.dir:(ka>kb?STATS.dir:0);
  });
  const th=(col,label,cls)=>{
    const on=STATS.sort===col;
    return `<th class="${cls||""} ${on?"sorted":""}" data-sort="${col}">${label}${on?(STATS.dir<0?" ▾":" ▴"):""}</th>`;
  };
  const sign=v=>(v!=null&&v>0)?"+":"";
  return `<table class="ledger-table stats-table">
    <thead><tr>
      ${th("name","Blueprint","lg-item")}${th("batches","Batches","num")}${th("units","Sold","num")}
      ${th("revenue","Revenue","num")}${th("cost","Cost","num")}${th("profit","Profit","num")}
      ${th("margin","Margin","num")}${th("ppu","Profit / unit","num")}
      ${th("wait","Avg sell time","num")}${th("last","Last sale","num")}
    </tr></thead>
    <tbody>${rows.map(p=>`<tr>
      <td class="lg-item">${authEsc(p.name)}</td>
      <td class="num">${p.batches}</td>
      <td class="num">${fmtNum(p.units)}</td>
      <td class="num">${fmtISK(p.revenue)}</td>
      <td class="num lg-dimval">${fmtISK(p.cost+(p.writeoff||0))}</td>
      <td class="num lg-prof ${_statsPn(p.profit)}">${sign(p.profit)}${fmtISK(p.profit)}</td>
      <td class="num lg-prof ${_statsPn(p.margin)}">${_statsPct(p.margin)}</td>
      <td class="num">${p.profit_per_unit==null?"—":fmtISK(p.profit_per_unit)}</td>
      <td class="num" title="Unit-weighted time from delivery to sale">${_statsDays(p.avg_days_to_sell)}</td>
      <td class="num lg-when">${p.last_sale?fmtTs(p.last_sale):"—"}</td>
    </tr>`).join("")}</tbody>
  </table>`;
}

// ── Wiring — range / metric / period toggles and table sorting ───────────────
(function wireStats(){
  const body=$("#stats-body"); if(!body) return;
  body.addEventListener("click", e=>{
    const btn=e.target.closest&&e.target.closest(".stats-seg-btn");
    if(btn){
      const group=btn.parentElement.dataset.group, k=btn.dataset.k;
      if(group==="range"){ if(STATS.range!==k){ STATS.range=k; loadStats(); } return; }
      if(group==="marginView"){
        STATS.marginView=k;
        btn.parentElement.querySelectorAll(".stats-seg-btn").forEach(b=>b.classList.toggle("active", b===btn));
        const mv=_statsMarginView();
        $("#stats-margin-title").textContent=mv.title;
        $("#stats-margin-note").textContent=mv.note;
        if(_statsMarginChart){ _statsMarginChart.destroy(); _statsMarginChart=null; }
        const mc=$("#stats-margin-chart");
        if(mc && STATS.data && typeof ApexCharts!=="undefined") _statsDrawMarginChart(mc);
        return;
      }
      if(group==="metric") STATS.metric=k;
      if(group==="period") STATS.period=k;
      // Only the time chart depends on these — redraw it in place.
      btn.parentElement.querySelectorAll(".stats-seg-btn").forEach(b=>b.classList.toggle("active", b===btn));
      const title=$("#stats-time-title");
      if(title) title.textContent=(_STATS_METRICS[STATS.metric]||_STATS_METRICS.blueprints).title;
      if(_statsTimeChart){ _statsTimeChart.destroy(); _statsTimeChart=null; }
      const tc=$("#stats-time-chart");
      if(tc && STATS.data && typeof ApexCharts!=="undefined") _statsDrawTimeChart(tc);
      return;
    }
    const th=e.target.closest&&e.target.closest(".stats-table th[data-sort]");
    if(th && STATS.data){
      const col=th.dataset.sort;
      if(STATS.sort===col) STATS.dir*=-1; else { STATS.sort=col; STATS.dir=(col==="name"||col==="wait")?1:-1; }
      $("#stats-table").innerHTML=_statsTableHtml(STATS.data.products||[]);
    }
  });
})();
