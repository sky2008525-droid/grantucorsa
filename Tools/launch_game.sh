#!/usr/bin/env bash
# ゲームとして起動する（エディタの UI を出さない）。
#
#     ./Tools/launch_game.sh
#
# **-game を付ける。** 付けないとエディタが開くだけで、HUD もメニューも
# 出ない（どちらも BeginPlay でビューポートに足しているため）。
set -euo pipefail

UE_ROOT="${UE_ROOT:-/c/Program Files/Epic Games/UE_5.8}"
EDITOR="$UE_ROOT/Engine/Binaries/Win64/UnrealEditor.exe"
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PROJECT="$REPO/Unreal/ZN6DigitalTwin/ZN6DigitalTwin.uproject"
MAP="/Game/ZN6/Maps/PhysicsTestTrack"

if [ ! -f "$EDITOR" ]; then
    echo "ERROR  UnrealEditor.exe が無い: $EDITOR" >&2
    echo "       UE_ROOT を設定して実行すること" >&2
    exit 1
fi

# **エディタが動いていると Live Coding がビルドを掴む。**
# ここでは起動するだけなので止めないが、気づけるように知らせる。
if tasklist //FI "IMAGENAME eq UnrealEditor.exe" 2>/dev/null | grep -qi UnrealEditor; then
    echo "NOTE   UnrealEditor が既に起動している。二重に開くことになる。"
fi

echo "起動: $MAP"
echo "  Esc / P    メニュー"
echo "  W A S D    運転（W=アクセル S=ブレーキ A/D=操舵）"
echo "  E / Q      シフトアップ / ダウン"
echo "  R          スタート位置へ戻す"

# **MSYS2_ARG_CONV_EXCL と MSYS_NO_PATHCONV が要る。**
#
# Git Bash は "/Game/..." を Windows のパスだと思い込み、
# "C:/Program Files/Git/Game/..." へ書き換えてしまう。そのせいで
# レベルを読めず、既定の空マップが開いていた。ログには
# "Failed to enter C:/Program Files/Git/Game/..." と出る。
#
# **エラーではなく「別のマップが開く」形で失敗する**ので、
# 画面を見ただけでは原因が分からない。
# **除外するのは "/Game" で始まる引数だけ。** "*" にすると
# .uproject のパスまで変換されなくなり、今度はプロジェクトが見つからない
# （UE は起動するが 1 秒で止まる）。
export MSYS2_ARG_CONV_EXCL="/Game"

"$EDITOR" "$PROJECT" "$MAP" -game -ResX=1600 -ResY=900 -windowed
