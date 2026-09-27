"""Tools/ Physics/ Tracks/ Audio/ をインポート可能にする。"""
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
for sub in ("Tools", "Physics", "Tracks", "Audio"):
    sys.path.insert(0, str(REPO_ROOT / sub))
