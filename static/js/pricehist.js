// ══════════════════════════════════════════════════════════════════════════
// PRICE HISTORY MODAL — momentum analysis for one item at one trade hub
// ══════════════════════════════════════════════════════════════════════════
// Opened from the Industry planner's detail header. Pulls the full ESI region
// history (~13 months of daily rows) through /api/history, then lets you slice
// it by range (7D … All), re-bucket it daily / weekly / monthly, overlay moving
// averages and the daily high/low band, and switch the lower pane between
// traded volume and RSI. A momentum strip above the chart always reads the
// DAILY series against calendar windows, so it doesn't move with the controls.
//
// Everything above the "modal" divider is pure (no DOM) so tests can run it
// under node; the modal markup is built lazily on first open.

const _PH_DAY=86400000;
const PH_RANGES=[["7","7D"],["30","1M"],["90","3M"],["180","6M"],["365","1Y"],["all","All"]];
const PH_INTERVALS=[["day","Daily"],["week","Weekly"],["month","Monthly"]];
// Shortest range each interval makes sense for — a 7-day window holds one or
// two weekly bars, which is not a chart.
const PH_INTERVAL_MIN_RANGE={day:0, week:30, month:180};
// Moving-average windows per interval, in calendar days, with their labels.
const PH_MA={day:[[7,"7d avg"],[30,"30d avg"]],
             week:[[28,"4w avg"],[91,"13w avg"]],
             month:[[91,"3m avg"],[182,"6m avg"]]};
// Series ink (validated: dark surface #162130, adjacent CVD ΔE ≥ 10.9).
const PH_INK={price:"#2a9fd8", maS:"#b5850f", maL:"#a070f0", vol:"#5a7a95",
              axis:"#5a7a95", grid:"#1f3044", fg:"#c8d8e8", pos:"#66bb6a", neg:"#e05555"};

function _phTime(date){ return Date.parse(date+"T00:00:00Z"); }

function _phIso(t){ return new Date(t).toISOString().slice(0,10); }

// Start (ms, UTC midnight) of the bucket a day falls in: the day itself, the
// Monday of its week, or the 1st of its month.
function _phBucketStart(t, interval){
  if(interval==="week") return t-((new Date(t).getUTCDay()+6)%7)*_PH_DAY;
  if(interval==="month"){ const d=new Date(t); return Date.UTC(d.getUTCFullYear(), d.getUTCMonth(), 1); }
  return t;
}

// Collapse ESI daily rows into bars. avg is volume-weighted (a week where most
// units moved cheaply should read cheap); open/close are the first/last day's
// average; high/low are the extremes; volume and orders are summed. `end` is
// the bar's last trading day.
function _phBucket(hist, interval){
  const out=[]; let cur=null;
  for(const h of hist){
    const t=_phTime(h.date), s=_phBucketStart(t, interval);
    if(!cur || cur.t!==s){
      cur={t:s, end:t, date:_phIso(s), open:h.average, close:h.average, high:h.highest, low:h.lowest,
           volume:0, orders:0, days:0, _pv:0, _ps:0};
      out.push(cur);
    }
    cur.close=h.average;
    cur.end=t;
    cur.high=Math.max(cur.high, h.highest);
    cur.low=Math.min(cur.low, h.lowest);
    cur.volume+=h.volume||0;
    cur.orders+=h.order_count||0;
    cur.days+=1;
    cur._pv+=h.average*(h.volume||0);
    cur._ps+=h.average;
  }
  for(const b of out){
    b.avg=b.volume>0 ? b._pv/b.volume : b._ps/b.days;
    delete b._pv; delete b._ps;
  }
  return out;
}

// Time-window simple moving average of bar averages: at each bar, the mean of
// every bar whose start falls in the trailing `days` window. Calendar-based, so
// gaps (days nothing traded) don't stretch a "7d" average across months. Null
// until the history actually covers a full window.
function _phTimeSma(bars, days){
  if(!bars.length) return [];
  const first=bars[0].t, win=days*_PH_DAY;
  let lo=0, sum=0;
  return bars.map((b,i)=>{
    sum+=b.avg;
    while(bars[lo].t<=b.t-win){ sum-=bars[lo].avg; lo++; }
    return b.t-first>=win-_PH_DAY ? sum/(i-lo+1) : null;
  });
}

// Wilder's RSI over `n` bars. Null for the first n bars (not enough moves yet).
function _phRsi(vals, n=14){
  const out=vals.map(()=>null);
  if(vals.length<=n) return out;
  let g=0, l=0;
  for(let i=1;i<=n;i++){ const d=vals[i]-vals[i-1]; if(d>0) g+=d; else l-=d; }
  g/=n; l/=n;
  const rsi=()=>l===0 ? (g===0?50:100) : 100-100/(1+g/l);
  out[n]=rsi();
  for(let i=n+1;i<vals.length;i++){
    const d=vals[i]-vals[i-1];
    g=(g*(n-1)+Math.max(d,0))/n;
    l=(l*(n-1)+Math.max(-d,0))/n;
    out[i]=rsi();
  }
  return out;
}

// Bars inside the range: the last `range` calendar days ending on the newest
// trading day. "all" keeps everything.
function _phSlice(bars, range, interval="day"){
  if(range==="all" || !bars.length) return bars.slice();
  const from=bars.at(-1).end-(parseInt(range,10)-1)*_PH_DAY;
  // A week/month bar that STARTS before the cutoff but contains the cutoff day
  // still belongs.
  const fromBucket=_phBucketStart(from, interval);
  const i=bars.findIndex(b=>b.t>=fromBucket);
  return i<0 ? [] : bars.slice(i);
}

// A coarse interval on a short range collapses to one or two bars; fall back to
// daily rather than draw a non-chart.
function _phCoerceInterval(range, interval){
  const days=range==="all" ? Infinity : parseInt(range,10);
  return days>=(PH_INTERVAL_MIN_RANGE[interval]||0) ? interval : "day";
}

// Momentum read-out over the DAILY history, in calendar windows anchored on
// the newest ESI day. Every field is null when the history can't support it.
function _phMomentum(hist){
  if(!hist.length) return null;
  const rows=hist.map(h=>({t:_phTime(h.date), p:h.average, v:h.volume||0}));
  const last=rows.at(-1), T=last.t;
  const within=n=>rows.filter(r=>r.t>T-n*_PH_DAY);
  // Price of the last trading day on or before n days ago; null when the
  // history doesn't reach back that far.
  const priceAgo=n=>{
    const cut=T-n*_PH_DAY; let p=null;
    for(const r of rows){ if(r.t<=cut) p=r.p; else break; }
    return p;
  };
  const pct=(a,b)=>(a!=null && b)? (a-b)/b*100 : null;
  const vwap=n=>{
    const w=within(n), v=w.reduce((s,r)=>s+r.v,0);
    if(!w.length) return null;
    return v>0 ? w.reduce((s,r)=>s+r.p*r.v,0)/v : w.reduce((s,r)=>s+r.p,0)/w.length;
  };
  const changes={};
  for(const n of [1,7,30,90,365]) changes[n]=pct(last.p, priceAgo(n));

  const w30=within(30);
  // Day-to-day volatility: stdev of log returns between consecutive trading
  // days in the last 30, as a percentage.
  let volatility=null;
  if(w30.length>=6){
    const rets=w30.slice(1).map((r,i)=>Math.log(r.p/w30[i].p));
    const m=rets.reduce((s,x)=>s+x,0)/rets.length;
    volatility=Math.sqrt(rets.reduce((s,x)=>s+(x-m)**2,0)/(rets.length-1))*100;
  }
  // Least-squares trend of log price over the last 30 days, as % per week.
  let trendWeek=null;
  if(w30.length>=5){
    const xs=w30.map(r=>(r.t-T)/_PH_DAY), ys=w30.map(r=>Math.log(r.p));
    const mx=xs.reduce((s,x)=>s+x,0)/xs.length, my=ys.reduce((s,y)=>s+y,0)/ys.length;
    const den=xs.reduce((s,x)=>s+(x-mx)**2,0);
    if(den>0){
      const slope=xs.reduce((s,x,i)=>s+(x-mx)*(ys[i]-my),0)/den;
      trendWeek=(Math.exp(slope*7)-1)*100;
    }
  }
  const lo30=w30.length?Math.min(...w30.map(r=>r.p)):null;
  const hi30=w30.length?Math.max(...w30.map(r=>r.p)):null;
  const range30=(lo30!=null && hi30>lo30)?(last.p-lo30)/(hi30-lo30)*100:null;
  const unitsDay=n=>within(n).reduce((s,r)=>s+r.v,0)/n;
  const vol7=unitsDay(7), vol30=unitsDay(30);
  const rsiArr=_phRsi(rows.map(r=>r.p),14);
  const vw7=vwap(7), vw30=vwap(30);
  return {
    last:last.p, lastDate:hist.at(-1).date, changes,
    avg7:vw7, avg30:vw30, avg90:vwap(90),
    vsAvg7:pct(last.p, vw7), vsAvg30:pct(last.p, vw30),
    shortVsLong:pct(vw7, vw30),
    rsi:rsiArr.at(-1), volatility, trendWeek, lo30, hi30, range30,
    vol7, vol30, volTrend:vol30>0?(vol7/vol30-1)*100:null,
    iskDay30:within(30).reduce((s,r)=>s+r.p*r.v,0)/30,
    tradeDays30:w30.length,
  };
}

// Everything the chart and table draw for one (range, interval) choice:
// indicators run over the WHOLE bucketed series first, then get sliced, so a
// moving average is already warmed up at the left edge of the window.
function _phView(hist, range, interval){
  const iv=_phCoerceInterval(range, interval);
  const bars=_phBucket(hist, iv);
  const [[sDays,sLbl],[lDays,lLbl]]=PH_MA[iv];
  const maS=_phTimeSma(bars, sDays), maL=_phTimeSma(bars, lDays);
  const rsi=_phRsi(bars.map(b=>b.avg),14);
  const shown=_phSlice(bars, range, iv);
  const off=bars.length-shown.length;
  return {interval:iv, bars:shown, maS:maS.slice(off), maL:maL.slice(off), rsi:rsi.slice(off),
          maSLabel:sLbl, maLLabel:lLbl,
          prev:off>0?bars[off-1]:null};
}

// ── modal ────────────────────────────────────────────────────────────────

const PH={open:false, typeId:null, regionId:null, name:"", live:null, hist:null,
          range:"90", interval:"day", lower:"volume", band:true, maOn:true, table:false,
          seq:0, charts:[]};

// Prices: cheap items (minerals, ammo) live on 0.01-ISK ticks, so keep cents
// below 1,000 where fmtISK would round 3.84 to "4".
function _phIsk(n){ return n!=null && Math.abs(n)<1000 ? fmtISKFull(n) : fmtISK(n); }
// Unit counts: minerals trade billions a day; compact them past 10k.
function _phQty(n){ return n!=null && Math.abs(n)>=1e4 ? fmtISK(n) : fmtNum(n); }

function _phPct(v, digits=1){
  if(v==null || !isFinite(v)) return "—";
  return (v>0?"+":"")+v.toFixed(digits)+"%";
}
function _phSignCls(v){ return v==null?"":v>0?"pos":v<0?"neg":""; }
function _phArrow(v){ return v==null?"":v>0?"▲":v<0?"▼":"■"; }

function _phEnsureModal(){
  let m=document.getElementById("phModal");
  if(m) return m;
  m=document.createElement("div");
  m.id="phModal"; m.className="hidden";
  m.innerHTML=`<div class="ph-box" role="dialog" aria-modal="true" aria-labelledby="phTitle">
    <div class="ph-head">
      <div class="ph-title"><h3 id="phTitle"></h3><span class="ph-sub" id="phSub"></span></div>
      <span class="close" id="phClose" title="Close (Esc)">✕</span>
    </div>
    <div class="ph-mom" id="phMom"></div>
    <div class="ph-ctl" id="phCtl"></div>
    <div class="ph-body" id="phBody"></div>
    <div class="ph-foot">Daily figures from ESI market history for this region; today's trading appears after ESI's daily update. High/low wicks can be single fat-finger trades, so the band is clipped to the chart.</div>
  </div>`;
  document.body.appendChild(m);
  m.addEventListener("click", e=>{ if(e.target===m) closePriceHistory(); });
  m.querySelector("#phClose").onclick=closePriceHistory;
  document.addEventListener("keydown", e=>{ if(e.key==="Escape" && PH.open) closePriceHistory(); });
  return m;
}

async function openPriceHistory({typeId, regionId, name, live, hub}){
  const m=_phEnsureModal();
  Object.assign(PH, {open:true, typeId, regionId, name, live:live||null, hist:null});
  const seq=++PH.seq;
  m.querySelector("#phTitle").textContent=name||"";
  m.querySelector("#phSub").textContent=hub?`${hub} · price history`:"Price history";
  m.querySelector("#phMom").innerHTML="";
  m.querySelector("#phCtl").innerHTML="";
  _phDestroyCharts();
  m.querySelector("#phBody").innerHTML=`<div class="ph-empty">Loading market history…</div>`;
  m.classList.remove("hidden");
  let hist=[];
  try{
    const d=await (await fetch(`/api/history?type_id=${typeId}&region_id=${regionId}`)).json();
    hist=(d.history||[]).filter(h=>h && h.date && h.average>0);
  }catch{ hist=null; }
  if(seq!==PH.seq || !PH.open) return;
  if(hist===null){ m.querySelector("#phBody").innerHTML=`<div class="ph-empty">Couldn't load market history. Try again in a moment.</div>`; return; }
  PH.hist=hist;
  _phRender();
}

function closePriceHistory(){
  PH.open=false; PH.seq++;
  _phDestroyCharts();
  const m=document.getElementById("phModal");
  if(m) m.classList.add("hidden");
}

function _phDestroyCharts(){
  PH.charts.forEach(c=>{ try{ c.destroy(); }catch{} });
  PH.charts=[];
}

function _phRender(){
  const m=document.getElementById("phModal"); if(!m) return;
  const hist=PH.hist||[];
  if(!hist.length){
    m.querySelector("#phBody").innerHTML=`<div class="ph-empty">No market history for this item in this region.</div>`;
    return;
  }
  m.querySelector("#phMom").innerHTML=_phMomentumHtml(_phMomentum(hist));
  _phRenderControls();
  _phRenderBody();
}

function _phMomentumHtml(mo){
  if(!mo) return "";
  const chg=[[1,"1D"],[7,"7D"],[30,"30D"],[90,"90D"],[365,"1Y"]].map(([n,l])=>{
    const v=mo.changes[n];
    return `<div class="ph-chg" data-tip="Daily average on ${mo.lastDate} against the last trading day at least ${n} day${n===1?"":"s"} earlier.">
      <span class="k">${l}</span><b class="${_phSignCls(v)}">${v==null?"—":_phArrow(v)+" "+_phPct(v)}</b></div>`;
  }).join("");
  const rsiLbl=mo.rsi==null?"":mo.rsi>=70?"overbought":mo.rsi<=30?"oversold":"neutral";
  const cell=(k,v,sub,tip,cls)=>`<div class="ph-ind" data-tip="${tip}"><span class="k">${k}</span>`
    +`<b class="${cls||""}">${v}</b>${sub?`<span class="s">${sub}</span>`:""}</div>`;
  const trendWord=mo.shortVsLong==null?"":mo.shortVsLong>0?"7d above 30d":"7d below 30d";
  return `<div class="ph-mom-row">
      <div class="ph-mom-last" data-tip="Volume-weighted average price on the newest day in ESI history.">
        <span class="k">Last day avg</span><b>${_phIsk(mo.last)}</b><span class="s">${mo.lastDate}</span></div>
      ${chg}
    </div>
    <div class="ph-mom-row ph-mom-ind">
      ${cell("vs 7d avg", _phPct(mo.vsAvg7), _phIsk(mo.avg7), "Last day's average against the volume-weighted average of the past 7 days.", _phSignCls(mo.vsAvg7))}
      ${cell("vs 30d avg", _phPct(mo.vsAvg30), _phIsk(mo.avg30), "Last day's average against the volume-weighted average of the past 30 days.", _phSignCls(mo.vsAvg30))}
      ${cell("Short vs long", _phPct(mo.shortVsLong), trendWord, "7-day average against the 30-day average. Above zero means the recent week traded higher than the month: upward momentum.", _phSignCls(mo.shortVsLong))}
      ${cell("30d trend", mo.trendWeek==null?"—":_phPct(mo.trendWeek)+"/wk", "", "Straight-line fit through the last 30 days of daily prices, expressed as % change per week.", _phSignCls(mo.trendWeek))}
      ${cell("RSI 14", mo.rsi==null?"—":mo.rsi.toFixed(0), rsiLbl, "Relative Strength Index over the last 14 trading days. Above 70 the price has risen fast and may be stretched; below 30 it has fallen fast.")}
      ${cell("30d range", mo.range30==null?"—":mo.range30.toFixed(0)+"%", mo.lo30!=null?`${_phIsk(mo.lo30)} – ${_phIsk(mo.hi30)}`:"", "Where the last day sits between the lowest (0%) and highest (100%) daily average of the past 30 days.")}
      ${cell("Volatility", mo.volatility==null?"—":mo.volatility.toFixed(1)+"%", "per day, 30d", "Standard deviation of day-to-day price moves over the last 30 days. Higher means a jumpier market.")}
      ${cell("Units/day", _phQty(mo.vol7), `7d · 30d ${_phQty(mo.vol30)}`, "Units traded per calendar day over the last 7 days, against the 30-day rate.")}
      ${cell("Volume trend", _phPct(mo.volTrend,0), `${mo.tradeDays30}/30 days traded`, "7-day traded volume rate against the 30-day rate. Rising volume behind a price move makes the move more believable.", _phSignCls(mo.volTrend))}
      ${cell("ISK/day", _phIsk(mo.iskDay30), "30d", "Average ISK value traded per day over the last 30 days.")}
    </div>`;
}

function _phSeg(name, opts, cur, disabled){
  return `<span class="ph-seg" data-seg="${name}">${opts.map(([k,l])=>{
    const dis=disabled&&disabled(k);
    return `<button data-v="${k}" class="${k===cur?"on":""}"${dis?` disabled data-tip="${dis}"`:""}>${l}</button>`;
  }).join("")}</span>`;
}

function _phRenderControls(){
  const el=document.getElementById("phCtl");
  const iv=_phCoerceInterval(PH.range, PH.interval);
  const ivDis=k=>_phCoerceInterval(PH.range,k)===k?"":`Pick a range of ${PH_INTERVAL_MIN_RANGE[k]>=180?"6 months":"1 month"} or longer`;
  const [[,sL],[,lL]]=PH_MA[iv];
  el.innerHTML=`
    <span class="ph-ctl-grp"><span class="ph-ctl-lbl">Range</span>${_phSeg("range", PH_RANGES, PH.range)}</span>
    <span class="ph-ctl-grp"><span class="ph-ctl-lbl">Bars</span>${_phSeg("interval", PH_INTERVALS, iv, ivDis)}</span>
    <span class="ph-ctl-grp"><span class="ph-ctl-lbl">Show</span>
      <button class="ph-tog${PH.maOn?" on":""}" data-tog="maOn" data-tip="${sL} and ${lL} moving averages">Averages</button>
      <button class="ph-tog${PH.band?" on":""}" data-tog="band" data-tip="Shaded band between each bar's lowest and highest trade">High/low</button>
    </span>
    <span class="ph-ctl-grp"><span class="ph-ctl-lbl">Lower pane</span>${_phSeg("lower", [["volume","Volume"],["rsi","RSI"]], PH.lower)}</span>
    <span class="ph-ctl-grp ph-ctl-end">${_phSeg("table", [["0","Chart"],["1","Table"]], PH.table?"1":"0")}</span>`;
  el.querySelectorAll(".ph-seg").forEach(seg=>{
    seg.querySelectorAll("button").forEach(b=>b.onclick=()=>{
      const k=seg.dataset.seg, v=b.dataset.v;
      if(k==="table") PH.table=v==="1";
      else PH[k]=v;
      _phSavePrefs();
      _phRenderControls(); _phRenderBody();
    });
  });
  el.querySelectorAll(".ph-tog").forEach(b=>b.onclick=()=>{
    PH[b.dataset.tog]=!PH[b.dataset.tog];
    _phSavePrefs();
    _phRenderControls(); _phRenderBody();
  });
}

function _phSavePrefs(){
  try{ localStorage.setItem("ph.prefs", JSON.stringify({range:PH.range, interval:PH.interval,
    lower:PH.lower, band:PH.band, maOn:PH.maOn, table:PH.table})); }catch{}
}
function _phLoadPrefs(){
  try{
    const p=JSON.parse(localStorage.getItem("ph.prefs")||"null"); if(!p) return;
    if(PH_RANGES.some(r=>r[0]===p.range)) PH.range=p.range;
    if(PH_INTERVALS.some(r=>r[0]===p.interval)) PH.interval=p.interval;
    if(p.lower==="volume"||p.lower==="rsi") PH.lower=p.lower;
    ["band","maOn","table"].forEach(k=>{ if(typeof p[k]==="boolean") PH[k]=p[k]; });
  }catch{}
}
if(typeof localStorage!=="undefined") _phLoadPrefs();

function _phBarLabel(b, interval){
  const d=new Date(b.t), o={timeZone:"UTC"};
  if(interval==="month") return d.toLocaleDateString(undefined,{...o, month:"long", year:"numeric"});
  const day=d.toLocaleDateString(undefined,{...o, day:"numeric", month:"short", year:"numeric"});
  return interval==="week"?`Week of ${day}`:day;
}

function _phRenderBody(){
  const body=document.getElementById("phBody");
  _phDestroyCharts();
  const v=_phView(PH.hist, PH.range, PH.interval);
  if(!v.bars.length){ body.innerHTML=`<div class="ph-empty">Nothing traded in this range.</div>`; return; }
  if(PH.table){ body.innerHTML=_phTableHtml(v); return; }
  body.innerHTML=`<div class="ph-legend" id="phLegend"></div>
    <div id="phMain" class="ph-chart"></div><div id="phLower" class="ph-chart"></div>`;
  if(typeof ApexCharts==="undefined"){
    body.innerHTML=`<div class="ph-empty">Chart unavailable (no internet). The Table view has every figure.</div>`;
    return;
  }
  _phDrawCharts(v, 0);
}

function _phTableHtml(v){
  const rows=v.bars.map((b,i)=>{
    const prev=i>0?v.bars[i-1]:v.prev;
    const chg=prev?(b.avg-prev.avg)/prev.avg*100:null;
    return `<tr><td>${_phBarLabel(b, v.interval)}</td><td class="num">${_phIsk(b.avg)}</td>
      <td class="num ${_phSignCls(chg)}">${_phPct(chg)}</td>
      <td class="num">${_phIsk(b.low)}</td><td class="num">${_phIsk(b.high)}</td>
      <td class="num">${v.maS[i]==null?"—":_phIsk(v.maS[i])}</td>
      <td class="num">${v.maL[i]==null?"—":_phIsk(v.maL[i])}</td>
      <td class="num">${v.rsi[i]==null?"—":v.rsi[i].toFixed(0)}</td>
      <td class="num">${_phQty(b.volume)}</td><td class="num">${_phQty(b.orders)}</td></tr>`;
  }).reverse().join("");
  return `<div class="ph-table-wrap"><table class="ph-table"><thead><tr>
    <th>${v.interval==="day"?"Day":v.interval==="week"?"Week":"Month"}</th><th class="num">Avg price</th><th class="num">Change</th>
    <th class="num">Low</th><th class="num">High</th><th class="num">${v.maSLabel}</th><th class="num">${v.maLLabel}</th>
    <th class="num">RSI</th><th class="num">Units</th><th class="num">Orders</th></tr></thead>
    <tbody>${rows}</tbody></table></div>`;
}

// Y-domain from the averages (and live ask) only — ESI highs/lows include
// single fat-finger trades that would flatten the real price action. The band
// is clipped into this domain.
function _phDomain(v, live){
  const vals=[...v.bars.map(b=>b.avg)];
  if(PH.maOn) vals.push(...v.maS.filter(x=>x!=null), ...v.maL.filter(x=>x!=null));
  if(live>0) vals.push(live);
  let lo=Math.min(...vals), hi=Math.max(...vals);
  if(PH.band){
    // Let the band widen the view by up to 50% of the price spread, no more.
    const span=Math.max(hi-lo, hi*0.02);
    lo=Math.max(Math.min(lo, ...v.bars.map(b=>b.low)), lo-span*0.5);
    hi=Math.min(Math.max(hi, ...v.bars.map(b=>b.high)), hi+span*0.5);
  }
  const pad=Math.max((hi-lo)*0.06, hi*0.005);
  return [Math.max(0, lo-pad), hi+pad];
}

function _phDrawCharts(v, tries){
  const main=document.getElementById("phMain"), lower=document.getElementById("phLower");
  if(!main || !lower) return;
  // ApexCharts lays out against the container width; wait for layout.
  if(!main.clientWidth){ if(tries<30) requestAnimationFrame(()=>_phDrawCharts(v, tries+1)); return; }
  const [yMin,yMax]=_phDomain(v, PH.live);
  const clip=x=>Math.min(yMax, Math.max(yMin, x));
  const series=[], colors=[], widths=[], dashes=[];
  const add=(s,c,w,d)=>{ series.push(s); colors.push(c); widths.push(w); dashes.push(d); };
  if(PH.band) add({name:"High/low", type:"rangeArea",
    data:v.bars.map(b=>({x:b.t, y:[clip(b.low), clip(b.high)]}))}, PH_INK.price, 0, 0);
  add({name:"Avg price", type:"line", data:v.bars.map(b=>({x:b.t, y:b.avg}))}, PH_INK.price, 2, 0);
  if(PH.maOn){
    add({name:v.maSLabel, type:"line", data:v.bars.map((b,i)=>({x:b.t, y:v.maS[i]}))}, PH_INK.maS, 2, 0);
    add({name:v.maLLabel, type:"line", data:v.bars.map((b,i)=>({x:b.t, y:v.maL[i]}))}, PH_INK.maL, 2, 4);
  }
  _phLegend(series.map((s,i)=>({name:s.name, color:colors[i], band:s.type==="rangeArea", dash:dashes[i]>0})));
  const tip=({dataPointIndex:i})=>_phTip(v, i);
  // The panes share a hover (chart group), so the lower one only names its own
  // figure instead of repeating the full read-out.
  const lowTip=({dataPointIndex:i})=>{
    const bar=v.bars[i]; if(!bar) return "";
    const val=PH.lower==="rsi" ? (v.rsi[i]==null?"—":v.rsi[i].toFixed(0)) : _phQty(bar.volume);
    return `<div class="ph-tip">${_phBarLabel(bar, v.interval)} · ${PH.lower==="rsi"?"RSI 14":"Units"} <b>${val}</b></div>`;
  };
  const common={background:"transparent", fontFamily:"inherit", group:"ph",
    toolbar:{show:true, tools:{download:false, selection:true, zoom:true, zoomin:true, zoomout:true, pan:true, reset:true}},
    zoom:{enabled:true, type:"x"}, animations:{enabled:false}};
  const xaxis={type:"datetime", labels:{datetimeUTC:true, style:{colors:PH_INK.axis, fontSize:"10px"}},
    axisBorder:{color:PH_INK.grid}, axisTicks:{color:PH_INK.grid}, tooltip:{enabled:false}};
  const ylab={minWidth:64, maxWidth:64, style:{colors:PH_INK.axis, fontSize:"10px"}};
  const annotations=PH.live>0?{yaxis:[{y:PH.live, borderColor:PH_INK.fg, strokeDashArray:3, opacity:.6,
    label:{text:`Live ask ${_phIsk(PH.live)}`, position:"left", textAnchor:"start", borderWidth:0,
      style:{background:"#1c2a3a", color:PH_INK.fg, fontSize:"10px"}}}]}:{};
  const mainChart=new ApexCharts(main, {
    // A rangeArea series only combines with lines when the chart itself is a
    // rangeArea chart and every series uses {x,y} points; [t,v] tuples mixed
    // with the band's {x,y:[lo,hi]} throw inside ApexCharts.
    chart:{...common, id:"phMain", type:PH.band?"rangeArea":"line", height:300},
    theme:{mode:"dark"}, series, colors,
    fill:{opacity:series.map(s=>s.type==="rangeArea"?.16:1)},
    stroke:{width:widths, dashArray:dashes, curve:"straight"},
    markers:{size:0, hover:{size:4}},
    xaxis, yaxis:{min:yMin, max:yMax, tickAmount:5, labels:{...ylab, formatter:x=>_phIsk(x)}},
    grid:{borderColor:PH_INK.grid, strokeDashArray:3},
    annotations, dataLabels:{enabled:false}, legend:{show:false},
    tooltip:{theme:"dark", shared:true, intersect:false, custom:tip},
  });
  const isRsi=PH.lower==="rsi";
  const lowerChart=new ApexCharts(lower, isRsi ? {
    chart:{...common, id:"phLower", type:"line", height:130, toolbar:{show:false}},
    theme:{mode:"dark"},
    series:[{name:"RSI 14", data:v.bars.map((b,i)=>[b.t, v.rsi[i]==null?null:+v.rsi[i].toFixed(1)])}],
    colors:[PH_INK.price], stroke:{width:2, curve:"straight"}, markers:{size:0, hover:{size:4}},
    xaxis, yaxis:{min:0, max:100, tickAmount:2, labels:{...ylab, formatter:x=>Math.round(x)}},
    grid:{borderColor:PH_INK.grid, strokeDashArray:3},
    annotations:{yaxis:[
      {y:70, y2:100, fillColor:PH_INK.neg, opacity:.08, borderColor:"transparent",
        label:{text:"Overbought", borderWidth:0, style:{background:"transparent", color:PH_INK.axis, fontSize:"9.5px"}}},
      {y:0, y2:30, fillColor:PH_INK.pos, opacity:.08, borderColor:"transparent",
        label:{text:"Oversold", position:"right", offsetY:14, borderWidth:0, style:{background:"transparent", color:PH_INK.axis, fontSize:"9.5px"}}}]},
    dataLabels:{enabled:false}, legend:{show:false},
    tooltip:{theme:"dark", shared:true, intersect:false, custom:lowTip},
  } : {
    chart:{...common, id:"phLower", type:"bar", height:130, toolbar:{show:false}},
    theme:{mode:"dark"},
    series:[{name:"Units traded", data:v.bars.map(b=>[b.t, b.volume])}],
    colors:[PH_INK.vol],
    plotOptions:{bar:{columnWidth:v.bars.length>60?"90%":"70%", borderRadius:2, borderRadiusApplication:"end"}},
    xaxis, yaxis:{tickAmount:2, labels:{...ylab, formatter:x=>_phQty(x)}},
    grid:{borderColor:PH_INK.grid, strokeDashArray:3},
    dataLabels:{enabled:false}, legend:{show:false},
    tooltip:{theme:"dark", shared:true, intersect:false, custom:lowTip},
  });
  PH.charts.push(mainChart, lowerChart);
  mainChart.render(); lowerChart.render();
}

function _phLegend(items){
  const el=document.getElementById("phLegend"); if(!el) return;
  el.innerHTML=items.map(it=>`<span class="ph-leg"><i class="${it.band?"band":it.dash?"dash":"line"}" style="--c:${it.color}"></i>${it.name}</span>`).join("")
    +(PH.live>0?`<span class="ph-leg"><i class="dash ref"></i>Live ask</span>`:"")
    +`<span class="ph-leg ph-leg-pane">Lower pane: ${PH.lower==="rsi"?"RSI 14":"units traded"}</span>`;
}

function _phTip(v, i){
  const b=v.bars[i]; if(!b) return "";
  const prev=i>0?v.bars[i-1]:v.prev;
  const chg=prev?(b.avg-prev.avg)/prev.avg*100:null;
  const row=(k,val,cls)=>`<tr><td>${k}</td><td class="num ${cls||""}">${val}</td></tr>`;
  return `<div class="ph-tip"><div class="ph-tip-h">${_phBarLabel(b, v.interval)}</div><table>
    ${row("Avg price", _phIsk(b.avg))}
    ${row(v.interval==="day"?"vs prev day":v.interval==="week"?"vs prev week":"vs prev month", _phPct(chg), _phSignCls(chg))}
    ${row("Low / high", `${_phIsk(b.low)} / ${_phIsk(b.high)}`)}
    ${v.interval!=="day"?row("Open → close", `${_phIsk(b.open)} → ${_phIsk(b.close)}`):""}
    ${v.maS[i]!=null?row(v.maSLabel, _phIsk(v.maS[i])):""}
    ${v.maL[i]!=null?row(v.maLLabel, _phIsk(v.maL[i])):""}
    ${v.rsi[i]!=null?row("RSI 14", v.rsi[i].toFixed(0)):""}
    ${row("Units", _phQty(b.volume))}
    ${row("Orders", _phQty(b.orders))}
    ${v.interval!=="day"?row("Days traded", b.days):""}
  </table></div>`;
}
