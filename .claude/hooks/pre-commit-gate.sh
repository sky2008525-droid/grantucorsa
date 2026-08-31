#!/usr/bin/env bash
#
# 憲法の層1 — 機械的に強制するもの（Docs/RULE_CLASSIFICATION.md）
#
#   1. Level 0 パラメータの変更を検出してコミットを止める
#      （排気量・ホイールベース・公式ギア比・公式車重・ファイナルの variant）
#   2. vehicle.json のスキーマ検証
#      （source / confidence / unit の必須、SI単位、"unknown" の許容）
#   3. pytest 失敗時のコミット停止
#
# 検証対象はワーキングツリーではなく **ステージされた内容**。
# `git add` していない修正でコミットが通る／落ちる、という食い違いを防ぐため。
#
# 単体でも実行できる:
#   .claude/hooks/pre-commit-gate.sh
#
# 緊急時の回避: git commit --no-verify
#   ただしこれは層1を無効化する行為。使ったらコミットメッセージに理由を書くこと。

set -uo pipefail

REPO_ROOT=$(git rev-parse --show-toplevel 2>/dev/null) || {
    echo "pre-commit-gate: git リポジトリの中で実行すること" >&2
    exit 1
}
cd "$REPO_ROOT"

VALIDATOR="Tools/validate_vehicle.py"
FAILED=0

# venv があればそれを使う。無ければシステムの python。
# Windows の venv は .venv/Scripts/python.exe（POSIX は .venv/bin/python3）。
# Git for Windows の bash から呼ばれることを想定している。
if [ -x ".venv/bin/python3" ]; then
    PYTHON=".venv/bin/python3"
elif [ -x ".venv/Scripts/python.exe" ]; then
    PYTHON=".venv/Scripts/python.exe"
elif command -v python3 >/dev/null 2>&1; then
    PYTHON="python3"
elif command -v python >/dev/null 2>&1 && python -c "import sys; sys.exit(0 if sys.version_info[0] == 3 else 1)" 2>/dev/null; then
    PYTHON="python"
else
    echo "pre-commit-gate: Python 3 が見つからない。検証できないためコミットを止める" >&2
    echo "  venv を作る:  python3 -m venv .venv" >&2
    exit 1
fi

TMPDIR_GATE=$(mktemp -d)
trap 'rm -rf "$TMPDIR_GATE"' EXIT

echo "=============================================="
echo " pre-commit gate"
echo "=============================================="

# ---------------------------------------------------------------------------
# 1) 車両データ（Vehicles/**/vehicle.json）
# ---------------------------------------------------------------------------
#
# **対象は車両仕様ファイルだけ。** 以前は Vehicles/**/*.json を全て検証して
# いたが、Phase 9 で Vehicles/ZN6/Clean/recon.json（3Dモデルの実測レポート）を
# 追加したところ、車両スキーマ違反として弾かれた。
#
# validate_vehicle.py は vehicle.json 専用のスキーマ検証器であり
# （Tools/validate_vehicle.py の docstring）、Raw/ Clean/ PBR/ Export/ に
# 今後置かれる派生データを車両仕様として検証するのは誤り。
#
# **これは層1を緩める変更ではない。** 車両仕様ファイルは引き続き
# 全て検証される。対象をこのゲートの意図に一致させただけ。
STAGED_JSON=$(git diff --cached --name-only --diff-filter=ACM -- 'Vehicles/vehicle.json' 'Vehicles/**/vehicle.json')

if [ -z "$STAGED_JSON" ]; then
    echo
    echo "[1/3] 車両データ: ステージされた変更なし — スキップ"
    echo "[2/3] Level 0 検証: 対象なし — スキップ"
else
    if [ ! -f "$VALIDATOR" ]; then
        echo "pre-commit-gate: $VALIDATOR が無い。検証できないためコミットを止める" >&2
        exit 1
    fi

    echo
    echo "[1/3] 車両データのスキーマ検証"
    while IFS= read -r path; do
        [ -z "$path" ] && continue
        staged="$TMPDIR_GATE/staged.json"
        git show ":$path" > "$staged" 2>/dev/null || {
            echo "  ERROR  $path のステージ内容を取得できない" >&2
            FAILED=1
            continue
        }
        if ! "$PYTHON" "$VALIDATOR" "$staged" 2>&1 | sed "s|$staged|$path|g" | sed 's/^/  /'; then
            FAILED=1
        fi
    done <<< "$STAGED_JSON"

    echo
    echo "[2/3] Level 0 パラメータの変更検出"
    if ! git rev-parse --verify HEAD >/dev/null 2>&1; then
        echo "  HEAD が無い（初回コミット）— スキップ"
    else
        while IFS= read -r path; do
            [ -z "$path" ] && continue
            old="$TMPDIR_GATE/old.json"
            new="$TMPDIR_GATE/new.json"
            if ! git show "HEAD:$path" > "$old" 2>/dev/null; then
                echo "  $path は新規ファイル — 比較対象なし"
                continue
            fi
            git show ":$path" > "$new" 2>/dev/null || continue
            if ! "$PYTHON" "$VALIDATOR" --level0 "$old" "$new" 2>&1 \
                    | sed "s|$old|HEAD:$path|g; s|$new|staged:$path|g" | sed 's/^/  /'; then
                FAILED=1
            fi
        done <<< "$STAGED_JSON"
    fi
fi

# ---------------------------------------------------------------------------
# 2) pytest
# ---------------------------------------------------------------------------

echo
echo "[3/3] 自動回帰テスト"

if [ ! -d "Tests" ]; then
    echo "  Tests/ が無い — スキップ（Phase 4 で作る）"
elif ! "$PYTHON" -c "import pytest" >/dev/null 2>&1; then
    echo "  WARN  pytest が入っていないため実行できない。"
    echo "        venv を有効にして pip install pytest すること。"
    echo "        テストを検証せずにコミットしている。"
elif [ -z "$(find Tests -name 'test_*.py' -o -name '*_test.py' 2>/dev/null | head -1)" ]; then
    echo "  Tests/ にテストファイルが無い — スキップ"
else
    if "$PYTHON" -m pytest -q Tests 2>&1 | sed 's/^/  /'; then
        echo "  OK  pytest 通過"
    else
        echo "  ERROR  pytest が失敗した。失敗した状態でコミットしない（憲法ルール16）"
        FAILED=1
    fi
fi

# ---------------------------------------------------------------------------

echo
echo "=============================================="
if [ "$FAILED" -ne 0 ]; then
    cat >&2 <<'MSG'
 コミットを中止した。

 直し方:
   - スキーマ違反   → python3 Tools/validate_vehicle.py Vehicles/ZN6/vehicle.json
   - Level 0 の変更 → 公式値は変更禁止。一次資料を示して人間の承認を得ること
                      保護対象の一覧: python3 Tools/validate_vehicle.py --list-level0
   - テスト失敗     → python3 -m pytest Tests

 どうしても回避が必要なら git commit --no-verify。
 ただしこれは層1を無効化する行為。理由をコミットメッセージに書くこと。
MSG
    echo "==============================================" >&2
    exit 1
fi
echo " すべて通過"
echo "=============================================="
exit 0
