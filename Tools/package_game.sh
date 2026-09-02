#!/usr/bin/env bash
# exe として動くパッケージを作る。
#
#     ./Tools/package_game.sh
#
# 出力: Build/Windows/ZN6DigitalTwin.exe
#
# ## 実データを同梱する理由
#
# vehicle.json もコースの高さ場も音の定義も、**UE のアセットではなく
# 素の JSON** である（物理の唯一の情報源をエンジンの中に隠さないため、
# 憲法ルール4）。エディタから動かすときは `<repo>/` を直接読めるが、
# exe にすると読めない。**パッケージの中へ持っていく必要がある。**
#
# 読む側は `AZN6VehicleActor::DataRoot()` が
#
#   1. `<パッケージ>/ZN6DigitalTwin/ZN6Data/`（同梱したもの）
#   2. `<repo>/`（エディタ）
#
# の順で探す。ここでは 1 を用意する。
#
# **メッシュ（FBX）は同梱しない。** あれは UE 側へ取り込み済みで、
# 実行時には読まない。同梱すると 1 GB 超が二重になる。
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
UE_ROOT="${UE_ROOT:-/c/Program Files/Epic Games/UE_5.8}"
UAT="$UE_ROOT/Engine/Build/BatchFiles/RunUAT.bat"
PROJECT="$REPO/Unreal/ZN6DigitalTwin/ZN6DigitalTwin.uproject"
DATA="$REPO/Unreal/ZN6DigitalTwin/ZN6Data"
ARCHIVE="$REPO/Build"

if [ ! -f "$UAT" ]; then
    echo "ERROR  RunUAT.bat が無い: $UAT" >&2
    exit 1
fi

if [ ! -f "$REPO/Tracks/Export/physics_test_track/heightfield.json" ]; then
    echo "ERROR  コースの書き出しが無い。先に ./Tools/build_tracks.sh" >&2
    exit 1
fi

# --- 1. 実データを集める -----------------------------------------------------
echo "=============================================="
echo " [1/2] 実データを同梱用に集める"
echo "=============================================="
rm -rf "$DATA"
mkdir -p "$DATA/Vehicles/ZN6/Export" "$DATA/Tracks/Export" "$DATA/Audio"

cp "$REPO/Vehicles/ZN6/vehicle.json" "$DATA/Vehicles/ZN6/"
cp "$REPO/Vehicles/ZN6/Export/manifest.json" "$DATA/Vehicles/ZN6/Export/"
cp "$REPO/Audio/audio.json" "$DATA/Audio/"

# 中心線（コースごとに 1 つ）
cp "$REPO"/Tracks/*.json "$DATA/Tracks/" 2>/dev/null || true

# コースごとの書き出し。**JSON だけ。** FBX は取り込み済みなので要らない。
for DIR in "$REPO"/Tracks/Export/*/; do
    KEY="$(basename "$DIR")"
    [ "$KEY" = "Textures" ] && continue
    mkdir -p "$DATA/Tracks/Export/$KEY"
    cp "$DIR"*.json "$DATA/Tracks/Export/$KEY/" 2>/dev/null || true
done

echo "  同梱: $(find "$DATA" -name '*.json' | wc -l) 個の JSON"
echo "  容量: $(du -sh "$DATA" | cut -f1)"

# --- 2. パッケージ ----------------------------------------------------------
echo
echo "=============================================="
echo " [2/2] BuildCookRun（時間が掛かる）"
echo "=============================================="
# **MSYS2 のパス変換を止める。** `/Script/...` のような引数が
# `C:/Program Files/Git/Script/...` に書き換わる（Tools/launch_game.sh と同じ話）。
export MSYS2_ARG_CONV_EXCL="*"

"$UAT" BuildCookRun \
    -project="$(cygpath -w "$PROJECT" 2>/dev/null || echo "$PROJECT")" \
    -noP4 -platform=Win64 -clientconfig=Development \
    -cook -build -stage -pak -archive \
    -archivedirectory="$(cygpath -w "$ARCHIVE" 2>/dev/null || echo "$ARCHIVE")" \
    -utf8output

echo
echo "完了。起動:"
echo "  $ARCHIVE/Windows/ZN6DigitalTwin.exe"
echo
echo "コースを選ぶ（既定は physics_test_track）:"
echo "  ZN6DigitalTwin.exe /Game/ZN6/Maps/mountain_pass"
