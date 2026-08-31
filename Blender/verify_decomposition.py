"""分解結果を組み直して描画し、正しさを目で確認する（Blender headless）.

    blender --background --python Blender/verify_decomposition.py -- \
        <export_dir> <out_dir>

**なぜ組み直すのか**

`decompose_vehicle.py` の出力は数値としては検算済みだが、
「車として成立しているか」は数値では分からない。次のような壊れ方は
寸法の検算を通過してしまう:

  - 車輪が抜けたあとのボディに穴が開いている
  - ブレーキキャリパまで車輪側へ行ってしまい、車体に残っていない
  - 左右を取り違えて鏡像になっている

**回して確かめる**

車輪の原点が車軸中心からずれていると、回転させた瞬間に軌道が振れる。
静止画を1枚見るだけでは分からないので、**回転させた状態と操舵させた
状態を描画する。** ずれていれば、タイヤがホイールアーチから飛び出す。
"""

from __future__ import annotations

import json
import math
import os
import sys

import bpy
from mathutils import Vector

WHEELS = ("FL", "FR", "RL", "RR")


def clear_scene():
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete(use_global=False)


def import_fbx(path):
    before = set(bpy.data.objects.keys())
    bpy.ops.import_scene.fbx(filepath=path, axis_forward="X", axis_up="Z")
    return [bpy.data.objects[n] for n in bpy.data.objects.keys() if n not in before]


def scene_bounds():
    # **測る前に依存グラフを更新する。** location を代入しただけでは
    # matrix_world がまだ古く、置いたはずの車輪が原点にいるものとして
    # 測ってしまう（実際、接地しているのに「地面下 0.312 m」と出た）。
    bpy.context.view_layer.update()

    lo = Vector((1e30, 1e30, 1e30))
    hi = Vector((-1e30, -1e30, -1e30))
    for obj in bpy.context.scene.objects:
        if obj.type != "MESH":
            continue
        for vert in obj.data.vertices:
            p = obj.matrix_world @ vert.co
            for a in range(3):
                lo[a] = min(lo[a], p[a])
                hi[a] = max(hi[a], p[a])
    return lo, hi


def setup_camera(centre, span, direction, up_axis):
    distance = max(span) * 3.0
    cam_data = bpy.data.cameras.new("Cam")
    cam_data.type = "ORTHO"
    cam_data.ortho_scale = max(span) * 1.2
    cam_data.clip_start = distance * 0.01
    cam_data.clip_end = distance * 3.0
    cam = bpy.data.objects.new("Cam", cam_data)
    bpy.context.scene.collection.objects.link(cam)
    cam.location = centre + direction.normalized() * distance
    cam.rotation_euler = (-direction.normalized()).to_track_quat("-Z", up_axis).to_euler()
    bpy.context.scene.camera = cam
    return cam


def render_to(path):
    scene = bpy.context.scene
    scene.render.engine = "BLENDER_WORKBENCH"
    scene.render.resolution_x = 1024
    scene.render.resolution_y = 1024
    scene.render.filepath = path
    bpy.ops.render.render(write_still=True)


def render_views(out_dir, tag):
    lo, hi = scene_bounds()
    centre = (lo + hi) / 2.0
    span = Vector((hi[a] - lo[a] for a in range(3)))

    views = {
        "side": (Vector((0.0, 1.0, 0.0)), "Z"),
        "front": (Vector((1.0, 0.0, 0.0)), "Z"),
        "quarter": (Vector((1.0, 0.9, 0.5)), "Z"),
    }
    for name, (direction, up_axis) in views.items():
        for cam in [o for o in bpy.context.scene.objects if o.type == "CAMERA"]:
            bpy.data.objects.remove(cam, do_unlink=True)
        setup_camera(centre, span, direction, up_axis)
        render_to(os.path.join(out_dir, "%s_%s.png" % (tag, name)))
    print("[verify] rendered %s (bbox %s .. %s)" % (
        tag,
        ",".join("%.3f" % v for v in lo),
        ",".join("%.3f" % v for v in hi)))


def main():
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    if len(argv) < 2:
        print("usage: ... -- <export_dir> <out_dir>")
        return 1
    export_dir, out_dir = argv[0], argv[1]
    os.makedirs(out_dir, exist_ok=True)

    with open(os.path.join(export_dir, "manifest.json"), encoding="utf-8") as handle:
        manifest = json.load(handle)

    clear_scene()

    body_objs = import_fbx(os.path.join(export_dir, manifest["parts"]["body"]["file"]))
    print("[verify] body objects: %d" % len(body_objs))

    wheel_objs = {}
    for name in WHEELS:
        spec = manifest["parts"]["wheel_%s" % name]
        objs = import_fbx(os.path.join(export_dir, spec["file"]))
        if len(objs) != 1:
            print("[verify] !! %s のインポート結果が1つでない (%d)" % (name, len(objs)))
        wheel = objs[0]
        # **取り付け位置は manifest の値をそのまま使う。**
        # ここで座標を作り直すと、UE 側と食い違っても気づけない。
        wheel.location = Vector(spec["attach_m"])
        wheel_objs[name] = wheel
        print("[verify] %s -> %s" % (name, ",".join("%+.4f" % v for v in spec["attach_m"])))

    # (1) 静止状態
    render_views(out_dir, "assembled")

    # (2) 車輪を回し、前輪を操舵した状態
    #
    # **これが原点の検査。** 原点が車軸から外れていれば、回した瞬間に
    # タイヤがホイールアーチから飛び出す。
    spin = math.radians(35.0)     # 転がり方向（Y 左まわり）
    steer = math.radians(22.0)    # 操舵（Z まわり）
    for name, wheel in wheel_objs.items():
        wheel.rotation_mode = "XYZ"
        if name.startswith("F"):
            wheel.rotation_euler = (0.0, spin, steer)
        else:
            wheel.rotation_euler = (0.0, spin, 0.0)
    render_views(out_dir, "spun_steered")

    # (3) 車輪を外したボディだけ。**穴が開いていないかを見る。**
    for wheel in wheel_objs.values():
        bpy.data.objects.remove(wheel, do_unlink=True)
    render_views(out_dir, "body_only")

    return 0


if __name__ == "__main__":
    sys.exit(main())
