"""Tools/ Physics/ Tracks/ をインポート可能にする。"""
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
for sub in ("Tools", "Physics", "Tracks"):
    sys.path.insert(0, str(REPO_ROOT / sub))
