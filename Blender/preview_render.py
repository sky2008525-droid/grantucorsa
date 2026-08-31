"""GLB を4方向からレンダリングして目視確認用の画像を出す（Blender headless）.

    blender --background --python Blender/preview_render.py -- <in.glb> <out_dir>

**なぜレンダリングするか**

ノード名が意味を持たないモデルなので、「どちらが前か」「内装はあるか」
「最も高い部品は何か」を座標だけから断定できない。**推測して進めると、
前後逆に取り付けた車が出来上がる。** 一度描いて見れば確実に分かる。

Workbench エンジンを使う。ライティング設定が要らず、形の確認には十分で速い。
"""

from __future__ import annotations

import os
import sys

import bpy
from mathutils import Vector


def clear_scene():
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete(use_global=False)


def scene_bounds():
    lo = Vector((1e18, 1e18, 1e18))
    hi = Vector((-1e18, -1e18, -1e18))
    for obj in bpy.context.scene.objects:
        if obj.type != "MESH":
            continue
        for corner in obj.bound_box:
            p = obj.matrix_world @ Vector(corner)
            for a in range(3):
                lo[a] = min(lo[a], p[a])
                hi[a] = max(hi[a], p[a])
    return lo, hi


def setup_camera(centre, span, direction, up_axis):
    """指定方向からの正射投影カメラを作る。**遠近を付けない。**

    遠近が付くと寸法の見た目が変わり、比率の確認に使えなくなる。

    up_axis は `to_track_quat` の第2引数と同じ文字列（"Z" / "Y" 等）。
    ここで首をかしげると、前後の判定を誤る。
    """
    distance = max(span) * 3.0

    cam_data = bpy.data.cameras.new("PreviewCam")
    cam_data.type = "ORTHO"
    cam_data.ortho_scale = max(span) * 1.15
    # **クリップ範囲をモデルの寸法に合わせる。** 既定は 0.1〜100 で、
    # このモデルは 487 単位あるため、既定のままだと全部が遠方クリップに
    # 落ちて真っ黒な画像になる（実際に一度そうなった）。
    cam_data.clip_start = distance * 0.01
    cam_data.clip_end = distance * 3.0

    cam = bpy.data.objects.new("PreviewCam", cam_data)
    bpy.context.scene.collection.objects.link(cam)
    cam.location = centre + direction.normalized() * distance

    # カメラのローカル -Z を被写体へ向ける
    forward = -direction.normalized()
    cam.rotation_euler = forward.to_track_quat("-Z", up_axis).to_euler()

    bpy.context.scene.camera = cam
    return cam


def render_to(path):
    scene = bpy.context.scene
    scene.render.engine = "BLENDER_WORKBENCH"
    # **正方形で撮る。** 16:9 だと ortho_scale が横基準になり、
    # 全長 487 単位のモデルが縦方向でクロップされる（実際に切れた）。
    scene.render.resolution_x = 1024
    scene.render.resolution_y = 1024
    scene.render.film_transparent = False
    scene.render.filepath = path
    bpy.ops.render.render(write_still=True)


def main():
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    if len(argv) < 2:
        print("usage: blender --background --python preview_render.py -- <in.glb> <out_dir>")
        return 1
    glb_path, out_dir = argv[0], argv[1]
    os.makedirs(out_dir, exist_ok=True)

    clear_scene()
    bpy.ops.import_scene.gltf(filepath=glb_path)

    lo, hi = scene_bounds()
    centre = (lo + hi) / 2.0
    span = Vector((hi[a] - lo[a] for a in range(3)))
    print("[preview] bounds lo=%s hi=%s" % (list(lo), list(hi)))

    # モデルは Z-up・Y が進行方向（inspect_glb.py の計測）。
    # Y の +/- どちらが前かは**これから目で決める**ので、両側から撮る。
    views = {
        "side_Yplus":    (Vector((0.0, 1.0, 0.0)),  "Z"),
        "side_Yminus":   (Vector((0.0, -1.0, 0.0)), "Z"),
        "end_Xplus":     (Vector((1.0, 0.0, 0.0)),  "Z"),
        "top":           (Vector((0.0, 0.0, 1.0)),  "Y"),
        "three_quarter": (Vector((1.0, 1.0, 0.6)),  "Z"),
    }

    for name, (direction, up_axis) in views.items():
        for cam in [o for o in bpy.context.scene.objects if o.type == "CAMERA"]:
            bpy.data.objects.remove(cam, do_unlink=True)
        setup_camera(centre, span, direction, up_axis)
        path = os.path.join(out_dir, "%s.png" % name)
        render_to(path)
        print("[preview] wrote %s" % path)

    return 0


if __name__ == "__main__":
    sys.exit(main())
