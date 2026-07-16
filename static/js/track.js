// ── Exploration tracking (live location trail) ─────────────────────────────
// The server samples the active character's ESI location while a session is
// running and appends a trail entry on every system change. This tab renders the
// current run's trail live (pushed via the shared /api/char/stream), computes
// dwell time and cargo-value deltas client-side, and drives the session controls
// (Start / Pause / Resume / Stop & finish). Loot value is an optional per-system
// number the pilot types in — ESI has no live cargo feed, so it's manual.

const TRACK = { state:"stopped", pauseReason:null, runId:null, trail:[], online:null,
                error:null, scopeOk:true, loading:false, minDwell:0 };
const TRACK_MIN_LS = "eve-track-min-dwell";  // {sec} — a local view preference

function loadTrackMinDwell(){
  try{ TRACK.minDwell = Math.max(0, +JSON.parse(localStorage.getItem(TRACK_MIN_LS)||"0") || 0); }
  catch(_){ TRACK.minDwell = 0; }
}
function saveTrackMinDwell(){
  try{ localStorage.setItem(TRACK_MIN_LS, JSON.stringify(TRACK.minDwell)); }catch(_){}
}

function fmtDwell(sec){
  if(sec==null) return "—";
  sec=Math.max(0,Math.round(sec));
  if(sec<60) return sec+"s";
  const m=Math.floor(sec/60), s=sec%60;
  if(m<60) return s? `${m}m ${s}s` : `${m}m`;
  const h=Math.floor(m/60), mm=m%60;
  return mm? `${h}h ${mm}m` : `${h}h`;
}
function fmtClock(ts){
  if(!ts) return "—";
  return new Date(ts*1000).toLocaleTimeString([], {hour:"2-digit", minute:"2-digit"});
}
// EVE security-status → the same sec-band buckets the Arbitrage table uses.
function secBand(sec){
  if(sec==null) return "";
  if(sec>=0.5) return "high";
  if(sec>0) return "low";
  return "null";
}
function fmtSec(sec){ return sec==null? "—" : sec.toFixed(1); }

async function refreshTrail(){
  if(!AUTH.loggedIn) return;
  try{
    const r=await fetch("/api/track/trail");
    const j=await r.json();
    if(j.error){ return; }
    TRACK.state=j.state||"stopped";
    TRACK.pauseReason=j.pause_reason||null;
    TRACK.runId=j.run_id||null;
    TRACK.trail=j.trail||[];
    TRACK.online=j.online;
    TRACK.error=j.error||null;
    TRACK.scopeOk=j.scope_ok!==false;
    renderTrack();
  }catch(_){}
}

async function trackAction(path){
  try{
    const r=await fetch(path,{method:"POST",headers:{"Content-Type":"application/json"},body:"{}"});
    const j=await r.json();
    if(j && !j.error && j.state){
      TRACK.state=j.state; TRACK.pauseReason=j.pause_reason||null;
      TRACK.runId=j.run_id||null; TRACK.trail=j.trail||TRACK.trail;
      TRACK.online=j.online; TRACK.error=j.error||null;
      TRACK.scopeOk=j.scope_ok!==false;
    }
    renderTrack();
  }catch(_){}
}

function renderTrack(){
  const active=TRACK.state==="active", paused=TRACK.state==="paused", stopped=TRACK.state==="stopped";
  $("#track-start").classList.toggle("hidden", !stopped);
  $("#track-pause").classList.toggle("hidden", !active);
  $("#track-resume").classList.toggle("hidden", !paused);
  $("#track-stop").classList.toggle("hidden", stopped);

  const st=$("#track-status");
  if(stopped) st.textContent="Not tracking — start a session to log your route.";
  else if(active) st.textContent=`● Live${TRACK.online===false?" (waiting for pilot)":""}`;
  else if(paused) st.textContent=TRACK.pauseReason==="auto"
    ? "⏸ Auto-paused — pilot logged out. Resumes when you're back in game."
    : "⏸ Paused.";
  st.className="track-status"+(active?" track-live":"");

  const err=$("#track-error");
  const msg=TRACK.error || (!TRACK.scopeOk && !stopped
    ? "Location access not granted yet — log out and back in to authorise the new permission."
    : null);
  err.classList.toggle("hidden", !msg);
  if(msg) err.textContent=msg;

  const rows=TRACK.trail;
  const tbl=$("#track-table"), tb=tbl.querySelector("tbody");
  const now=Date.now()/1000;
  let tripStart=rows.length?rows[0].entered_at:null;

  // Dwell is measured against the real next entry (or "now" for the last row) —
  // computed on the full trail so the filter never shifts a system's own dwell.
  // The min-dwell filter only hides short stops from the table; the underlying
  // trail is untouched. The current (last) system is always shown, since its
  // dwell is still accruing and would otherwise flicker in/out under the cutoff.
  const withDwell=rows.map((r,i)=>{
    const isLast=i+1>=rows.length;
    const nextTs=isLast? now : rows[i+1].entered_at;
    return {r, idx:i, isLast, dwell:nextTs-r.entered_at};
  });
  const min=TRACK.minDwell||0;
  const shown=withDwell.filter(x=>x.isLast || x.dwell>=min);
  const hiddenCount=withDwell.length-shown.length;

  tbl.classList.toggle("hidden", shown.length===0);
  let lastCargo=null;
  tb.innerHTML=shown.map((x,vi)=>{
    const r=x.r;
    let delta="";
    if(r.cargo_isk!=null){
      if(lastCargo!=null){
        const d=r.cargo_isk-lastCargo;
        delta=`<span class="${d>=0?'track-up':'track-down'}">${d>=0?"+":""}${fmtISK(d)}</span>`;
      }
      lastCargo=r.cargo_isk;
    }
    const band=secBand(r.security);
    return `<tr>
      <td>${vi+1}</td>
      <td class="track-sys">${authEsc(r.system_name)}</td>
      <td class="${band?'sec-'+band:''}">${fmtSec(r.security)}</td>
      <td>${fmtClock(r.entered_at)}</td>
      <td>${fmtDwell(x.dwell)}</td>
      <td class="track-scan"><input type="checkbox" data-at="${r.entered_at}" ${r.scanned?"checked":""}></td>
      <td class="track-cargo"><input type="number" min="0" step="1000000" placeholder="—"
          data-at="${r.entered_at}" value="${r.cargo_isk!=null?r.cargo_isk:""}"></td>
      <td>${delta}</td>
    </tr>`;
  }).join("");

  $("#track-hidden-note").textContent = (min>0 && hiddenCount>0)
    ? `${hiddenCount} short stop${hiddenCount===1?"":"s"} hidden` : "";

  const sum=$("#track-summary");
  if(rows.length){
    const jumps=rows.length-1;
    const scanned=rows.filter(r=>r.scanned).length;
    const cargoVals=rows.filter(r=>r.cargo_isk!=null).map(r=>r.cargo_isk);
    const haul=cargoVals.length>=2 ? cargoVals[cargoVals.length-1]-cargoVals[0] : null;
    const dur=tripStart? fmtDwell(now-tripStart) : "—";
    sum.textContent=`${rows.length} systems · ${jumps} jumps · ${scanned} scanned · ${dur}`
      + (haul!=null? ` · haul ${fmtISK(haul)}` : "");
  } else sum.textContent="";

  // Wire the per-row annotation inputs (re-bound each render — rows are cheap).
  tb.querySelectorAll(".track-scan input").forEach(cb=>{
    cb.onchange=()=>postPrefs("/api/track/scanned",{entered_at:+cb.dataset.at, scanned:cb.checked});
  });
  tb.querySelectorAll(".track-cargo input").forEach(inp=>{
    inp.onchange=()=>{
      postPrefs("/api/track/cargo",{entered_at:+inp.dataset.at, cargo_isk:inp.value});
      refreshTrail();  // re-pull so the Δ column recomputes against neighbours
    };
  });
}

// Read the min-dwell control (value × unit-in-seconds) back into TRACK.minDwell,
// persist it, and re-render live — no server round-trip, it's a view filter.
function applyMinDwellFromInputs(){
  const val=Math.max(0, +($("#track-min-dwell").value) || 0);
  const unit=+($("#track-min-unit").value) || 1;
  TRACK.minDwell=val*unit;
  saveTrackMinDwell();
  renderTrack();
}
function syncMinDwellInputs(){
  // Show the stored threshold in whole minutes when it divides evenly, else secs.
  const sec=TRACK.minDwell||0;
  const inp=$("#track-min-dwell"), unit=$("#track-min-unit");
  if(!inp||!unit) return;
  if(sec>0 && sec%60===0){ unit.value="60"; inp.value=String(sec/60); }
  else { unit.value="1"; inp.value=String(sec); }
}

document.addEventListener("DOMContentLoaded", ()=>{
  const b=(id,fn)=>{ const el=$(id); if(el) el.onclick=fn; };
  b("#track-start", ()=>trackAction("/api/track/start"));
  b("#track-pause", ()=>trackAction("/api/track/pause"));
  b("#track-resume", ()=>trackAction("/api/track/resume"));
  b("#track-stop", ()=>trackAction("/api/track/stop"));
  loadTrackMinDwell();
  syncMinDwellInputs();
  const md=$("#track-min-dwell"), mu=$("#track-min-unit");
  if(md) md.oninput=applyMinDwellFromInputs;
  if(mu) mu.onchange=applyMinDwellFromInputs;
});
