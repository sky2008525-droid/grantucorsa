#!/usr/bin/env python3
"""Physics Test Track の中心線を JSON へ書き出す（Blender / UE5 への受け渡し用）.

    python3 Tools/export_track.py

出力先: Tracks/physics_test_track.json

**なぜ書き出すのか**

コースは `Tracks/physics_test_track.py` が手続き的に生成している。
描画側（Blender の路面メッシュ、UE5 の樹木配置）が**同じ形状を別々に
持つと、物理と絵がずれる。** 中心線を1箇所から配り、描画側は必ず
これを読むようにする。

**ここで形状を「作らない」こと。** このスクリプトは変換するだけで、
コース形状の定義は physics_test_track.py にしかない。
"""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

sys.path.insert(0, str(REPO_ROOT / "Tracks"))

from Tracks.physics_test_track import closure_error  # noqa: E402
from Tracks.track_catalogue import CATALOGUE, build  # noqa: E402
from Tracks.elevation import profile_for  # noqa: E402

OUTPUT_DIR = REPO_ROOT / "Tracks"

#: 既定のコース。**書き出し先の名前は昔のままにする。**
#: Blender と UE の既定の読み先が physics_test_track.json なので、
#: ここを変えると両方を同時に直す必要がある。
DEFAULT_KEY = "physics_test_track"


def output_path(key: str) -> Path:
    return OUTPUT_DIR / ("{}.json".format(key))

# 路肩の余裕 [m]。**路面はここまで作り、樹木はこれより外に置く。**
# 車がコースアウトしたときに木へ突っ込むのは、物理に衝突判定が無いので
# 「すり抜ける」という不自然な絵になる。距離を取って起きにくくする。
SHOULDER_M = 6.0

# 樹木を置き始める距離 [m]（中心線から）。
TREE_MIN_OFFSET_M = 18.0
TREE_MAX_OFFSET_M = 70.0


def export(key: str) -> int:
    track = build(key, spacing_m=1.0)
    elevation = profile_for(key)
    output = output_path(key)

    position_error, heading_error = closure_error(track)
    # **閉じていないコースを書き出さない。** 終端で中心線が始点へ飛ぶと、
    # 路面メッシュがねじれ、樹木が1箇所に固まる。
    if position_error > 0.5 or abs(heading_error) > math.radians(1.0):
        print(
            "ERROR: コースが閉じていない（位置 %.3f m / 方位 %.4f rad）"
            % (position_error, heading_error),
            file=sys.stderr,
        )
        return 1

    points = [
        {
            "s_m": p.s_m,
            "x_m": p.x_m,
            "y_m": p.y_m,
            "heading_rad": p.heading_rad,
            "curvature_1pm": p.curvature_1pm,
            "z_m": p.z_m,
            "gradient_pct": p.gradient_pct,
            "label": p.label,
        }
        for p in track.points
    ]

    labels = {}
    for p in track.points:
        labels.setdefault(p.label, {"count": 0, "start_s_m": p.s_m})
        labels[p.label]["count"] += 1

    payload = {
        "_meta": {
            "generator": "Tools/export_track.py",
            "source": "Tracks/track_catalogue.py:{}".format(key),
            "purpose": (
                "物理と描画で同じコース形状を使うための受け渡し。"
                "**このファイルを手で編集しないこと。** 形状の定義は "
                "physics_test_track.py にしかない。変えたら再生成する。"
            ),
            "frame": "physics (X forward-ish, Y left, Z up, metres, right-handed)",
        },
        "name": track.name,
        "length_m": track.length_m,
        "width_m": track.width_m,
        "shoulder_m": SHOULDER_M,
        "tree_offset_m": [TREE_MIN_OFFSET_M, TREE_MAX_OFFSET_M],
        "spacing_m": track.points[1].s_m - track.points[0].s_m,
        # **縦断の性格を持ち回る。** 高架かどうかで地面の作り方が変わる
        # （高架は路面に追従させない。桁の下は地面のまま）。
        "elevation": {
            "is_viaduct": elevation.is_viaduct,
            "ground_level_m": elevation.ground_level_m,
            "min_z_m": min(p.z_m for p in track.points),
            "max_z_m": max(p.z_m for p in track.points),
            "max_gradient_pct": max(abs(p.gradient_pct) for p in track.points),
        },
        "closure": {"position_error_m": position_error, "heading_error_rad": heading_error},
        "sections": labels,
        "points": points,
    }

    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=1)
        handle.write("\n")

    xs = [p.x_m for p in track.points]
    ys = [p.y_m for p in track.points]

    print("書き出した: {}".format(output.relative_to(REPO_ROOT)))
    print("  周長     : {:.1f} m（点 {} 個、間隔 {:.2f} m）".format(
        track.length_m, len(track.points), payload["spacing_m"]))
    print("  路面幅   : {:.1f} m（路肩 +{:.1f} m）".format(track.width_m, SHOULDER_M))
    print("  閉合誤差 : 位置 {:.4f} m / 方位 {:.5f} rad".format(position_error, heading_error))
    print("  範囲     : x {:.1f}..{:.1f} m / y {:.1f}..{:.1f} m".format(
        min(xs), max(xs), min(ys), max(ys)))
    print()
    print("  区間:")
    for label, info in sorted(labels.items(), key=lambda kv: kv[1]["start_s_m"]):
        print("    {:<16s} {:6.1f} m から {:5d} m 分".format(
            label, info["start_s_m"], info["count"]))
    print()
    return 0


def main() -> int:
    keys = sys.argv[1:] or sorted(CATALOGUE)
    for key in keys:
        if key not in CATALOGUE:
            print("ERROR: 知らないコース: {}（ある: {}）".format(
                key, ", ".join(sorted(CATALOGUE))), file=sys.stderr)
            return 1
        if export(key) != 0:
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
