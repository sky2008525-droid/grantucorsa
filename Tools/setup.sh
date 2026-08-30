#!/usr/bin/env bash
#
# clone 直後の初期設定。**マシンを変えるたびに1回実行すること。**
#
#   ./Tools/setup.sh
#
# なぜスクリプトにしてあるか:
#   core.hooksPath は .git/config に入るためリポジトリに含まれない。
#   設定を忘れると層1（コミット前ゲート）が黙って無効になる。
#   エラーも警告も出ずに、Level 0 の変更もスキーマ違反もテスト失敗も素通りする。
#   これが最も危険な失敗の仕方なので、手順を1コマンドにまとめてある。
#
# Windows: Git for Windows の bash（Git Bash）で実行すること。

set -uo pipefail

cd "$(git rev-parse --show-toplevel)" || exit 1

FAILED=0

echo "=============================================="
echo " grantucorsa セットアップ"
echo "=============================================="

# ---------------------------------------------------------------------------
echo
echo "[1/3] コミット前ゲートを有効化"

git config core.hooksPath .claude/hooks
echo "  core.hooksPath = $(git config --get core.hooksPath)"

# Windows でクローンすると実行ビットが落ちることがある
chmod +x .claude/hooks/pre-commit .claude/hooks/pre-commit-gate.sh 2>/dev/null || true

# CRLF 混入の検出（.gitattributes があれば起きないはずだが、既存クローンでは起こりうる）
for f in .claude/hooks/pre-commit .claude/hooks/pre-commit-gate.sh Tools/setup.sh; do
    if [ -f "$f" ] && head -c 200 "$f" | grep -q $'\r'; then
        echo "  ERROR  $f が CRLF になっている。フックが動かない。" >&2
        echo "         直し方: git rm --cached -r . && git reset --hard" >&2
        FAILED=1
    fi
done

# ---------------------------------------------------------------------------
echo
echo "[2/3] Python 環境"

if [ -d ".venv" ]; then
    echo "  .venv は既にある"
else
    echo "  .venv を作る"
    if command -v python3 >/dev/null 2>&1; then
        python3 -m venv .venv
    elif command -v python >/dev/null 2>&1; then
        python -m venv .venv
    else
        echo "  ERROR  Python 3 が見つからない" >&2
        FAILED=1
    fi
fi

if [ -x ".venv/bin/python3" ]; then
    VENV_PY=".venv/bin/python3"
elif [ -x ".venv/Scripts/python.exe" ]; then
    VENV_PY=".venv/Scripts/python.exe"
else
    VENV_PY=""
fi

if [ -n "$VENV_PY" ]; then
    echo "  $($VENV_PY --version)"
    if ! "$VENV_PY" -c "import numpy, scipy, matplotlib, pytest" 2>/dev/null; then
        echo "  パッケージを入れる（numpy scipy matplotlib pytest）"
        "$VENV_PY" -m pip install --quiet --upgrade pip
        "$VENV_PY" -m pip install --quiet numpy scipy matplotlib pytest || FAILED=1
    fi
    "$VENV_PY" -m pip list 2>/dev/null | grep -Ei "^(numpy|scipy|matplotlib|pytest) " | sed 's/^/    /'
fi

# ---------------------------------------------------------------------------
echo
echo "[3/3] 動作確認"

if [ -n "$VENV_PY" ] && [ -d "Tests" ]; then
    if "$VENV_PY" -m pytest -q Tests > /dev/null 2>&1; then
        echo "  OK  pytest 通過"
    else
        echo "  ERROR  pytest が失敗した。python3 -m pytest Tests で詳細を見ること" >&2
        FAILED=1
    fi
fi

if [ -f "Tools/validate_vehicle.py" ] && [ -f "Vehicles/ZN6/vehicle.json" ]; then
    if "${VENV_PY:-python3}" Tools/validate_vehicle.py Vehicles/ZN6/vehicle.json > /dev/null 2>&1; then
        echo "  OK  vehicle.json のスキーマ検証 通過"
    else
        echo "  ERROR  vehicle.json がスキーマ違反" >&2
        FAILED=1
    fi
fi

if .claude/hooks/pre-commit-gate.sh > /dev/null 2>&1; then
    echo "  OK  コミット前ゲートが動く"
else
    echo "  ERROR  コミット前ゲートが失敗した" >&2
    FAILED=1
fi

# ---------------------------------------------------------------------------
echo
echo "=============================================="
if [ "$FAILED" -ne 0 ]; then
    echo " 設定が完了していない。上のエラーを直すこと。" >&2
    echo "==============================================" >&2
    exit 1
fi
cat <<'MSG'
 セットアップ完了

 次に読むもの:
   CLAUDE.md              判断を要するルール
   Docs/SPEC_ZN6.md       仕様書 兼 要件定義書
   Docs/ZN6_BASELINE.md   基準車両と罠2件

 venv を有効にする:
   source .venv/bin/activate          # Windows: .venv\Scripts\activate
MSG
echo "=============================================="
