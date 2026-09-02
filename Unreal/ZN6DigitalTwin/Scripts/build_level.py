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
import sys

import unreal

PKG_ROOT = "/Game/ZN6"
PKG_VEHICLE = PKG_ROOT + "/Vehicle"
PKG_TRACK = PKG_ROOT + "/Track"
PKG_FOLIAGE = PKG_ROOT + "/Foliage"
PKG_TEXTURE = PKG_ROOT + "/Textures"
PKG_MATERIAL = PKG_ROOT + "/Materials"
#: どのコースを組むか。**コマンドラインで渡す。**
#:
#:     -ExecutePythonScript="build_level.py technical_circuit"
#:
#: 省略すると既存のコース。`Tracks/Export/<key>/` と
#: `/Game/ZN6/Track/<key>/` を読み、`/Game/ZN6/Maps/<key>` を作る。
TRACK_KEY = sys.argv[1] if len(sys.argv) > 1 else "physics_test_track"

LEVEL_PATH = PKG_ROOT + "/Maps/" + TRACK_KEY

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


def make_tyre_mark_material(name="M_ZN6_TyreMark"):
    """タイヤ痕のデカール用マテリアル。

    **ディファードデカールにする。** 通常のマテリアルを板に貼ると、
    路面の起伏や継ぎ目で浮いて見える。デカールなら路面へ投影される。

    濃さは `Opacity` パラメータで外から変える。滑りが強いほど濃く、
    時間とともに薄くする（`UZN6TyreMarkComponent`）。
    """
    package = PKG_MATERIAL
    unreal.EditorAssetLibrary.make_directory(package)

    asset_path = "%s/%s" % (package, name)
    if unreal.EditorAssetLibrary.does_asset_exist(asset_path):
        unreal.EditorAssetLibrary.delete_asset(asset_path)

    tools = unreal.AssetToolsHelpers.get_asset_tools()
    material = tools.create_asset(name, package, unreal.Material,
                                  unreal.MaterialFactoryNew())
    if material is None:
        unreal.log_error("[ZN6 level] マテリアルを作れない: %s" % asset_path)
        return None

    # **デカールにする。** これを忘れると板がそのまま宙に浮く。
    material.set_editor_property("material_domain",
                                 unreal.MaterialDomain.MD_DEFERRED_DECAL)
    # **decal_blend_mode は使えない。** UE 5.8 では protected になっており
    # set_editor_property が例外を投げる（実際に build_level が途中で止まった）。
    # デカールも通常の blend_mode を使う形に変わっている。
    material.set_editor_property("blend_mode", unreal.BlendMode.BLEND_TRANSLUCENT)

    lib = unreal.MaterialEditingLibrary

    # 色。**真っ黒にしない。** 実際のタイヤ痕は路面より少し暗いだけで、
    # 真っ黒だと穴が開いたように見える。
    colour = lib.create_material_expression(
        material, unreal.MaterialExpressionVectorParameter, -600, -200)
    colour.set_editor_property("parameter_name", "MarkColour")
    colour.set_editor_property("default_value",
                               unreal.LinearColor(0.035, 0.033, 0.032, 1.0))
    lib.connect_material_property(colour, "", unreal.MaterialProperty.MP_BASE_COLOR)

    # 濃さ。外から毎フレーム変える。
    opacity = lib.create_material_expression(
        material, unreal.MaterialExpressionScalarParameter, -600, 100)
    opacity.set_editor_property("parameter_name", "Opacity")
    opacity.set_editor_property("default_value", 0.75)
    lib.connect_material_property(opacity, "", unreal.MaterialProperty.MP_OPACITY)

    # ゴムは路面より艶がある
    rough = lib.create_material_expression(
        material, unreal.MaterialExpressionScalarParameter, -600, 300)
    rough.set_editor_property("parameter_name", "Roughness")
    rough.set_editor_property("default_value", 0.62)
    lib.connect_material_property(rough, "", unreal.MaterialProperty.MP_ROUGHNESS)

    lib.recompile_material(material)
    unreal.EditorAssetLibrary.save_asset(material.get_path_name(),
                                         only_if_is_dirty=False)
    log("タイヤ痕のマテリアル: %s" % name)
    return material


def make_road_material(name, diffuse, normal, rough,
                       overlay_diff, overlay_mask, overlay_rough):
    """アスファルトの上に白線・ひび割れ・補修跡を重ねたマテリアル。

    **2 つの UV を使い分ける。**

    | UV | 何に使うか | 作っている場所 |
    |---|---|---|
    | UV0 | アスファルト。**実寸でタイリング** | `build_track.py` |
    | UV1 | 白線・ひび割れ。**U が 0..1 でコース幅** | 同上 |

    白線は幅に対する比で位置を決めたいので実寸にできない。逆に
    アスファルトを比で貼ると、幅 12 m に 1 枚が引き伸ばされて
    **のっぺりした灰色の帯**になる（実際そうなっていた）。

    合成は `lerp(アスファルト, 上書き, mask)`。**置き換えではなく混ぜる**
    ので、白線の下にもアスファルトの粒が残る。
    """
    package = PKG_MATERIAL
    unreal.EditorAssetLibrary.make_directory(package)

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

    def coords(index, x, y):
        node = lib.create_material_expression(
            material, unreal.MaterialExpressionTextureCoordinate, x, y)
        node.set_editor_property("coordinate_index", index)
        return node

    base_uv = coords(0, -1400, 0)
    mark_uv = coords(1, -1400, 600)

    def sample(texture, uv, x, y, sampler_type):
        node = lib.create_material_expression(
            material, unreal.MaterialExpressionTextureSample, x, y)
        node.set_editor_property("texture", texture)
        node.set_editor_property("sampler_type", sampler_type)
        lib.connect_material_expressions(uv, "", node, "UVs")
        return node

    colour = unreal.MaterialSamplerType.SAMPLERTYPE_COLOR
    linear = unreal.MaterialSamplerType.SAMPLERTYPE_LINEAR_GRAYSCALE

    asphalt = sample(diffuse, base_uv, -1000, -400, colour)
    over_c = sample(overlay_diff, mark_uv, -1000, 200, colour)
    mask = sample(overlay_mask, mark_uv, -1000, 600, linear)

    # **mask で混ぜる。** 上書きするとアスファルトの粒が消える。
    blend = lib.create_material_expression(
        material, unreal.MaterialExpressionLinearInterpolate, -600, 0)
    lib.connect_material_expressions(asphalt, "", blend, "A")
    lib.connect_material_expressions(over_c, "", blend, "B")
    lib.connect_material_expressions(mask, "", blend, "Alpha")
    lib.connect_material_property(blend, "", unreal.MaterialProperty.MP_BASE_COLOR)

    if normal is not None:
        node = sample(normal, base_uv, -1000, -100,
                      unreal.MaterialSamplerType.SAMPLERTYPE_NORMAL)
        lib.connect_material_property(node, "", unreal.MaterialProperty.MP_NORMAL)

    if rough is not None:
        base_r = sample(rough, base_uv, -1000, 900, linear)
        over_r = sample(overlay_rough, mark_uv, -1000, 1200, linear)
        blend_r = lib.create_material_expression(
            material, unreal.MaterialExpressionLinearInterpolate, -600, 1000)
        lib.connect_material_expressions(base_r, "", blend_r, "A")
        lib.connect_material_expressions(over_r, "", blend_r, "B")
        lib.connect_material_expressions(mask, "", blend_r, "Alpha")
        lib.connect_material_property(blend_r, "",
                                      unreal.MaterialProperty.MP_ROUGHNESS)

    lib.recompile_material(material)
    unreal.EditorAssetLibrary.save_asset(material.get_path_name(),
                                         only_if_is_dirty=False)
    return material


def make_colour_material(name, colour, roughness=0.5, metallic=0.0):
    """テクスチャを使わず、色だけのマテリアルを作る。

    **水面のように、手持ちのテクスチャに該当が無いもの用。**
    PolyHaven の textures に水は入っていない。無理に別のテクスチャを
    貼るより、色と粗さだけで置くほうが素直である
    （粗さを下げると空を映して水らしくなる）。
    """
    package = PKG_MATERIAL
    unreal.EditorAssetLibrary.make_directory(package)
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
    base = lib.create_material_expression(
        material, unreal.MaterialExpressionVectorParameter, -600, -200)
    base.set_editor_property("parameter_name", "BaseColour")
    base.set_editor_property("default_value", colour)
    lib.connect_material_property(base, "", unreal.MaterialProperty.MP_BASE_COLOR)

    for value, prop, y in ((roughness, unreal.MaterialProperty.MP_ROUGHNESS, 60),
                           (metallic, unreal.MaterialProperty.MP_METALLIC, 220)):
        node = lib.create_material_expression(
            material, unreal.MaterialExpressionScalarParameter, -600, y)
        node.set_editor_property("parameter_name",
                                 "Roughness" if y == 60 else "Metallic")
        node.set_editor_property("default_value", value)
        lib.connect_material_property(node, "", prop)

    lib.recompile_material(material)
    unreal.EditorAssetLibrary.save_asset(material.get_path_name(),
                                         only_if_is_dirty=False)
    return material


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
        # ラフネスは線形グレースケール。**import_assets.py の
        # configure_textures() が先に色空間を直していることが前提。**
        # 食い違うとマテリアルは警告だけ出して既定のグレーに差し替わる。
        node = sample(rough, -600, 400, unreal.MaterialSamplerType.SAMPLERTYPE_LINEAR_GRAYSCALE)
        lib.connect_material_property(node, "", unreal.MaterialProperty.MP_ROUGHNESS)

    lib.recompile_material(material)
    unreal.EditorAssetLibrary.save_asset(material.get_path_name(), only_if_is_dirty=False)

    # **コンパイルの成否はここでは分からない。** 失敗しても UE は警告を
    # 出して既定マテリアルに差し替えるだけで、例外も戻り値も無い。
    # ログの "Failed to compile Material" を見ること。
    return material


def build_track_materials():
    def texture(name):
        return find_asset(PKG_TEXTURE, name, unreal.Texture2D)

    overlay_diff = texture("road_overlay_diff")
    overlay_mask = texture("road_overlay_mask")
    overlay_rough = texture("road_overlay_rough")

    if overlay_diff is None or overlay_mask is None:
        # **黙ってアスファルトだけにしない**（憲法ルール6）。
        # 白線が消えていることに気づけなくなる。
        unreal.log_error(
            "[ZN6 level] 白線のテクスチャが無い。"
            "`python Tracks/road_texture.py` と import_assets.py を先に走らせること。")
        road = make_surface_material(
            "M_TrackRoad",
            texture("asphalt_pit_lane_diff"),
            texture("asphalt_pit_lane_nor_gl"),
            texture("asphalt_pit_lane_rough"),
            uv_scale=1.0)
    else:
        road = make_road_material(
            "M_TrackRoad",
            texture("asphalt_pit_lane_diff"),
            texture("asphalt_pit_lane_nor_gl"),
            texture("asphalt_pit_lane_rough"),
            overlay_diff, overlay_mask, overlay_rough)
    # **地面のテクスチャはコースごと**（`Tracks/environment.py`）。
    # 指定が無い／取り込まれていない場合は既定に落ちる。黙って
    # 落ちると気づけないので、そのときは警告する。
    def surface(name, fallback="aerial_grass_rock"):
        if texture("%s_diff" % name) is None:
            if name != fallback:
                unreal.log_warning(
                    "[ZN6 level] テクスチャ %s が無いので %s を使う"
                    % (name, fallback))
            name = fallback
        return (texture("%s_diff" % name), texture("%s_nor_gl" % name),
                texture("%s_rough" % name))

    names = placement_textures(repo_root())
    ground = make_surface_material("M_TrackGround",
                                   *surface(names.get("ground",
                                                      "aerial_grass_rock")),
                                   uv_scale=1.0)

    # 縁石。**UV は Blender 側で実寸に合わせて焼いてある**ので、
    # ここでタイリングを掛けない（uv_scale=1.0）。掛けると縞の間隔が
    # `Tracks/kerb.py` の設計と変わる。
    kerb_diff = texture("kerb_diff")
    if kerb_diff is None:
        # **黙って縁石を灰色にしない**（憲法ルール6）。
        unreal.log_error(
            "[ZN6 level] 縁石のテクスチャが無い。"
            "`python Tracks/road_texture.py` と import_assets.py を先に走らせること。")
        kerb = None
    else:
        kerb = make_surface_material("M_TrackKerb", kerb_diff, None,
                                     texture("kerb_rough"), uv_scale=1.0)
    make_tyre_mark_material()

    # --- 道路構造のマテリアル -------------------------------------------
    #
    # **遠景の山を近くの地面と同じ材質にしない。** 同じにすると、
    # 2.6 km 先の尾根に手前と同じ草のテクスチャが同じ大きさで貼られ、
    # 距離が分からなくなる（遠くのものほど細かく見えるはずがない）。
    distant = make_surface_material("M_TrackDistant",
                                    *surface(names.get("distant",
                                                       "aerial_grass_rock")),
                                    uv_scale=1.0)

    # ガードレールと高架の構造物。**手続きで作った面**なので UV は粗い。
    # コンクリート／金属らしい無地で塗る。
    structure = make_surface_material(
        "M_TrackStructure",
        texture("concrete_road_barrier_diff"),
        texture("concrete_road_barrier_nor_gl"),
        texture("concrete_road_barrier_rough"),
        uv_scale=1.0)

    # 海。**テクスチャが無いので色で塗る。**
    # 水のテクスチャは PolyHaven の textures に入っていない。
    # board のような平らな青にせず、粗さを下げて空を映すようにする。
    sea = make_colour_material("M_TrackSea",
                               unreal.LinearColor(0.012, 0.035, 0.055, 1.0),
                               roughness=0.08, metallic=0.0)

    log("マテリアル: road=%s kerb=%s ground=%s distant=%s structure=%s"
        % (road.get_name() if road else "None",
           kerb.get_name() if kerb else "None",
           ground.get_name() if ground else "None",
           distant.get_name() if distant else "None",
           structure.get_name() if structure else "None"))
    return road, kerb, ground, distant, structure, sea


def place_track(road_material, kerb_material, ground_material,
                distant_material=None, structure_material=None,
                sea_material=None):
    """路面と地面を置く。

    **メッシュは既にワールド座標で作られている**（Blender が中心線から
    直接生成した）ので、原点にそのまま置く。ここで位置を調整しないこと。
    """
    placed = []
    # **道路構造はコースによって在ったり無かったりする。**
    # 峠に橋脚は無いし、サーキットにガードレールは無い。
    optional = {"TrackDistant", "TrackGuardrail", "TrackViaduct",
                "TrackPit", "TrackSea"}
    for mesh_name, material in (("TrackRoad", road_material),
                                ("TrackKerb", kerb_material),
                                ("TrackGround", ground_material),
                                ("TrackDistant", distant_material),
                                ("TrackGuardrail", structure_material),
                                ("TrackViaduct", structure_material),
                                ("TrackPit", road_material),
                                ("TrackSea", sea_material)):
        mesh = find_asset("%s/%s" % (PKG_TRACK, TRACK_KEY), mesh_name,
                          unreal.StaticMesh)
        if mesh is None:
            if mesh_name not in optional:
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
    with open(os.path.join(root, "Tracks", "Export", TRACK_KEY, "placement.json"),
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


def place_props(root):
    """コース周りの物（バリア・タイヤ・フェンス・街灯・建物など）を置く。

    **樹木と同じ仕組みで置く。** どちらも `/Game/ZN6/Foliage/<kind>/` に
    取り込まれた CC0 のモデルで、配置は `placement.json` が決めている。

    **数が多い**（1コースで 2000 個超）。Nanite が効いているので描画は
    持つが、Actor が増えるとレベルの読み込みが遅くなる。
    減らしたいときは `Blender/build_track.py` の `PROP_PLAN` の間隔を
    広げること。**ここで間引かない**（配置の決定は1箇所に置く）。
    """
    with open(os.path.join(root, "Tracks", "Export", TRACK_KEY, "placement.json"),
              encoding="utf-8") as handle:
        placement = json.load(handle)

    props = placement.get("props", [])
    if not props:
        log("props: 配置データが無い（build_track.py が古い可能性）")
        return 0

    meshes = tree_meshes()          # Foliage 以下を全部拾うので props も入る
    missing = set()
    count = 0
    for index, prop in enumerate(props):
        mesh = meshes.get(prop["kind"])
        if mesh is None:
            missing.add(prop["kind"])
            continue
        actor = spawn_mesh(
            mesh,
            to_ue_location(prop["x_m"], prop["y_m"], prop["z_m"]),
            to_ue_yaw(prop["yaw_rad"]),
            "Prop_%s_%04d" % (prop["kind"], index))
        if actor is None:
            continue
        scale = prop["scale"]
        actor.set_actor_scale3d(unreal.Vector(scale, scale, scale))
        count += 1

    if missing:
        # **黙って減らさない。** 取り込み忘れに気づけなくなる。
        unreal.log_error("[ZN6 level] メッシュが見つからない: %s"
                         % ", ".join(sorted(missing)))
    log("props %d / %d 個を配置" % (count, len(props)))
    return count


def placement_textures(root):
    """`placement.json` からテクスチャ名を読む。**無ければ空。**"""
    path = os.path.join(root, "Tracks", "Export", TRACK_KEY, "placement.json")
    if not os.path.isfile(path):
        return {}
    with open(path, encoding="utf-8") as handle:
        data = json.load(handle)
    return {"ground": data.get("ground_texture"),
            "distant": data.get("distant_texture")}


def placement_lighting(root):
    """`placement.json` から空と光の設定を読む。**無ければ既定。**"""
    path = os.path.join(root, "Tracks", "Export", TRACK_KEY, "placement.json")
    if not os.path.isfile(path):
        return {}
    with open(path, encoding="utf-8") as handle:
        return json.load(handle).get("lighting", {})


def place_lighting(settings=None):
    """空と光。

    HDRIBackdrop を使う。**SkyLight だけでは背景が描かれない**（環境光には
    なるが空が見えない）。HDRIBackdrop は HDRI を見える空として貼りつつ
    ライティングにも使う。
    """
    # **太陽を高くする。** 低いと車体の影側が真っ黒になり、
    # そこにある車輪が見えない（実際に「タイヤが見えない」と指摘された）。
    # **コースごとに空気感を変える**（`Tracks/environment.py` の Lighting）。
    # 空は手続き生成（SkyAtmosphere）なので、HDRI を増やさなくても
    # 朝夕・霞・晴天を作り分けられる。
    settings = settings or {}
    sun_pitch = settings.get("sun_pitch_deg", -58.0)
    sun_yaw = settings.get("sun_yaw_deg", 35.0)

    sun = spawn_class(unreal.DirectionalLight, unreal.Vector(0.0, 0.0, 5000.0),
                      unreal.Rotator(0.0, sun_pitch, sun_yaw), "Sun")
    if sun is not None:
        # **SkyAtmosphere の太陽として使う。** これを立てないと空が
        # 昼にならない（太陽の位置が空の色を決めている）。
        sun.light_component.set_intensity(
            settings.get("sun_intensity", 10.0))
        sun.light_component.set_editor_property("atmosphere_sun_light", True)

    # **霧は薄くする。** 既定の密度 0.02 のままだと、コース規模
    # （800 x 400 m）では画面全体が一様な灰色になり、地形も車も見えない
    # （実際に撮影した3枚とも灰色一色になった）。
    fog = spawn_class(unreal.ExponentialHeightFog, unreal.Vector(0.0, 0.0, 0.0),
                      unreal.Rotator(0.0, 0.0, 0.0), "HeightFog")
    if fog is not None:
        # **霧が遠景の距離感を作る。**
        # 濃さを 0 にすると、2.6 km 先の尾根が手前の斜面と同じ濃さで
        # 描かれ、遠くにあるように見えない（空気遠近）。
        fog.component.set_editor_property(
            "fog_density", settings.get("fog_density", 0.0008))
        fog.component.set_editor_property(
            "fog_height_falloff", settings.get("fog_height_falloff", 0.05))
        colour = settings.get("fog_colour")
        if colour:
            fog.component.set_editor_property(
                "fog_inscattering_luminance",
                unreal.LinearColor(colour[0], colour[1], colour[2], 1.0))

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
        # **環境光を効かせる。** 1.0 だと影側に光が回らず、
        # 車体の陰にある車輪が黒く潰れて見えなくなる。
        component.set_editor_property("intensity", 3.0)

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

    # 車輪の取り付け位置は manifest が持っている。**ここで焼き込む。**
    #
    # 実行時（BeginPlay）にも読むが、**エディタでは BeginPlay が走らない**
    # ので、レベル側で設定しておかないと4輪とも原点に重なる。実際それで
    # 車体にタイヤが埋まった状態になっていた。
    with open(os.path.join(root, "Vehicles", "ZN6", "Export", "manifest.json"),
              encoding="utf-8") as handle:
        manifest = json.load(handle)

    # **生成した車輪を使う**（SPEC_PHASE2_BACKLOG.md 3.2-5）。
    # 元モデルから切り出したもの（wheel_FL 等）はリムが潰れていて
    # 「黒い輪」にしか見えなかった。左右で鏡像を使い分ける。
    for name in ("FL", "FR", "RL", "RR"):
        side = "left" if name.endswith("L") else "right"
        mesh = find_asset("%s/generated_%s" % (PKG_VEHICLE, side),
                          "ZN6_", unreal.StaticMesh)
        component = by_name.get("Wheel" + name)
        if mesh is None or component is None:
            unreal.log_error("[ZN6 level] 車輪 %s を割り当てられない" % name)
            continue

        component.set_static_mesh(mesh)
        attach = manifest["parts"]["wheel_%s" % name]["attach_m"]
        component.set_relative_location(
            to_ue_location(attach[0], attach[1], attach[2]), False, False)
        assigned += 1

    # **この車をプレイヤーが操作する。**
    #
    # AZN6GameMode は DefaultPawnClass を持たないので、ここで
    # auto possess を立てないと操作対象が存在しない。逆に GameMode 側で
    # spawn させると、Blender が決めた車輪位置も描画メッシュも持たない
    # 「空の車」が出てくる。
    actor.set_editor_property("auto_possess_player",
                              unreal.AutoReceiveInput.PLAYER0)

    log("車体メッシュ %d / 5 を割り当て（コンポーネント: %s）/ auto possess = Player0"
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

    (road_material, kerb_material, ground_material,
     distant_material, structure_material,
     sea_material) = build_track_materials()
    place_track(road_material, kerb_material, ground_material,
                distant_material, structure_material, sea_material)
    place_trees(root)
    place_props(root)
    place_lighting(placement_lighting(root))
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
