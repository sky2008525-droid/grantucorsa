"""公開寸法からホイールとタイヤを生成する（Blender headless）.

    blender --background --python Blender/build_wheels.py -- <vehicle.json> <out_dir>

## なぜ元モデルの車輪を使わないのか

`Docs/SPEC_PHASE2_BACKLOG.md` §3.2-5 が指示している:

> **個別生成**: ホイール、タイヤ、ブレーキ等、精度が低い部品は、
> **公開寸法に基づき Blender で幾何学的に自動生成して置換する**

Sketchfab のモデルから切り出した車輪は、リムのディテールが乏しく
UE 上で「黒い輪」にしか見えなかった。**タイヤ・ホイールは寸法が
公表されている数少ない部品**なので、生成したほうが実車に近い。

## 寸法の出どころ（すべて vehicle.json）

    tires.size        215/45R17 87W          official
    tires.wheel       17x7J +48 PCD100 5H    secondary

    タイヤ外径   17in * 25.4 + 2 * 215 * 0.45  = 625.3 mm
    リム外径     17in                          = 431.8 mm
    サイドウォール 215 * 0.45                   =  96.75 mm
    トレッド幅   215 mm
    リム幅       7J = 7in                      = 177.8 mm
    ボルト       PCD 100 mm / 5 穴

**ここで寸法を決め打ちしないこと。** すべて vehicle.json から読み、
読めなければ止まる（憲法ルール1）。

## 座標系

切り出した車輪と同じにする（`Blender/decompose_vehicle.py`）:
原点は車軸中心、**回転軸は Y（車幅方向）**、単位 m。
X と Z が直径方向。
"""

from __future__ import annotations

import json
import math
import os
import re
import sys

import bpy
import bmesh

INCH_M = 0.0254

# 見た目の作り込み。**寸法ではないので vehicle.json に入れない**（ルール18）。
SPOKE_COUNT = 10
RIM_SEGMENTS = 64
TREAD_SHOULDER_RATIO = 0.12   # 肩の丸みをトレッド幅の何割にするか
# リム面（スポークが見える面）を、**トレッド外面の何割の位置に置くか**。
#
# **リム幅を基準にしてはいけない。** 最初リム幅の 30% 奥に置いたところ、
# タイヤ外面より 7 cm も内側になり、ホイールアーチの奥で
# スポークがほとんど見えなくなった。実車のホイールはタイヤ外面の
# すぐ内側に面がある。
RIM_FACE_OF_TREAD = 0.80


def log(fmt, *args):
    print(("[wheels] " + fmt) % args)


def clear_scene():
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete(use_global=False)
    for block in (bpy.data.meshes, bpy.data.materials):
        for item in list(block):
            block.remove(item)


def read_dimensions(vehicle_json_path):
    """vehicle.json からタイヤとリムの寸法を読む。

    **読めなければ例外で止める。** 既定値で代用すると、実車と無関係な
    寸法の車輪が「生成できた」ことになる（憲法ルール1）。
    """
    with open(vehicle_json_path, encoding="utf-8") as handle:
        data = json.load(handle)
    tires = data["tires"]

    size_text = tires["size"]["value"]           # "215/45R17 87W"
    match = re.match(r"(\d+)/(\d+)R(\d+)", size_text)
    if match is None:
        raise ValueError("tires.size を解釈できない: %r" % size_text)
    section_mm, aspect_pct, rim_inch = (int(g) for g in match.groups())

    wheel_text = tires["wheel"]["value"]         # "17x7J +48 PCD100 5H hub56"
    rim_width_match = re.search(r"x(\d+(?:\.\d+)?)J", wheel_text)
    pcd_match = re.search(r"PCD(\d+)", wheel_text)
    holes_match = re.search(r"(\d+)H", wheel_text)
    if not (rim_width_match and pcd_match and holes_match):
        raise ValueError("tires.wheel を解釈できない: %r" % wheel_text)

    tread_m = section_mm / 1000.0
    sidewall_m = section_mm * aspect_pct / 100.0 / 1000.0
    rim_radius_m = rim_inch * INCH_M / 2.0
    tyre_radius_m = rim_radius_m + sidewall_m

    dims = {
        "tyre_radius_m": tyre_radius_m,
        "rim_radius_m": rim_radius_m,
        "tread_m": tread_m,
        "rim_width_m": float(rim_width_match.group(1)) * INCH_M,
        "pcd_m": int(pcd_match.group(1)) / 1000.0,
        "bolt_holes": int(holes_match.group(1)),
        "size_text": size_text,
        "wheel_text": wheel_text,
    }

    # **公表されている転がり半径と突き合わせる。** ここがずれるなら
    # サイズ文字列の解釈を間違えている。
    published = tires["unloaded_radius"]["value"]
    error = abs(tyre_radius_m - published) / published
    if error > 0.01:
        raise ValueError(
            "サイズから求めた半径 %.4f m が tires.unloaded_radius %.4f m と %.1f%% 違う"
            % (tyre_radius_m, published, error * 100.0)
        )
    dims["radius_check_error_pct"] = error * 100.0
    return dims


def revolve_profile(bm, profile, segments, axis_is_y=True):
    """(radius, offset) の並びを回転体にする。offset は回転軸方向。"""
    rings = []
    for radius, offset in profile:
        ring = []
        for index in range(segments):
            angle = 2.0 * math.pi * index / segments
            c, s = math.cos(angle), math.sin(angle)
            if axis_is_y:
                position = (radius * c, offset, radius * s)
            else:
                position = (offset, radius * c, radius * s)
            ring.append(bm.verts.new(position))
        rings.append(ring)

    for lower, upper in zip(rings, rings[1:]):
        for index in range(segments):
            nxt = (index + 1) % segments
            try:
                bm.faces.new((lower[index], lower[nxt], upper[nxt], upper[index]))
            except ValueError:
                pass
    return rings


def cap_ring(bm, ring, offset, axis_is_y=True):
    """リングの中心に頂点を置いて円盤で塞ぐ。"""
    centre = bm.verts.new((0.0, offset, 0.0) if axis_is_y else (offset, 0.0, 0.0))
    count = len(ring)
    for index in range(count):
        nxt = (index + 1) % count
        try:
            bm.faces.new((centre, ring[index], ring[nxt]))
        except ValueError:
            pass
    return centre


def build_tyre(dims):
    """タイヤ。断面を回転させて作る。"""
    radius = dims["tyre_radius_m"]
    half_tread = dims["tread_m"] / 2.0
    shoulder = dims["tread_m"] * TREAD_SHOULDER_RATIO
    bead = dims["rim_radius_m"]
    half_rim = dims["rim_width_m"] / 2.0

    # (半径, 軸方向位置)。内側ビード -> サイドウォール -> 肩 -> トレッド -> 反対側
    profile = [
        (bead, -half_rim),
        (radius * 0.86, -half_tread),
        (radius - shoulder * 0.35, -half_tread),
        (radius, -half_tread + shoulder),
        (radius, half_tread - shoulder),
        (radius - shoulder * 0.35, half_tread),
        (radius * 0.86, half_tread),
        (bead, half_rim),
    ]

    mesh = bpy.data.meshes.new("ZN6_tyre")
    bm = bmesh.new()
    revolve_profile(bm, profile, RIM_SEGMENTS)
    bm.normal_update()
    bm.to_mesh(mesh)
    bm.free()

    obj = bpy.data.objects.new("ZN6_tyre", mesh)
    bpy.context.scene.collection.objects.link(obj)
    return obj


def build_rim(dims):
    """リム。バレル + 面 + スポーク + ハブ。"""
    rim_radius = dims["rim_radius_m"]
    half_rim = dims["rim_width_m"] / 2.0
    # リム面はトレッド外面のすぐ内側。**アーチの奥に埋めない。**
    face_offset = dims["tread_m"] / 2.0 * RIM_FACE_OF_TREAD
    dish = dims["rim_width_m"] * 0.22      # 面まわりの落とし込み
    hub_radius = rim_radius * 0.30

    mesh = bpy.data.meshes.new("ZN6_rim")
    bm = bmesh.new()

    # バレル（タイヤの内側に隠れる筒）
    barrel = [
        (rim_radius * 0.97, -half_rim),
        (rim_radius * 0.88, -half_rim * 0.55),
        (rim_radius * 0.88, face_offset * 0.4),
        (rim_radius * 0.97, face_offset),
    ]
    rings = revolve_profile(bm, barrel, RIM_SEGMENTS)
    cap_ring(bm, rings[0], -half_rim)

    # 外周リップ
    lip = [
        (rim_radius * 0.97, face_offset),
        (rim_radius, face_offset + dish * 0.25),
        (rim_radius * 0.95, face_offset + dish * 0.35),
    ]
    revolve_profile(bm, lip, RIM_SEGMENTS)

    # ハブ
    hub = [
        (hub_radius, face_offset * 0.2),
        (hub_radius, face_offset + dish * 0.30),
        (hub_radius * 0.45, face_offset + dish * 0.34),
    ]
    hub_rings = revolve_profile(bm, hub, RIM_SEGMENTS)
    cap_ring(bm, hub_rings[-1], face_offset + dish * 0.34)

    bm.to_mesh(mesh)
    bm.free()

    obj = bpy.data.objects.new("ZN6_rim", mesh)
    bpy.context.scene.collection.objects.link(obj)

    spokes = build_spokes(dims, face_offset, dish, hub_radius)
    return obj, spokes


def build_spokes(dims, face_offset, dish, hub_radius):
    """スポーク。ハブから外周へ伸びる板を回して並べる。"""
    rim_radius = dims["rim_radius_m"]
    mesh = bpy.data.meshes.new("ZN6_spokes")
    bm = bmesh.new()

    inner = hub_radius * 0.92
    outer = rim_radius * 0.95
    thickness = dims["rim_width_m"] * 0.075
    half_width_inner = 2.0 * math.pi * inner / SPOKE_COUNT * 0.34
    half_width_outer = 2.0 * math.pi * outer / SPOKE_COUNT * 0.20

    for index in range(SPOKE_COUNT):
        angle = 2.0 * math.pi * index / SPOKE_COUNT
        c, s = math.cos(angle), math.sin(angle)

        def place(radius, half_width, offset):
            # 半径方向に radius、周方向に ±half_width、軸方向に offset
            return bm.verts.new((
                radius * c - half_width * s,
                offset,
                radius * s + half_width * c,
            ))

        front = face_offset + dish * 0.30
        back = front - thickness
        corners = [
            place(inner, -half_width_inner, front),
            place(inner, half_width_inner, front),
            place(outer, half_width_outer, front),
            place(outer, -half_width_outer, front),
            place(inner, -half_width_inner, back),
            place(inner, half_width_inner, back),
            place(outer, half_width_outer, back),
            place(outer, -half_width_outer, back),
        ]
        quads = [(0, 1, 2, 3), (7, 6, 5, 4), (0, 4, 5, 1),
                 (1, 5, 6, 2), (2, 6, 7, 3), (3, 7, 4, 0)]
        for quad in quads:
            try:
                bm.faces.new([corners[i] for i in quad])
            except ValueError:
                pass

    bm.normal_update()
    bm.to_mesh(mesh)
    bm.free()

    obj = bpy.data.objects.new("ZN6_spokes", mesh)
    bpy.context.scene.collection.objects.link(obj)
    return obj


def make_material(name, base_color, roughness, metallic):
    material = bpy.data.materials.new(name)
    material.use_nodes = True

    # **ノードを名前で探さないこと。** Blender 5.0 では既定シェーダの
    # 名前が "Principled BSDF" ではなく、KeyError で落ちる。型で探す。
    bsdf = next(
        (n for n in material.node_tree.nodes if n.type == "BSDF_PRINCIPLED"), None)
    if bsdf is None:
        bsdf = material.node_tree.nodes.new("ShaderNodeBsdfPrincipled")
        output = next(
            (n for n in material.node_tree.nodes if n.type == "OUTPUT_MATERIAL"), None)
        if output is not None:
            material.node_tree.links.new(bsdf.outputs[0], output.inputs["Surface"])
    bsdf.inputs["Base Color"].default_value = base_color
    bsdf.inputs["Roughness"].default_value = roughness
    bsdf.inputs["Metallic"].default_value = metallic
    return material


def export_glb(objects, path):
    bpy.ops.object.select_all(action="DESELECT")
    for obj in objects:
        obj.select_set(True)
    bpy.context.view_layer.objects.active = objects[0]
    bpy.ops.export_scene.gltf(
        filepath=path, export_format="GLB", use_selection=True,
        export_yup=True, export_apply=True, export_materials="EXPORT",
    )


def main():
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    if len(argv) < 2:
        print("usage: ... -- <vehicle.json> <out_dir>")
        return 1
    vehicle_json, out_dir = argv[0], argv[1]
    os.makedirs(out_dir, exist_ok=True)

    dims = read_dimensions(vehicle_json)
    log("%s / %s", dims["size_text"], dims["wheel_text"])
    log("タイヤ外径 %.4f m / リム外径 %.4f m / トレッド %.3f m / リム幅 %.4f m",
        dims["tyre_radius_m"] * 2, dims["rim_radius_m"] * 2,
        dims["tread_m"], dims["rim_width_m"])
    log("公表転がり半径との差 %.2f%%", dims["radius_check_error_pct"])

    clear_scene()

    tyre = build_tyre(dims)
    rim, spokes = build_rim(dims)

    tyre.data.materials.append(
        make_material("ZN6_TyreRubber", (0.020, 0.020, 0.022, 1.0), 0.85, 0.0))
    rim_material = make_material("ZN6_RimAlloy", (0.34, 0.35, 0.37, 1.0), 0.28, 1.0)
    rim.data.materials.append(rim_material)
    spokes.data.materials.append(rim_material)

    # **左右で鏡像にする。** 片側ぶんだけ作ってスケール -1 で反転すると
    # 法線が裏返るので、頂点を作り直す。ここでは軸対称な形なので
    # そのまま両側に使える（スポークの向きだけが左右同じになる）。
    parts = [tyre, rim, spokes]
    total_tris = sum(len(o.data.polygons) for o in parts)

    # **3つを1つのオブジェクトに結合してから出す。**
    #
    # タイヤ・リム・スポークを別オブジェクトのまま glTF に入れると、
    # UE の Interchange が**別々の StaticMesh として取り込む。**
    # 名前で1つ選ぶ実装だとリムだけが使われ、**タイヤの無い車輪**に
    # なった（実際にそうなり、ホイールアーチが黒い穴に見えた）。
    bpy.ops.object.select_all(action="DESELECT")
    for obj in parts:
        obj.select_set(True)
    bpy.context.view_layer.objects.active = parts[0]
    bpy.ops.object.join()
    wheel = bpy.context.view_layer.objects.active
    wheel.name = "ZN6_wheel"
    parts = [wheel]

    # **左右を別に出す。** リム面（スポーク側）は車体の外を向く。
    # 片側ぶんだけ作って UE 側でスケール -1 にすると法線が裏返るので、
    # 頂点を鏡像にしたものを作って出す。
    #
    # 物理座標系は Y が左。生成した車輪は面が +Y を向いているので
    # そのまま左輪、Y を反転したものが右輪。
    written = {}
    for side, sign in (("left", 1.0), ("right", -1.0)):
        if sign < 0.0:
            for obj in parts:
                mesh = obj.data
                for vert in mesh.vertices:
                    vert.co.y = -vert.co.y
                mesh.flip_normals()

        path = os.path.join(out_dir, "ZN6_wheel_%s.glb" % side)
        export_glb(parts, path)
        written[side] = os.path.basename(path)
        log("書き出した: %s（%d 面）", path, total_tris)

    manifest = {
        "_note": "vehicle.json の公開寸法から生成した車輪"
                 "（SPEC_PHASE2_BACKLOG.md 3.2-5）。元モデルの車輪は使わない。",
        "source_fields": ["tires.size", "tires.wheel", "tires.unloaded_radius"],
        "tyre_diameter_m": dims["tyre_radius_m"] * 2,
        "rim_diameter_m": dims["rim_radius_m"] * 2,
        "tread_m": dims["tread_m"],
        "rim_width_m": dims["rim_width_m"],
        "pcd_m": dims["pcd_m"],
        "bolt_holes": dims["bolt_holes"],
        "spoke_count": SPOKE_COUNT,
        "triangles": total_tris,
        "files": written,
        "note_sides": "left はリム面が +Y（物理座標系の左）を向く。right はその鏡像。",
    }
    with open(os.path.join(out_dir, "wheel_manifest.json"), "w", encoding="utf-8") as handle:
        json.dump(manifest, handle, ensure_ascii=False, indent=2)

    return 0


if __name__ == "__main__":
    sys.exit(main())
