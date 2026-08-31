"""GLB の中身を幾何から偵察する（Blender headless 用）.

    blender --background --python Blender/recon_glb.py -- <input.glb> <report.json>

**なぜ名前を使わないか**

`Vehicles/ZN6/Raw/toyota_gt-86.glb` は Sketchfab 経由の SketchUp 出力で、
ノード名が `Material2`〜`Material5` になっている。**名前はマテリアル名であって
部位名ではない。** メッシュもマテリアル単位で結合されており、1つのメッシュに
ボディ外板と車輪が混在している。

したがって「車輪」を名前で取り出すことはできない。**形で見つけるしかない。**

**なぜスケールを先に決められないか**

このモデルは寸法が任意単位（全体 AABB が 228.6 x 487.0 x 162.5）。
実車と突き合わせようにも、比較対象（ホイールベース）を測るには先に車輪を
見つける必要がある。**鶏と卵になる。**

これを避けるため、車輪の判定を**スケール不変な量だけ**で行う:

  - 回転体であること      -> 進行方向の径 と 上下方向の径 がほぼ等しい
  - 扁平であること        -> 車幅方向の厚み < 径 * 0.6
  - 接地していること      -> 中心の高さ が 全体高さの下から 30% 以内

この3つは単位に依存しない。4つ見つかれば、その中心間距離が
ホイールベースとトレッドになり、そこから初めてスケールが決まる。
"""

from __future__ import annotations

import json
import sys
import time
from collections import defaultdict

import bpy
from mathutils import Vector

# --- モデルの軸（inspect_glb.py の計測結果）------------------------------------
#
# SketchUp 由来のため Z-up のまま出力されている（glTF の規約は Y-up だが、
# このファイルは従っていない）。**規約を信じず、実測した軸を使う。**
#
#   X = 車幅方向 (span 228.6)
#   Y = 進行方向 (span 487.0)
#   Z = 上下方向 (span 162.5)
AXIS_WIDTH, AXIS_LENGTH, AXIS_HEIGHT = 0, 1, 2

# 車輪判定のしきい値。**スケール不変な比だけで書くこと。**
ROUNDNESS_TOLERANCE = 0.18   # |L - H| / max(L, H) がこれ以下なら回転体とみなす
FLATNESS_RATIO = 0.60        # 幅 / 径 がこれ以下なら扁平とみなす
GROUND_FRACTION = 0.30       # 中心高さが全体の下から何割以内にあれば接地とみなす
MIN_DIAMETER_FRACTION = 0.06 # 全長のこの割合より小さい回転体は部品（ボルト等）


def world_bbox(obj):
    """ワールド座標での AABB。**ローカル座標で測らないこと。**

    ノード階層に回転・スケールが乗っている場合、ローカルの寸法は
    実際の見た目と一致しない。
    """
    corners = [obj.matrix_world @ Vector(c) for c in obj.bound_box]
    lo = Vector((min(c[a] for c in corners) for a in range(3)))
    hi = Vector((max(c[a] for c in corners) for a in range(3)))
    return lo, hi


def clear_scene():
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete(use_global=False)
    for block in (bpy.data.meshes, bpy.data.materials, bpy.data.images):
        for item in list(block):
            block.remove(item)


def separate_loose(objects):
    """全メッシュを連結成分（loose parts）に分解する。

    マテリアル単位で結合されているものを、**部品単位に戻す。**
    ここで初めて「1つの車輪」が1オブジェクトになる。
    """
    bpy.ops.object.select_all(action="DESELECT")
    for obj in objects:
        obj.select_set(True)
    bpy.context.view_layer.objects.active = objects[0]

    bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.mesh.select_all(action="SELECT")
    bpy.ops.mesh.separate(type="LOOSE")
    bpy.ops.object.mode_set(mode="OBJECT")

    return [o for o in bpy.context.scene.objects if o.type == "MESH"]


def describe(obj):
    lo, hi = world_bbox(obj)
    size = [hi[a] - lo[a] for a in range(3)]
    centre = [(hi[a] + lo[a]) / 2.0 for a in range(3)]
    return {
        "name": obj.name,
        "lo": list(lo),
        "hi": list(hi),
        "size": size,
        "centre": centre,
        "verts": len(obj.data.vertices),
        "tris": sum(len(p.vertices) - 2 for p in obj.data.polygons),
    }


def looks_like_wheel(part, world_lo, world_hi):
    """スケール不変な条件だけで車輪らしさを判定する。"""
    size = part["size"]
    length_d = size[AXIS_LENGTH]
    height_d = size[AXIS_HEIGHT]
    width_t = size[AXIS_WIDTH]

    diameter = max(length_d, height_d)
    if diameter <= 0.0:
        return False, "degenerate"

    total_length = world_hi[AXIS_LENGTH] - world_lo[AXIS_LENGTH]
    if diameter < total_length * MIN_DIAMETER_FRACTION:
        return False, "too small"

    roundness = abs(length_d - height_d) / diameter
    if roundness > ROUNDNESS_TOLERANCE:
        return False, "not round (%.3f)" % roundness

    if width_t > diameter * FLATNESS_RATIO:
        return False, "not flat (%.3f)" % (width_t / diameter)

    total_height = world_hi[AXIS_HEIGHT] - world_lo[AXIS_HEIGHT]
    height_above_ground = part["centre"][AXIS_HEIGHT] - world_lo[AXIS_HEIGHT]
    if height_above_ground > total_height * GROUND_FRACTION:
        return False, "too high (%.3f)" % (height_above_ground / total_height)

    return True, "roundness=%.3f flat=%.3f" % (roundness, width_t / diameter)


# --- 実車の公式値（Vehicles/ZN6/vehicle.json、source=official）------------------
#
# **ここに書いた値は検算用であって、車両仕様の定義ではない。**
# 正本は vehicle.json。食い違ったら vehicle.json が正しい。
WHEELBASE_M = 2.570
LENGTH_M = 4.240
TYRE_UNLOADED_RADIUS_M = 0.3127   # tires.unloaded_radius (calculated)


def cluster_wheels(candidates, tolerance=20.0):
    """車輪候補を (幅方向, 進行方向) の位置でまとめ、4つの車輪にする。

    1つの車輪はタイヤ・リム・スポーク等の複数パーツに割れているので、
    **候補の数 = 車輪の数ではない。** 位置でまとめて初めて4つになる。
    """
    clusters = []
    for cand in candidates:
        cx = cand["centre"][AXIS_WIDTH]
        cy = cand["centre"][AXIS_LENGTH]
        for cl in clusters:
            if abs(cl["x"] - cx) < tolerance and abs(cl["y"] - cy) < tolerance:
                cl["members"].append(cand)
                break
        else:
            clusters.append({"x": cx, "y": cy, "members": [cand]})

    raw = []
    for cl in clusters:
        members = cl["members"]
        raw.append(
            {
                "diameter": max(m["size"][AXIS_LENGTH] for m in members),
                "width": max(m["size"][AXIS_WIDTH] for m in members),
                "members": members,
            }
        )

    # **薄い円盤の誤検出を落とす。**
    #
    # 「回転体・扁平・接地」の3条件だけだと、車体下面の平たい円板
    # （直径 40.6 / 32.1 / 29.8、厚み 0.4〜3.9）まで拾ってしまい、
    # クラスタが7つになる。
    #
    # **ZN6 GT は4輪とも 215/45R17 で同一径**（vehicle.json tires.size）。
    # したがって「最大径とほぼ等しい径を持つ」ことを条件に加えれば、
    # 本物の車輪だけが残る。**径の絶対値をしきい値に書かないこと**
    # （スケールが未確定な段階なので書けない）。
    if not raw:
        return []
    largest = max(c["diameter"] for c in raw)
    kept = [c for c in raw if c["diameter"] >= largest * 0.9]

    out = []
    for cl in kept:
        # 中心は「タイヤと同径で、かつ真円のパーツ」の合併 AABB の中点で取る。
        #
        # **進行方向の径だけで絞ってはいけない。** フェンダーライナーのような
        # 縦長の部品（67.56 x 57.10）が径の条件だけを通り、車輪中心とは違う
        # 位置にあるため、合併 AABB を膨らませて軸位置を 2% ずらす
        # （実際にホイールベースが 295.9 -> 289.0 に化け、検算誤差が
        #  0.2% から 2.2% に悪化した）。**上下方向の径も見ること。**
        core = [
            m for m in cl["members"]
            if m["size"][AXIS_LENGTH] >= largest * 0.95
            and m["size"][AXIS_HEIGHT] >= largest * 0.95
        ]
        if not core:
            core = cl["members"]
        lo = [min(m["lo"][a] for m in core) for a in range(3)]
        hi = [max(m["hi"][a] for m in core) for a in range(3)]
        out.append(
            {
                "centre": [(lo[a] + hi[a]) / 2.0 for a in range(3)],
                "lo": lo,
                "hi": hi,
                "diameter": cl["diameter"],
                "width": hi[AXIS_WIDTH] - lo[AXIS_WIDTH],
                "part_count": len(cl["members"]),
            }
        )
    return out


def derive_axes(clusters, world_lo, world_hi):
    """車輪の配置からスケールと前後の向きを決める。

    **スケールの第一基準はホイールベース**（Docs/SPEC_PHASE2_BACKLOG.md §3.2）。
    全長やタイヤ径は独立検算に使う。ここで前後の向きも確定させる。

    前方向は `preview_render.py` の描画で目視確認した結果を根拠にする
    （このモデルは +進行方向側が車体前面）。**座標だけからは決められない。**
    """
    if len(clusters) != 4:
        return {"error": "車輪クラスタが4つでない (%d)。しきい値を疑うこと。" % len(clusters)}

    ys = sorted(set(round(c["centre"][AXIS_LENGTH], 1) for c in clusters))
    xs = sorted(set(round(c["centre"][AXIS_WIDTH], 1) for c in clusters))
    if len(ys) != 2:
        return {"error": "前後の軸位置が2つにならない: %s" % ys}

    wheelbase_u = abs(ys[1] - ys[0])
    scale = WHEELBASE_M / wheelbase_u

    diameter_u = max(c["diameter"] for c in clusters)
    length_u = world_hi[AXIS_LENGTH] - world_lo[AXIS_LENGTH]

    return {
        "scale_m_per_unit": scale,
        "scale_basis": "wheelbase (Docs/SPEC_PHASE2_BACKLOG.md 3.2 の第一基準)",
        "wheelbase_units": wheelbase_u,
        "front_axle_y": max(ys),      # +進行方向側が前（描画で確認済み）
        "rear_axle_y": min(ys),
        "left_right_x": xs,
        "ground_z": world_lo[AXIS_HEIGHT],
        "wheel_radius_units": diameter_u / 2.0,
        "cross_check": {
            "tyre_radius_m": diameter_u / 2.0 * scale,
            "tyre_radius_official_m": TYRE_UNLOADED_RADIUS_M,
            "tyre_radius_error_pct": (diameter_u / 2.0 * scale / TYRE_UNLOADED_RADIUS_M - 1.0) * 100.0,
            "length_m": length_u * scale,
            "length_official_m": LENGTH_M,
            "length_error_pct": (length_u * scale / LENGTH_M - 1.0) * 100.0,
        },
    }


def main():
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    if len(argv) < 2:
        print("usage: blender --background --python recon_glb.py -- <in.glb> <out.json>")
        return 1
    glb_path, report_path = argv[0], argv[1]

    started = time.time()
    clear_scene()

    print("[recon] importing %s" % glb_path)
    bpy.ops.import_scene.gltf(filepath=glb_path)
    imported = [o for o in bpy.context.scene.objects if o.type == "MESH"]
    print("[recon] imported meshes: %d  (%.1fs)" % (len(imported), time.time() - started))

    if not imported:
        print("[recon] ERROR: メッシュが1つも読み込めていない")
        return 1

    before = [describe(o) for o in imported]

    t0 = time.time()
    parts_objs = separate_loose(imported)
    print("[recon] loose parts: %d  (%.1fs)" % (len(parts_objs), time.time() - t0))

    parts = [describe(o) for o in parts_objs]

    world_lo = [min(p["lo"][a] for p in parts) for a in range(3)]
    world_hi = [max(p["hi"][a] for p in parts) for a in range(3)]

    wheels, rejected = [], []
    for part in parts:
        ok, why = looks_like_wheel(part, world_lo, world_hi)
        (wheels if ok else rejected).append({**part, "why": why})

    wheels.sort(key=lambda p: -p["size"][AXIS_LENGTH])

    clusters = cluster_wheels(wheels)
    axes = derive_axes(clusters, world_lo, world_hi)

    report = {
        "input": glb_path,
        "world_lo": world_lo,
        "world_hi": world_hi,
        "world_span": [world_hi[a] - world_lo[a] for a in range(3)],
        "mesh_count_before_split": len(before),
        "part_count_after_split": len(parts),
        "total_tris": sum(p["tris"] for p in parts),
        "wheel_clusters": clusters,
        "axes": axes,
        "wheel_candidates": wheels[:40],
        "largest_parts": sorted(parts, key=lambda p: -p["tris"])[:40],
        "elapsed_s": time.time() - started,
    }

    with open(report_path, "w", encoding="utf-8") as handle:
        json.dump(report, handle, ensure_ascii=False, indent=2)

    print("[recon] wheel candidates: %d -> clusters: %d" % (len(wheels), len(clusters)))
    for cl in sorted(clusters, key=lambda c: (-c["centre"][AXIS_LENGTH], c["centre"][AXIS_WIDTH])):
        print("   centre=%s  diam=%.3f  width=%.3f  parts=%d" % (
            ",".join("%8.3f" % v for v in cl["centre"]),
            cl["diameter"], cl["width"], cl["part_count"],
        ))

    if "error" in axes:
        print("[recon] !! %s" % axes["error"])
    else:
        cc = axes["cross_check"]
        print("[recon] scale = %.8f m/unit (基準: ホイールベース %.3f u)"
              % (axes["scale_m_per_unit"], axes["wheelbase_units"]))
        print("[recon] 独立検算  タイヤ半径 %.4f m (公式 %.4f) 誤差 %+.2f%%"
              % (cc["tyre_radius_m"], cc["tyre_radius_official_m"], cc["tyre_radius_error_pct"]))
        print("[recon] 独立検算  全長      %.4f m (公式 %.4f) 誤差 %+.2f%%"
              % (cc["length_m"], cc["length_official_m"], cc["length_error_pct"]))

    print("[recon] wrote %s (%.1fs)" % (report_path, time.time() - started))
    return 0


if __name__ == "__main__":
    sys.exit(main())
