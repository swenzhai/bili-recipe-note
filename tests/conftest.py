from __future__ import annotations

import os
import sys
from pathlib import Path

# Must be set before Streamlit imports PyArrow. The macOS/Python 3.14 mimalloc
# path can segfault from Streamlit's script thread during dataframe IPC writes.
os.environ.setdefault("ARROW_DEFAULT_MEMORY_POOL", "system")

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
