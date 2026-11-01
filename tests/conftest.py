"""Loads the integration's pure modules without Home Assistant (package __init__ bypassed)."""

import sys
import types
from pathlib import Path

PKG = Path(__file__).resolve().parents[1] / "custom_components" / "tsun_microinverters"
if "tsun" not in sys.modules:
    pkg = types.ModuleType("tsun")
    pkg.__path__ = [str(PKG)]
    sys.modules["tsun"] = pkg
