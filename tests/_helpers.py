"""Load the project's standalone scripts as importable modules.

The Python lives in per-role directories (backtest/, ml/, data/) as runnable
scripts, not an installed package, so tests load each file by path.
"""

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def load(name: str, relpath: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / relpath)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module
