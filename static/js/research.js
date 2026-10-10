// Industry → Research. Which blueprints have I researched, what's in the lab
// right now, and what is each level actually worth? Reads /api/ind/research:
// the server keeps an ME/TE job ledger (outliving ESI's 90-day job window) and
// each blueprint's last-known levels, and prices every level off the SDE bill
// of materials at Jita (ME: 1% of base materials per run per level; TE: 2% of
// base build time per level).
//
// Layout: KPI strip · jobs in the lab (progress + what they'll unlock) · a
// "value of research" chart (ISK/run gained vs still on the table) · the owned-
// blueprint table · the completed-job history.

let RESEARCH = { data:null, loading:false, seq:0, sig:null, sort:"left", dir:-1 };
let _researchChart=null, _researchChartRetry=0;

// The blueprint table's sort survives a reload (server pref ``ind_research_view``).
function researchApplyStored(v){
  if(!v || typeof v!=="object") return;
  if(typeof v.sort==="string") RESEARCH.sort=v.sort;
  if(v.dir===1 || v.dir===-1) RESEARCH.dir=v.dir;
  const t=$("#research-bp-table");
  if(t && RESEARCH.data) t.innerHTML=_researchBpTableHtml(RESEARCH.data.blueprints||[]);
}

function loadResearch(){
  if(!AUTH.loggedIn) return;
  const seq=++RESEARCH.seq;
  RESEARCH.loading=!RESEARCH.data;
  if(RESEARCH.loading) renderResearch();
  fetch("/api/ind/research").then(r=>r.json()).then(res=>{
    if(seq!==RESEARCH.seq) return;
    const next=(res && !res.error) ? res : null;
    const sig=JSON.stringify(next);
    const wasLoading=RESEARCH.loading;
    RESEARCH.loading=false;
    _researchUpdateCount(next);
    // Background sweeps re-pull; only rebuild when something moved. Progress
    // bars still tick because the server recomputes them from the clock, so a
    // running job changes the signature each sweep.
    if(!wasLoading && sig===RESEARCH.sig && $("#research-body .stats-head")) return;
    RESEARCH.sig=sig;
    RESEARCH.data=next;
    renderResearch();
  }).catch(()=>{ if(seq===RESEARCH.seq){ RESEARCH.loading=false; renderResearch(); } });
}

// The Research button's (N) badge = jobs currently in the lab.
function _researchUpdateCount(d){
  const badge=$("#ind-research-count"); if(!badge) return;
  const n=d&&d.totals?d.totals.in_progress:0;
  badge.textContent=n?`(${n})`:"";
  badge.classList.toggle("hidden", !n);
}

function _resDur(sec){
  if(sec==null) return "—";
  sec=Math.max(0, Math.round(sec));
  const d=Math.floor(sec/86400), h=Math.floor(sec%86400/3600), m=Math.floor(sec%3600/60);
  if(d) return h?`${d}d ${h}h`:`${d}d`;
  if(h) return m?`${h}h ${m}m`:`${h}h`;
  return `${Math.max(m,sec?1:0)}m`;
}
function _resIcon(tid, sz){
  return tid?`<img class="res-icon" src="https://images.evetech.net/types/${tid}/bp?size=${sz||32}" alt="" loading="lazy">`:"";
}
function _resDate(ts){
  if(!ts) return "—";
  return new Date(ts*1000).toLocaleDateString(undefined,{day:"numeric", month:"short"});
}
// Level pips: filled up to `cur`, the job's gain highlighted, rest empty.
function _resPips(cur, max, step, gainTo){
  if(cur==null) return `<span class="res-dim">?</span>`;
  let h=`<span class="res-pips" title="${cur}${step===2?"%":""} of ${max}${step===2?"%":""}">`;
  for(let v=step; v<=max; v+=step){
    const cls=v<=cur?"on":((gainTo!=null && v<=gainTo)?"gain":"");
    h+=`<i class="${cls}"></i>`;
  }
  return h+`</span>`;
}
function _resLevel(act, frm, to){
  const tag=act==="TE"?"TE":"ME";
  if(frm==null) return `${tag} <span class="res-dim">+?</span>`;
  return `${tag} ${frm}→<b>${to}</b>`;
}
function _resBenefit(j){
  if(j.activity==="ME"){
    if(j.isk_per_run==null) return `<span class="res-dim">no price data</span>`;
    let s=`saves <b>${fmtISK(j.isk_per_run)}</b>/run`;
    if(j.pct_of_materials!=null) s+=` <span class="res-dim">(−${j.pct_of_materials.toFixed(1)}% materials)</span>`;
    return s;
  }
  return j.seconds_per_run!=null?`<b>−${_resDur(j.seconds_per_run)}</b>/run build time`:`<span class="res-dim">—</span>`;
}
function _resPayback(j){
  if(j.payback_runs==null) return "—";
  const n=j.payback_runs;
  return n<1?"< 1 run":`${n<10?n.toFixed(1):Math.ceil(n).toLocaleString()} runs`;
}

function renderResearch(){
  const body=$("#research-body"); if(!body) return;
  if(_researchChart){ _researchChart.destroy(); _researchChart=null; }
  const head=`<div class="stats-head"><div class="stats-head-title">Blueprint research
      <span class="stats-head-sub">ME / TE jobs and what each level is worth</span></div></div>`;
  if(RESEARCH.loading){ body.innerHTML=head+`<div class="sum-loading">Loading research…</div>`; return; }
  const d=RESEARCH.data;
  if(!d){ body.innerHTML=head+`<div class="sum-empty-note">Couldn't load research data.</div>`; return; }
  const t=d.totals||{};
  if(!t.in_progress && !t.completed && !(d.blueprints||[]).length){
    body.innerHTML=head+`<div class="sum-empty-note">${d.levels_known
      ? "No blueprint originals or research jobs found yet. Start an ME or TE research job in game and it'll show up here on the next sync."
      : "Waiting for the first character sync — your blueprints and research jobs will appear here shortly."}</div>`;
    return;
  }
  const read=(label,val,cls,note)=>`<div class="sum-read">
    <div class="sum-read-label">${label}</div>
    <div class="sum-read-val ${cls||""}">${val}</div>
    ${note?`<div class="sum-read-note">${note}</div>`:""}</div>`;
  const lvl=[t.me_levels?`ME +${t.me_levels}`:"", t.te_levels?`TE +${t.te_levels}%`:""].filter(Boolean).join(" · ")||"—";
  body.innerHTML=head+`<div class="sum-strip stats-strip">
      ${read("In the lab", (t.in_progress||0).toLocaleString(), "", `${(t.completed||0).toLocaleString()} job${t.completed===1?"":"s"} done`)}
      ${read("Levels gained", lvl, "")}
      ${read("Lab time", _resDur(t.lab_seconds), "dim", "on completed jobs")}
      ${read("Research fees", fmtISK(t.fees), "dim")}
      ${read("Saved per run", fmtISK(t.isk_per_run_gained), t.isk_per_run_gained>0?"pos":"", "from your ME research")}
      ${read("ME saved on builds", fmtISK(t.realized_savings), t.realized_savings>0?"pos":"", "vs ME0, tracked builds")}
    </div>
    ${_researchLabHtml(d.active||[])}
    <section class="stats-card">
      <div class="stats-card-head"><h3>Value of research by blueprint</h3>
        <span class="stats-card-note">material ISK saved per run — gained by your research vs still left up to ME 10</span></div>
      <div id="research-chart" class="stats-chart"></div>
    </section>
    <section class="stats-card">
      <div class="stats-card-head"><h3>Your blueprints</h3>
        <span class="stats-card-note">${(t.bpos_owned||0).toLocaleString()} originals · ${(t.bpos_maxed||0).toLocaleString()} fully researched</span></div>
      <div id="research-bp-table" class="stats-table-wrap">${_researchBpTableHtml(d.blueprints||[])}</div>
    </section>
    ${_researchHistoryHtml(d.history||[])}`;
  _researchChartRetry=0;
  _researchDrawChart();
}

function _researchLabHtml(active){
  if(!active.length) return `<section class="stats-card"><div class="stats-card-head"><h3>In the lab</h3></div>
    <div class="res-none">Nothing researching right now.</div></section>`;
  const now=Date.now()/1000;
  const cards=active.map(j=>{
    const ready=j.status==="ready", paused=j.status==="paused";
    const pct=Math.round((j.progress||0)*100);
    const left=j.end?Math.max(0, j.end-now):null;
    const max=j.activity==="TE"?20:10, step=j.activity==="TE"?2:1;
    const eta=ready?`<span class="res-ready">Ready — deliver to apply</span>`
      :(paused?`<span class="res-dim">Paused</span>`:`${_resDur(left)} left <span class="res-dim">· ${_resDate(j.end)}</span>`);
    return `<div class="res-job${ready?" ready":""}">
      <div class="res-job-top">${_resIcon(j.blueprint_type_id, 64)}
        <div class="res-job-title"><div class="res-job-name">${authEsc(j.blueprint_name)}</div>
          <div class="res-job-sub">${authEsc(j.character_name||"")}${j.location?` · ${authEsc(j.location)}`:""}</div></div>
        <span class="res-act ${j.activity.toLowerCase()}">${_resLevel(j.activity, j.from, j.to)}</span>
      </div>
      <div class="res-job-pips">${_resPips(j.from, max, step, j.to)}</div>
      <div class="res-prog"><div class="res-prog-fill" style="width:${pct}%"></div></div>
      <div class="res-job-eta"><span>${eta}</span><span class="res-dim">${pct}%</span></div>
      <div class="res-job-benefit">${_resBenefit(j)}${j.payback_runs!=null?` <span class="res-dim">· fee ${fmtISK(j.cost)} pays back in ${_resPayback(j)}</span>`:(j.cost?` <span class="res-dim">· fee ${fmtISK(j.cost)}</span>`:"")}</div>
    </div>`;
  }).join("");
  return `<section class="stats-card"><div class="stats-card-head"><h3>In the lab</h3>
    <span class="stats-card-note">${active.length} job${active.length===1?"":"s"}</span></div>
    <div class="res-jobs">${cards}</div></section>`;
}

function _researchDrawChart(){
  const el=$("#research-chart"); if(!el) return;
  const rows=(RESEARCH.data.blueprints||[])
    .filter(b=>(b.isk_per_run_gained||0)+(b.isk_per_run_left||0)>0)
    .sort((a,b)=>((b.isk_per_run_gained||0)+(b.isk_per_run_left||0))-((a.isk_per_run_gained||0)+(a.isk_per_run_left||0)))
    .slice(0,15);
  if(!rows.length){ el.innerHTML=`<div class="wallet-chart-none">No priced blueprints to compare yet.</div>`; return; }
  if(typeof ApexCharts==="undefined"){ el.innerHTML=`<div class="wallet-chart-none">Chart unavailable (no internet) — the table below has every figure.</div>`; return; }
  if(!el.clientWidth){ if(_researchChartRetry++<30) requestAnimationFrame(_researchDrawChart); return; }
  const opts={
    chart:{type:"bar", stacked:true, height:Math.max(140, rows.length*30+70), background:"transparent",
      fontFamily:"inherit", toolbar:{show:false}, zoom:{enabled:false}, animations:{enabled:false}},
    theme:{mode:"dark"},
    series:[{name:"Gained by your research", data:rows.map(b=>Math.round(b.isk_per_run_gained||0))},
            {name:"Left on the table (to ME 10)", data:rows.map(b=>Math.round(b.isk_per_run_left||0))}],
    colors:[_STATS_INK.pos, "#3d5a70"],
    stroke:{width:2, colors:["#162130"]},
    plotOptions:{bar:{horizontal:true, barHeight:"62%", borderRadius:3, borderRadiusApplication:"end",
      borderRadiusWhenStacked:"last"}},
    xaxis:{categories:rows.map(b=>b.name.replace(/ Blueprint$/,"")),
      labels:{style:{colors:_STATS_INK.axis, fontSize:"10px"}, formatter:v=>fmtISK(v)},
      axisBorder:{color:_STATS_INK.grid}, axisTicks:{color:_STATS_INK.grid}},
    yaxis:{labels:{maxWidth:240, style:{colors:_STATS_INK.fg, fontSize:"11.5px"}}},
    grid:{borderColor:_STATS_INK.grid, strokeDashArray:3, xaxis:{lines:{show:true}}, yaxis:{lines:{show:false}}},
    dataLabels:{enabled:false},
    legend:{show:true, position:"top", horizontalAlign:"left", fontSize:"11px",
      labels:{colors:_STATS_INK.fg}, markers:{size:5}},
    tooltip:{theme:"dark", custom:({dataPointIndex})=>{
      const b=rows[dataPointIndex]; if(!b) return "";
      return `<div class="stats-tip"><div class="stats-tip-h">${authEsc(b.name)}</div><table>
        <tr><td>Now</td><td class="num">ME ${b.me??"?"} · TE ${b.te??"?"}</td></tr>
        <tr><td>Gained / run</td><td class="num pos">${fmtISK(b.isk_per_run_gained||0)}</td></tr>
        <tr><td>Left to ME 10 / run</td><td class="num">${b.isk_per_run_left==null?"—":fmtISK(b.isk_per_run_left)}</td></tr>
        <tr><td>Research fees</td><td class="num">${fmtISK(b.fees||0)}</td></tr>
        ${b.realized_savings?`<tr><td>ME saved on builds</td><td class="num pos">${fmtISK(b.realized_savings)}</td></tr>`:""}
      </table></div>`;
    }},
  };
  _researchChart=new ApexCharts(el, opts);
  _researchChart.render();
}

function _researchBpTableHtml(rows){
  const key={
    name:b=>b.name.toLowerCase(), me:b=>b.me??-1, te:b=>b.te??-1, jobs:b=>b.jobs,
    fees:b=>b.fees, gained:b=>b.isk_per_run_gained||0, left:b=>b.isk_per_run_left??-1,
    saved:b=>b.realized_savings||0,
  }[RESEARCH.sort]||(b=>b.isk_per_run_left??-1);
  rows=rows.slice().sort((a,b)=>{ const ka=key(a), kb=key(b); return ka<kb?-RESEARCH.dir:(ka>kb?RESEARCH.dir:0); });
  const th=(col,label,cls)=>{
    const on=RESEARCH.sort===col;
    return `<th class="${cls||""} ${on?"sorted":""}" data-sort="${col}">${label}${on?(RESEARCH.dir<0?" ▾":" ▴"):""}</th>`;
  };
  if(!rows.length) return `<div class="res-none">No blueprint originals found.</div>`;
  return `<table class="ledger-table stats-table res-table">
    <thead><tr>${th("name","Blueprint","lg-item")}${th("me","ME","")}${th("te","TE","")}
      ${th("jobs","Jobs","num")}${th("fees","Fees","num")}${th("gained","Gained / run","num")}
      ${th("left","Left to ME 10 / run","num")}${th("saved","ME saved on builds","num")}</tr></thead>
    <tbody>${rows.map(b=>`<tr>
      <td class="lg-item"><span class="res-bp">${_resIcon(b.type_id)}<span>${authEsc(b.name.replace(/ Blueprint$/,""))}
        ${b.in_lab?`<span class="res-lab-chip">in lab</span>`:""}${b.maxed?`<span class="res-max-chip">maxed</span>`:""}</span></span></td>
      <td>${_resPips(b.me, 10, 1)} <span class="res-lv">${b.me??"?"}</span></td>
      <td>${_resPips(b.te, 20, 2)} <span class="res-lv">${b.te??"?"}</span></td>
      <td class="num">${b.jobs||"—"}</td>
      <td class="num lg-dimval">${b.fees?fmtISK(b.fees):"—"}</td>
      <td class="num lg-prof ${b.isk_per_run_gained>0?"pos":""}">${b.isk_per_run_gained?fmtISK(b.isk_per_run_gained):"—"}</td>
      <td class="num">${b.isk_per_run_left==null?"—":(b.isk_per_run_left?fmtISK(b.isk_per_run_left):"✓")}${b.priced===false?` <span class="res-dim" title="Some materials have no Jita price — this is a lower bound">*</span>`:""}</td>
      <td class="num lg-prof ${b.realized_savings>0?"pos":""}">${b.realized_savings?fmtISK(b.realized_savings):"—"}</td>
    </tr>`).join("")}</tbody></table>`;
}

function _researchHistoryHtml(hist){
  if(!hist.length) return "";
  return `<section class="stats-card"><div class="stats-card-head"><h3>Completed research</h3>
      <span class="stats-card-note">${hist.length} job${hist.length===1?"":"s"}</span></div>
    <div class="stats-table-wrap"><table class="ledger-table stats-table res-table">
    <thead><tr><th class="lg-when">Done</th><th class="lg-item">Blueprint</th><th>Research</th>
      <th class="num">Duration</th><th class="num">Fee</th><th class="num">Benefit</th><th class="num">Payback</th></tr></thead>
    <tbody>${hist.map(j=>`<tr>
      <td class="lg-when">${_resDate(j.completed||j.end)}</td>
      <td class="lg-item"><span class="res-bp">${_resIcon(j.blueprint_type_id)}<span>${authEsc((j.blueprint_name||"?").replace(/ Blueprint$/,""))}</span></span></td>
      <td><span class="res-act ${j.activity.toLowerCase()}">${_resLevel(j.activity, j.from, j.to)}</span></td>
      <td class="num">${_resDur(j.duration)}</td>
      <td class="num lg-dimval">${j.cost?fmtISK(j.cost):"—"}</td>
      <td class="num">${_resBenefit(j)}</td>
      <td class="num">${_resPayback(j)}</td>
    </tr>`).join("")}</tbody></table></div></section>`;
}

(function wireResearch(){
  const body=$("#research-body"); if(!body) return;
  body.addEventListener("click", e=>{
    const th=e.target.closest&&e.target.closest("#research-bp-table th[data-sort]");
    if(!th || !RESEARCH.data) return;
    const col=th.dataset.sort;
    if(RESEARCH.sort===col) RESEARCH.dir*=-1; else { RESEARCH.sort=col; RESEARCH.dir=col==="name"?1:-1; }
    if(typeof setPref==="function") setPref("ind_research_view", {sort:RESEARCH.sort, dir:RESEARCH.dir});
    $("#research-bp-table").innerHTML=_researchBpTableHtml(RESEARCH.data.blueprints||[]);
  });
})();
