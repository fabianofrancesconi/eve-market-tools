// ── Exploration journal (live tracking + session history) ──────────────────
// Lives inside the Exploration module's "Journal" mode. The server tracks the
// active character's route while a session runs (background thread — it keeps
// going no matter what you browse). This renders the live session controls, the
// session list, and the selected session's trail + per-session annotations
// (name, per-system cargo value, per-system + overall notes). Dwell is derived client-side; a
// min-dwell filter hides short stops from the table without truncating the trail.

const TRACK = { state:"stopped", pauseReason:null, liveRunId:null, online:null,
                error:null, scopeOk:true, sessions:[], selRunId:null,
                detail:null, trail:[], minDwell:0 };
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
function fmtDay(ts){
  if(!ts) return "";
  return new Date(ts*1000).toLocaleDateString([], {day:"2-digit", month:"short"});
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
function stripCargo(v){ return String(v==null?"":v).replace(/[^\d]/g, ""); }
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
  const btn=(id,show)=>{ const el=$(id); if(el) el.classList.toggle("hidden", !show); };
  btn("#track-start", stopped);
  btn("#track-pause", active);
  btn("#track-resume", paused);
  btn("#track-stop", !stopped);

  const st=$("#exp-live-status");
  if(st){
    if(stopped) st.textContent="No live session — start one to log your route.";
    else if(active) st.textContent=`● Live${TRACK.online===false?" (waiting for pilot to come online…)":""}`;
    else if(paused) st.textContent=TRACK.pauseReason==="auto"
      ? "⏸ Auto-paused — pilot offline. Resumes automatically when you're back in game."
      : "⏸ Paused.";
    st.className="track-status"+(active?" track-live":"");
  }
  const err=$("#track-error");
  const msg=TRACK.error || (!TRACK.scopeOk && !stopped
    ? "Location access not granted yet — log out and back in to authorise the new permission."
    : null);
  if(err){ err.classList.toggle("hidden", !msg); if(msg) err.textContent=msg; }
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
    const live=s.is_live?`<span class="exp-live-dot"></span>`:"";
    const stats=`${s.systems} sys${s.cargo_value!=null?` · ${fmtISK(s.cargo_value)} ISK`:""}`;
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
  const withDwell=rows.map((r,i)=>{
    const last=i+1>=rows.length;
    const nextTs=last? endTs : rows[i+1].entered_at;
    return {r, last, dwell:Math.max(0,nextTs-r.entered_at)};
  });
  const min=TRACK.minDwell||0;
  const shown=withDwell.filter(x=>(isLive&&x.last) || x.dwell>=min);
  const hidden=withDwell.length-shown.length;

  tbl.classList.toggle("hidden", shown.length===0);
  const cap=$("#track-table-caption");
  if(cap) cap.classList.toggle("hidden", shown.length===0);
  if(emptyEl) emptyEl.classList.toggle("hidden", rows.length!==0);
  // "You are here": the last row of a live, active session is the system the
  // pilot is currently sitting in (dwell still counting up).
  const liveActive = isLive && TRACK.state==="active";
  tb.innerHTML=shown.map((x,vi)=>{
    const r=x.r, band=secBand(r.security);
    const here = liveActive && x.last;
    const badge = here ? ` <span class="track-here-badge">▸ you are here</span>` : "";
    return `<tr class="${here?'track-here':''}">
      <td>${here?'▸':(vi+1)}</td>
      <td class="track-sys">${authEsc(r.system_name)}${badge}</td>
      <td class="${band?'sec-'+band:''}">${fmtSec(r.security)}</td>
      <td>${fmtClock(r.entered_at)}</td>
      <td>${fmtDwell(x.dwell)}${here?" · now":""}</td>
      <td class="track-cargo num"><input type="text" inputmode="numeric" placeholder="—" data-at="${r.entered_at}" value="${fmtCargoInput(r.cargo_isk)}"></td>
      <td class="track-note">${noteBtnHtml(r)}</td>
    </tr>`;
  }).join("");

  const note=$("#track-hidden-note");
  if(note) note.textContent=(min>0 && hidden>0)? `${hidden} short stop${hidden===1?"":"s"} hidden` : "";

  tb.querySelectorAll(".track-cargo input").forEach(inp=>{
    // Re-group the digits with commas as the user types, keeping the caret near
    // the end (this is an append-heavy field, so a full re-place reads fine).
    inp.oninput=()=>{ inp.value=fmtCargoInput(inp.value); };
    inp.onchange=async ()=>{
      const digits=stripCargo(inp.value);       // server wants a bare number / blank
      await fetch("/api/track/cargo",{method:"POST",headers:{"Content-Type":"application/json"},
        body:JSON.stringify({entered_at:+inp.dataset.at, cargo_isk:digits})});
      // Re-pull so the session total (sum of rows) reflects the edit.
      if(TRACK.selRunId){ await loadTrackSession(TRACK.selRunId); await loadTrackSessions(); renderJournal(); }
    };
  });
  tb.querySelectorAll(".track-note-btn").forEach(btn=>{
    btn.onclick=()=>openNoteModal(+btn.dataset.at);
  });
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
