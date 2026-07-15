"""Guards the boot-time settings-load path against the post-deploy data-loss bug.

Durable settings live in Postgres (the per-account blob). Right after a redeploy
the server cold-starts: the first /api/settings request can come back
_server_synced:false even though the real blob exists (session/DB pool not warm
yet). The client used to treat that as "no saved settings", paint its defaults,
then push them back — quietly overwriting the good copy. The visible symptom was
Industry filters/build-location selection reverting to defaults after a deploy.

The load path now (a) retries a couple of times so a transient unsynced reply is
re-asked, and (b) if it STILL can't get the authoritative blob while an existing
local cache proves the account has synced before, paints from local but
suppresses pushing so the durable copy is protected. We exercise that exact
decision logic with node, plus assert the guard rails stay in the source.
"""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
_INIT_JS = _ROOT / "static" / "js" / "init.js"
_SHARED_JS = _ROOT / "static" / "js" / "shared.js"
_NODE = shutil.which("node")

# A self-contained re-implementation of the fetch+branch decision, kept in lock
# step with loadSettings(). Fed a list of per-attempt server replies and the
# local cache, it reports which source won and whether server-sync was suppressed.
_DRIVER = r"""
let syncPushed=false, suppressed=false;
function syncSettingsToServer(){ syncPushed=true; }
function suppressServerSync(){ suppressed=true; }
const LS_KEY="k";
const responses = %s;
const localBlob = %s;
let call=0;
globalThis.fetch = async ()=>({ json: async ()=> responses[Math.min(call++, responses.length-1)] });
globalThis.localStorage = { getItem:()=> localBlob?JSON.stringify(localBlob):null };
globalThis.setTimeout = (fn)=>fn();
async function _fetchSettings(){
  for(let attempt=0; attempt<3; attempt++){
    let r=null;
    try{ r=await (await fetch("/api/settings")).json(); }catch(e){}
    if(r && (r._server_synced || !r._logged_in)) return r;
    await new Promise(res=>setTimeout(res, 400*(attempt+1)));
    if(attempt===2) return r;
  }
  return null;
}
async function decide(){
  const server=await _fetchSettings();
  let s=null;
  if(server && server._server_synced){ s=server; }
  else if(server && server._logged_in){
    let local=null;
    try{ local=JSON.parse(localStorage.getItem(LS_KEY)); }catch(e){}
    if(local && Object.keys(local).length){ s=local; suppressServerSync(); }
  } else {
    try{ s=JSON.parse(localStorage.getItem(LS_KEY)); }catch(e){}
    if(!s) s=server;
  }
  process.stdout.write(JSON.stringify({attempts:call, chosen:(s&&s._tag)||null, suppressed, syncPushed}));
}
decide();
"""


def _run(responses, local):
    script = _DRIVER % (json.dumps(responses),
                        "null" if local is None else json.dumps(local))
    proc = subprocess.run([_NODE, "-e", script], capture_output=True,
                          text=True, timeout=30)
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


SYNCED = {"_server_synced": True, "_logged_in": True, "_tag": "server"}
UNSYNCED = {"_server_synced": False, "_logged_in": True, "_tag": "unsynced"}
LOGGED_OUT = {"_server_synced": False, "_logged_in": False, "_tag": "filed"}
LOCAL = {"_tag": "local"}


@pytest.mark.skipif(not _NODE, reason="node not available")
def test_warm_load_uses_server_copy_without_suppressing():
    r = _run([SYNCED], LOCAL)
    assert r == {"attempts": 1, "chosen": "server",
                 "suppressed": False, "syncPushed": False}


@pytest.mark.skipif(not _NODE, reason="node not available")
def test_coldstart_hiccup_retries_then_takes_server_copy():
    # Unsynced first, synced on the retry — must re-ask and win with the server.
    r = _run([UNSYNCED, SYNCED], LOCAL)
    assert r["chosen"] == "server"
    assert r["attempts"] == 2
    assert r["suppressed"] is False


@pytest.mark.skipif(not _NODE, reason="node not available")
def test_persistent_cold_miss_paints_local_and_suppresses_push():
    # The bug scenario: server never confirms, but a local cache proves this
    # account synced before — paint local, and DO NOT push over the durable copy.
    r = _run([UNSYNCED], LOCAL)
    assert r["chosen"] == "local"
    assert r["suppressed"] is True
    assert r["syncPushed"] is False


@pytest.mark.skipif(not _NODE, reason="node not available")
def test_fresh_character_with_no_local_cache_allows_seeding():
    # No durable row and no local cache = genuinely new: don't suppress, so the
    # first real saveLS() can seed the server.
    r = _run([UNSYNCED], None)
    assert r["suppressed"] is False


def test_source_keeps_retry_and_suppress_guards():
    init_src = _INIT_JS.read_text()
    shared_src = _SHARED_JS.read_text()
    assert "_fetchSettings" in init_src
    assert "suppressServerSync" in init_src
    # saveLS must honour the suppression flag.
    assert "_serverSyncSuppressed" in shared_src
    assert "if(!_serverSyncSuppressed) syncSettingsToServer(blob)" in shared_src
