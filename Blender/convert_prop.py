"""外部から取得したモデル（OBJ / glTF）を UE へ入れられる GLB へ変換する（Blender headless）.

    blender --background --python Blender/convert_prop.py -- \
        <in.obj|in.gltf> <out.glb> <幅 [m]> <高さ [m]> \
        [--keep 名前,名前] [--uniform 倍率]

`幅` `高さ` に `-` を書くと**尺度を変えない**（元が実寸のとき）。
`--uniform` はキット全体に同じ倍率を掛ける（`-` `-` と併用する）。

## なぜ変換するか

`import_assets.py` は PolyHaven の glTF を Interchange で取り込んでいる。
**同じ経路に乗せる**ほうが、形式ごとの取り込み設定を増やすより安全
（FBX でテクスチャが静かに欠けた前例がある / `import_assets.py` の注記）。

## なぜ寸法を焼き込むか

配布されているモデルの寸法は作者の都合で、実寸ではない。
**尺度を配置側（`PROP_PLAN`）に持たせると、同じ数字の意味がアセットごとに
変わる。** 取り込む時点で実寸にしておけば、配置側は倍率 1.0 で済む。

**縦横を別々に合わせることがある。** 元モデルの縦横比が実物と違うとき、
等倍で拡げると片方が必ず外れる。円錐は回転体なので、縦に伸ばしても
形は破綻しない。**やった場合は `Docs/PHASE15_DATA_LICENCE.md` に書くこと。**

## `--keep`（変種の選別）と `-`（実寸のまま）を足した理由

PolyHaven のフォトグラメトリの樹木は**すでに実寸**で、拡大縮小しては
いけない（若木を引き伸ばして大木のふりをするのが元の問題だった）。
一方でファイルが重い。`fir_tree_01` は 1 ファイルに木が 3 本
（18.93 m / 14.06 m / 14.52 m）入っていて、glTF の .bin が **478 MB** ある。

`--keep fir_tree_01_c_LOD0` のように**残す木を名前で選ぶ**と、頂点を
1 つも書き換えずに 36 MB まで落ちる。**間引き（Decimate）はしていない。**
落とすのは「どの木を入れるか」だけで、残した木の形は配布物のままである。
"""

from __future__ import annotations

import os
import sys

import bpy


def log(fmt, *args):
    print(("[convert] " + fmt) % args)


def clear_scene():
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete(use_global=False)
    for block in (bpy.data.meshes, bpy.data.objects, bpy.data.materials):
        for item in list(block):
            try:
                block.remove(item)
            except (RuntimeError, ReferenceError):
                pass


def bounds(objects):
    """オブジェクト全体のワールド座標のバウンディングボックス。

    **`bound_box` を使わない。** あれはローカル座標で、尺度を掛けた後の
    値が入っているとは限らない。頂点から直接測る。
    """
    lows = [1e30, 1e30, 1e30]
    highs = [-1e30, -1e30, -1e30]
    for obj in objects:
        if obj.type != "MESH":
            continue
        for vertex in obj.data.vertices:
            world = obj.matrix_world @ vertex.co
            for axis in range(3):
                lows[axis] = min(lows[axis], world[axis])
                highs[axis] = max(highs[axis], world[axis])
    return lows, highs


def apply_transforms(objects):
    """回転と尺度を頂点へ焼き込む。以後ローカル座標＝ワールド座標。"""
    bpy.ops.object.select_all(action="DESELECT")
    for obj in objects:
        obj.select_set(True)
    bpy.context.view_layer.objects.active = objects[0]
    bpy.ops.object.transform_apply(location=False, rotation=True, scale=True)
    bpy.context.view_layer.update()


def keep_only(keep):
    """`keep` に載っている名前（前方一致）以外のオブジェクトを消す。

    **前方一致にする理由。** glTF を読むと Blender 側で `.001` が付く
    ことがあり、完全一致だと静かに 0 個になる（＝全部消える）。
    残った数を呼び出し側で必ず確認すること。
    """
    survivors = []
    for obj in list(bpy.context.scene.objects):
        if obj.type != "MESH":
            continue
        if any(obj.name == name or obj.name.startswith(name) for name in keep):
            survivors.append(obj)
        else:
            bpy.data.objects.remove(obj, do_unlink=True)
    return survivors


def main():
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    if len(argv) < 4:
        print("usage: ... -- <in.obj|in.gltf> <out.glb> <幅 [m]|-> <高さ [m]|-> "
              "[--keep 名前,名前]")
        return 1
    source, destination = argv[0], argv[1]
    # `-` は「尺度を変えない」。PolyHaven のフォトグラメトリは実寸なので、
    # **拡大縮小してはいけない**（それが元の問題だった）。
    native_scale = argv[2] == "-" or argv[3] == "-"
    target_width_m = None if native_scale else float(argv[2])
    target_height_m = None if native_scale else float(argv[3])

    keep = []
    if "--keep" in argv:
        keep = [name for name in argv[argv.index("--keep") + 1].split(",") if name]

    # **等倍の一括スケール。** キット全体で 1 つの倍率を掛けたいときに使う
    # （縦横別々に合わせると、キットの中で寸法の関係が壊れる）。
    uniform = float(argv[argv.index("--uniform") + 1]) if "--uniform" in argv else None
    if uniform is not None and not native_scale:
        print("--uniform と 幅/高さ 指定は同時に使えない")
        return 1

    clear_scene()

    if source.lower().endswith((".gltf", ".glb")):
        # glTF は Y 上 → Z 上 を読み込み側が処理する。
        bpy.ops.import_scene.gltf(filepath=source)
    else:
        # **OBJ は Y 上のことが多い。** Blender の既定（forward=-Z / up=Y）で
        # 読むと Z 上に直る。ここを変えると寝たモデルが出来る。
        bpy.ops.wm.obj_import(filepath=source)

    objects = [obj for obj in bpy.context.scene.objects if obj.type == "MESH"]
    if not objects:
        log("!! メッシュが 1 つも読めなかった: %s", source)
        return 1
    log("読み込んだメッシュ: %s", ", ".join(sorted(o.name for o in objects))[:400])

    if keep:
        objects = keep_only(keep)
        if not objects:
            # **黙って空の glb を書かない**（憲法ルール6）。
            log("!! --keep %s に一致するメッシュが無かった", ",".join(keep))
            return 1
        log("残した %d メッシュ: %s", len(objects),
            ", ".join(sorted(o.name for o in objects)))

    # **軸変換を頂点に焼き込む。**
    #
    # OBJ の Y 上 → Z 上 の変換は、**メッシュではなくオブジェクトの回転**
    # として入る。焼き込まずに `obj.scale` をいじると、それはローカル軸
    # （＝元の Y 上）への尺度になり、**ワールドでは別の軸が伸びる。**
    # 実際に高さを 0.70 m にしたつもりで幅が 0.74 m になった。
    apply_transforms(objects)

    # **尺度と位置は親を持たないオブジェクトにだけ掛ける。**
    #
    # 子は親の変換を継承する。全部に掛けると**子だけ二重に掛かる。**
    # 実際に Kenney の dumpster（蓋が本体の子になっている）で起きた:
    # 7.36 倍のつもりが 12.57 x 11.56 x 4.03 m になった（正しくは
    # 2.02 x 2.72 x 1.55 m）。
    roots = [obj for obj in objects if obj.parent not in objects]

    lows, highs = bounds(objects)
    size = [highs[axis] - lows[axis] for axis in range(3)]
    log("読み込み: %d メッシュ（うち親なし %d）/ 元の寸法 %.3f x %.3f x %.3f m",
        len(objects), len(roots), size[0], size[1], size[2])
    if min(size) <= 0.0:
        log("!! 寸法が 0 の軸がある")
        return 1

    if uniform is not None:
        # **等倍。** キットの中の寸法の関係を壊さない。
        log("尺度: 等倍 %.4f 倍", uniform)
        for obj in roots:
            obj.scale = tuple(value * uniform for value in obj.scale)
        bpy.context.view_layer.update()
    elif native_scale:
        # **触らない。** 元が実寸（フォトグラメトリ）のときはここが正解で、
        # 「見栄えのために少し大きく」は憲法ルール1 に触れる。
        log("尺度: 変更しない（元の実寸をそのまま使う）")
    else:
        # 横は大きいほうの軸に合わせる（底面は正方形に近い想定）。
        scale_xy = target_width_m / max(size[0], size[1])
        scale_z = target_height_m / size[2]
        log("尺度: 横 %.4f / 縦 %.4f", scale_xy, scale_z)

        for obj in roots:
            obj.scale = (obj.scale[0] * scale_xy, obj.scale[1] * scale_xy,
                         obj.scale[2] * scale_z)
        bpy.context.view_layer.update()

    lows, highs = bounds(objects)

    # **原点を底面の中心へ。** 配置側は「地面の点」に置くので、
    # 原点が中心にあると半分埋まる。
    centre_x = (lows[0] + highs[0]) / 2.0
    centre_y = (lows[1] + highs[1]) / 2.0
    for obj in roots:
        obj.location = (obj.location[0] - centre_x,
                        obj.location[1] - centre_y,
                        obj.location[2] - lows[2])

    bpy.context.view_layer.update()
    lows, highs = bounds(objects)
    log("変換後: %.3f x %.3f x %.3f m（底面 z = %.4f）",
        highs[0] - lows[0], highs[1] - lows[1], highs[2] - lows[2], lows[2])

    os.makedirs(os.path.dirname(destination), exist_ok=True)
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.export_scene.gltf(
        filepath=destination,
        export_format="GLB",          # テクスチャを 1 ファイルに埋め込む
        use_selection=True,
        export_apply=True,
        export_yup=True,
    )
    log("書き出した: %s (%d bytes)", destination, os.path.getsize(destination))
    return 0


if __name__ == "__main__":
    sys.exit(main())
