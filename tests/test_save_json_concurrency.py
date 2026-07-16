"""Regression test: save_json must tolerate concurrent writers to one path.

do_char_data fetches a multi-character account's data in a thread pool, and
several of those fetches write the SAME store file (order events, delivered-jobs
counter, …). save_json writes to a temp file then os.replace()s it into place;
with a shared "<path>.tmp" two threads race — the first replace consumes the
temp, the second raises FileNotFoundError and that character's fetch is lost.
The temp name is per-writer (pid+thread) so this can't happen.
"""
import json
import threading

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from lp_core import save_json, load_json


def test_concurrent_writes_to_same_path_never_raise(tmp_path):
    path = tmp_path / "store.json"
    errors = []
    barrier = threading.Barrier(8)

    def writer(n):
        barrier.wait()  # maximise overlap
        try:
            for i in range(50):
                save_json(path, {"writer": n, "i": i})
        except Exception as e:  # noqa: BLE001
            errors.append(e)

    threads = [threading.Thread(target=writer, args=(n,)) for n in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors, f"save_json raced under concurrency: {errors[:3]}"
    # The file is always a whole, valid JSON doc (os.replace stays atomic).
    doc = load_json(path, None)
    assert isinstance(doc, dict) and "writer" in doc


def test_no_temp_files_left_behind(tmp_path):
    path = tmp_path / "store.json"
    save_json(path, {"a": 1})
    leftovers = [p.name for p in tmp_path.iterdir() if p.name != "store.json"]
    assert leftovers == [], f"stray temp files: {leftovers}"
    assert load_json(path, None) == {"a": 1}
