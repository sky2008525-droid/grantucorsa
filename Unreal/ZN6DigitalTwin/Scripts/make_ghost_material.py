# -*- coding: utf-8 -*-
"""ゴースト（自己ベストの再生）用の半透明マテリアルを作る。
#
実行:

    UnrealEditor-Cmd.exe <uproject> \
        -ExecutePythonScript="<repo>/Unreal/ZN6DigitalTwin/Scripts/make_ghost_material.py" \
        -unattended -nosplash

**`build_level.py` に混ぜていない。** ゴーストはレベルの中身ではなく
車の見た目の一部で、コースを作り直すたびに作り直す必要が無い。
別々にしておけば、コース側を触っている作業とぶつからない。

作るもの: `/Game/ZN6/Materials/M_ZN6_Ghost`

**アンリット（陰影なし）にする。** 陰影を付けると、ゴーストが
路面の影に入ったとき見失う。ゴーストは「そこに車がある」ことを
示す印であって、光を受ける物体ではない（憲法ルール18: 演出）。
"""

import unreal

PKG_MATERIAL = "/Game/ZN6/Materials"
NAME = "M_ZN6_Ghost"


def log(message):
    unreal.log("[ZN6 ghost] %s" % message)


def make_ghost_material(name=NAME):
    unreal.EditorAssetLibrary.make_directory(PKG_MATERIAL)

    asset_path = "%s/%s" % (PKG_MATERIAL, name)
    if unreal.EditorAssetLibrary.does_asset_exist(asset_path):
        unreal.EditorAssetLibrary.delete_asset(asset_path)

    tools = unreal.AssetToolsHelpers.get_asset_tools()
    material = tools.create_asset(name, PKG_MATERIAL, unreal.Material,
                                  unreal.MaterialFactoryNew())
    if material is None:
        unreal.log_error("[ZN6 ghost] マテリアルを作れない: %s" % asset_path)
        return None

    material.set_editor_property("blend_mode", unreal.BlendMode.BLEND_TRANSLUCENT)
    material.set_editor_property("shading_model",
                                 unreal.MaterialShadingModel.MSM_UNLIT)
    # **両面にする。** 半透明で片面だと、内側から見たときに車体が
    # 消えて「屋根だけが浮いている」ように見える。
    material.set_editor_property("two_sided", True)

    lib = unreal.MaterialEditingLibrary

    # 色。**自車と紛れない色にする。** 同じオレンジだと、追い抜く瞬間に
    # どちらが自分か分からなくなる。
    colour = lib.create_material_expression(
        material, unreal.MaterialExpressionVectorParameter, -600, -200)
    colour.set_editor_property("parameter_name", "GhostColour")
    colour.set_editor_property("default_value",
                               unreal.LinearColor(0.10, 0.62, 0.72, 1.0))
    lib.connect_material_property(colour, "", unreal.MaterialProperty.MP_EMISSIVE_COLOR)

    # 濃さ。**薄くしすぎない。** 0.2 だと明るい路面の上で消える。
    opacity = lib.create_material_expression(
        material, unreal.MaterialExpressionScalarParameter, -600, 100)
    opacity.set_editor_property("parameter_name", "Opacity")
    opacity.set_editor_property("default_value", 0.38)
    lib.connect_material_property(opacity, "", unreal.MaterialProperty.MP_OPACITY)

    lib.recompile_material(material)
    unreal.EditorAssetLibrary.save_asset(material.get_path_name(),
                                         only_if_is_dirty=False)
    log("作成: %s" % asset_path)
    return material


if __name__ == "__main__":
    make_ghost_material()
