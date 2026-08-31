#!/usr/bin/env bash
#
# UE5(C++) 実装の自動テストを実行する。
#
#   ./Tools/run_ue_tests.sh
#
# Python 実装との突き合わせ（Docs/SPEC_ZN6.md §10.3）を含む。
# **参照値は Python 側から生成する。** このスクリプトが先に
# Tools/export_reference.py を回すのはそのため。片方だけを更新すると、
# どちらが正しいのか分からない状態になる。
#
# Windows: Git Bash で実行する。

set -uo pipefail

cd "$(git rev-parse --show-toplevel)" || exit 1

UE_ROOT="${UE_ROOT:-/c/Program Files/Epic Games/UE_5.8}"
EDITOR_CMD="$UE_ROOT/Engine/Binaries/Win64/UnrealEditor-Cmd.exe"
UPROJECT="$(pwd)/Unreal/ZN6DigitalTwin/ZN6DigitalTwin.uproject"
REPORT_DIR="$(pwd)/Unreal/ZN6DigitalTwin/Saved/TestReports"

if [ ! -x "$EDITOR_CMD" ]; then
    echo "ERROR  UnrealEditor-Cmd.exe が見つからない: $EDITOR_CMD" >&2
    echo "       UE_ROOT 環境変数で場所を指定できる。" >&2
    exit 1
fi

# --- venv の python を探す（Tools/setup.sh と同じ探し方） -------------------
if [ -x ".venv/bin/python3" ]; then
    VENV_PY=".venv/bin/python3"
elif [ -x ".venv/Scripts/python.exe" ]; then
    VENV_PY=".venv/Scripts/python.exe"
else
    echo "ERROR  .venv が無い。./Tools/setup.sh を実行すること。" >&2
    exit 1
fi

echo "=============================================="
echo " [1/2] Python 側の参照値を生成"
echo "=============================================="
# Windows のコンソールは cp932 なので、UTF-8 を明示しないと出力で落ちる
if ! PYTHONIOENCODING=utf-8 "$VENV_PY" Tools/export_reference.py; then
    echo "ERROR  参照値を生成できなかった。" >&2
    exit 1
fi

echo
echo "=============================================="
echo " [2/2] UE5 の自動テストを実行"
echo "=============================================="
rm -rf "$REPORT_DIR"

# -NullRHI: 描画せずに走らせる（物理の検証に GPU は要らない）
"$EDITOR_CMD" "$UPROJECT" \
    -ExecCmds="Automation RunTests ZN6" \
    -TestExit="Automation Test Queue Empty" \
    -ReportExportPath="$REPORT_DIR" \
    -unattended -nopause -nosplash -NullRHI > /dev/null 2>&1

if [ ! -f "$REPORT_DIR/index.json" ]; then
    echo "ERROR  テストレポートが出力されなかった。エディタが起動できていない可能性がある。" >&2
    exit 1
fi

# **合否をレポートから読む。** UnrealEditor-Cmd の終了コードはテストが
# 失敗しても 0 になることがあるため、それだけを信用しない。
PYTHONIOENCODING=utf-8 "$VENV_PY" - "$REPORT_DIR/index.json" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8-sig") as handle:
    report = json.load(handle)

succeeded = report.get("succeeded", 0)
failed = report.get("failed", 0)

for test in report.get("tests", []):
    state = test.get("state")
    mark = "OK  " if state == "Success" else "FAIL"
    print("  {} {}".format(mark, test.get("fullTestPath")))
    if state != "Success":
        for entry in test.get("entries", []):
            event = entry.get("event", {})
            if event.get("type") == "Error":
                print("       {}".format(event.get("message")))

print()
print("  成功 {} 件 / 失敗 {} 件".format(succeeded, failed))
sys.exit(1 if failed else 0)
PY
