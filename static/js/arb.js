// ══════════════════════════════════════════════════════════════════════════
// ARB TAB
// ══════════════════════════════════════════════════════════════════════════
let ARB = {rows:[], sort:{key:"isk_opportunity", dir:-1}, colw:{}, lastData:null, avoidLowsec:false, es:null};
let ARB_RESIZING = false;

const ARB_COLS = [
  {k:"name",           t:"Item",        w:240, tip:"Item to flip."},
  {k:"sell_price",     t:"Ask",         w:120, tip:"Lowest sell order — what you pay to buy the item.", f:fmtISK},
  {k:"buy_price",      t:"Bid",         w:120, tip:"Highest buy order — what you receive when you sell instantly.", f:fmtISK},
  {k:"net_per_unit",   t:"Net/u",       w:105, tip:"Profit per unit after sales tax.", f:fmtISK, pn:true},
  {k:"margin_pct",     t:"Margin %",    w: 80, tip:"Net profit as % of ask price.", f:v=>v.toFixed(1)+"%", pn:true},
  {k:"flippable_qty",  t:"Qty",         w: 75, tip:"Units available (min of sell vol and buy vol).", f:fmtNum},
  {k:"isk_opportunity",t:"ISK Opp",     w:115, tip:"Total ISK profit if you flip all available units.", f:fmtISK, pn:true},
  {k:"total_volume",   t:"Vol m³",      w: 90, tip:"Total cargo volume for the flippable quantity.", f:v=>v===null?"?":fmtVol(v)},
  {k:"sell_station",   t:"From",        w:220, tip:"Station where you buy (sell order location)."},
  {k:"from_sec",       t:"Sec",         w: 52, tip:"Security status of From station's system.", f:v=>v===null?"?":v.toFixed(1), secBand:"from_sec_band"},
  {k:"buy_station",    t:"To",          w:220, tip:"Station where you deliver and sell.", cls:""},
  {k:"to_sec",         t:"Sec",         w: 52, tip:"Security status of To station's system.", f:v=>v===null?"?":v.toFixed(1), secBand:"to_sec_band"},
  {k:"jumps",          t:"Jumps",       w: 65, tip:"Jump count From→To (0 = same station).", f:fmtNum},
  {k:"risk",           t:"Risk",        w: 80, tip:"SAFE = all highsec. LOWSEC/NULLSEC = route touches lower security.", riskBand:"risk_band"},
];

function arbSetColgroup(){
  $("#arb-cg").innerHTML=ARB_COLS.map(c=>{
    const w=ARB.colw[c.k]; return `<col${w?` style="width:${w}px"`:""}>`;
  }).join("");
}

const _ARB_RESIZE_CTX={get resizing(){return ARB_RESIZING;},set resizing(v){ARB_RESIZING=v;},tblSel:'#arb-tbl',get colw(){return ARB.colw;},setCg:arbSetColgroup,save:null};
function startArbResize(e,key){ startResize(e,key,_ARB_RESIZE_CTX); }

function renderArbTable(){
  const thead=$("#arb-tbl thead"), tbody=$("#arb-tbl tbody");
  const haveW=ARB_COLS.every(c=>ARB.colw[c.k]);
  $("#arb-tbl").style.tableLayout=haveW?"fixed":"auto";
  arbSetColgroup();
  thead.innerHTML="<tr>"+ARB_COLS.map(c=>{
    const active=ARB.sort.key===c.k;
    const arrow=active?(ARB.sort.dir<0?" ▼":" ▲"):"";
    const tip=c.tip?` data-tip="${c.tip.replace(/"/g,'&quot;')}"`: "";
    return `<th data-k="${c.k}"${tip}${active?' class="sorted"':''}>${c.t}${arrow}<span class="resizer"></span></th>`;
  }).join("")+"</tr>";
  thead.querySelectorAll("th").forEach((th,i)=>{
    th.onclick=()=>{
      if(ARB_RESIZING){ ARB_RESIZING=false; return; }
      const k=th.dataset.k;
      if(ARB.sort.key===k) ARB.sort.dir*=-1;
      else ARB.sort={key:k, dir:k==="name"||k==="sell_station"||k==="buy_station"?1:-1};
      renderArbTable();
    };
    th.querySelector(".resizer").addEventListener("mousedown",e=>startArbResize(e,ARB_COLS[i].k));
  });
  if(!haveW){
    requestAnimationFrame(()=>{
      thead.querySelectorAll("th").forEach((th,i)=>{
        const c=ARB_COLS[i];
        ARB.colw[c.k]=ARB.colw[c.k]||c.w||Math.ceil(th.getBoundingClientRect().width);
      });
      $("#arb-tbl").style.tableLayout="fixed"; arbSetColgroup();
    });
  }
  const rows=[...ARB.rows].sort((a,b)=>{
    const k=ARB.sort.key, d=ARB.sort.dir;
    let x=a[k], y=b[k];
    if(typeof x==="string") return x.localeCompare(y)*d;
    if(x===null) x=-Infinity; if(y===null) y=-Infinity;
    return (x-y)*d;
  });
  tbody.innerHTML=rows.map((r,i)=>{
    const tds=ARB_COLS.map(c=>{
      let v=r[c.k], txt=c.f?c.f(v):(v===null||v===undefined?"-":v);
      let cls=c.cls||"";
      if(c.secBand) cls+=" sec-"+r[c.secBand];
      if(c.riskBand) cls+=" risk-"+r[c.riskBand];
      if(c.pn) cls+=(v>0?" pos":(v<0?" neg":""));
      const titleAttr=(c.k==="sell_station"||c.k==="buy_station")&&v?` data-tip="${String(v).replace(/"/g,'&quot;')}"` :"";
      return `<td class="${cls.trim()}"${titleAttr}>${txt}</td>`;
    }).join("");
    return `<tr style="cursor:pointer" data-ridx="${i}">${tds}</tr>`;
  }).join("");
  tbody.querySelectorAll("tr").forEach((tr,i)=>{
    tr.onclick=()=>{
      if(ARB_RESIZING){ARB_RESIZING=false;return;}
      openArbChart(rows[i]);
    };
  });
}

function renderArbStatus(){
  const d=ARB.lastData; if(!d||ACTIVE_TAB!=="arb") return;
  const mode=d.cross_station?`Cross-station ≤${d.max_jumps}J RT`:"Same-station";
  const stale = d.snap_expires && (Date.now()/1000) > d.snap_expires;
  const staleNote = stale
    ? ` <span style="color:var(--yellow);font-size:13px">· order book expired — click ⟳ Refresh for latest prices</span>`
    : "";
  setStatus(
    `<span class="pill"><b>${d.region_name}</b></span>`
    +`<span class="pill"><b>${d.count}</b> deals · <b>${d.total_spreads}</b> spreads · ${mode}</span>`
    +`<span class="ts">book ${fmtTs(d.snap_fetched_at)} · scan ${fmtTs(d.scanned_at)}</span>`
    +staleNote);
}

function showArbProgress(msg, sub, pct){
  $("#arb-tbl").classList.add("hidden");
  $("#arb-progress").classList.remove("hidden");
  $("#arb-prog-label").textContent = msg;
  $("#arb-prog-sub").textContent = sub || "";
  $("#arb-prog-fill").style.width = (pct || 0) + "%";
}
function hideArbProgress(){
  $("#arb-progress").classList.add("hidden");
  $("#arb-tbl").classList.remove("hidden");
}

function scanArb(){
  // Close any in-flight scan.
  if(ARB.es){ ARB.es.close(); ARB.es=null; }

  const btn=$("#arb-go");
  btn.disabled=true; btn.textContent="Scanning…";

  const arbTax=parseFloat(pctToFrac($("#g-tax").value)||0)+parseFloat(pctToFrac($("#g-broker").value)||0);
  const p=new URLSearchParams({
    region:       $("#arb-region").value,
    cross_station: $("#arb-cross").value,
    sales_tax:    String(arbTax),
    min_isk:      $("#arb-minisk").value||"0",
    max_jumps:    $("#arb-maxjumps").value||"6",
    route_flag:   $("#arb-route").value,
    avoid_lowsec: ARB.avoidLowsec?"1":"0",
  });

  showArbProgress("Connecting to ESI…", "", 1);
  setStatus("Scanning…");

  const es = new EventSource("/api/arb/scan?"+p);
  ARB.es = es;

  es.onmessage = e => {
    let data;
    try{ data=JSON.parse(e.data); }catch(err){ return; }

    if(data.type==="progress"){
      showArbProgress(data.msg, data.sub||"", data.pct||0);
      setStatus(data.msg + (data.sub ? " — "+data.sub : ""));

    } else if(data.type==="result"){
      es.close(); ARB.es=null;
      btn.disabled=false; btn.textContent="Scan";
      ARB.rows=data.rows; ARB.lastData=data;
      hideArbProgress();
      renderArbStatus(); renderArbTable();

    } else if(data.type==="error"){
      es.close(); ARB.es=null;
      btn.disabled=false; btn.textContent="Scan";
      hideArbProgress();
      setStatus(data.error, true);
    }
  };

  es.onerror = () => {
    es.close(); ARB.es=null;
    btn.disabled=false; btn.textContent="Scan";
    hideArbProgress();
    setStatus("Connection error — server may have stopped.", true);
  };
}

function saveArbPrefs(){
  postPrefs('/api/arb/prefs',{
    region:       $("#arb-region").value,
    cross_station: $("#arb-cross").value,
    sales_tax:    pctToFrac($("#g-tax").value),
    min_isk:      $("#arb-minisk").value||"",
    max_jumps:    $("#arb-maxjumps").value||"6",
    route_flag:   $("#arb-route").value,
    avoid_lowsec: ARB.avoidLowsec?"1":"0",
  }); saveLS();
}
function updateArbJumpsVisibility(){
  const cross=$("#arb-cross").value==="1";
  $("#arb-maxjumps-field").style.display=cross?"":"none";
}
$("#arb-cross").addEventListener("change",()=>{ updateArbJumpsVisibility(); saveArbPrefs(); });
["#arb-region","#arb-minisk","#arb-maxjumps","#arb-route"].forEach(sel=>{
  const el=$(sel); if(!el) return;
  el.addEventListener("change", saveArbPrefs);
  el.addEventListener("input", saveArbPrefs);
});
$("#arb-go").onclick=()=>scanArb();
$("#arb-toggleLowsec").onclick=()=>{
  ARB.avoidLowsec=!ARB.avoidLowsec;
  $("#arb-toggleLowsec").classList.toggle("active",ARB.avoidLowsec);
  saveArbPrefs();
  if(ARB.rows.length) scanArb(false);
};
setInterval(renderArbStatus, 30000);

function openExpandChart(typeId, regionId, currentPrice, title){
  document.getElementById('arbChartModal').classList.add('hidden');
  document.getElementById('chartExpandTitle').textContent=title||'';
  document.getElementById('chartExpandStats').textContent='';
  document.getElementById('chartExpandModal').classList.remove('hidden');
  requestAnimationFrame(()=>{
    const c=document.getElementById('chartExpandCanvas');
    if(c) _attachChart(c,document.getElementById('chartExpandTip'),document.getElementById('chartExpandStats'),typeId,regionId,currentPrice,title);
  });
}

function openArbChart(row){
  const regionId=parseInt($("#arb-region").value)||10000002;
  document.getElementById('arbChartTitle').textContent=row.name;
  document.getElementById('arbChartStats').textContent='';
  document.getElementById('arbChartModal').classList.remove('hidden');
  requestAnimationFrame(()=>{
    const c=document.getElementById('arbChartCanvas');
    if(c) _attachChart(c,document.getElementById('arbChartTip'),document.getElementById('arbChartStats'),row.type_id,regionId,row.sell_price||null,row.name);
  });
}
(()=>{
  const arbModal=document.getElementById('arbChartModal');
  const expModal=document.getElementById('chartExpandModal');
  document.getElementById('arbChartClose').onclick=()=>arbModal.classList.add('hidden');
  document.getElementById('chartExpandClose').onclick=()=>expModal.classList.add('hidden');
  document.addEventListener('keydown',e=>{
    if(e.key==='Escape'){arbModal.classList.add('hidden');expModal.classList.add('hidden');}
  });
  arbModal.onclick=e=>{if(e.target===arbModal) arbModal.classList.add('hidden');};
  expModal.onclick=e=>{if(e.target===expModal) expModal.classList.add('hidden');};
})();

