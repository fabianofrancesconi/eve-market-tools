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
// Rebuild the nested {…, arb:{…}, ind:{…}} shape the apply block below expects
// from the server's flat dotted-key pref map. e.g. "arb.region" → s.arb.region,
// "corp" → s.corp. Favorites/profiles come from their own server lists, not the
// pref map, and are surfaced under s.ind for the existing apply code.
function _nestPrefs(prefs){
  const s={};
  for(const k in prefs){
    const dot=k.indexOf(".");
    if(dot<0){ s[k]=prefs[k]; continue; }
    const sec=k.slice(0,dot), sub=k.slice(dot+1);
    (s[sec]||(s[sec]={}))[sub]=prefs[k];
  }
  return s;
}
async function loadSettings(){
  // The server is the sole source of truth. Fetch it once and apply it as-is;
  // no retry/merge/localStorage dance — if the fetch fails we simply keep the
  // built-in defaults and DON'T persist them (markSettingsApplied stays off).
  let server=null;
  try{ server=await (await fetch("/api/settings")).json(); }catch(e){}
  const gotSettings = !!(server && server.prefs);
  if(gotSettings){
    SETTINGS.prefs = server.prefs || {};
    SETTINGS.favorites = server.favorites || [];
    SETTINGS.profiles = server.profiles || [];
  }
  // Build the shape the field-application block was written against. Profiles
  // and favorites are JSON-stringified under ind.* only so the existing parsing
  // (which expects strings there) keeps working unchanged.
  const s = _nestPrefs(SETTINGS.prefs);
  (s.ind||(s.ind={})).profiles = JSON.stringify(SETTINGS.profiles);
  s.ind.favorites = JSON.stringify(SETTINGS.favorites);
  if(gotSettings){
      if(s.corp) $("#corp").value=s.corp;
      if(s.lp && !AUTH.loggedIn) $("#lp").value=s.lp;
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
      if(ind.hidden_bps){ try{
        const hb=typeof ind.hidden_bps==="string"?JSON.parse(ind.hidden_bps):ind.hidden_bps;
        if(Array.isArray(hb)) IND.hidden=new Set(hb);
      }catch(e){} }
      if(ind.notes){ try{
        const nt=typeof ind.notes==="string"?JSON.parse(ind.notes):ind.notes;
        if(nt&&typeof nt==="object") IND.notes=nt;
      }catch(e){} }
      if(ind.sections){ try{
        const sec=typeof ind.sections==="string"?JSON.parse(ind.sections):ind.sections;
        if(sec&&typeof sec==="object") Object.assign(IND.sections, sec);
      }catch(e){} }
      // Tracker status-group collapse state (stage key -> collapsed).
      if(ind.build_groups){ try{
        const bg=typeof ind.build_groups==="string"?JSON.parse(ind.build_groups):ind.build_groups;
        if(bg&&typeof bg==="object") Object.assign(IND.buildGroups, bg);
      }catch(e){} }
      // The Archived group always starts collapsed on a fresh load, whatever its
      // last persisted state — it's an out-of-the-way declutter bucket, so dropping
      // the restored value lets the render default (collapsed) take over. It's still
      // freely expandable within the session.
      delete IND.buildGroups.archived;
      // Exploration recent lookups — server-authoritative so every device converges.
      if(s.exp_recent!==undefined && typeof EXP!=="undefined"){ try{
        const er=typeof s.exp_recent==="string"?JSON.parse(s.exp_recent):s.exp_recent;
        if(Array.isArray(er)){
          EXP.recent=er.slice(0,10);
          expRenderRecent();
        }
      }catch(e){} }
      // Exploration journal min-dwell filter. track.js seeds this from its own
      // DOMContentLoaded handler, but that fires before this async /api/settings
      // fetch resolves — so re-apply it here now the real prefs are in, or the
      // saved value is silently lost on every reload.
      if(typeof loadTrackMinDwell==="function"){ loadTrackMinDwell(); if(typeof syncMinDwellInputs==="function") syncMinDwellInputs(); }
      // View modes (Industry Planner/Summary, Exploration Guides/Journal) and the
      // Abyss selections are all server-authoritative now.
      if(typeof IND!=="undefined" && (s.ind_mode==="summary"||s.ind_mode==="planner")) IND.mode=s.ind_mode;
      if(typeof abyApplyStored==="function") abyApplyStored(s.aby_state);
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
  // When logged in, the LP budget comes from character data (refreshCharData),
  // not the settings blob.  If char data is already loaded, apply it now;
  // otherwise _doRefreshCharData() will call updateMyLpBadge() + scan when it
  // arrives, so we skip the auto-scan here to avoid using a stale budget.
  if(typeof updateMyLpBadge==="function" && AUTH.data) updateMyLpBadge();
  const _skipLpScan = AUTH.loggedIn && !AUTH.data;
  // Open the write gate only once we've applied a real server response. While
  // the gate is shut, setPref() updates its in-memory mirror but sends nothing —
  // so applying the fetched values back into the DOM never echoes to the server,
  // and a failed fetch can't push our built-in defaults over the durable copy.
  // A later reload re-attempts the load.
  if(gotSettings) markSettingsApplied();
  // Restore last scan results from server cache, then auto-scan if the LP tab
  // is active and a corp is set.
  restoreLastScans().then(restored=>{
    if(ACTIVE_TAB==="lp" && $("#corp").value.trim() && !restored.lp && !_skipLpScan) scan(false);
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

