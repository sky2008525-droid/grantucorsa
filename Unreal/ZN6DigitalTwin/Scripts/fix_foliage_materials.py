# -*- coding: utf-8 -*-
"""樹木の葉を**自前のマスクマテリアル**で描き直す。

    UnrealEditor-Cmd.exe <uproject> \
        -ExecutePythonScript=".../fix_foliage_materials.py" -unattended -nosplash

## なぜ要るのか

**峠の木が幹だけの棒に見えていた。** 画面を撮って初めて分かった。

調べた順序と分かったこと:

1. glTF の葉のマテリアルは `alphaMode = BLEND`。取り込むと親が
   `/InterchangeAssets/gltf/MaterialInstances/MI_Default_Blend_DS`
   （半透明・両面）になる
2. 葉のベースカラーだけが PNG で**アルファを持っている**
   （幹・枝は JPEG。アルファが要るのは葉だけなので筋が通っている）
3. メッシュには葉の面がちゃんとある（`fir_tree_01_twig` だけで
   **446,074 三角形**。木の面の 88%）
4. それでも描かれない。ブレンドモードを Masked に上書きしても変わらない

つまり**面もテクスチャもあるのに、不透明度が伝わっていない。**
Interchange の既定マテリアルの中でどう繋がっているかは、こちらからは
触れない（親も MaterialInstance で、ブレンドモードすら読めない）。

**依存をやめる。** 葉に必要なのは

  - ベースカラーの RGB を色に
  - ベースカラーの **A を不透明マスクに**
  - Masked・両面

の3つだけなので、その形のマテリアルを自分で作って差し替える。
これは「見た目を作り込む」のではなく、**取り込みの穴を塞ぐ**作業である。
"""

import unreal

PKG_FOLIAGE = "/Game/ZN6/Foliage"
PKG_MATERIAL = "/Game/ZN6/Materials/Foliage"
MASTER_NAME = "M_ZN6_Foliage"

#: 葉として扱う名前。**幹（bark / trunk）は触らない。**
FOLIAGE_WORDS = ("twig", "leaf", "leaves", "foliage", "needle", "frond",
                 "plant", "grass", "fern", "shrub", "weed", "flower",
                 "petal", "moss", "branch")

#: 幹・枝として**触らない**名前。上より優先する。
#: `dead_branches` は枝そのもの（不透明）なので、葉と一緒にしない。
TRUNK_WORDS = ("bark", "trunk", "dead_branch", "branches_dead", "wood",
               "stump", "root")

#: アルファがこれ以下の画素を捨てる。
#:
#: **0.5 では葉が痩せる。** PolyHaven の葉のアルファは縁が滑らかに
#: 落ちているので、高くすると輪郭が削れて隙間だらけになる。
OPACITY_CLIP = 0.33


def log(message):
    unreal.log("[ZN6 foliage] " + message)


def make_master():
    """葉用のマスターマテリアル。**Masked・両面・アルファは A から。**"""
    unreal.EditorAssetLibrary.make_directory(PKG_MATERIAL)
    path = "%s/%s" % (PKG_MATERIAL, MASTER_NAME)
    if unreal.EditorAssetLibrary.does_asset_exist(path):
        unreal.EditorAssetLibrary.delete_asset(path)

    tools = unreal.AssetToolsHelpers.get_asset_tools()
    material = tools.create_asset(MASTER_NAME, PKG_MATERIAL, unreal.Material,
                                  unreal.MaterialFactoryNew())
    if material is None:
        unreal.log_error("[ZN6 foliage] マスターを作れない: %s" % path)
        return None

    material.set_editor_property("blend_mode", unreal.BlendMode.BLEND_MASKED)
    # **両面。** 葉は板 1 枚で出来ているので、片面だと裏から見て消える。
    material.set_editor_property("two_sided", True)
    material.set_editor_property("opacity_mask_clip_value", OPACITY_CLIP)

    lib = unreal.MaterialEditingLibrary

    base = lib.create_material_expression(
        material, unreal.MaterialExpressionTextureSampleParameter2D, -600, -200)
    base.set_editor_property("parameter_name", "BaseColour")
    base.set_editor_property("sampler_type",
                             unreal.MaterialSamplerType.SAMPLERTYPE_COLOR)
    lib.connect_material_property(base, "RGB",
                                  unreal.MaterialProperty.MP_BASE_COLOR)
    # **ここが要点。** ベースカラーの A を不透明マスクへ。
    lib.connect_material_property(base, "A",
                                  unreal.MaterialProperty.MP_OPACITY_MASK)

    normal = lib.create_material_expression(
        material, unreal.MaterialExpressionTextureSampleParameter2D, -600, 200)
    normal.set_editor_property("parameter_name", "Normal")
    normal.set_editor_property("sampler_type",
                               unreal.MaterialSamplerType.SAMPLERTYPE_NORMAL)
    lib.connect_material_property(normal, "RGB",
                                  unreal.MaterialProperty.MP_NORMAL)

    rough = lib.create_material_expression(
        material, unreal.MaterialExpressionScalarParameter, -600, 460)
    rough.set_editor_property("parameter_name", "Roughness")
    rough.set_editor_property("default_value", 0.72)
    lib.connect_material_property(rough, "",
                                  unreal.MaterialProperty.MP_ROUGHNESS)

    lib.recompile_material(material)
    unreal.EditorAssetLibrary.save_asset(material.get_path_name(),
                                         only_if_is_dirty=False)
    log("マスターを作った: %s" % path)
    return material


def is_foliage(name):
    lowered = name.lower()
    if any(word in lowered for word in TRUNK_WORDS):
        return False
    return any(word in lowered for word in FOLIAGE_WORDS)


def textures_of(instance):
    """インスタンスが持つ BaseColor / Normal のテクスチャ。"""
    base = None
    normal = None
    for entry in instance.get_editor_property("texture_parameter_values"):
        texture = entry.get_editor_property("parameter_value")
        if texture is None:
            continue
        name = str(entry.get_editor_property("parameter_info")
                   .get_editor_property("name"))
        if "BaseColor" in name:
            base = texture
        elif "Normal" in name:
            normal = texture
    return base, normal


def make_instance(master, source, name):
    path = "%s/MI_%s" % (PKG_MATERIAL, name)
    if unreal.EditorAssetLibrary.does_asset_exist(path):
        unreal.EditorAssetLibrary.delete_asset(path)

    tools = unreal.AssetToolsHelpers.get_asset_tools()
    factory = unreal.MaterialInstanceConstantFactoryNew()
    instance = tools.create_asset("MI_%s" % name, PKG_MATERIAL,
                                  unreal.MaterialInstanceConstant, factory)
    if instance is None:
        return None
    instance.set_editor_property("parent", master)

    base, normal = textures_of(source)
    if base is None:
        # **黙って灰色の葉にしない。** テクスチャが無ければ差し替えない。
        return None
    unreal.MaterialEditingLibrary.set_material_instance_texture_parameter_value(
        instance, "BaseColour", base)
    if normal is not None:
        unreal.MaterialEditingLibrary.set_material_instance_texture_parameter_value(
            instance, "Normal", normal)

    unreal.EditorAssetLibrary.save_asset(instance.get_path_name(),
                                         only_if_is_dirty=False)
    return instance


def main():
    master = make_master()
    if master is None:
        return

    registry = unreal.AssetRegistryHelpers.get_asset_registry()
    assets = registry.get_assets_by_path(PKG_FOLIAGE, recursive=True)

    meshes = []
    replacements = {}          # 元のマテリアル名 -> 新しいインスタンス
    for data in assets:
        asset = data.get_asset()
        if isinstance(asset, unreal.StaticMesh):
            meshes.append(asset)

    made = 0
    for data in assets:
        asset = data.get_asset()
        if not isinstance(asset, unreal.MaterialInstanceConstant):
            continue
        name = asset.get_name()
        if not is_foliage(name):
            continue
        instance = make_instance(master, asset, name)
        if instance is not None:
            replacements[name] = instance
            made += 1

    log("葉のマテリアルを %d 件作った" % made)

    swapped = 0
    saved = 0
    for mesh in meshes:
        slots = mesh.static_materials
        touched = False
        for index in range(len(slots)):
            current = mesh.get_material(index)
            if current is None:
                continue
            new = replacements.get(current.get_name())
            if new is None:
                continue
            mesh.set_material(index, new)
            swapped += 1
            touched = True
        # **変えていないメッシュを保存しない。**
        #
        # 最初は全部保存していた。1 個 10 秒近く掛かるので、250 個で
        # 40 分。25 分で打ち切られて途中で止まった。
        if touched:
            unreal.EditorAssetLibrary.save_asset(mesh.get_path_name(),
                                                 only_if_is_dirty=False)
            saved += 1
    log("保存したメッシュ %d 個" % saved)

    log("メッシュの枠を %d 箇所差し替えた（メッシュ %d 個）"
        % (swapped, len(meshes)))

    if made == 0 or swapped == 0:
        # **黙って 0 件で終わらない**（憲法ルール6）。
        unreal.log_error(
            "[ZN6 foliage] 差し替えが 0 件。名前の付け方か取り込みの作りが"
            "変わっている可能性がある")


main()
