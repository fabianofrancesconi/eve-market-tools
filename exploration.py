#!/usr/bin/env python3
"""
Exploration journal — extracted feature module for the monolith (``lp-web.py``).

This owns the whole "exploration tracking" tab: the per-system location trail,
the session journal (name / times / notes / cargo value), the live-session state
machine, the background location poll, and the ``do_track_*`` route handlers.

## How it plugs into the host

The monolith keeps its shared state and services as module-level globals on
``lp-web.py`` and the test-suite drives the feature by monkeypatching *that*
module's namespace (rebinding ``_TRACK_SESSIONS``, ``require_account``,
``_resolve_track_region``, the cache paths, …). To keep that contract intact
after moving the code out, every reference to a name that lives on the host — the
mutable tracking state, the cache paths, the shared services, and even sibling
handlers in this feature — is resolved through the bound host ``self._h`` at call
time. ``lp-web.py`` then re-exports these methods into its own namespace, so a
``monkeypatch.setattr(lp_web, …)`` still takes effect here.

The feature is a *class* (``Exploration``), instantiated once per host by
``bind(host)``. It is deliberately not a module-level singleton: the test-suite
imports ``lp-web.py`` several times under different names, and each of those hosts
needs its own binding — a shared global would leave every host pointing at
whichever loaded last.

Names that are the *same object* regardless of who imports them (stdlib, the
``lp_core`` / ``arb_core`` / ``sso_core`` / ``pg_store`` modules) are imported
directly — patches to their attributes are visible everywhere anyway.
"""
import secrets
import time

import requests

import arb_core
import pg_store
import sso_core
from lp_core import LPError, load_json, save_json

# Method names re-exported into the host module (see module docstring). Keeping
# the list explicit means the host binds exactly this surface — nothing leaks by
# accident.
EXPORTS = (
    "_resolve_track_system", "_resolve_track_region", "_ensure_track_loaded",
    "_persist_track_sessions", "_append_trail", "_enrich_trail_regions",
    "_query_trail", "_annotate_trail", "_delete_trail_run",
    "_session_record_upsert", "_session_record_patch", "_session_records_list",
    "_session_record_get", "_session_record_delete", "_default_session_name",
    "_set_session_state", "_mark_track_error", "_poll_location_once",
    "_bg_location_poll_loop", "_track_target_cid", "_live_state", "_open_run_id",
    "_session_summary", "do_track_status", "do_track_sessions", "do_track_session",
    "do_track_start", "do_track_pause", "do_track_resume", "do_track_stop",
    "do_track_scanned", "do_track_cargo", "do_track_cargo_fetch",
    "do_track_note", "do_track_hide",
    "do_track_session_update", "do_track_session_delete",
)


class Exploration:
    """The exploration-journal feature bound to one host module (the monolith).

    ``_h`` is the host: all shared state/services and sibling handlers are reached
    through it so host-level monkeypatches apply to calls made from here."""

    def __init__(self, host):
        self._h = host

    # ── system / region resolution (cached forever; systems never change) ────

    def _resolve_track_system(self, system_id):
        """{name, sec} for a solar system, cached forever (systems never change)."""
        info = arb_core.resolve_system(system_id, self._h._TRACK_SYSTEM_CACHE, self._h.SESSION)
        return info or {"name": str(system_id), "sec": None}

    def _resolve_track_region(self, system_id):
        """{region_id, region, constellation} for a solar system, or None. The
        system→constellation→region chain never changes, so it's cached both in
        memory and on disk (one ESI round-trip per system, ever)."""
        if not system_id:
            return None
        with self._h._TRACK_REGION_LOCK:
            if not self._h._TRACK_REGION_CACHE:
                self._h._TRACK_REGION_CACHE.update(load_json(self._h._TRACK_REGION_PATH, {}))
            hit = self._h._TRACK_REGION_CACHE.get(str(system_id))
            if hit is not None:
                return hit or None
        info = None
        try:
            r = self._h.SESSION.get(f"{arb_core.ESI}/universe/systems/{system_id}/",
                                    headers=arb_core.HEADERS, timeout=15)
            if r.status_code == 200:
                const_id = r.json().get("constellation_id")
                r2 = self._h.SESSION.get(f"{arb_core.ESI}/universe/constellations/{const_id}/",
                                         headers=arb_core.HEADERS, timeout=15)
                if r2.status_code == 200:
                    cd = r2.json()
                    region_id = cd.get("region_id")
                    const_name = cd.get("name")
                    region_name = self._h.REGION_NAMES.get(region_id)
                    if region_name is None and region_id:
                        r3 = self._h.SESSION.get(f"{arb_core.ESI}/universe/regions/{region_id}/",
                                                 headers=arb_core.HEADERS, timeout=15)
                        region_name = (r3.json().get("name")
                                       if r3.status_code == 200 else f"Region {region_id}")
                    info = {"region_id": region_id, "region": region_name,
                            "constellation": const_name}
        except requests.RequestException:
            return None
        with self._h._TRACK_REGION_LOCK:
            # Cache the miss ({}) too, so a transient outage isn't retried forever.
            self._h._TRACK_REGION_CACHE[str(system_id)] = info or {}
            save_json(self._h._TRACK_REGION_PATH, self._h._TRACK_REGION_CACHE)
        return info

    # ── in-memory session hydration / persistence ────────────────────────────

    def _ensure_track_loaded(self, acct):
        """Hydrate an account's persisted tracking sessions into memory once. An
        active session found on disk resumes as paused(auto) — we can't know the
        pilot is online until the loop next checks, so we don't sample blindly."""
        if acct is None or acct.account_id is None:
            return
        with self._h._TRACK_LOCK:
            if acct.account_id in self._h._TRACK_LOADED_ACCTS:
                return
            blob = self._h._acct_kv_load(acct, "location_tracking", self._h.LOCATION_TRACK_PATH, {}) or {}
            for cid_str, sess in blob.items():
                try:
                    cid = int(cid_str)
                except (TypeError, ValueError):
                    continue
                if sess.get("state") == "active":
                    sess["state"] = "paused"
                    sess["pause_reason"] = "auto"
                sess.setdefault("online", False)
                sess["online_checked_at"] = 0.0
                self._h._TRACK_SESSIONS[cid] = sess
            self._h._TRACK_LOADED_ACCTS.add(acct.account_id)

    def _persist_track_sessions(self, acct):
        """Write an account's in-memory sessions back to durable storage."""
        if acct is None or acct.account_id is None:
            return
        with acct.lock:
            char_ids = list(acct.characters.keys())
        with self._h._TRACK_LOCK:
            blob = {str(cid): self._h._TRACK_SESSIONS[cid]
                    for cid in char_ids if cid in self._h._TRACK_SESSIONS}
        self._h._acct_kv_save(acct, "location_tracking", self._h.LOCATION_TRACK_PATH, blob)

    # ── trail store (per-system entries) ─────────────────────────────────────

    def _append_trail(self, acct, cid, entered_at, run_id, system_id, system_name, security):
        if pg_store.enabled():
            pg_store.location_trail_append(acct.account_id, cid, entered_at, run_id,
                                           system_id, system_name, security)
        else:
            with self._h._TRAIL_RECORD_LOCK:
                store = load_json(self._h.LOCATION_TRAIL_PATH, {})
                store.setdefault(str(cid), []).append(
                    {"entered_at": entered_at, "run_id": run_id, "system_id": system_id,
                     "system_name": system_name, "security": security,
                     "scanned": False, "note": "", "hidden": False,
                     "cargo_scanned_at": None, "cargo_expires": None})
                save_json(self._h.LOCATION_TRAIL_PATH, store)

    def _enrich_trail_regions(self, rows):
        """Attach region/constellation to each trail row (cached forever). Kept out
        of storage so old trails gain the attribute without a migration."""
        for r in rows:
            reg = self._h._resolve_track_region(r.get("system_id"))
            if reg:
                r["region"] = reg.get("region")
                r["constellation"] = reg.get("constellation")
        return rows

    def _query_trail(self, acct, cid, run_id=None, since_ts=0.0):
        if pg_store.enabled():
            rows = pg_store.location_trail_query(acct.account_id, cid, run_id, since_ts)
        else:
            store = load_json(self._h.LOCATION_TRAIL_PATH, {})
            rows = store.get(str(cid), [])
            if run_id is not None:
                rows = [r for r in rows if r.get("run_id") == run_id]
            else:
                rows = [r for r in rows if r.get("entered_at", 0) >= since_ts]
            rows = sorted(rows, key=lambda r: r.get("entered_at", 0))
        return self._h._enrich_trail_regions(rows)

    def _annotate_trail(self, acct, cid, entered_at, scanned=None, cargo_isk=None,
                        note=None, hidden=None, cargo_scanned_at=None,
                        cargo_expires=None):
        if pg_store.enabled():
            pg_store.location_trail_annotate(acct.account_id, cid, entered_at,
                                             scanned=scanned, cargo_isk=cargo_isk,
                                             note=note, hidden=hidden,
                                             cargo_scanned_at=cargo_scanned_at,
                                             cargo_expires=cargo_expires)
            return
        with self._h._TRAIL_RECORD_LOCK:
            store = load_json(self._h.LOCATION_TRAIL_PATH, {})
            for r in store.get(str(cid), []):
                if abs(r.get("entered_at", 0) - entered_at) < 1e-6:
                    if scanned is not None:
                        r["scanned"] = bool(scanned)
                    if cargo_isk is not None:
                        r["cargo_isk"] = cargo_isk if cargo_isk != "" else None
                    if note is not None:
                        r["note"] = str(note)
                    if hidden is not None:
                        r["hidden"] = bool(hidden)
                    if cargo_scanned_at is not None:
                        r["cargo_scanned_at"] = (cargo_scanned_at
                                                 if cargo_scanned_at != "" else None)
                    if cargo_expires is not None:
                        r["cargo_expires"] = (cargo_expires
                                              if cargo_expires != "" else None)
                    break
            save_json(self._h.LOCATION_TRAIL_PATH, store)

    def _delete_trail_run(self, acct, cid, run_id):
        if pg_store.enabled():
            pg_store.location_trail_delete_run(acct.account_id, cid, run_id)
            return
        with self._h._TRAIL_RECORD_LOCK:
            store = load_json(self._h.LOCATION_TRAIL_PATH, {})
            store[str(cid)] = [r for r in store.get(str(cid), [])
                               if r.get("run_id") != run_id]
            save_json(self._h.LOCATION_TRAIL_PATH, store)

    # ── session records (the journal: name / times / notes / cargo value) ────

    def _session_record_upsert(self, acct, cid, rec):
        """Create or update a session record."""
        if pg_store.enabled():
            pg_store.exploration_session_upsert(
                acct.account_id, cid, rec["run_id"], rec.get("name", ""),
                rec["started_at"], rec.get("ended_at"), rec.get("notes", ""),
                rec.get("cargo_value"))
            return
        with self._h._TRAIL_RECORD_LOCK:
            store = load_json(self._h.EXPLORATION_SESSIONS_PATH, {})
            by_run = {r["run_id"]: r for r in store.get(str(cid), [])}
            by_run[rec["run_id"]] = rec
            store[str(cid)] = list(by_run.values())
            save_json(self._h.EXPLORATION_SESSIONS_PATH, store)

    def _session_record_patch(self, acct, cid, run_id, **fields):
        """Update selected fields (name/ended_at/notes/cargo_value) of one session."""
        if pg_store.enabled():
            pg_store.exploration_session_patch(acct.account_id, cid, run_id, **fields)
            return
        with self._h._TRAIL_RECORD_LOCK:
            store = load_json(self._h.EXPLORATION_SESSIONS_PATH, {})
            for r in store.get(str(cid), []):
                if r.get("run_id") == run_id:
                    r.update(fields)
                    break
            save_json(self._h.EXPLORATION_SESSIONS_PATH, store)

    def _session_records_list(self, acct, cid):
        """All of a character's session records, newest first."""
        if pg_store.enabled():
            return pg_store.exploration_sessions_list(acct.account_id, cid)
        store = load_json(self._h.EXPLORATION_SESSIONS_PATH, {})
        recs = list(store.get(str(cid), []))
        recs.sort(key=lambda r: r.get("started_at", 0), reverse=True)
        return recs

    def _session_record_get(self, acct, cid, run_id):
        if pg_store.enabled():
            return pg_store.exploration_session_get(acct.account_id, cid, run_id)
        store = load_json(self._h.EXPLORATION_SESSIONS_PATH, {})
        for r in store.get(str(cid), []):
            if r.get("run_id") == run_id:
                return r
        return None

    def _session_record_delete(self, acct, cid, run_id):
        if pg_store.enabled():
            pg_store.exploration_session_delete(acct.account_id, cid, run_id)
            return
        with self._h._TRAIL_RECORD_LOCK:
            store = load_json(self._h.EXPLORATION_SESSIONS_PATH, {})
            store[str(cid)] = [r for r in store.get(str(cid), [])
                               if r.get("run_id") != run_id]
            save_json(self._h.EXPLORATION_SESSIONS_PATH, store)

    def _default_session_name(self, rows, started_at):
        """Auto name: 'YYYY-MM-DD · <first region/system>' from the trail, else date."""
        stamp = time.strftime("%Y-%m-%d %H:%M", time.gmtime(started_at or time.time()))
        if rows:
            return f"{stamp} · {rows[0].get('system_name', 'roam')}"
        return f"{stamp} · session"

    # ── live-session state machine + location poll ───────────────────────────

    def _set_session_state(self, acct, cid, state, pause_reason):
        with self._h._TRACK_LOCK:
            s = self._h._TRACK_SESSIONS.get(cid)
            if not s:
                return
            s["state"] = state
            s["pause_reason"] = pause_reason
        self._h._persist_track_sessions(acct)

    def _mark_track_error(self, cid, exc):
        """Record an ESI error on a session so the UI can surface it (a 403 means the
        location scope isn't granted — the pilot must re-authorise)."""
        status = exc.response.status_code if exc.response is not None else "?"
        with self._h._TRACK_LOCK:
            s = self._h._TRACK_SESSIONS.get(cid)
            if s is None:
                return
            if status == 403:
                s["error"] = ("Location access denied (403). Enable "
                              "'esi-location.read_location.v1' for your EVE application "
                              "at developers.eveonline.com, then log out and back in.")
            else:
                s["error"] = f"ESI location error ({status})."

    def _poll_location_once(self, acct, cid):
        """One poll tick for a tracked character: refresh online status (throttled to
        the ESI cache window, driving auto-pause/resume) and, when active+online,
        sample the location and extend the trail on a system change."""
        with self._h._TRACK_LOCK:
            sess = self._h._TRACK_SESSIONS.get(cid)
            if not sess or sess.get("state") == "stopped":
                return
            state = sess.get("state")
            pause_reason = sess.get("pause_reason")
            online = sess.get("online", False)
            online_checked_at = sess.get("online_checked_at", 0.0)
        try:
            token = self._h._access_token(acct, cid)
        except LPError:
            return
        now = time.time()
        changed = False

        if now - online_checked_at >= self._h._ONLINE_RECHECK_INTERVAL:
            try:
                data = sso_core.fetch_online(token, cid, self._h.SESSION)
                online = bool(data.get("online"))
                with self._h._TRACK_LOCK:
                    s = self._h._TRACK_SESSIONS.get(cid)
                    if s:
                        was_online = s.get("online")
                        s["online"] = online
                        s["online_checked_at"] = now
                        s.pop("error", None)
                        # Track the start of a continuous offline streak so we can
                        # require it to exceed the grace window before pausing.
                        if online:
                            s.pop("offline_since", None)
                        elif not was_online:
                            s.setdefault("offline_since", now)
                        else:
                            s["offline_since"] = now
                        offline_since = s.get("offline_since") if s else None
                # Auto-pause only after sustained offline (rides out the ESI cache
                # lag / a fresh login), and auto-resume the moment the pilot is back.
                if (not online and state == "active" and offline_since is not None
                        and now - offline_since >= self._h._ONLINE_OFFLINE_GRACE):
                    self._h._set_session_state(acct, cid, "paused", "auto")
                    state, pause_reason, changed = "paused", "auto", True
                elif online and state == "paused" and pause_reason == "auto":
                    self._h._set_session_state(acct, cid, "active", None)
                    state, pause_reason, changed = "active", None, True
            except requests.HTTPError as e:
                self._h._mark_track_error(cid, e)
            except requests.RequestException:
                pass

        if state == "active" and online:
            try:
                loc = sso_core.fetch_location(token, cid, self._h.SESSION)
                sysid = loc.get("solar_system_id")
                with self._h._TRACK_LOCK:
                    s = self._h._TRACK_SESSIONS.get(cid)
                    last = s.get("last_system_id") if s else None
                    run_id = s.get("run_id") if s else None
                if sysid and sysid != last and run_id:
                    info = self._h._resolve_track_system(sysid)
                    # New systems start with no cargo value of their own; the client
                    # shows the previous system's value as a greyed, editable
                    # placeholder (carry-forward is a display concern, not stored).
                    self._h._append_trail(acct, cid, now, run_id, sysid,
                                          info["name"], info["sec"])
                    with self._h._TRACK_LOCK:
                        s = self._h._TRACK_SESSIONS.get(cid)
                        if s:
                            s["last_system_id"] = sysid
                            s.pop("error", None)
                    self._h._persist_track_sessions(acct)
                    changed = True
            except requests.HTTPError as e:
                self._h._mark_track_error(cid, e)
            except requests.RequestException:
                pass

        if changed:
            self._h._CHAR_PUBSUB.bump(id(acct))

    def _bg_location_poll_loop(self):
        """Fast per-tracked-character location poll. Only touches characters with an
        open (active/paused) session; independent of the 5-min char-data refresh."""
        time.sleep(20)
        while True:
            try:
                accounts = list(self._h._ACCOUNTS.values())
                for acct in accounts:
                    self._h._ensure_track_loaded(acct)
                    with acct.lock:
                        char_ids = list(acct.characters.keys())
                    for cid in char_ids:
                        with self._h._TRACK_LOCK:
                            s = self._h._TRACK_SESSIONS.get(cid)
                            open_session = bool(s) and s.get("state") != "stopped"
                        if not open_session:
                            continue
                        try:
                            self._h._poll_location_once(acct, cid)
                        except Exception:
                            pass
            except Exception:
                pass
            time.sleep(self._h._LOCATION_POLL_INTERVAL)

    # ── request-scoped helpers + route handlers ──────────────────────────────

    def _track_target_cid(self, acct, q):
        raw = q.get("char_id", [None])[0]
        if raw:
            try:
                return int(raw)
            except (TypeError, ValueError):
                pass
        return acct.active_char_id

    def _live_state(self, cid):
        """(state, pause_reason, run_id, online, error) for a character's live session."""
        with self._h._TRACK_LOCK:
            s = dict(self._h._TRACK_SESSIONS.get(cid) or {})
        return (s.get("state", "stopped"), s.get("pause_reason"), s.get("run_id"),
                s.get("online"), s.get("error"))

    def _open_run_id(self, cid):
        """The run_id of the currently-OPEN (active/paused) session, or None. A
        stopped run is finished history, so it's not considered live here."""
        with self._h._TRACK_LOCK:
            s = self._h._TRACK_SESSIONS.get(cid)
            if s and s.get("state") != "stopped":
                return s.get("run_id")
        return None

    def _session_summary(self, acct, cid, rec, live_run_id):
        """A session record enriched with derived trail stats for the journal list."""
        rows = self._h._query_trail(acct, cid, run_id=rec["run_id"])
        scanned = sum(1 for r in rows if r.get("scanned"))
        # Per-system cargo carries forward as a running haul total, so the session
        # value is the LAST system's value (what's currently in the hold), not the
        # sum. Rows are chronological; take the most recent non-null. Fall back to
        # the legacy session-level cargo_value for old sessions without per-row cargo.
        row_cargo = [r.get("cargo_isk") for r in rows if r.get("cargo_isk") is not None]
        cargo_value = row_cargo[-1] if row_cargo else rec.get("cargo_value")
        return {
            "run_id": rec["run_id"],
            "name": rec.get("name") or self._h._default_session_name(rows, rec.get("started_at")),
            "started_at": rec.get("started_at"),
            "ended_at": rec.get("ended_at"),
            "notes": rec.get("notes", ""),
            "cargo_value": cargo_value,
            "systems": len(rows),
            "jumps": max(0, len(rows) - 1),
            "scanned": scanned,
            "is_live": rec["run_id"] == live_run_id,
        }

    def do_track_status(self, q):
        """The journal payload: live-session state + the current run's trail. Used for
        the always-visible live widget and SSE-driven refreshes."""
        acct = self._h.require_account()
        self._h._ensure_track_loaded(acct)
        cid = self._h._track_target_cid(acct, q)
        if cid is None:
            return {"error": "no character"}
        state, pause_reason, run_id, online, error = self._h._live_state(cid)
        rows = self._h._query_trail(acct, cid, run_id=run_id) if run_id else []
        rec = self._h._session_record_get(acct, cid, run_id) if run_id else None
        with acct.lock:
            scopes = (acct.characters.get(cid, {}) or {}).get("scopes") or []
        return {
            "char_id": cid,
            "state": state,
            "pause_reason": pause_reason,
            "run_id": run_id,
            "online": online,
            "error": error,
            "scope_ok": "esi-location.read_location.v1" in scopes,
            "name": (rec or {}).get("name") if rec else None,
            "trail": rows,
        }

    def do_track_sessions(self, q):
        """List a character's sessions (journal), newest first, with derived stats."""
        acct = self._h.require_account()
        self._h._ensure_track_loaded(acct)
        cid = self._h._track_target_cid(acct, q)
        if cid is None:
            return {"sessions": []}
        live_run_id = self._h._open_run_id(cid)
        recs = self._h._session_records_list(acct, cid)
        return {"sessions": [self._h._session_summary(acct, cid, r, live_run_id) for r in recs]}

    def do_track_session(self, q):
        """One session's full detail: record + its trail rows."""
        acct = self._h.require_account()
        self._h._ensure_track_loaded(acct)
        cid = self._h._track_target_cid(acct, q)
        run_id = q.get("run_id", [""])[0]
        if cid is None or not run_id:
            return {"error": "no session"}
        rec = self._h._session_record_get(acct, cid, run_id)
        if rec is None:
            return {"error": "not found"}
        rows = self._h._query_trail(acct, cid, run_id=run_id)
        live_run_id = self._h._open_run_id(cid)
        return {"session": self._h._session_summary(acct, cid, rec, live_run_id), "trail": rows}

    def do_track_start(self, q):
        """Open a fresh run for the character, create its journal record, and start
        sampling immediately."""
        acct = self._h.require_account()
        self._h._ensure_track_loaded(acct)
        cid = self._h._track_target_cid(acct, q)
        if cid is None or cid not in acct.characters:
            return {"error": "no character"}
        run_id = secrets.token_hex(8)
        started = time.time()
        with self._h._TRACK_LOCK:
            self._h._TRACK_SESSIONS[cid] = {
                "state": "active", "run_id": run_id, "pause_reason": None,
                "last_system_id": None, "started_at": started,
                "online": True, "online_checked_at": 0.0,
            }
        self._h._persist_track_sessions(acct)
        self._h._session_record_upsert(acct, cid, {
            "run_id": run_id, "name": "", "started_at": started,
            "ended_at": None, "notes": "", "cargo_value": None})
        self._h._CHAR_PUBSUB.bump(id(acct))
        return self._h.do_track_status(q)

    def do_track_pause(self, q):
        acct = self._h.require_account()
        self._h._ensure_track_loaded(acct)
        cid = self._h._track_target_cid(acct, q)
        with self._h._TRACK_LOCK:
            s = self._h._TRACK_SESSIONS.get(cid)
            if s and s.get("state") == "active":
                s["state"] = "paused"
                s["pause_reason"] = "user"
        self._h._persist_track_sessions(acct)
        self._h._CHAR_PUBSUB.bump(id(acct))
        return self._h.do_track_status(q)

    def do_track_resume(self, q):
        acct = self._h.require_account()
        self._h._ensure_track_loaded(acct)
        cid = self._h._track_target_cid(acct, q)
        with self._h._TRACK_LOCK:
            s = self._h._TRACK_SESSIONS.get(cid)
            if s and s.get("state") == "paused":
                s["state"] = "active"
                s["pause_reason"] = None
                s["online"] = True            # assume back until the next check says otherwise
                s.pop("offline_since", None)
                s["online_checked_at"] = 0.0  # re-confirm online on next tick
        self._h._persist_track_sessions(acct)
        self._h._CHAR_PUBSUB.bump(id(acct))
        return self._h.do_track_status(q)

    def do_track_stop(self, q):
        """Stop & finish: the only action that closes a run. Stamps ended_at so the
        session drops into history."""
        acct = self._h.require_account()
        self._h._ensure_track_loaded(acct)
        cid = self._h._track_target_cid(acct, q)
        run_id = None
        with self._h._TRACK_LOCK:
            s = self._h._TRACK_SESSIONS.get(cid)
            if s:
                run_id = s.get("run_id")
                s["state"] = "stopped"
                s["pause_reason"] = None
        if run_id:
            # Auto-name the session from its trail if the user never renamed it.
            rows = self._h._query_trail(acct, cid, run_id=run_id)
            rec = self._h._session_record_get(acct, cid, run_id) or {}
            patch = {"ended_at": time.time()}
            if not rec.get("name"):
                patch["name"] = self._h._default_session_name(rows, rec.get("started_at"))
            self._h._session_record_patch(acct, cid, run_id, **patch)
        self._h._persist_track_sessions(acct)
        self._h._CHAR_PUBSUB.bump(id(acct))
        return self._h.do_track_status(q)

    def do_track_scanned(self, q):
        acct = self._h.require_account()
        cid = self._h._track_target_cid(acct, q)
        entered_at = float(q.get("entered_at", ["0"])[0] or 0)
        scanned = str(q.get("scanned", ["true"])[0]).lower() in ("1", "true", "yes", "on")
        self._h._annotate_trail(acct, cid, entered_at, scanned=scanned)
        self._h._CHAR_PUBSUB.bump(id(acct))
        return {"ok": True}

    def do_track_cargo(self, q):
        """Set (or clear) the cargo value in one system's hold. Blank clears it back
        to NULL; the session total is the latest system's value (cargo carries
        forward for display)."""
        acct = self._h.require_account()
        cid = self._h._track_target_cid(acct, q)
        entered_at = float(q.get("entered_at", ["0"])[0] or 0)
        raw = q.get("cargo_isk", [""])[0]
        if raw in ("", None):
            cargo_isk = ""            # sentinel: clear back to NULL
        else:
            try:
                cargo_isk = float(raw)
            except (TypeError, ValueError):
                cargo_isk = ""
        # A hand-typed value isn't an ESI scan — clear any stale "as of" timestamp
        # and cache-expiry so the auto-refresh ring won't treat it as scanned.
        self._h._annotate_trail(acct, cid, entered_at, cargo_isk=cargo_isk,
                                cargo_scanned_at="", cargo_expires="")
        self._h._CHAR_PUBSUB.bump(id(acct))
        return {"ok": True}

    def do_track_cargo_fetch(self, q):
        """Pull the character's *actual* cargo from ESI, value it at Jita, write the
        total to this system's trail row, and return a breakdown. This auto-fills the
        cargo value the user would otherwise type by hand.

        Requires esi-assets.read_assets.v1 + esi-location.read_ship_type.v1. ESI
        caches assets ~1h and only refreshes on server-side changes, so freshly
        looted items may lag. The recorded `cargo_scanned_at` is ESI's Last-Modified
        (when CCP last refreshed the asset data), NOT our fetch time — that's the
        honest "as of" age; it falls back to the fetch time if the header is absent.
        The response also carries `cargo_expires` (ESI's Expires epoch, or None) so
        the client can time its next auto-fetch to when fresh data is actually due."""
        acct = self._h.require_account()
        cid = self._h._track_target_cid(acct, q)
        entered_at = float(q.get("entered_at", ["0"])[0] or 0)
        if not entered_at:
            return {"error": "no system row to fill"}

        scopes = (acct.characters.get(cid) or {}).get("scopes") or []
        need = ("esi-assets.read_assets.v1", "esi-location.read_ship_type.v1")
        missing = [s for s in need if s not in scopes]
        if missing:
            return {"error": "Cargo fetch needs new permissions. Enable "
                    "'esi-assets.read_assets.v1' and 'esi-location.read_ship_type.v1' "
                    "for your EVE application at developers.eveonline.com, then log out "
                    "and back in."}

        try:
            token = self._h._access_token(acct, cid)
            ship = sso_core.fetch_ship(token, cid, self._h.SESSION)
            assets, last_modified, expires = sso_core.fetch_assets(
                token, cid, self._h.SESSION)
        except LPError as e:
            return {"error": str(e)}
        except requests.HTTPError as e:
            status = e.response.status_code if e.response is not None else "?"
            return {"error": f"ESI returned {status} fetching cargo. If this is a "
                    "permissions error, re-enable the assets/ship scopes and log back in."}
        except requests.RequestException:
            return {"error": "Couldn't reach ESI to fetch cargo."}

        # ESI's Last-Modified is the true data age; fall back to now if it's absent.
        scanned_at = last_modified or time.time()
        ship_item_id = ship.get("ship_item_id")
        cargo = sso_core.cargo_items_in_ship(assets, ship_item_id)
        if not cargo:
            # Empty hold is a legitimate result: record 0 so the column shows it.
            self._h._annotate_trail(acct, cid, entered_at, cargo_isk=0.0,
                                    cargo_scanned_at=scanned_at,
                                    cargo_expires=expires if expires is not None else "")
            self._h._CHAR_PUBSUB.bump(id(acct))
            return {"ok": True, "total": 0.0, "items": [],
                    "ship_name": ship.get("ship_name"), "cargo_scanned_at": scanned_at,
                    "cargo_expires": expires}

        tids = list(cargo)
        prices = self._h.fetch_prices(tids, self._h.SESSION)
        names = self._h.resolve_names(tids, self._h.SESSION, self._h.CACHE_DIR)

        items, total = [], 0.0
        for tid, qty in cargo.items():
            p = prices.get(tid) or {}
            # Liquidation value: what you'd get dumping to Jita buy orders, falling
            # back to sell_min when nothing is bid. None -> unpriced (worth 0 here).
            unit = p.get("buy_max") or p.get("sell_min")
            line = (unit or 0.0) * qty
            total += line
            items.append({"type_id": tid, "name": names.get(tid, str(tid)),
                          "qty": qty, "unit": unit, "value": line,
                          "priced": unit is not None})
        items.sort(key=lambda i: i["value"], reverse=True)

        self._h._annotate_trail(acct, cid, entered_at, cargo_isk=total,
                                cargo_scanned_at=scanned_at,
                                cargo_expires=expires if expires is not None else "")
        self._h._CHAR_PUBSUB.bump(id(acct))
        return {"ok": True, "total": total, "items": items,
                "ship_name": ship.get("ship_name"), "cargo_scanned_at": scanned_at,
                "cargo_expires": expires,
                "unpriced": [i["name"] for i in items if not i["priced"]]}

    def do_track_note(self, q):
        """Set (or clear) the freeform note on one system. This is separate from the
        session-level notes; it annotates a single trail entry."""
        acct = self._h.require_account()
        cid = self._h._track_target_cid(acct, q)
        entered_at = float(q.get("entered_at", ["0"])[0] or 0)
        note = q.get("note", [""])[0] or ""
        self._h._annotate_trail(acct, cid, entered_at, note=note)
        self._h._CHAR_PUBSUB.bump(id(acct))
        return {"ok": True}

    def do_track_hide(self, q):
        """Manually hide (or unhide) one system from the journal. Hidden systems are
        kept in storage but filtered out of the trail unless the user reveals them."""
        acct = self._h.require_account()
        cid = self._h._track_target_cid(acct, q)
        entered_at = float(q.get("entered_at", ["0"])[0] or 0)
        hidden = str(q.get("hidden", ["true"])[0]).lower() in ("1", "true", "yes", "on")
        self._h._annotate_trail(acct, cid, entered_at, hidden=hidden)
        self._h._CHAR_PUBSUB.bump(id(acct))
        return {"ok": True}

    def do_track_session_update(self, q):
        """Rename / set notes / set cargo value on a session (any subset)."""
        acct = self._h.require_account()
        self._h._ensure_track_loaded(acct)
        cid = self._h._track_target_cid(acct, q)
        run_id = q.get("run_id", [""])[0]
        if cid is None or not run_id:
            return {"error": "no session"}
        fields = {}
        if "name" in q:
            fields["name"] = (q.get("name", [""])[0] or "").strip()
        if "notes" in q:
            fields["notes"] = q.get("notes", [""])[0] or ""
        if "cargo_value" in q:
            raw = q.get("cargo_value", [""])[0]
            try:
                fields["cargo_value"] = float(raw) if raw not in ("", None) else None
            except (TypeError, ValueError):
                fields["cargo_value"] = None
        if fields:
            self._h._session_record_patch(acct, cid, run_id, **fields)
        self._h._CHAR_PUBSUB.bump(id(acct))
        return {"ok": True}

    def do_track_session_delete(self, q):
        """Delete a session and its trail. A live session can't be deleted — stop it
        first."""
        acct = self._h.require_account()
        self._h._ensure_track_loaded(acct)
        cid = self._h._track_target_cid(acct, q)
        run_id = q.get("run_id", [""])[0]
        if cid is None or not run_id:
            return {"error": "no session"}
        if run_id == self._h._open_run_id(cid):
            return {"error": "Stop the live session before deleting it."}
        self._h._session_record_delete(acct, cid, run_id)
        self._h._delete_trail_run(acct, cid, run_id)
        self._h._CHAR_PUBSUB.bump(id(acct))
        return {"ok": True}


def bind(host):
    """Instantiate the feature against a host module and return the ``Exploration``.

    The host re-exports ``inst.<name> for name in EXPORTS`` into its own namespace
    so the route tables and the test-suite see plain ``lp_web.do_track_*`` /
    ``lp_web._…`` callables that honour host-level monkeypatches."""
    return Exploration(host)
