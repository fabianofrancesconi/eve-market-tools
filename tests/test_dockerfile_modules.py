"""Guards against the deploy that broke v1.150.0: a new local module
(ind_track.py) was added and imported by lp-web.py, but the Dockerfile copies
root modules by an explicit name list — which wasn't updated — so the image
shipped without it and the container crashed on import (healthcheck failed).

The Dockerfile copies files explicitly (not `COPY . .`) on purpose, to keep the
image lean. That trades a lean image for a list that silently drifts. This test
makes the drift loud: every first-party local module lp-web.py imports MUST be
in the Dockerfile's COPY line.
"""
import ast
import re
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent


def _local_modules():
    """Names of the root-level .py modules in the repo (candidates for a local
    import). lp-web.py isn't importable as a module name, so map by filename."""
    return {p.stem for p in _ROOT.glob("*.py")} | {"lp-web.py"}


def _imports_in(path):
    """Top-level module names imported by a source file (both `import x` and
    `from x import ...`), first path component only."""
    tree = ast.parse(path.read_text())
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                names.add(a.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if node.level == 0 and node.module:
                names.add(node.module.split(".")[0])
    return names


def _dockerfile_copied_files():
    """Every file named on a `COPY <files...> ./`-style line in the Dockerfile."""
    copied = set()
    for line in (_ROOT / "Dockerfile").read_text().splitlines():
        m = re.match(r"\s*COPY\s+(.+?)\s+\.?/?\s*$", line)
        if not m:
            continue
        for tok in m.group(1).split():
            copied.add(tok.rstrip("/"))
    return copied


def test_all_local_modules_imported_by_web_are_in_dockerfile():
    local = {m for m in _local_modules() if m != "lp-web"}
    web_imports = _imports_in(_ROOT / "lp-web.py")
    needed = {f"{name}.py" for name in web_imports if name in local}
    # ind_track / ind_core / arb_core / etc. — every first-party import must ship.
    assert needed, "expected lp-web.py to import at least one local module"
    copied = _dockerfile_copied_files()
    missing = sorted(f for f in needed if f not in copied)
    assert not missing, (
        f"lp-web.py imports {missing} but the Dockerfile doesn't COPY them — "
        "the image would crash on import. Add them to the COPY line.")


def test_web_entrypoint_itself_is_copied():
    assert "lp-web.py" in _dockerfile_copied_files()
