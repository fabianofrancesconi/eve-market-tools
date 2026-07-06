// ══════════════════════════════════════════════════════════════════════════
// Init
// ══════════════════════════════════════════════════════════════════════════
async function restoreLastScans(){
  const restored={lp:false, ind:false};
  try{
    const resp=await fetch("/api/last-scan");
    const cached=await resp.json();
    if(cached.lp && cached.lp.rows && cached.lp.rows.length){
      const _il=$("#init-loading"); if(_il) _il.remove();
      STATE.rows=cached.lp.rows;
      STATE.ctx={corp_id:cached.lp.corp_id, lp:cached.lp.lp,
        tax:cached.lp.tax, broker:cached.lp.broker, station:String(cached.lp.station_id)};
      STATE.lastScanData=cached.lp;
      if(ACTIVE_TAB==="lp"){ renderLPStatus(); renderTable(); }
      restored.lp=true;
    }
    if(cached.ind && cached.ind.rows && cached.ind.rows.length){
      IND.rows=cached.ind.rows; IND.lastData=cached.ind;
      computeIndTradeability();
      if(ACTIVE_TAB==="ind"){ renderIndStatus(); renderIndTable(); }
      restored.ind=true;
      if(IND.rows.some(r=>!r.liq_loaded)) fillIndTradeability();
    }
  }catch(e){}
  return restored;
}

updateArbJumpsVisibility();  // reflect default cross-station selection before settings load
async function loadSettings(){
  let server=null;
  try{ server=await (await fetch("/api/settings")).json(); }catch(e){}
  let s=null;
  if(server && server._server_synced){
    // This character has synced settings from some device before — that's
    // the cross-device source of truth, takes priority over this browser's
    // local copy.
    s=server;
  } else {
    try{ s=JSON.parse(localStorage.getItem(LS_KEY)); }catch(e){}
    if(!s) s=server;
    // First login on this character, before any device has synced yet — seed
    // the server row now so other devices see something right away.
    if(s && server && server._logged_in) syncSettingsToServer(s);
  }
  if(s && Object.keys(s).length){
      if(s.corp) $("#corp").value=s.corp;
      if(s.lp)   $("#lp").value=s.lp;
      if(s.market) $("#market").value=s.market;
      const _ms=s.maxspread??s.max_spread; if(_ms!=null) $("#maxspread").value=_ms;
      if(s.tax)   $("#g-tax").value=fracToPct(s.tax);
      if(s.broker) $("#g-broker").value=fracToPct(s.broker);
      if(s.sort_key && COLS.some(c=>c.k===s.sort_key))
        STATE.sort={key:s.sort_key, dir:Number(s.sort_dir)===1?1:-1};
      if(s.col_widths && s.col_layout_v==COL_LAYOUT_VERSION){
        try{
          STATE.colw=(typeof s.col_widths==="string"?JSON.parse(s.col_widths):s.col_widths)||{};
        }catch(e){}
      }
      if(s.col_order && s.col_layout_v==COL_LAYOUT_VERSION){
        try{
          const ord=typeof s.col_order==="string"?JSON.parse(s.col_order):s.col_order;
          if(Array.isArray(ord)){
            const known=ord.filter(k=>COL_BY_KEY[k]);
            if(known.length) STATE.colOrder=known;  // orderedCols() appends any missing
          }
        }catch(e){}
      }
      if(s.hide_illiquid==="1"){ STATE.hideIlliquid=true; $("#toggleIlliquid").checked=true; }
      if(s.hide_unaffordable==="1"){ STATE.hideUnaffordable=true; $("#toggleAffordable").checked=true; }
      if(s.trade_weight!==undefined && s.trade_weight!==""){
        const tw=parseFloat(s.trade_weight);
        if([0.25,0.5,0.75].includes(tw)){ STATE.tradeWeight=tw; syncBalanceButtons(); }
      }
      if(s.col_vis && typeof s.col_vis==="object")
        COLS.forEach(c=>{ if(c.k in s.col_vis) STATE.colVis[c.k]=!!s.col_vis[c.k]; });
      // Arb settings
      const a=s.arb||{};
      if(a.region) $("#arb-region").value=a.region;
      if(a.cross_station==="0"||a.cross_station==="1") $("#arb-cross").value=a.cross_station;
      if(a.min_isk)   $("#arb-minisk").value=a.min_isk;
      if(a.max_jumps) $("#arb-maxjumps").value=a.max_jumps;
      if(a.route_flag) $("#arb-route").value=a.route_flag;
      if(a.avoid_lowsec==="1"){
        ARB.avoidLowsec=true;
        $("#arb-toggleLowsec").classList.add("active");
      }
      updateArbJumpsVisibility();
      // Industry settings
      const ind=s.ind||{};
      // Category options load async; stash the saved one so loadIndGroups applies
      // it once the list exists (and set it now in case the list is already there).
      if(ind.market_group){ IND.savedGroup=ind.market_group; $("#ind-group").value=ind.market_group; }
      if(ind.sort_key && IND_COLS.some(c=>c.k===ind.sort_key))
        IND.sort={key:ind.sort_key, dir:Number(ind.sort_dir)===1?1:-1};
      if(ind.col_order){ try{
        const ord=typeof ind.col_order==="string"?JSON.parse(ind.col_order):ind.col_order;
        if(Array.isArray(ord)&&ord.length) IND.colOrder=ord;  // indOrderedCols() drops unknown / appends new
      }catch(e){} }
      if(ind.col_widths){ try{
        const cw=typeof ind.col_widths==="string"?JSON.parse(ind.col_widths):ind.col_widths;
        if(cw&&typeof cw==="object") Object.assign(IND.colw,cw);
      }catch(e){} }
      if(ind.col_vis){ try{
        const cv=typeof ind.col_vis==="string"?JSON.parse(ind.col_vis):ind.col_vis;
        if(cv&&typeof cv==="object") IND_COLS.forEach(c=>{ if(c.k in cv) IND.colVis[c.k]=!!cv[c.k]; });
      }catch(e){} }
      if(ind.station) $("#ind-station").value=ind.station;
      if(ind.job_rate) $("#ind-jobrate").value=ind.job_rate;
      if(ind.buildable_only==="1") $("#ind-buildable").checked=true;
      if(ind.include_unbuildable==="1") $("#ind-unobtainable").checked=true;
      if(ind.hide_t2==="1") $("#ind-hidet2").checked=true;
      if(ind.hide_bpc==="1") $("#ind-hidebpc").checked=true;
      if(ind.min_tradeability!==undefined&&ind.min_tradeability!=="") $("#ind-mintrade").value=ind.min_tradeability;
      if(ind.ind_trade_weight!==undefined){ IND.tradeWeight=parseFloat(ind.ind_trade_weight)||0.5; syncIndBalanceButtons(); }
      if(ind.profiles){ try{ IND.profiles=JSON.parse(ind.profiles)||[]; }catch(e){} }
      renderIndProfiles();
      if(ind.profile) $("#ind-profile").value=ind.profile;
      if(ind.favorites){ try{ IND.favorites=new Set(JSON.parse(ind.favorites)||[]); }catch(e){} }
      if(ind.hidden_bps){ try{ IND.hidden=new Set(JSON.parse(ind.hidden_bps)||[]); }catch(e){} }
      if(ind.sections){ try{
        const sec=typeof ind.sections==="string"?JSON.parse(ind.sections):ind.sections;
        if(sec&&typeof sec==="object") Object.assign(IND.sections, sec);
      }catch(e){} }
      // Exploration recent lookups — server-synced so every device converges.
      if(s.exp_recent!==undefined && typeof EXP!=="undefined"){ try{
        const er=typeof s.exp_recent==="string"?JSON.parse(s.exp_recent):s.exp_recent;
        if(Array.isArray(er)){
          EXP.recent=er.slice(0,10);
          try{ localStorage.setItem("exp-recent", JSON.stringify(EXP.recent)); }catch(e){}
          expRenderRecent();
        }
      }catch(e){} }
      // Restore the last-used tab saved server-side. A tab URL overrides this
      // just below; don't re-push history for either.
      if(s.active_tab==="arb") switchTab("arb", {url:false});
      else if(s.active_tab==="ind") switchTab("ind", {url:false});
      else if(s.active_tab==="notes") switchTab("notes", {url:false});
      else if(s.active_tab==="exp") switchTab("exp", {url:false});
      else if(s.active_tab==="aby") switchTab("aby", {url:false});
  }
  // A deep link / refresh on a tab URL wins over the saved pref. "/" is not an
  // explicit choice, so it defers to the pref restored above.
  const urlTab = location.pathname==="/" ? null : PATH_TAB[location.pathname];
  if(urlTab && urlTab!==ACTIVE_TAB && (urlTab!=="char" || AUTH.loggedIn))
    switchTab(urlTab, {url:false});
  // Restore last scan results from server cache, then auto-scan if the LP tab
  // is active and a corp is set.
  restoreLastScans().then(restored=>{
    if(ACTIVE_TAB==="lp" && $("#corp").value.trim() && !restored.lp) scan(false);
    if(!restored.ind) loadOwnedPreview();
  });
}
// ── Custom tooltip engine ──────────────────────────────────────────
// Reads data-tip on any element and shows a themed, cursor-following
// tooltip instead of the browser's default title= popup.
(function(){
  const tip=document.createElement("div");
  tip.id="tooltip"; document.body.appendChild(tip);
  let cur=null;
  document.addEventListener("mousemove",e=>{
    const el=e.target.closest?e.target.closest("[data-tip]"):null;
    if(el){
      if(el!==cur){ cur=el; tip.textContent=el.getAttribute("data-tip"); tip.classList.add("show"); }
      const pad=14, w=tip.offsetWidth, h=tip.offsetHeight;
      let x=e.clientX+pad, y=e.clientY+pad;
      if(x+w>innerWidth-8)  x=Math.max(8, e.clientX-w-pad);
      if(y+h>innerHeight-8) y=Math.max(8, e.clientY-h-pad);
      tip.style.left=x+"px"; tip.style.top=y+"px";
    } else if(cur){ cur=null; tip.classList.remove("show"); }
  },{passive:true});
  document.addEventListener("mouseleave",()=>{ cur=null; tip.classList.remove("show"); });
  // Hide while scrolling/clicking so it never lingers in a stale spot.
  document.addEventListener("scroll",()=>{ if(cur){ cur=null; tip.classList.remove("show"); } }, true);
})();

