// ══════════════════════════════════════════════════════════════════════════
// NOTES TAB
// ══════════════════════════════════════════════════════════════════════════
const NOTES = { items:[], loaded:false, active:null, saveTimer:null, dragId:null, fontSize:14 };
const NOTES_MAX_DEPTH = 3;
const NOTES_FONT_MIN = 10, NOTES_FONT_MAX = 28;
try{ const f=parseInt(localStorage.getItem("notes-font-size"),10); if(f>=NOTES_FONT_MIN && f<=NOTES_FONT_MAX) NOTES.fontSize=f; }catch(e){}

function _setNoteFont(px){
  NOTES.fontSize=Math.max(NOTES_FONT_MIN, Math.min(NOTES_FONT_MAX, px));
  try{ localStorage.setItem("notes-font-size", String(NOTES.fontSize)); }catch(e){}
  const ta=$("#note-body"); if(ta) ta.style.fontSize=NOTES.fontSize+"px";
}

function _uid(){ return Date.now().toString(36)+Math.random().toString(36).slice(2,8); }

function _isRootParent(p){ return !p||p==="None"||p==="null"; }
function _noteDepth(id){
  let depth=0, cur=NOTES.items.find(x=>x.id===id);
  while(cur && !_isRootParent(cur.parent_id)){ depth++; cur=NOTES.items.find(x=>x.id===cur.parent_id); }
  return depth;
}

function _subtreeMaxDepth(id){
  let max=0;
  function walk(pid, d){ NOTES.items.filter(n=>n.parent_id===pid).forEach(n=>{ if(d>max) max=d; if(n.kind==="folder") walk(n.id,d+1); }); }
  walk(id, 1);
  return max;
}

async function loadNotes(){
  try{
    const r=await fetch("/api/notes"); const d=await r.json();
    NOTES.items=d.notes||[];
    NOTES.loaded=true;
    renderNotesTree();
  }catch(e){ console.error("notes load",e); }
}

function renderNotesTree(){
  const tree=$("#notes-tree");
  tree.innerHTML="";
  const byParent={};
  NOTES.items.forEach(n=>{ const p=(n.parent_id&&n.parent_id!=="None"&&n.parent_id!=="null")?n.parent_id:"__root__"; (byParent[p]=byParent[p]||[]).push(n); });
  function build(parentId, container, depth){
    const children=byParent[parentId]||[];
    children.sort((a,b)=>a.pos-b.pos);
    children.forEach(n=>{
      if(n.kind==="folder"){
        const wrap=document.createElement("div");
        wrap.className="notes-tree-folder";
        wrap.dataset.id=n.id;
        const item=_makeTreeItem(n, depth);
        wrap.appendChild(item);
        const sub=document.createElement("div");
        sub.className="notes-tree-children";
        sub.dataset.folderId=n.id;
        sub.addEventListener("dragover", _onDragOver);
        sub.addEventListener("drop", e=>_onDrop(e, n.id));
        build(n.id, sub, depth+1);
        wrap.appendChild(sub);
        container.appendChild(wrap);
      } else {
        const item=_makeTreeItem(n, depth);
        container.appendChild(item);
      }
    });
  }
  tree.dataset.folderId="__root__";
  tree.addEventListener("dragover", _onDragOver);
  tree.addEventListener("drop", e=>_onDrop(e, null));
  build("__root__", tree, 0);
}

function _makeTreeItem(n, depth){
  const item=document.createElement("div");
  item.className="notes-tree-item"+(NOTES.active===n.id?" active":"");
  item.draggable=true;
  item.dataset.id=n.id;
  const icon=n.kind==="folder"?"📁":"📄";
  const label=n.title||(n.kind==="folder"?"Untitled folder":"Untitled");
  item.innerHTML=`<span class="ni-icon">${icon}</span><span class="ni-label">${_esc(label)}</span><span class="ni-actions"><button class="ni-btn ni-rename" title="Rename">✎</button><button class="ni-btn ni-del" title="Delete">✕</button></span>`;
  item.onclick=e=>{
    if(e.target.closest(".ni-actions")||e.target.tagName==="INPUT") return;
    selectNote(n.id);
  };
  item.querySelector(".ni-rename").onclick=e=>{ e.stopPropagation(); _inlineEditLabel(item, n); };
  item.querySelector(".ni-del").onclick=e=>{ e.stopPropagation(); deleteNote(n.id); };
  item.addEventListener("dragstart", e=>{
    NOTES.dragId=n.id;
    e.dataTransfer.effectAllowed="move";
    item.style.opacity="0.5";
  });
  item.addEventListener("dragend", ()=>{ item.style.opacity=""; NOTES.dragId=null; });
  return item;
}

function _inlineEditLabel(item, n){
  const labelSpan=item.querySelector(".ni-label");
  const inp=document.createElement("input");
  inp.type="text"; inp.value=n.title;
  inp.className="ni-inline-edit";
  inp.style.cssText="background:var(--bg);border:1px solid var(--cyan2);color:var(--fg);font:inherit;font-size:12.5px;padding:1px 4px;border-radius:3px;width:100%;outline:none;";
  labelSpan.replaceWith(inp);
  inp.focus(); inp.select();
  const commit=()=>{
    n.title=inp.value;
    _persistNote(n);
    renderNotesTree();
    const roEl=document.querySelector(".note-title-ro");
    if(roEl && NOTES.active===n.id) roEl.textContent=n.title||"Untitled";
  };
  inp.addEventListener("keydown", e=>{
    if(e.key==="Enter"){ e.preventDefault(); commit(); }
    if(e.key==="Escape"){ renderNotesTree(); }
  });
  inp.addEventListener("blur", commit);
}

function _onDragOver(e){
  e.preventDefault(); e.stopPropagation(); e.dataTransfer.dropEffect="move";
  document.querySelectorAll(".drag-over").forEach(el=>el.classList.remove("drag-over"));
  e.currentTarget.classList.add("drag-over");
}

function _onDrop(e, targetFolderId){
  e.preventDefault(); e.stopPropagation();
  document.querySelectorAll(".drag-over").forEach(el=>el.classList.remove("drag-over"));
  const dragId=NOTES.dragId; if(!dragId) return;
  const dragged=NOTES.items.find(x=>x.id===dragId); if(!dragged) return;
  if(targetFolderId===dragId) return;
  // Prevent dropping a folder into its own descendant
  let check=targetFolderId;
  while(check){ if(check===dragId) return; const p=NOTES.items.find(x=>x.id===check); check=p?p.parent_id:null; }
  // Enforce max depth
  const targetDepth=targetFolderId?_noteDepth(targetFolderId)+1:0;
  const dragSubDepth=dragged.kind==="folder"?_subtreeMaxDepth(dragId):0;
  if(targetDepth+dragSubDepth>=NOTES_MAX_DEPTH && dragged.kind==="folder") return;
  if(targetDepth>=NOTES_MAX_DEPTH) return;
  // Move
  dragged.parent_id=targetFolderId||null;
  dragged.pos=NOTES.items.filter(n=>(n.parent_id||null)===(targetFolderId||null)&&n.id!==dragId).length;
  renderNotesTree();
  _persistNote(dragged);
}

function _esc(s){ const d=document.createElement("span"); d.textContent=s; return d.innerHTML; }

function selectNote(id){
  if(id!==NOTES.active) _flushActiveNote();   // save the outgoing note first
  NOTES.active=id;
  renderNotesTree();
  const n=NOTES.items.find(x=>x.id===id);
  const ed=$("#notes-editor");
  if(!n){ ed.innerHTML=`<div class="notes-empty">Select or create a note</div>`; return; }
  const title=_esc(n.title||((n.kind==="folder")?"Untitled folder":"Untitled"));
  if(n.kind==="folder"){
    ed.innerHTML=`<div class="notes-editor-hdr"><span class="note-title-ro">${title}</span></div><div class="notes-empty">Folder — select a note inside, or drag items here</div>`;
  } else {
    ed.innerHTML=`<div class="notes-editor-hdr"><span class="note-title-ro">${title}</span><span class="notes-font-ctl"><button class="nf-btn" id="note-font-dec" data-tip="Smaller text">A−</button><button class="nf-btn" id="note-font-inc" data-tip="Larger text">A+</button></span></div><div class="notes-editor-body"><textarea id="note-body" placeholder="Write here…">${_esc(n.body)}</textarea></div>`;
    const ta=$("#note-body");
    ta.style.fontSize=NOTES.fontSize+"px";
    ta.oninput=()=>_scheduleNoteSave(id);
    $("#note-font-dec").onclick=()=>_setNoteFont(NOTES.fontSize-1);
    $("#note-font-inc").onclick=()=>_setNoteFont(NOTES.fontSize+1);
  }
}

function _scheduleNoteSave(id){
  if(NOTES.saveTimer) clearTimeout(NOTES.saveTimer);
  NOTES.saveTimer=setTimeout(()=>_saveActiveNote(id), 2000);
}

// Persist the note whose body the editor is CURRENTLY showing. The debounce
// timer must not read #note-body after the editor has been swapped to another
// note, or it would write that other note's text into `id` — so we only commit
// the textarea when `id` is still the active (displayed) note. If the editor
// already moved on, the switch itself flushed the old note (see
// _flushActiveNote), so there is nothing to lose here.
function _saveActiveNote(id){
  const n=NOTES.items.find(x=>x.id===id); if(!n) return;
  const bodyEl=$("#note-body");
  if(bodyEl && NOTES.active===id) n.body=bodyEl.value;
  _persistNote(n);
}

// Commit the in-editor text of the active note NOW and cancel any pending
// debounce. Called before the editor is pointed at a different note (selectNote,
// addNote) so an in-flight edit is never dropped or misattributed.
function _flushActiveNote(){
  if(NOTES.saveTimer){ clearTimeout(NOTES.saveTimer); NOTES.saveTimer=null; }
  const id=NOTES.active;
  if(id==null) return;
  const n=NOTES.items.find(x=>x.id===id); if(!n || n.kind==="folder") return;
  const bodyEl=$("#note-body");
  if(bodyEl && bodyEl.value!==n.body){ n.body=bodyEl.value; _persistNote(n); }
}

async function _persistNote(n){
  try{
    await fetch("/api/notes/save",{method:"POST",headers:{"Content-Type":"application/json"},
      body:JSON.stringify({id:n.id, parent_id:n.parent_id, kind:n.kind, title:n.title, body:n.body, pos:String(n.pos)})});
    _showNotesToast();
  }catch(e){ console.error("note save",e); }
}

function _showNotesToast(){
  const t=$("#notes-toast"); t.classList.add("show");
  if(NOTES.toastTimer) clearTimeout(NOTES.toastTimer);
  NOTES.toastTimer=setTimeout(()=>t.classList.remove("show"), 2000);
}

async function addNote(kind, parentId){
  // Enforce depth
  const parentDepth=parentId?_noteDepth(parentId)+1:0;
  if(kind==="folder" && parentDepth>=NOTES_MAX_DEPTH-1) return;
  if(parentDepth>=NOTES_MAX_DEPTH) return;
  const id=_uid();
  const pos=NOTES.items.filter(n=>(n.parent_id||null)===(parentId||null)).length;
  const n={id, parent_id:parentId||null, kind, title:"", body:"", pos, created_at:Date.now()/1000, updated_at:Date.now()/1000};
  NOTES.items.push(n);
  selectNote(id);
  // Start inline rename in the tree
  const treeItem=document.querySelector(`.notes-tree-item[data-id="${id}"]`);
  if(treeItem) _inlineEditLabel(treeItem, n);
}

async function deleteNote(id){
  const n=NOTES.items.find(x=>x.id===id); if(!n) return;
  const children=NOTES.items.filter(x=>x.parent_id===id);
  const msg=n.kind==="folder"&&children.length
    ? `Delete folder "${n.title||"Untitled"}" and its ${children.length} item${children.length>1?"s":""}?`
    : `Delete ${n.kind==="folder"?"folder":"note"} "${n.title||"Untitled"}"?`;
  if(!confirm(msg)) return;
  const idsToRemove=new Set([id]);
  let queue=[id];
  while(queue.length){ const pid=queue.shift(); NOTES.items.filter(x=>x.parent_id===pid).forEach(x=>{ idsToRemove.add(x.id); queue.push(x.id); }); }
  NOTES.items=NOTES.items.filter(x=>!idsToRemove.has(x.id));
  if(idsToRemove.has(NOTES.active)){ NOTES.active=null; $("#notes-editor").innerHTML=`<div class="notes-empty">Select or create a note</div>`; }
  renderNotesTree();
  try{
    await fetch("/api/notes/delete",{method:"POST",headers:{"Content-Type":"application/json"},
      body:JSON.stringify({id})});
  }catch(e){ console.error("note del",e); }
}

$("#notes-add-folder").onclick=()=>{
  const sel=NOTES.active&&NOTES.items.find(x=>x.id===NOTES.active);
  const parent=(sel&&sel.kind==="folder")?sel.id:(sel?sel.parent_id:null);
  addNote("folder", parent);
};
$("#notes-add-note").onclick=()=>{
  const sel=NOTES.active&&NOTES.items.find(x=>x.id===NOTES.active);
  const parent=(sel&&sel.kind==="folder")?sel.id:(sel?sel.parent_id:null);
  addNote("note", parent);
};

