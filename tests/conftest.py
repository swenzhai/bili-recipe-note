from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

# Must be set before Streamlit imports PyArrow. The macOS/Python 3.14 mimalloc
# path can segfault from Streamlit's script thread during dataframe IPC writes.
os.environ.setdefault("ARROW_DEFAULT_MEMORY_POOL", "system")

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


@pytest.fixture(autouse=True)
def isolate_data_environment(monkeypatch):
    # Explicit project_root fixtures must never be redirected into a user's
    # live data directory. Tests can still opt into their own temporary root.
    # setenv also registers restoration when the variable was originally absent.
    monkeypatch.setenv("BILI_RECIPE_DATA_DIR", "")
