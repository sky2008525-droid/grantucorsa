#!/usr/bin/env bash
# コース定義から路面・縁石・地面メッシュと樹木配置を作る（全コース）。
#
# **縁石の赤白テクスチャは別に作る。**
#     python Tracks/road_texture.py
#
#     ./Tools/build_tracks.sh              # 全部
#     ./Tools/build_tracks.sh mountain_pass  # 1本だけ
#
# 出力は `Tracks/Export/<key>/` に分ける。
# **分けないと、同じ名前のメッシュ（TrackRoad / TrackGround）が
# 上書きし合う。**
#
# この後に UE 側で:
#     import_assets.py                      # 全コースを取り込む
#     build_level.py <key>                  # コースごとにレベルを作る
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BLENDER="${BLENDER:-/c/Program Files/Blender Foundation/Blender 5.0/blender.exe}"
PYTHON="$REPO/.venv/Scripts/python.exe"
[ -x "$PYTHON" ] || PYTHON="python"

if [ ! -f "$BLENDER" ]; then
    echo "ERROR  Blender が無い: $BLENDER" >&2
    echo "       BLENDER=... を設定して実行すること" >&2
    exit 1
fi

cd "$REPO"

# **中心線を先に書き出す。** 形状の定義は Tracks/track_catalogue.py に
# しかない。JSON はその受け渡しでしかないので、毎回作り直す。
"$PYTHON" Tools/export_track.py "$@" > /dev/null

KEYS="$*"
if [ -z "$KEYS" ]; then
    KEYS="$("$PYTHON" -c "
import sys; sys.path.insert(0, 'Tracks')
from track_catalogue import CATALOGUE
print(' '.join(sorted(CATALOGUE)))
")"
fi

WIN_REPO="$(pwd -W 2>/dev/null || pwd)"

for KEY in $KEYS; do
    OUT="$WIN_REPO/Tracks/Export/$KEY"
    echo "=== $KEY"
    # **Blender の終了コードを見る。**
    #
    # 以前はここで出力を grep に通すだけで、終了コードを見ていなかった。
    # そのため **Blender が例外で落ちても「完了」と表示していた。**
    # 前回の FBX が残っているので、次の「路面メッシュが出来ているか」の
    # 検査も通ってしまう。
    #
    # 実際これで3回、環境設定の書き間違い（`Environment` に無い引数を
    # 渡した等）に気づかず、**古いデータのまま UE 側まで進めた。**
    # 憲法ルール6（エラー隠蔽禁止）・ルール16（失敗を完成と呼ばない）。
    #
    # 路面の平坦性・縦断・地面の追従の検査は build_track.py の中にあり、
    # 失敗すると 0 以外で返る。それをここで拾う。
    set +e
    "$BLENDER" --background --python Blender/build_track.py -- \
        "$WIN_REPO/Tracks/$KEY.json" "$OUT" 2>&1 | tee "$OUT.build.log" \
        | grep -E "^\[track\]|!!"
    STATUS=${PIPESTATUS[0]}
    set -e

    if [ "$STATUS" -ne 0 ]; then
        echo "ERROR  $KEY: Blender が失敗した（終了コード $STATUS）" >&2
        grep -E "Error|Traceback|File \"|line [0-9]+" "$OUT.build.log" \
            | tail -10 >&2
        exit 1
    fi
    rm -f "$OUT.build.log"

    for MESH in TrackRoad TrackKerb TrackGround; do
        if [ ! -f "$REPO/Tracks/Export/$KEY/$MESH.fbx" ]; then
            echo "ERROR  $KEY: $MESH.fbx が出来ていない" >&2
            exit 1
        fi
    done
done

echo
echo "完了。次は UE 側で:"
echo "  import_assets.py     を実行してメッシュを取り込む"
for KEY in $KEYS; do
    echo "  build_level.py $KEY"
done
