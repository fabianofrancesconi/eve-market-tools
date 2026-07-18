// ── Exploration journal (live tracking + session history) ──────────────────
// Lives inside the Exploration module's "Journal" mode. The server tracks the
// active character's route while a session runs (background thread — it keeps
// going no matter what you browse). This renders the live session controls, the
// session list, and the selected session's trail + per-session annotations
// (name, per-system cargo value, per-system + overall notes). Dwell is derived client-side; a
// min-dwell filter hides short stops from the table without truncating the trail.

const TRACK = { state:"stopped", pauseReason:null, liveRunId:null, online:null,
                error:null, scopeOk:true, sessions:[], selRunId:null,
                detail:null, trail:[], minDwell:0, showHidden:false,
                showManualHidden:false };
// Min-dwell journey filter — server-authoritative ('track_min_dwell' pref).
function loadTrackMinDwell(){
  const v = (typeof getPref==="function") ? getPref('track_min_dwell', 0) : 0;
  TRACK.minDwell = Math.max(0, +v || 0);
}
function saveTrackMinDwell(){
  if(typeof setPref==="function") setPref('track_min_dwell', TRACK.minDwell);
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
function fmtDay(ts){
  if(!ts) return "";
  return new Date(ts*1000).toLocaleDateString([], {day:"2-digit", month:"short"});
}
// Compact "as of" label for a cargo scan: HH:MM, prefixed with the date when the
// reading isn't from today (an ESI-cached value can be hours/days old), plus the
// age in parentheses so its staleness is obvious at a glance.
function fmtScanAge(ts){
  if(!ts) return "—";
  const d=new Date(ts*1000), now=new Date();
  const sameDay = d.toDateString()===now.toDateString();
  const clock = fmtClock(ts);
  const stamp = sameDay ? clock : `${fmtDay(ts)} ${clock}`;
  const secs=Math.max(0, Math.round(now.getTime()/1000 - ts));
  let age;
  if(secs<90) age="just now";
  else if(secs<5400) age=`${Math.round(secs/60)}m ago`;
  else if(secs<86400) age=`${Math.round(secs/3600)}h ago`;
  else age=`${Math.round(secs/86400)}d ago`;
  return `${stamp} · ${age}`;
}
// EVE security-status → the sec-band buckets the Arbitrage table already styles.
function secBand(sec){
  if(sec==null) return "";
  if(sec>=0.5) return "high";
  if(sec>0) return "low";
  return "null";
}
function fmtSec(sec){ return sec==null? "—" : sec.toFixed(1); }

// Cargo (ISK) input helpers: the field is a plain integer with thousands commas
// for readability. stripCargo() gives back the bare digits (or "" when blank)
// for the API; fmtCargoInput() re-adds the commas for display.
// A fetched cargo value is a float (e.g. 55798397.52), so drop anything from the
// decimal point on BEFORE removing separators — otherwise the fractional digits
// glue onto the integer and 55798397.52 shows as 5,579,839,752 (100× too big).
function stripCargo(v){ return String(v==null?"":v).split(".")[0].replace(/[^\d]/g, ""); }
function fmtCargoInput(v){
  const d=stripCargo(v);
  return d ? Number(d).toLocaleString("en-US") : "";
}

// ── data loads ─────────────────────────────────────────────────────────────

async function loadTrackStatus(){
  if(!AUTH.loggedIn) return;
  try{
    const j=await (await fetch("/api/track/status")).json();
    if(j.error) return;
    TRACK.state=j.state||"stopped";
    TRACK.pauseReason=j.pause_reason||null;
    TRACK.liveRunId=j.run_id||null;
    TRACK.online=j.online;
    TRACK.error=j.error||null;
    TRACK.scopeOk=j.scope_ok!==false;
  }catch(_){}
}
async function loadTrackSessions(){
  if(!AUTH.loggedIn) return;
  try{
    const j=await (await fetch("/api/track/sessions")).json();
    TRACK.sessions=j.sessions||[];
  }catch(_){}
}
async function loadTrackSession(runId){
  if(!AUTH.loggedIn || !runId) return;
  try{
    const j=await (await fetch("/api/track/session?run_id="+encodeURIComponent(runId))).json();
    if(j.error){ TRACK.detail=null; TRACK.trail=[]; return; }
    TRACK.detail=j.session||null;
    TRACK.trail=j.trail||[];
  }catch(_){}
}

// Full refresh: status + session list, then (re)load the selected session. The
// live session is selected by default; a manual pick sticks until it's deleted.
async function refreshJournal(){
  if(!AUTH.loggedIn) return;
  await Promise.all([loadTrackStatus(), loadTrackSessions()]);
  if(!TRACK.selRunId || !TRACK.sessions.some(s=>s.run_id===TRACK.selRunId))
    TRACK.selRunId = TRACK.liveRunId || (TRACK.sessions[0] && TRACK.sessions[0].run_id) || null;
  if(TRACK.selRunId) await loadTrackSession(TRACK.selRunId);
  else { TRACK.detail=null; TRACK.trail=[]; }
  renderJournal();
}

// Called by the SSE hook on any live push while the Exploration tab is open.
async function trackOnLivePush(){
  await loadTrackStatus();
  // Only reload the detail/list if the live session is the one on screen.
  if(TRACK.selRunId===TRACK.liveRunId){
    await Promise.all([loadTrackSessions(), loadTrackSession(TRACK.selRunId)]);
  } else {
    await loadTrackSessions();
  }
  renderJournal();
}

// ── actions ────────────────────────────────────────────────────────────────

async function trackAction(path){
  try{ await fetch(path,{method:"POST",headers:{"Content-Type":"application/json"},body:"{}"}); }
  catch(_){}
  // A fresh Start should select the new live session.
  if(path.endsWith("/start")) TRACK.selRunId=null;
  await refreshJournal();
}
function sessionUpdate(fields){
  if(!TRACK.selRunId) return;
  postPrefs("/api/track/session/update", Object.assign({run_id:TRACK.selRunId}, fields));
}
async function sessionDelete(){
  if(!TRACK.selRunId) return;
  const s=TRACK.sessions.find(x=>x.run_id===TRACK.selRunId);
  if(s && s.is_live){ return; }
  if(!confirm("Delete this session and its route? This can't be undone.")) return;
  try{ await fetch("/api/track/session/delete",{method:"POST",headers:{"Content-Type":"application/json"},
        body:JSON.stringify({run_id:TRACK.selRunId})}); }catch(_){}
  TRACK.selRunId=null;
  await refreshJournal();
}

// ── render ─────────────────────────────────────────────────────────────────

function renderJournal(){
  // Live-dot on the Journal mode button whenever a session is active/paused.
  // Green = live, yellow = paused.
  const dot=$("#exp-journal-live-dot");
  if(dot){
    dot.classList.toggle("hidden", TRACK.state==="stopped");
    dot.classList.toggle("exp-live-dot-paused", TRACK.state==="paused");
  }

  renderLiveControls();
  renderSessionList();
  renderSessionDetail();
}

function renderLiveControls(){
  const active=TRACK.state==="active", paused=TRACK.state==="paused", stopped=TRACK.state==="stopped";
  // Pause/Resume/Stop act on the ONE live session, so they belong to that
  // session — we only surface them when it's the one on screen. Viewing a past
  // session shows no controls. Start creates a NEW session, so it lives in the
  // sidebar (never on a session's card) and shows whenever nothing is live.
  const viewingLive = !stopped && TRACK.liveRunId!=null && TRACK.selRunId===TRACK.liveRunId;
  const btn=(id,show)=>{ const el=$(id); if(el) el.classList.toggle("hidden", !show); };
  btn("#track-start", stopped);
  btn("#track-pause", viewingLive && active);
  btn("#track-resume", viewingLive && paused);
  btn("#track-stop", viewingLive && !stopped);

  // The status line only ever describes the live session, so it shows solely when
  // that session is the one on screen. A closed session on screen shows no live
  // card — the coloured dot beside the live session in the list is its cue — so
  // "live session paused/in progress" can never appear to belong to a closed one.
  const st=$("#exp-live-status");
  if(st){
    if(viewingLive && active) st.textContent=`● Live${TRACK.online===false?" (waiting for pilot to come online…)":""}`;
    else if(viewingLive && paused) st.textContent=TRACK.pauseReason==="auto"
      ? "⏸ Auto-paused — pilot offline. Resumes automatically when you're back in game."
      : "⏸ Paused.";
    else st.textContent="";
    st.className="track-status"+((viewingLive&&active)?" track-live":"");
  }
  const err=$("#track-error");
  const msg=TRACK.error || (!TRACK.scopeOk && !stopped
    ? "Location access not granted yet — log out and back in to authorise the new permission."
    : null);
  if(err){ err.classList.toggle("hidden", !msg); if(msg) err.textContent=msg; }
  // Hide the whole live card unless the live session is on screen or there's an
  // error to surface — otherwise it's an empty box atop a closed session.
  const card=$("#exp-live");
  if(card) card.classList.toggle("hidden", !viewingLive && !msg);
}

function renderSessionList(){
  const wrap=$("#exp-session-list");
  if(!wrap) return;
  if(!TRACK.sessions.length){
    wrap.innerHTML=`<div class="exp-session-empty">No sessions yet.</div>`;
    return;
  }
  wrap.innerHTML=TRACK.sessions.map(s=>{
    const sel=s.run_id===TRACK.selRunId?" active":"";
    // The live session's dot mirrors the mode-button dot: green while active,
    // yellow while paused — so a paused session reads as paused everywhere, not
    // green in the list and yellow up top.
    const live=s.is_live
      ? `<span class="exp-live-dot${TRACK.state==="paused"?" exp-live-dot-paused":""}"></span>`
      : "";
    // Total duration: end→now for a live/ongoing session, else end−start.
    const endTs=s.ended_at || (Date.now()/1000);
    const dur=(s.started_at!=null)? fmtDwell(endTs-s.started_at) : null;
    const stats=`${s.systems} sys${dur?` · ${dur}`:""}${s.cargo_value!=null?` · ${fmtISK(s.cargo_value)} ISK`:""}`;
    return `<button class="exp-session-item${sel}" type="button" data-run="${authEsc(s.run_id)}">
      <span class="exp-session-item-name">${live}${authEsc(s.name)}</span>
      <span class="exp-session-item-meta">${fmtDay(s.started_at)} · ${stats}</span>
    </button>`;
  }).join("");
  wrap.querySelectorAll(".exp-session-item").forEach(b=>{
    b.onclick=async ()=>{ TRACK.selRunId=b.dataset.run; await loadTrackSession(TRACK.selRunId); renderJournal(); };
  });
}

function renderSessionDetail(){
  const detailWrap=$("#exp-session-detail");
  if(!detailWrap) return;
  if(!TRACK.detail){ detailWrap.classList.add("hidden"); return; }
  detailWrap.classList.remove("hidden");
  const d=TRACK.detail;

  // Name (edit box) — don't clobber what the user is mid-typing.
  const nameEl=$("#exp-session-name");
  if(nameEl && document.activeElement!==nameEl) nameEl.value=d.name||"";

  const cargoTotEl=$("#exp-session-cargo-total");
  if(cargoTotEl) cargoTotEl.textContent=(d.cargo_value!=null?fmtISK(d.cargo_value):"—");

  const notesEl=$("#exp-session-notes");
  if(notesEl && document.activeElement!==notesEl) notesEl.value=d.notes||"";

  // Delete allowed only for non-live sessions.
  const delBtn=$("#exp-session-delete");
  if(delBtn) delBtn.classList.toggle("hidden", !!d.is_live);

  const meta=$("#exp-session-meta");
  if(meta){
    const when=d.ended_at
      ? `${fmtDay(d.started_at)} ${fmtClock(d.started_at)} → ${fmtClock(d.ended_at)}`
      : `${fmtDay(d.started_at)} ${fmtClock(d.started_at)} · ongoing`;
    meta.textContent=`${when} · ${d.systems} systems · ${d.jumps} jumps`;
  }

  renderTrail();
}

function renderTrail(){
  const rows=TRACK.trail;
  const tbl=$("#track-table"), tb=tbl?tbl.querySelector("tbody"):null;
  const emptyEl=$("#exp-session-empty");
  if(!tbl||!tb) return;
  const now=Date.now()/1000;
  const isLive=TRACK.detail && TRACK.detail.is_live;

  // Dwell measured against the real next entry (or "now" for the live session's
  // last row; a finished session's last row uses ended_at). The filter only
  // hides short stops; the current live system is always shown.
  const endTs=(TRACK.detail && TRACK.detail.ended_at) ? TRACK.detail.ended_at : now;
  // Cargo carries forward for display: a system with no value of its own shows
  // the previous system's value greyed out (inherited) and editable. `eff` is
  // the effective (shown) value; `inherited` flags a carried-not-typed value.
  let carry=null;
  const withDwell=rows.map((r,i)=>{
    const last=i+1>=rows.length;
    const nextTs=last? endTs : rows[i+1].entered_at;
    const own=r.cargo_isk;
    const inherited = own==null && carry!=null;
    const eff = own!=null ? own : carry;
    const prevEff = carry;                 // effective value of the previous row
    // ISK delta vs the previous system's effective value — what changed here.
    const delta=(eff!=null && prevEff!=null) ? eff-prevEff : null;
    carry = eff;
    return {r, last, eff, inherited, delta, dwell:Math.max(0,nextTs-r.entered_at)};
  });
  const min=TRACK.minDwell||0;
  // A row shows unless it's a manually-hidden system or a too-short stop. The
  // live session's current system is always shown. Manual hides and short stops
  // are tallied separately so each gets its own reveal control.
  const isShown = x => (isLive&&x.last) || (!x.r.hidden && x.dwell>=min);
  const shown=withDwell.filter(isShown);
  const hiddenRows=withDwell.filter(x=>!isShown(x) && !x.r.hidden);   // short stops only
  const manualHidden=withDwell.filter(x=>!isShown(x) && x.r.hidden);
  const hidden=hiddenRows.length;

  tbl.classList.toggle("hidden", shown.length===0);
  const cap=$("#track-table-caption");
  if(cap) cap.classList.toggle("hidden", shown.length===0);
  if(emptyEl) emptyEl.classList.toggle("hidden", rows.length!==0);
  // "You are here": the last row of a live, active session is the system the
  // pilot is currently sitting in (dwell still counting up).
  const liveActive = isLive && TRACK.state==="active";
  // The ▸ marker only appears when the last row is shown for a live session —
  // reveal its legend under exactly the same condition.
  const hasHere = liveActive && shown.length>0 && shown[shown.length-1].last;
  const legend=$("#track-here-legend");
  if(legend) legend.classList.toggle("hidden", !hasHere);
  tb.innerHTML=shown.map((x,vi)=>{
    const r=x.r, band=secBand(r.security);
    // The ▸ in the # column marks the current system; no text badge needed.
    const here = liveActive && x.last;
    // Region/constellation as a subtitle under the system name — resolved
    // server-side, so old trails may lack it.
    const sub = r.region
      ? `<span class="track-sys-sub">${authEsc(r.region)}${r.constellation?` · ${authEsc(r.constellation)}`:""}</span>`
      : "";
    // Inherited (carried-forward) values render greyed via .inherited; the value
    // is a real editable default the user can overwrite to record this system.
    const cargoVal = fmtCargoInput(x.eff);
    const cargoCls = "track-cargo-input" + (x.inherited ? " inherited" : "");
    const delta = x.delta ? `<span class="track-cargo-delta ${x.delta>0?'pos':'neg'}" title="Change vs the previous system">${x.delta>0?'+':'−'}${fmtISK(Math.abs(x.delta))}</span>` : "";
    // "as of <when>" under the value when it came from an ESI cargo fetch (own row
    // only — a carried-forward value belongs to an earlier system's scan). The time
    // is ESI's Last-Modified (when CCP last refreshed the assets), not the fetch
    // click — the honest data age. If the scan hit items with no market price, flag
    // it (⚠) so the total isn't trusted blindly.
    const unpriced = (TRACK.unpricedByRow||{})[r.entered_at];
    const scanTip = "EVE asset data as of this time (ESI Last-Modified; assets are "
      + "cached ~1h, so this may lag what's physically in the hold)."
      + (unpriced ? ` No market price for: ${unpriced.join(", ")}.` : "");
    const scanAt = (!x.inherited && r.cargo_scanned_at)
      ? `<span class="track-cargo-scanat" title="${authEsc(scanTip)}">as of ${fmtScanAge(r.cargo_scanned_at)}${unpriced?" ⚠":""}</span>` : "";
    return `<tr class="${here?'track-here':''}" title="${here?'You are here':''}">
      <td>${here?'▸':(vi+1)}</td>
      <td class="track-sys"><span class="track-sys-name">${authEsc(r.system_name)}</span>${sub}</td>
      <td class="${band?'sec-'+band:''}">${fmtSec(r.security)}</td>
      <td>${fmtClock(r.entered_at)}</td>
      <td>${fmtDwell(x.dwell)}${here?" · now":""}</td>
      <td class="track-cargo num"><input type="text" inputmode="numeric" placeholder="—" class="${cargoCls}" data-at="${r.entered_at}" value="${cargoVal}"><button class="track-cargo-fetch" type="button" data-at="${r.entered_at}" title="Fetch this ship's cargo from ESI and value it at Jita (assets are cached ~1h)">⟳</button>${delta}${scanAt}</td>
      <td class="track-note">${noteBtnHtml(r)}</td>
      <td class="track-hide"><button class="track-hide-btn" type="button" data-at="${r.entered_at}" title="Hide this system from the journal">✕</button></td>
    </tr>`;
  }).join("");

  // Reveal controls, one per hidden bucket:
  //  • "N short stops hidden" — filtered by the min-dwell slider (chips only).
  //  • "N systems hidden" — manually hidden by the user; each chip has an
  //    unhide (↺) button so they can bring a system back.
  const note=$("#track-hidden-note");
  if(note){
    let html="";
    if(min>0 && hidden>0){
      const chips=hiddenRows.map(x=>{
        const band=secBand(x.r.security);
        return `<span class="track-hidden-chip ${band?'sec-'+band:''}">${authEsc(x.r.system_name)} <span class="track-hidden-dwell">${fmtDwell(x.dwell)}</span></span>`;
      }).join("");
      html+=`<button class="track-hidden-toggle" data-which="short" type="button">${hidden} short stop${hidden===1?"":"s"} hidden ${TRACK.showHidden?"▾":"▸"}</button>`
        + (TRACK.showHidden? `<div class="track-hidden-list">${chips}</div>` : "");
    }
    if(manualHidden.length){
      const chips=manualHidden.map(x=>{
        const band=secBand(x.r.security);
        return `<span class="track-hidden-chip ${band?'sec-'+band:''}">${authEsc(x.r.system_name)} <button class="track-unhide-btn" type="button" data-at="${x.r.entered_at}" title="Unhide this system">↺</button></span>`;
      }).join("");
      html+=`<button class="track-hidden-toggle" data-which="manual" type="button">${manualHidden.length} system${manualHidden.length===1?"":"s"} hidden ${TRACK.showManualHidden?"▾":"▸"}</button>`
        + (TRACK.showManualHidden? `<div class="track-hidden-list">${chips}</div>` : "");
    }
    note.innerHTML=html;
    note.querySelectorAll(".track-hidden-toggle").forEach(tgl=>{
      tgl.onclick=()=>{
        if(tgl.dataset.which==="manual") TRACK.showManualHidden=!TRACK.showManualHidden;
        else TRACK.showHidden=!TRACK.showHidden;
        renderTrail();
      };
    });
    note.querySelectorAll(".track-unhide-btn").forEach(btn=>{
      btn.onclick=()=>setSystemHidden(+btn.dataset.at, false);
    });
  }

  tb.querySelectorAll(".track-cargo input").forEach(inp=>{
    // Editing an inherited (greyed) value promotes it to this system's own value.
    inp.onfocus=()=>inp.classList.remove("inherited");
    // Re-group the digits with commas as the user types, keeping the caret near
    // the end (this is an append-heavy field, so a full re-place reads fine).
    inp.oninput=()=>{ inp.value=fmtCargoInput(inp.value); };
    inp.onchange=async ()=>{
      const digits=stripCargo(inp.value);       // server wants a bare number / blank
      await fetch("/api/track/cargo",{method:"POST",headers:{"Content-Type":"application/json"},
        body:JSON.stringify({entered_at:+inp.dataset.at, cargo_isk:digits})});
      // Re-pull so the session total (latest system's value) reflects the edit.
      if(TRACK.selRunId){ await loadTrackSession(TRACK.selRunId); await loadTrackSessions(); renderJournal(); }
    };
  });

  // "⟳ Fetch": pull the real cargo from ESI, value it at Jita, fill this row.
  // Success is shown inline (the value + a "scanned HH:MM" stamp) — no popup. Only
  // errors (e.g. missing scopes) interrupt. Unpriced items (datacores, BPCs with no
  // market price) are recorded per-row so the stamp can flag that the total may
  // understate the haul.
  tb.querySelectorAll(".track-cargo-fetch").forEach(btn=>{
    btn.onclick=async ()=>{
      btn.disabled=true; const glyph=btn.textContent; btn.textContent="…";
      try{
        const r=await fetch("/api/track/cargo/fetch",{method:"POST",
          headers:{"Content-Type":"application/json"},
          body:JSON.stringify({entered_at:+btn.dataset.at})});
        const j=await r.json();
        if(j.error){ alert(j.error); return; }
        TRACK.unpricedByRow = TRACK.unpricedByRow || {};
        if(j.unpriced && j.unpriced.length) TRACK.unpricedByRow[+btn.dataset.at] = j.unpriced;
        else delete TRACK.unpricedByRow[+btn.dataset.at];
        if(TRACK.selRunId){ await loadTrackSession(TRACK.selRunId); await loadTrackSessions(); renderJournal(); }
      }catch(e){ alert("Cargo fetch failed."); }
      finally{ btn.disabled=false; btn.textContent=glyph; }
    };
  });
  tb.querySelectorAll(".track-note-btn").forEach(btn=>{
    btn.onclick=()=>openNoteModal(+btn.dataset.at);
  });
  tb.querySelectorAll(".track-hide-btn").forEach(btn=>{
    btn.onclick=()=>setSystemHidden(+btn.dataset.at, true);
  });
}

// Manually hide/unhide one system from the journal, then refresh.
async function setSystemHidden(enteredAt, hidden){
  await fetch("/api/track/hide",{method:"POST",headers:{"Content-Type":"application/json"},
    body:JSON.stringify({entered_at:enteredAt, hidden})});
  if(TRACK.selRunId){ await loadTrackSession(TRACK.selRunId); await loadTrackSessions(); renderJournal(); }
}

// A per-system note button: 📝 when empty, a filled chip previewing the note
// (with the full text on hover) when set. Clicking opens the note modal.
function noteBtnHtml(r){
  const has=(r.note||"").trim().length>0;
  const at=r.entered_at;
  if(!has) return `<button class="track-note-btn" type="button" data-at="${at}" title="Add a note for ${authEsc(r.system_name)}">📝</button>`;
  const preview=r.note.length>28 ? r.note.slice(0,27)+"…" : r.note;
  return `<button class="track-note-btn has-note" type="button" data-at="${at}" title="${authEsc(r.note)}">📝 ${authEsc(preview)}</button>`;
}

// ── per-system note modal ────────────────────────────────────────────────────

function openNoteModal(enteredAt){
  const row=TRACK.trail.find(r=>Math.abs(r.entered_at-enteredAt)<1e-6);
  if(!row) return;
  const modal=$("#trackNoteModal");
  $("#track-note-title").textContent=`Note — ${row.system_name}`;
  const ta=$("#track-note-text");
  ta.value=row.note||"";
  ta.dataset.at=enteredAt;
  modal.classList.remove("hidden");
  ta.focus();
}
function closeNoteModal(){ $("#trackNoteModal").classList.add("hidden"); }
async function saveNoteModal(){
  const ta=$("#track-note-text");
  const at=+ta.dataset.at;
  postPrefs("/api/track/note",{entered_at:at, note:ta.value});
  // Keep the in-memory row in sync so the button re-renders without a round-trip.
  const row=TRACK.trail.find(r=>Math.abs(r.entered_at-at)<1e-6);
  if(row) row.note=ta.value;
  closeNoteModal();
  renderTrail();
}

// ── min-dwell filter control ─────────────────────────────────────────────────

function applyMinDwellFromInputs(){
  const val=Math.max(0, +($("#track-min-dwell").value) || 0);
  const unit=+($("#track-min-unit").value) || 1;
  TRACK.minDwell=val*unit;
  saveTrackMinDwell();
  renderTrail();
}
function syncMinDwellInputs(){
  const sec=TRACK.minDwell||0;
  const inp=$("#track-min-dwell"), unit=$("#track-min-unit");
  if(!inp||!unit) return;
  if(sec>0 && sec%60===0){ unit.value="60"; inp.value=String(sec/60); }
  else { unit.value="1"; inp.value=String(sec); }
}

// ── wiring ───────────────────────────────────────────────────────────────────

document.addEventListener("DOMContentLoaded", ()=>{
  const b=(id,fn)=>{ const el=$(id); if(el) el.onclick=fn; };
  b("#track-start", ()=>trackAction("/api/track/start"));
  b("#track-pause", ()=>trackAction("/api/track/pause"));
  b("#track-resume", ()=>trackAction("/api/track/resume"));
  b("#track-stop", ()=>trackAction("/api/track/stop"));
  b("#exp-session-delete", sessionDelete);

  const nameEl=$("#exp-session-name");
  if(nameEl) nameEl.onchange=()=>sessionUpdate({name:nameEl.value});
  const notesEl=$("#exp-session-notes");
  if(notesEl) notesEl.onchange=()=>sessionUpdate({notes:notesEl.value});
  const notesToggle=$("#exp-session-notes-toggle");
  if(notesToggle) notesToggle.onclick=()=>$("#exp-session-notes-wrap").classList.toggle("hidden");

  loadTrackMinDwell();
  syncMinDwellInputs();
  const md=$("#track-min-dwell"), mu=$("#track-min-unit");
  if(md) md.oninput=applyMinDwellFromInputs;
  if(mu) mu.onchange=applyMinDwellFromInputs;

  // Per-system note modal.
  b("#track-note-save", saveNoteModal);
  b("#track-note-cancel", closeNoteModal);
  const noteModal=$("#trackNoteModal");
  if(noteModal) noteModal.addEventListener("click", e=>{ if(e.target.id==="trackNoteModal") closeNoteModal(); });
  const noteTa=$("#track-note-text");
  if(noteTa) noteTa.addEventListener("keydown", e=>{
    // Ctrl/⌘-Enter saves; Escape cancels. Plain Enter stays a newline.
    if(e.key==="Enter" && (e.ctrlKey||e.metaKey)){ e.preventDefault(); saveNoteModal(); }
    else if(e.key==="Escape"){ e.preventDefault(); closeNoteModal(); }
  });
});
