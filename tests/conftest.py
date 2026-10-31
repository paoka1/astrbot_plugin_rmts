import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
pkg = types.ModuleType("rmts")
pkg.__path__ = [str(ROOT)]
sys.modules.setdefault("rmts", pkg)
