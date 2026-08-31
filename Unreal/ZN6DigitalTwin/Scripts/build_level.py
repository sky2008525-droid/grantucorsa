"""取り込んだアセットからテストコースのレベルを組む（UE Python）.

    UnrealEditor-Cmd.exe <uproject> -run=pythonscript
        -script="Unreal/ZN6DigitalTwin/Scripts/build_level.py"

`import_assets.py` の後に実行する。

**配置を手で決めない。** 樹木の位置は `Tracks/Export/placement.json`、
車輪の位置は `Vehicles/ZN6/Export/manifest.json` が持っている。
どちらも Blender が中心線と3Dモデルから生成したもので、**このスクリプトは
座標系を変換して置くだけ。**

## 単位と座標系

  Blender / 物理 : X 前方 / Y 左 / Z 上、右手系、m
  UE5            : X 前方 / Y 右 / Z 上、左手系、cm

したがって   UE = (x * 100, -y * 100, z * 100)、ヨーは符号反転。
**この変換をこのファイルの外に散らさないこと。**
"""

import json
import math
import os

import unreal

PKG_ROOT = "/Game/ZN6"
PKG_VEHICLE = PKG_ROOT + "/Vehicle"
PKG_TRACK = PKG_ROOT + "/Track"
PKG_FOLIAGE = PKG_ROOT + "/Foliage"
PKG_TEXTURE = PKG_ROOT + "/Textures"
PKG_MATERIAL = PKG_ROOT + "/Materials"
LEVEL_PATH = PKG_ROOT + "/Maps/PhysicsTestTrack"

M_TO_CM = 100.0


def log(message):
    unreal.log("[ZN6 level] " + message)


def actor_subsystem():
    """Actor を置くサブシステム。

    **`EditorLevelLibrary` を使わないこと。** UE5.8 では非推奨で、
    `spawn_actor_from_object` が黙って None を返す（例外も警告も出ない）。
    None のまま使って「'NoneType' に set_actor_label が無い」で初めて気づく。
    """
    return unreal.get_editor_subsystem(unreal.EditorActorSubsystem)


def spawn_mesh(mesh, location, rotation, label):
    actor = actor_subsystem().spawn_actor_from_object(mesh, location, rotation)
    if actor is None:
        unreal.log_error("[ZN6 level] Actor を生成できない: %s" % label)
        return None
    actor.set_actor_label(label)
    return actor


def spawn_class(actor_class, location, rotation, label):
    actor = actor_subsystem().spawn_actor_from_class(actor_class, location, rotation)
    if actor is None:
        unreal.log_error("[ZN6 level] Actor を生成できない: %s" % label)
        return None
    actor.set_actor_label(label)
    return actor


def repo_root():
    # **絶対パスにする。** project_dir() は相対で返ることがあり、
    # そのままだと実行ディレクトリ依存になる。
    return os.path.abspath(
        os.path.join(unreal.Paths.convert_relative_path_to_full(
            unreal.Paths.project_dir()), "..", ".."))


def to_ue_location(x_m, y_m, z_m):
    """物理座標 [m] を UE の位置 [cm] へ。**Y の符号を反転する。**"""
    return unreal.Vector(x_m * M_TO_CM, -y_m * M_TO_CM, z_m * M_TO_CM)


def to_ue_yaw(yaw_rad):
    """物理のヨー（左が正）を UE のヨー（右が正）へ。"""
    return unreal.Rotator(0.0, 0.0, -math.degrees(yaw_rad))


def find_asset(folder, name_contains, cls):
    """フォルダ以下から名前で1つ探す。**見つからなければ None を返して呼び側で止める。**"""
    for path in unreal.EditorAssetLibrary.list_assets(folder, recursive=True):
        asset = unreal.EditorAssetLibrary.load_asset(path)
        if isinstance(asset, cls) and name_contains in asset.get_name():
            return asset
    return None


def make_surface_material(name, diffuse, normal, rough, uv_scale):
    """テクスチャ3枚から不透明マテリアルを1つ作る。

    路面と地面用。**凝ったシェーダを書かない。** ここで見たいのは
    「コースが正しい形で存在しているか」であって質感ではない。
    """
    package = PKG_MATERIAL
    unreal.EditorAssetLibrary.make_directory(package)

    # **既にあるなら作り直さない。** create_asset は同名資産があると
    # None を返すだけで例外を出さないため、気づかずに「マテリアル無し」の
    # まま配置してしまう（実際に road=None ground=None で通過した）。
    asset_path = "%s/%s" % (package, name)
    if unreal.EditorAssetLibrary.does_asset_exist(asset_path):
        unreal.EditorAssetLibrary.delete_asset(asset_path)

    tools = unreal.AssetToolsHelpers.get_asset_tools()
    material = tools.create_asset(name, package, unreal.Material,
                                  unreal.MaterialFactoryNew())
    if material is None:
        unreal.log_error("[ZN6 level] マテリアルを作れない: %s" % asset_path)
        return None

    lib = unreal.MaterialEditingLibrary

    # UV をタイリングする。地面は 1 枚を 10 m 相当で貼っている想定
    coords = lib.create_material_expression(
        material, unreal.MaterialExpressionTextureCoordinate, -900, 0)
    coords.set_editor_property("u_tiling", uv_scale)
    coords.set_editor_property("v_tiling", uv_scale)

    def sample(texture, x, y, sampler_type):
        node = lib.create_material_expression(
            material, unreal.MaterialExpressionTextureSample, x, y)
        node.set_editor_property("texture", texture)
        node.set_editor_property("sampler_type", sampler_type)
        lib.connect_material_expressions(coords, "", node, "UVs")
        return node

    if diffuse is not None:
        node = sample(diffuse, -600, -200, unreal.MaterialSamplerType.SAMPLERTYPE_COLOR)
        lib.connect_material_property(node, "", unreal.MaterialProperty.MP_BASE_COLOR)
    if normal is not None:
        node = sample(normal, -600, 100, unreal.MaterialSamplerType.SAMPLERTYPE_NORMAL)
        lib.connect_material_property(node, "", unreal.MaterialProperty.MP_NORMAL)
    if rough is not None:
        node = sample(rough, -600, 400, unreal.MaterialSamplerType.SAMPLERTYPE_LINEAR_GRAYSCALE)
        lib.connect_material_property(node, "", unreal.MaterialProperty.MP_ROUGHNESS)

    lib.recompile_material(material)
    unreal.EditorAssetLibrary.save_asset(material.get_path_name(), only_if_is_dirty=False)
    return material


def build_track_materials():
    def texture(name):
        return find_asset(PKG_TEXTURE, name, unreal.Texture2D)

    road = make_surface_material(
        "M_TrackRoad",
        texture("asphalt_pit_lane_diff"),
        texture("asphalt_pit_lane_nor_gl"),
        texture("asphalt_pit_lane_rough"),
        uv_scale=1.0)
    ground = make_surface_material(
        "M_TrackGround",
        texture("aerial_grass_rock_diff"),
        texture("aerial_grass_rock_nor_gl"),
        texture("aerial_grass_rock_rough"),
        uv_scale=1.0)
    log("マテリアル: road=%s ground=%s"
        % (road.get_name() if road else "None", ground.get_name() if ground else "None"))
    return road, ground


def place_track(road_material, ground_material):
    """路面と地面を置く。

    **メッシュは既にワールド座標で作られている**（Blender が中心線から
    直接生成した）ので、原点にそのまま置く。ここで位置を調整しないこと。
    """
    placed = []
    for mesh_name, material in (("TrackRoad", road_material),
                                ("TrackGround", ground_material)):
        mesh = find_asset(PKG_TRACK, mesh_name, unreal.StaticMesh)
        if mesh is None:
            unreal.log_error("[ZN6 level] %s が無い" % mesh_name)
            continue
        actor = spawn_mesh(mesh, unreal.Vector(0.0, 0.0, 0.0),
                           unreal.Rotator(0.0, 0.0, 0.0), mesh_name)
        if actor is None:
            continue
        if material is not None:
            actor.static_mesh_component.set_material(0, material)
        placed.append(actor)
    log("コースメッシュ %d 個を配置" % len(placed))
    return placed


def tree_meshes():
    """種ごとに代表メッシュを1つ選ぶ。

    PolyHaven の glTF は 1 資産が a/b/c… の変種に分かれている。
    **どれでもよいので決定的に選ぶ**（名前順の先頭）。無作為に選ぶと
    実行するたびに絵が変わり、差分の確認ができなくなる。
    """
    meshes = {}
    for species_dir in unreal.EditorAssetLibrary.list_assets(
            PKG_FOLIAGE, recursive=True, include_folder=False):
        asset = unreal.EditorAssetLibrary.load_asset(species_dir)
        if not isinstance(asset, unreal.StaticMesh):
            continue
        # /Game/ZN6/Foliage/<species>/... という構造
        parts = species_dir.split("/")
        try:
            species = parts[parts.index("Foliage") + 1]
        except (ValueError, IndexError):
            continue
        current = meshes.get(species)
        if current is None or asset.get_name() < current.get_name():
            meshes[species] = asset
    return meshes


def place_trees(root):
    with open(os.path.join(root, "Tracks", "Export", "placement.json"),
              encoding="utf-8") as handle:
        placement = json.load(handle)

    meshes = tree_meshes()
    log("樹種 %d 件: %s" % (len(meshes), ", ".join(sorted(meshes))))
    missing = set()

    count = 0
    for tree in placement["trees"]:
        mesh = meshes.get(tree["species"])
        if mesh is None:
            missing.add(tree["species"])
            continue
        actor = spawn_mesh(
            mesh,
            to_ue_location(tree["x_m"], tree["y_m"], tree["z_m"]),
            to_ue_yaw(tree["yaw_rad"]),
            "Tree_%s_%04d" % (tree["species"], count))
        if actor is None:
            continue
        scale = tree["scale"]
        actor.set_actor_scale3d(unreal.Vector(scale, scale, scale))
        count += 1

    if missing:
        # **黙って減らさない。** 置けなかった種があるなら言う。
        unreal.log_error("[ZN6 level] メッシュが見つからない樹種: %s"
                         % ", ".join(sorted(missing)))
    log("樹木 %d / %d 本を配置" % (count, len(placement["trees"])))
    return count


def place_lighting():
    """空と光。

    HDRIBackdrop を使う。**SkyLight だけでは背景が描かれない**（環境光には
    なるが空が見えない）。HDRIBackdrop は HDRI を見える空として貼りつつ
    ライティングにも使う。
    """
    sun = spawn_class(unreal.DirectionalLight, unreal.Vector(0.0, 0.0, 5000.0),
                      unreal.Rotator(0.0, -42.0, 30.0), "Sun")
    if sun is not None:
        # **SkyAtmosphere の太陽として使う。** これを立てないと空が
        # 昼にならない（太陽の位置が空の色を決めている）。
        sun.light_component.set_intensity(6.0)
        sun.light_component.set_editor_property("atmosphere_sun_light", True)

    # **霧は薄くする。** 既定の密度 0.02 のままだと、コース規模
    # （800 x 400 m）では画面全体が一様な灰色になり、地形も車も見えない
    # （実際に撮影した3枚とも灰色一色になった）。
    fog = spawn_class(unreal.ExponentialHeightFog, unreal.Vector(0.0, 0.0, 0.0),
                      unreal.Rotator(0.0, 0.0, 0.0), "HeightFog")
    if fog is not None:
        fog.component.set_editor_property("fog_density", 0.0008)
        fog.component.set_editor_property("fog_height_falloff", 0.05)

    # **空は SkyAtmosphere（手続き）で描く。**
    #
    # 最初は HDRIBackdrop に HDRI を貼ったが、撮影すると画面全体が
    # ドーム内側の一様な灰色になった（地形も車も見えない）。原因の切り分けに
    # 時間が掛かるうえ、見える空としては SkyAtmosphere で十分。
    #
    # **HDRI はアセットとして残してある。** 環境光の精度を上げたくなったら
    # SkyLight のキューブマップに使う（Tracks/Assets/polyhaven に取得済み）。
    atmosphere = spawn_class(unreal.SkyAtmosphere, unreal.Vector(0.0, 0.0, 0.0),
                             unreal.Rotator(0.0, 0.0, 0.0), "SkyAtmosphere")

    sky_light = spawn_class(unreal.SkyLight, unreal.Vector(0.0, 0.0, 20000.0),
                            unreal.Rotator(0.0, 0.0, 0.0), "SkyLight")
    if sky_light is not None:
        component = sky_light.light_component
        component.set_editor_property("source_type",
                                      unreal.SkyLightSourceType.SLS_CAPTURED_SCENE)
        component.set_editor_property("real_time_capture", True)
        component.set_editor_property("intensity", 1.0)

    log("空: SkyAtmosphere=%s / SkyLight=%s"
        % (atmosphere is not None, sky_light is not None))

    return sun


def place_vehicle(root):
    """車を置き、分解した描画メッシュを割り当てる。

    **物理は Actor 自身が vehicle.json から初期化する。** ここでやるのは
    描画メッシュの割り当てだけ。
    """
    # C++ のクラスは unreal モジュールに直接生える（Blueprint ではない）
    actor_class = unreal.ZN6VehicleActor

    # スタートライン（中心線の s=0）に置く。**物理が動き出せば Actor は
    # 自分で位置を上書きする**ので、ここは初期表示のためだけ。
    actor = spawn_class(actor_class, unreal.Vector(0.0, 0.0, 0.0),
                        unreal.Rotator(0.0, 0.0, 0.0), "ZN6")
    if actor is None:
        return None

    body = find_asset(PKG_VEHICLE + "/body", "ZN6_body", unreal.StaticMesh)
    components = actor.get_components_by_class(unreal.StaticMeshComponent)
    by_name = {c.get_name(): c for c in components}

    assigned = 0
    if body is not None and "BodyMesh" in by_name:
        by_name["BodyMesh"].set_static_mesh(body)
        assigned += 1

    for name in ("FL", "FR", "RL", "RR"):
        mesh = find_asset("%s/wheel_%s" % (PKG_VEHICLE, name),
                          "ZN6_wheel_%s" % name, unreal.StaticMesh)
        component = by_name.get("Wheel" + name)
        if mesh is not None and component is not None:
            component.set_static_mesh(mesh)
            assigned += 1

    log("車体メッシュ %d / 5 を割り当て（コンポーネント: %s）"
        % (assigned, ", ".join(sorted(by_name))))
    return actor


def main():
    root = repo_root()
    log("repo = %s" % root)

    level_subsystem = unreal.get_editor_subsystem(unreal.LevelEditorSubsystem)
    unreal.EditorAssetLibrary.make_directory(PKG_ROOT + "/Maps")

    # **既存のレベルを消してから作る。**
    #
    # `new_level()` は同じパスに既にレベルがあると **false を返すだけ**で
    # 例外を出さない。戻り値を捨てていたため、Actor が起動時の空ワールドへ
    # 置かれ、.umap は作成直後の 6,414 バイトのまま「保存した」とログに
    # 出ていた。**毎回同じ結果になるよう、作り直す。**
    # **既にあるなら作り直さず、開いて中身を空にする。**
    #
    # `new_level()` は同じパスにレベルがあると **false を返すだけ**で例外を
    # 出さない。戻り値を捨てていたため、Actor が起動時の空ワールドへ置かれ、
    # .umap は作成直後の 6,414 バイトのまま「保存した」とログに出ていた。
    # **ログが成功と言うことと、ファイルが書かれたことは別。**
    #
    # ファイルを消してから new_level しても駄目だった（アセットレジストリ上は
    # まだ存在扱いのまま）。開いて空にするのが確実。
    if unreal.EditorAssetLibrary.does_asset_exist(LEVEL_PATH):
        if not level_subsystem.load_level(LEVEL_PATH):
            unreal.log_error("[ZN6 level] レベルを開けない: %s" % LEVEL_PATH)
            return
        existing = actor_subsystem().get_all_level_actors()
        for actor in existing:
            actor_subsystem().destroy_actor(actor)
        log("既存レベルを開いて Actor %d 個を削除" % len(existing))
    elif not level_subsystem.new_level(LEVEL_PATH):
        unreal.log_error("[ZN6 level] レベルを作れない: %s" % LEVEL_PATH)
        return

    road_material, ground_material = build_track_materials()
    place_track(road_material, ground_material)
    place_trees(root)
    place_lighting()
    place_vehicle(root)

    # **保存できたかを必ず確かめる。**
    #
    # 以前 `save_current_level()` の戻り値を捨てていたため、
    # 「レベルを保存」とログに出しながら .umap が空のまま（6,414 バイト、
    # 作成直後のサイズ）という状態を成功と誤認した。**ログが成功と言うこと
    # と、ファイルが書かれたことは別。**
    saved = level_subsystem.save_current_level()
    if not saved:
        unreal.log_warning("[ZN6 level] save_current_level が false。"
                           "save_dirty_packages で再試行する")
        saved = unreal.EditorLoadingAndSavingUtils.save_dirty_packages(True, True)

    world = unreal.get_editor_subsystem(unreal.UnrealEditorSubsystem).get_editor_world()
    actor_count = len(actor_subsystem().get_all_level_actors())
    log("レベルを保存: %s（保存=%s / Actor %d 個 / world=%s）"
        % (LEVEL_PATH, saved, actor_count, world.get_name() if world else "None"))

    if not saved:
        unreal.log_error("[ZN6 level] レベルを保存できなかった")


main()
