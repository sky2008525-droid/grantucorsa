"""GLB を部位ごとに分解し、実寸・物理座標系で書き出す（Blender headless）.

    blender --background --python Blender/decompose_vehicle.py -- \
        <in.glb> <recon.json> <out_dir>

`Blender/recon_glb.py` が求めた車輪位置とスケールを入力として受け取り、
UE5 が取り込める形（実寸メートル・物理座標系・部位ごとに独立した原点）で
書き出す。

**分解が必要な理由**

物理モデル（`Physics/ZN6Vehicle`）は車輪ごとの回転角と操舵角を出力する。
車輪が車体と一体のメッシュだと、これを描画に反映できない。

**原点の置き方**

  ボディ : 重心の真下・接地面（`inertia.cg_longitudinal_from_front_axle`）
           AZN6VehicleActor が Actor 位置に物理の (x, y) をそのまま入れ、
           z=0 に置くため、**メッシュ側の原点も接地面でなければ浮く／沈む**
  車輪   : それぞれの車軸中心。**原点が軸から外れると、回した瞬間に振れる**

**座標系**

出力は物理の座標系（X 前方 / Y 左 / Z 上、右手系、単位 m）に合わせる。
Blender も右手系 Z-up なので、この座標をそのまま Blender 座標として置けば、
FBX 経由で UE5（X 前方 / Y 右 / Z 上、左手系）へ渡るときに Y が反転し、
結果として正しい向きになる。**ここで UE の都合を先取りして反転しないこと。**

モデル座標は X = 車幅（右が正）、Y = 進行方向（前が正）、Z = 上。
glTF は右手系であり、Y=前 / Z=上 と確定した時点で X = Y×Z = 右 が決まる。
したがって変換は

    X_out = (Y_model - Y_cg) * scale        前方
    Y_out = -(X_model - X_centre) * scale   左
    Z_out = (Z_model - Z_ground) * scale    上

行列式は +1（鏡像にならない）。
"""

from __future__ import annotations

import json
import math
import os
import sys
import time

import bpy
import bmesh
from mathutils import Matrix, Vector

AXIS_WIDTH, AXIS_LENGTH, AXIS_HEIGHT = 0, 1, 2

# --- 実車の値（Vehicles/ZN6/vehicle.json）--------------------------------------
#
# **正本は vehicle.json。** ここに写しているのは原点を置くために必要な最小限。
CG_FROM_FRONT_AXLE_M = 1.208   # inertia.cg_longitudinal_from_front_axle (estimated)

# 車輪シリンダの余裕。**タイヤ径ちょうどで切ると、タイヤ外周の頂点を取り
# こぼしてリムだけが回る。** 少し大きめに取り、はみ出した車体側の部品は
# 後段の半径判定で戻す。
WHEEL_RADIUS_MARGIN = 1.06
WHEEL_HALF_WIDTH_MARGIN = 1.35

# 回転部品と非回転部品の切り分け。
#
# タイヤ・リム・ブレーキディスクは車軸と同心なので、部品の AABB 中心は
# 軸のごく近くに来る。ブレーキキャリパやサスペンションアームは軸から
# 外れた位置にあり、中心が半径方向へずれる。
#
# **「車輪の内側にある」だけで回してはいけない。** キャリパは回らない。
ROTATING_RADIAL_FRACTION = 0.45


def log(fmt, *args):
    print(("[decompose] " + fmt) % args)


def clear_scene():
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete(use_global=False)
    for block in (bpy.data.meshes, bpy.data.materials, bpy.data.images):
        for item in list(block):
            block.remove(item)


def world_bbox(obj):
    """頂点から直接ワールド AABB を求める。

    **`obj.bound_box` を使わないこと。** あれは評価済みデータのキャッシュで、
    `bmesh` で頂点を書き換えた直後は更新されていない。実際、これが原因で
    変換後も変換前の座標が返り、「半径 35.93 m の車輪」という結果が出た。
    """
    matrix = obj.matrix_world
    verts = obj.data.vertices
    if not verts:
        return Vector((0.0, 0.0, 0.0)), Vector((0.0, 0.0, 0.0))
    lo = Vector((1e30, 1e30, 1e30))
    hi = Vector((-1e30, -1e30, -1e30))
    for vert in verts:
        p = matrix @ vert.co
        for a in range(3):
            if p[a] < lo[a]:
                lo[a] = p[a]
            if p[a] > hi[a]:
                hi[a] = p[a]
    return lo, hi


def join_all(objects, name):
    """複数オブジェクトを1つに結合する。空なら None。"""
    objects = [o for o in objects if o and o.name in bpy.data.objects]
    if not objects:
        return None
    bpy.ops.object.select_all(action="DESELECT")
    for obj in objects:
        obj.select_set(True)
    bpy.context.view_layer.objects.active = objects[0]
    if len(objects) > 1:
        bpy.ops.object.join()
    result = bpy.context.view_layer.objects.active
    result.name = name
    return result


def select_cylinder_verts(obj, centre, radius, half_width):
    """車軸方向のシリンダに入る頂点を選択する。

    シリンダの軸は車幅方向（AXIS_WIDTH）。
    **AABB ではなくシリンダで切る。** AABB だとホイールアーチ上部の
    車体パネルまで巻き込む。
    """
    bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.mesh.select_all(action="DESELECT")
    bpy.ops.object.mode_set(mode="OBJECT")

    count = 0
    matrix = obj.matrix_world
    for vert in obj.data.vertices:
        p = matrix @ vert.co
        if abs(p[AXIS_WIDTH] - centre[AXIS_WIDTH]) > half_width:
            continue
        dy = p[AXIS_LENGTH] - centre[AXIS_LENGTH]
        dz = p[AXIS_HEIGHT] - centre[AXIS_HEIGHT]
        if dy * dy + dz * dz > radius * radius:
            continue
        vert.select = True
        count += 1
    return count


def separate_selected(obj):
    """選択頂点を別オブジェクトへ分離し、新しくできたものを返す。"""
    existing = set(bpy.data.objects.keys())
    bpy.ops.object.mode_set(mode="EDIT")
    try:
        bpy.ops.mesh.separate(type="SELECTED")
    except RuntimeError:
        bpy.ops.object.mode_set(mode="OBJECT")
        return []
    bpy.ops.object.mode_set(mode="OBJECT")
    return [bpy.data.objects[n] for n in bpy.data.objects.keys() if n not in existing]


def split_rotating(wheel_obj, centre, radius):
    """車輪の塊を「回るもの」と「回らないもの」に分ける。

    戻り値: (回転部品のオブジェクト列, 非回転部品のオブジェクト列)
    """
    bpy.ops.object.select_all(action="DESELECT")
    wheel_obj.select_set(True)
    bpy.context.view_layer.objects.active = wheel_obj

    bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.mesh.select_all(action="SELECT")
    bpy.ops.mesh.separate(type="LOOSE")
    bpy.ops.object.mode_set(mode="OBJECT")

    parts = [o for o in bpy.context.selected_objects if o.type == "MESH"]

    rotating, static = [], []
    for part in parts:
        lo, hi = world_bbox(part)
        cy = (lo[AXIS_LENGTH] + hi[AXIS_LENGTH]) / 2.0
        cz = (lo[AXIS_HEIGHT] + hi[AXIS_HEIGHT]) / 2.0
        offset = math.hypot(cy - centre[AXIS_LENGTH], cz - centre[AXIS_HEIGHT])
        (rotating if offset <= radius * ROTATING_RADIAL_FRACTION else static).append(part)
    return rotating, static


def to_output_frame(obj, origin_model, scale):
    """モデル座標のオブジェクトを、物理座標系・実寸へ移す。

    origin_model はこのオブジェクトの原点にしたいモデル座標。
    """
    mesh = obj.data
    bm = bmesh.new()
    bm.from_mesh(mesh)

    matrix = obj.matrix_world
    for vert in bm.verts:
        p = matrix @ vert.co
        x_fwd = (p[AXIS_LENGTH] - origin_model[AXIS_LENGTH]) * scale
        y_left = -(p[AXIS_WIDTH] - origin_model[AXIS_WIDTH]) * scale
        z_up = (p[AXIS_HEIGHT] - origin_model[AXIS_HEIGHT]) * scale
        vert.co = Vector((x_fwd, y_left, z_up))

    bm.to_mesh(mesh)
    bm.free()
    mesh.update()

    # 頂点を直接ワールド座標で書き換えたので、オブジェクト側の変換は
    # 単位行列に戻す。**`matrix_world.identity()` ではダメ。**
    # あれは返ってきた行列のコピーを書き換えるだけで、オブジェクトには
    # 反映されない（変換が二重に掛かったままになる）。代入すること。
    obj.matrix_world = Matrix.Identity(4)


def export_glb(objects, path):
    """glTF(.glb) で書き出す。**FBX ではない。**

    FBX に `embed_textures=True` で埋め込んだテクスチャは、UE5 の
    Interchange が取り出せなかった（「無効なトランスレータでペイロードを
    取得できませんでした」が5枚とも出る）。メッシュとマテリアルは入るが、
    **テクスチャだけ静かに欠ける。**

    同じ経路で PolyHaven の樹木（glTF）はテクスチャまで完全に入っている
    ので、車体も glTF に揃える。

    座標系は Blender のものをそのまま出す（既に物理座標系へ変換済み）。
    glTF は Y-up が規約なので `+Y Up` 変換が入り、UE 側の取り込みで
    元に戻る。**ここで UE の都合を先取りして回さないこと。**
    """
    bpy.ops.object.select_all(action="DESELECT")
    for obj in objects:
        obj.select_set(True)
    bpy.context.view_layer.objects.active = objects[0]
    bpy.ops.export_scene.gltf(
        filepath=path,
        export_format="GLB",
        use_selection=True,
        export_yup=True,
        export_apply=True,
        export_texture_dir="",
        export_materials="EXPORT",
    )


def main():
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    if len(argv) < 3:
        print("usage: ... -- <in.glb> <recon.json> <out_dir>")
        return 1
    glb_path, recon_path, out_dir = argv[0], argv[1], argv[2]
    os.makedirs(out_dir, exist_ok=True)

    with open(recon_path, encoding="utf-8") as handle:
        recon = json.load(handle)
    axes = recon["axes"]
    if "error" in axes:
        log("recon に問題がある: %s", axes["error"])
        return 1

    scale = axes["scale_m_per_unit"]
    radius_u = axes["wheel_radius_units"]
    ground_z = axes["ground_z"]
    front_y = axes["front_axle_y"]
    clusters = recon["wheel_clusters"]

    started = time.time()
    clear_scene()
    bpy.ops.import_scene.gltf(filepath=glb_path)
    imported = [o for o in bpy.context.scene.objects if o.type == "MESH"]
    log("imported %d meshes (%.1fs)", len(imported), time.time() - started)

    # 全体を1つにまとめてから切り出す。**先に loose 分解しない。**
    # 17,753 個に割ってから結合し直すと極端に遅い（実測 45s + 結合分）。
    whole = join_all(imported, "ZN6_whole")

    lo, hi = world_bbox(whole)
    centre_x = (lo[AXIS_WIDTH] + hi[AXIS_WIDTH]) / 2.0

    # **軸の取り違えを検出する。**
    #
    # このスクリプトは AXIS_WIDTH/LENGTH/HEIGHT = 0/1/2 が Blender の
    # ワールド座標でも成り立つことを前提にしている。glTF は Y-up、Blender は
    # Z-up なので、インポータが変換を掛けると軸が入れ替わる（このファイルは
    # SketchUp 由来で中身が既に Z-up のため、現状は変換されずに入る）。
    #
    # **Blender 側の挙動が変わったときに、黙って90度回った車が出力される
    # のが最悪。** recon が測った span と突き合わせて、違ったら止める。
    span = [hi[a] - lo[a] for a in range(3)]
    expected = recon["world_span"]
    for a in range(3):
        if abs(span[a] - expected[a]) > max(1e-3, expected[a] * 1e-4):
            log("!! 軸が recon と一致しない。span=%s expected=%s",
                ["%.3f" % v for v in span], ["%.3f" % v for v in expected])
            log("   glTF インポータの座標変換が変わった可能性がある。")
            log("   AXIS_WIDTH/LENGTH/HEIGHT の定義を見直すこと。")
            return 1

    # --- 車輪に名前を付ける -----------------------------------------------
    #
    # 前後は recon が求めた車軸位置で、左右は車幅方向の符号で決める。
    # **物理側の左右（Y が左が正）に合わせること。**
    named = {}
    for cl in clusters:
        cx, cy = cl["centre"][AXIS_WIDTH], cl["centre"][AXIS_LENGTH]
        front = abs(cy - front_y) < abs(cy - axes["rear_axle_y"])
        left = -(cx - centre_x) > 0.0        # 出力座標系の Y が正なら左
        name = ("F" if front else "R") + ("L" if left else "R")
        named[name] = cl

    if len(named) != 4:
        log("!! 車輪の名前付けが4つにならない: %s", sorted(named))
        return 1
    log("wheels: %s", ", ".join(sorted(named)))

    # --- 車輪をシリンダで切り出す -----------------------------------------
    wheel_objects = {}
    static_leftovers = []
    for name in ("FL", "FR", "RL", "RR"):
        cl = named[name]
        centre = cl["centre"]
        bpy.ops.object.select_all(action="DESELECT")
        whole.select_set(True)
        bpy.context.view_layer.objects.active = whole

        picked = select_cylinder_verts(
            whole, centre,
            radius_u * WHEEL_RADIUS_MARGIN,
            cl["width"] / 2.0 * WHEEL_HALF_WIDTH_MARGIN,
        )
        new_objects = separate_selected(whole)
        if not new_objects:
            log("!! %s を切り出せない（選択頂点 %d）", name, picked)
            return 1

        chunk = join_all(new_objects, "chunk_%s" % name)
        rotating, static = split_rotating(chunk, centre, radius_u)
        log("%s: verts=%d  回転 %d 部品 / 非回転 %d 部品",
            name, picked, len(rotating), len(static))

        wheel = join_all(rotating, "ZN6_wheel_%s" % name)
        if wheel is None:
            log("!! %s に回転部品が無い", name)
            return 1
        wheel_objects[name] = wheel
        static_leftovers.extend(static)

    # 非回転部品（キャリパ等）は車体側へ戻す
    body = join_all([whole] + static_leftovers, "ZN6_body")
    log("body parts merged: 1 + %d", len(static_leftovers))

    # --- 原点を決めて出力座標系へ ----------------------------------------
    #
    # ボディ: 重心の真下・接地面。AZN6VehicleActor が z=0 に置くため。
    cg_y_model = front_y - CG_FROM_FRONT_AXLE_M / scale
    body_origin = [centre_x, cg_y_model, ground_z]
    to_output_frame(body, body_origin, scale)

    # 車輪: それぞれの車軸中心
    for name, wheel in wheel_objects.items():
        to_output_frame(wheel, named[name]["centre"], scale)

    # --- 検算 -------------------------------------------------------------
    manifest = {
        "source_glb": glb_path,
        "scale_m_per_unit": scale,
        "frame": "physics (X forward, Y left, Z up, metres)",
        "body_origin": "重心の真下・接地面 (cg_longitudinal_from_front_axle=%.3f m)"
                       % CG_FROM_FRONT_AXLE_M,
        "parts": {},
    }

    lo_b, hi_b = world_bbox(body)
    manifest["parts"]["body"] = {
        "file": "ZN6_body.glb",
        "bbox_lo_m": list(lo_b), "bbox_hi_m": list(hi_b),
        "tris": sum(len(p.vertices) - 2 for p in body.data.polygons),
    }
    log("body  bbox %s .. %s m",
        ",".join("%.3f" % v for v in lo_b), ",".join("%.3f" % v for v in hi_b))

    for name in ("FL", "FR", "RL", "RR"):
        wheel = wheel_objects[name]
        lo_w, hi_w = world_bbox(wheel)
        # 車輪の原点は軸中心。**AABB の中心が原点付近に無ければ、回すと振れる。**
        offset = max(abs(lo_w[a] + hi_w[a]) / 2.0 for a in range(3))

        # **取り付け位置を書き出す。** 車輪メッシュの原点は自身の軸中心に
        # あるので、UE 側はボディ原点からのこのオフセットに置けばよい。
        #
        # ここは**モデル自身の車輪位置**であって、物理の車輪位置
        # （vehicle.json の wheelbase / track）ではない。憲法ルール4により
        # 両者は一致しなくてよい。描画側の車輪は自身のホイールアーチに
        # 収まっている必要があり、それを決めるのはモデルの方。
        cl = named[name]
        attach = [
            (cl["centre"][AXIS_LENGTH] - cg_y_model) * scale,
            -(cl["centre"][AXIS_WIDTH] - centre_x) * scale,
            (cl["centre"][AXIS_HEIGHT] - ground_z) * scale,
        ]

        manifest["parts"]["wheel_%s" % name] = {
            "file": "ZN6_wheel_%s.glb" % name,
            "attach_m": attach,
            "attach_note": "ボディ原点からの位置 [m]（X 前方 / Y 左 / Z 上）",
            "bbox_lo_m": list(lo_w), "bbox_hi_m": list(hi_w),
            "radius_m": max(hi_w[0] - lo_w[0], hi_w[2] - lo_w[2]) / 2.0,
            "width_m": hi_w[1] - lo_w[1],
            "origin_offset_m": offset,
            "tris": sum(len(p.vertices) - 2 for p in wheel.data.polygons),
        }
        log("wheel %s  半径 %.4f m  幅 %.4f m  原点ずれ %.5f m  取付 %s",
            name,
            max(hi_w[0] - lo_w[0], hi_w[2] - lo_w[2]) / 2.0,
            hi_w[1] - lo_w[1], offset,
            ",".join("%+.4f" % v for v in attach))

    # --- 書き出し ---------------------------------------------------------
    export_glb([body], os.path.join(out_dir, "ZN6_body.glb"))
    for name, wheel in wheel_objects.items():
        export_glb([wheel], os.path.join(out_dir, "ZN6_wheel_%s.glb" % name))

    with open(os.path.join(out_dir, "manifest.json"), "w", encoding="utf-8") as handle:
        json.dump(manifest, handle, ensure_ascii=False, indent=2)

    log("done (%.1fs) -> %s", time.time() - started, out_dir)
    return 0


if __name__ == "__main__":
    sys.exit(main())
