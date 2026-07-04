"""Shared test fixtures and path configuration.

This conftest ensures that:
1. The backend package is importable (adds backend/ to sys.path)
2. The backward-compatibility shims (lp_core, arb_core, sso_core, ind_core)
   are importable from the backend directory
3. Common fixtures are available to all test files
"""
import sys
from pathlib import Path

# Add backend/ to sys.path so `import lp_core` etc. works via the shims,
# and `from app.core...` works for the new module structure.
_BACKEND_DIR = Path(__file__).resolve().parent.parent / "backend"
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))
