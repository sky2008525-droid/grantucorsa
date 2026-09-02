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
echo " [1/3] C++ をビルド"
echo "=============================================="
# **ビルドを省略しないこと。**
#
# 以前このスクリプトはビルドせず、既存のバイナリでテストを実行していた。
# その結果、**C++ を変更してもテストが古いバイナリで通り続け、
# 「11件パス」を新しいコードの検証結果だと誤読した。**
# 新しく追加したテストが一覧に現れないことで、ようやく気づいた。
#
# 失敗した状態を「完成」と呼ばないための最低条件（憲法ルール6）。
# **Build.bat ではなく UnrealBuildTool を直接呼ぶ。**
#
# Git Bash から .bat を起動すると cmd 側でパスが分割され、
# 「'C:\Program' は認識されていません」で落ちる（UE も本リポジトリも
# 空白を含むパスに置かれている）。
#
# ただし UnrealBuildTool.exe を素で呼ぶと .NET 10 が無いと言われる
# （システムには 7.0 と 8.0 しか無い）。Build.bat がやっているのは
# **UE 同梱の dotnet を使わせること**なので、それを明示的に指定する。
DOTNET="$UE_ROOT/Engine/Binaries/ThirdParty/DotNet/10.0/win-x64/dotnet.exe"
UBT_DLL="$UE_ROOT/Engine/Binaries/DotNET/UnrealBuildTool/UnrealBuildTool.dll"
if [ ! -x "$DOTNET" ] || [ ! -f "$UBT_DLL" ]; then
    echo "ERROR  UnrealBuildTool を実行できない:" >&2
    echo "         dotnet: $DOTNET" >&2
    echo "         dll   : $UBT_DLL" >&2
    exit 1
fi

BUILD_LOG="$(mktemp)"
if ! "$DOTNET" "$UBT_DLL" ZN6DigitalTwinEditor Win64 Development \
        -Project="$UPROJECT" -WaitMutex > "$BUILD_LOG" 2>&1; then
    echo "ERROR  ビルドに失敗した。" >&2
    tail -40 "$BUILD_LOG" >&2
    rm -f "$BUILD_LOG"
    exit 1
fi
grep -E "^\[[0-9]+/[0-9]+\]|Result:|Target is up to date" "$BUILD_LOG" | tail -6
rm -f "$BUILD_LOG"

echo
echo "=============================================="
echo " [2/3] Python 側の参照値を生成"
echo "=============================================="
# Windows のコンソールは cp932 なので、UTF-8 を明示しないと出力で落ちる
if ! PYTHONIOENCODING=utf-8 "$VENV_PY" Tools/export_reference.py; then
    echo "ERROR  参照値を生成できなかった。" >&2
    exit 1
fi

echo
echo "=============================================="
echo " [3/3] UE5 の自動テストを実行"
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
# **警告つきの成功を「成功」に混ぜない。**
#
# UE は succeeded と succeededWithWarnings を別に数える。前者だけを出して
# いたときは、一覧に 48 件並んでいるのに「成功 45 件」と出て、3 件が
# どこへ行ったのか分からなかった。数が合わない表示は、そこに何かが
# あることを隠す（憲法ルール6）。
with_warnings = report.get("succeeded_with_warnings",
                           report.get("succeededWithWarnings", 0))
not_run = report.get("notRun", 0)

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
print("  成功 {} 件（うち警告つき {} 件） / 失敗 {} 件 / 未実行 {} 件".format(
    succeeded + with_warnings, with_warnings, failed, not_run))
if with_warnings:
    print("  NOTE 警告つきの成功がある。想定内なら")
    print("       AddExpectedMessage で登録して、本物の警告と区別すること。")
sys.exit(1 if failed else 0)
PY
