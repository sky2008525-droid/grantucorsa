"""Blender と PolyHaven が出したアセットを UE5 へ取り込む（UE Python）.

    UnrealEditor-Cmd.exe <uproject> -run=pythonscript
        -script="Unreal/ZN6DigitalTwin/Scripts/import_assets.py"

**GUI の操作手順を README に書かない。** Blender 側と同じ方針で、
取り込みも自動化する。手順書にすると、誰が何回やっても同じ結果になる
保証が無くなる。

取り込むもの:

  Vehicles/ZN6/Export/*.fbx      車体（ボディ + 4輪）
  Tracks/Export/*.fbx            路面・縁石・地面
  Tracks/Assets/polyhaven/*      樹木（glTF）・テクスチャ・HDRI
  Tracks/Assets/opengameart/*    PolyHaven に無かった小物（パイロン）

**単位に注意。** Blender 側は m で書き出しており、FBX は 1 unit = 1 cm の
UE へ入るときに 100 倍される。`import_uniform_scale` を触らないこと。
"""

import json
import os

import unreal

# --- 配置先（/Game 以下）------------------------------------------------------
PKG_ROOT = "/Game/ZN6"
PKG_VEHICLE = PKG_ROOT + "/Vehicle"
PKG_TRACK = PKG_ROOT + "/Track"
PKG_FOLIAGE = PKG_ROOT + "/Foliage"
PKG_TEXTURE = PKG_ROOT + "/Textures"


def repo_root():
    """<repo> を返す。プロジェクトは <repo>/Unreal/ZN6DigitalTwin/ にある。"""
    return os.path.normpath(os.path.join(unreal.Paths.project_dir(), "..", ".."))


def log(message):
    unreal.log("[ZN6 import] " + message)


def build_task(filename, destination, options=None, replace=True):
    task = unreal.AssetImportTask()
    task.filename = filename
    task.destination_path = destination
    task.automated = True          # ダイアログを出さない
    task.replace_existing = replace
    task.save = True
    if options is not None:
        task.options = options
    return task


def static_mesh_options():
    """FBX を StaticMesh として取り込む設定（路面・地面用）。

    glTF には使わない。**glTF は Interchange 任せ**で、FbxImportUI は効かない。
    """
    options = unreal.FbxImportUI()
    options.set_editor_property("import_mesh", True)
    options.set_editor_property("import_textures", False)
    options.set_editor_property("import_materials", False)
    options.set_editor_property("import_as_skeletal", False)
    options.set_editor_property("mesh_type_to_import",
                                unreal.FBXImportType.FBXIT_STATIC_MESH)

    mesh_data = options.static_mesh_import_data
    mesh_data.set_editor_property("combine_meshes", True)
    mesh_data.set_editor_property("generate_lightmap_u_vs", True)
    # **コリジョンを作らない。** UE の物理は使わない（憲法ルール4）。
    # 当たり判定を持たせると、Chaos が車体に干渉する余地が生まれる。
    mesh_data.set_editor_property("auto_generate_collision", False)
    return options


def configure_textures():
    """法線・ラフネスを線形データとして扱わせる。

    **取り込みの直後、マテリアルを作る前に済ませる。**

    JPG から取り込むと既定で sRGB のカラーテクスチャになる。法線や
    ラフネスは線形データなので、マテリアル側のサンプラ型と食い違い、
    UE は「コンパイルできない」と警告して**既定のグレーマテリアルに
    差し替える**。エラーではなく警告なので、気づかないまま
    「なぜか灰色の路面」になる。

    **設定の反映は次にアセットを読み込んだときになる。** 同じ実行の中で
    設定してすぐマテリアルを作ると、まだ古い状態で判定されて失敗する
    （実際に Color と Linear Grayscale で往復した）。だから取り込み側に
    置き、レベル構築とは別の実行にしている。
    """
    changed = 0
    for path in unreal.EditorAssetLibrary.list_assets(PKG_TEXTURE, recursive=True):
        asset = unreal.EditorAssetLibrary.load_asset(path)
        if not isinstance(asset, unreal.Texture2D):
            continue
        name = asset.get_name().lower()

        if "_nor" in name:
            settings = unreal.TextureCompressionSettings.TC_NORMALMAP
        elif "_rough" in name or "_arm" in name:
            settings = unreal.TextureCompressionSettings.TC_GRAYSCALE
        else:
            continue                      # カラーはそのままでよい

        asset.set_editor_property("srgb", False)
        asset.set_editor_property("compression_settings", settings)
        unreal.EditorAssetLibrary.save_asset(path, only_if_is_dirty=False)
        changed += 1

    log("テクスチャの色空間を修正: %d 枚" % changed)
    return changed


def configure_generated_textures():
    """生成テクスチャの色空間を直す。

    **mask と rough は線形データ。** sRGB のまま読むと、白線の縁が
    にじみ、ラフネスが全体に明るく出る（`configure_textures()` と同じ話）。
    """
    fixed = 0
    for name in ("road_overlay_mask", "road_overlay_rough", "kerb_rough"):
        path = "%s/%s" % (PKG_TEXTURE, name)
        asset = unreal.EditorAssetLibrary.load_asset(path)
        if asset is None:
            unreal.log_error("[ZN6 import] %s が無い（色空間を直せない）" % path)
            continue
        asset.set_editor_property("srgb", False)
        # **TC_MASKS を使わないこと。**
        #
        # マテリアル側は SAMPLERTYPE_LINEAR_GRAYSCALE で読む。TC_MASKS と
        # 組み合わせるとサンプラ型が食い違い、**マテリアルがコンパイルに
        # 失敗して路面がチェッカー模様になる**（実際にそうなった）。
        # 既存のラフネス（configure_textures）と同じ TC_GRAYSCALE に揃える。
        asset.set_editor_property(
            "compression_settings", unreal.TextureCompressionSettings.TC_GRAYSCALE)
        unreal.EditorAssetLibrary.save_asset(path, only_if_is_dirty=False)
        fixed += 1
    log("生成テクスチャの色空間: %d 枚" % fixed)


def enable_nanite_everywhere():
    """取り込んだ StaticMesh すべてで Nanite を有効にし、**データが出来たか確かめる。**

    PolyHaven の樹木は 1 本 11〜16 万ポリゴンあり、525 本で約 5,200 万
    三角形になる。Nanite が無いと描画が破綻する。

    ## `set_editor_property("nanite_settings", ...)` を使ってはいけない

    あれは**フラグを立てるだけで Nanite データをビルドしない。**
    その結果、メッシュは「Nanite 有効」だが中身が無い状態になり、
    **画面には空しか映らなくなる**（地面も車も 525 本の木も全て消えた）。

    エラーも警告も出ない。`is_visible()` は True、三角形数も
    フォールバックの値が返るので、**調べても正常に見える。**
    実際にこれで「レベルは正しいのに何も映らない」状態に数時間費やした。

    `StaticMeshEditorSubsystem.set_nanite_settings(..., apply_changes=True)`
    がビルドまで行う正しい入り口。そのうえで
    `get_num_nanite_triangles()` が 0 でないことを確認する。
    """
    subsystem = unreal.get_editor_subsystem(unreal.StaticMeshEditorSubsystem)

    built, failed = 0, []
    for path in unreal.EditorAssetLibrary.list_assets(PKG_ROOT, recursive=True):
        asset = unreal.EditorAssetLibrary.load_asset(path)
        if not isinstance(asset, unreal.StaticMesh):
            continue

        settings = subsystem.get_nanite_settings(asset)
        if not settings.enabled:
            settings.enabled = True
            subsystem.set_nanite_settings(asset, settings, apply_changes=True)
            unreal.EditorAssetLibrary.save_asset(path, only_if_is_dirty=False)

        # **ここが要。** 有効になっただけでは描画されない。
        if asset.get_num_nanite_triangles() <= 0:
            failed.append(asset.get_name())
        else:
            built += 1

    log("Nanite: %d メッシュでデータを確認" % built)
    if failed:
        # **黙って通さない。** これを見逃すと画面が真っ白になる。
        unreal.log_error(
            "[ZN6 import] Nanite データが空のメッシュ: %s" % ", ".join(failed))
    return len(failed) == 0


def run_tasks(tasks):
    tools = unreal.AssetToolsHelpers.get_asset_tools()
    tools.import_asset_tasks(tasks)
    imported = []
    for task in tasks:
        paths = list(task.get_editor_property("imported_object_paths") or [])
        if not paths:
            unreal.log_error("[ZN6 import] 取り込めなかった: %s" % task.filename)
        imported.extend(paths)
    return imported


def import_vehicle(root):
    export_dir = os.path.join(root, "Vehicles", "ZN6", "Export")
    manifest_path = os.path.join(export_dir, "manifest.json")
    with open(manifest_path, encoding="utf-8") as handle:
        manifest = json.load(handle)

    tasks = []
    for name, part in manifest["parts"].items():
        path = os.path.join(export_dir, part["file"])
        if not os.path.isfile(path):
            unreal.log_error("[ZN6 import] 車体パーツが無い: %s" % path)
            continue
        # **glTF なのでオプションを渡さない。** Interchange が処理する。
        # FBX で埋め込みテクスチャを渡したときは、メッシュだけ入って
        # テクスチャ5枚が静かに欠けた（Blender/decompose_vehicle.py 参照）。
        tasks.append(build_task(path, "%s/%s" % (PKG_VEHICLE, name)))

    # **生成した車輪を取り込む**（SPEC_PHASE2_BACKLOG.md 3.2-5）。
    # 元モデルから切り出した車輪はリムのディテールが乏しく、UE 上で
    # 「黒い輪」にしか見えなかった。公開寸法から生成したものへ差し替える。
    for side in ("left", "right"):
        path = os.path.join(export_dir, "ZN6_wheel_%s.glb" % side)
        if not os.path.isfile(path):
            unreal.log_error("[ZN6 import] 生成した車輪が無い: %s" % path)
            continue
        tasks.append(build_task(path, "%s/generated_%s" % (PKG_VEHICLE, side)))

    log("車体 %d パーツを取り込む" % len(tasks))
    return run_tasks(tasks)


def track_keys(root):
    """`Tracks/Export/` の下にあるコースの一覧。

    **フォルダの有無で決める。** 一覧をここに書くと、コースを増やすたびに
    2 箇所を直すことになり、必ず片方を忘れる。
    """
    export_dir = os.path.join(root, "Tracks", "Export")
    if not os.path.isdir(export_dir):
        return []
    keys = []
    for name in sorted(os.listdir(export_dir)):
        folder = os.path.join(export_dir, name)
        if os.path.isfile(os.path.join(folder, "TrackRoad.fbx")):
            keys.append(name)
    return keys


def import_track(root):
    """コースごとに `/Game/ZN6/Track/<key>/` へ取り込む。

    **同じ名前のメッシュが複数のコースにある**（TrackRoad / TrackGround）
    ので、フォルダを分けないと後から取り込んだものが前を上書きする。
    """
    keys = track_keys(root)
    if not keys:
        unreal.log_error(
            "[ZN6 import] コースメッシュが1つも無い。"
            "先に Tools/build_tracks.sh を走らせること")
        return []

    imported = []
    for key in keys:
        folder = os.path.join(root, "Tracks", "Export", key)
        tasks = []
        # **道路構造は「あれば取り込む」。**
        #
        # 遠景の山・ガードレール・高架の橋脚は、コースによって在ったり
        # 無かったりする（`Tracks/environment.py` が決める）。峠に橋脚は
        # 無いし、サーキットにガードレールは無い。**無いことを error に
        # しない**（error にすると、正常な状態が毎回赤く出て意味を失う）。
        required = ("TrackRoad.fbx", "TrackKerb.fbx", "TrackGround.fbx")
        optional = ("TrackDistant.fbx", "TrackGuardrail.fbx", "TrackViaduct.fbx")
        for name in required + optional:
            path = os.path.join(folder, name)
            if not os.path.isfile(path):
                if name in required:
                    unreal.log_error("[ZN6 import] %s が無い" % path)
                continue
            tasks.append(build_task(path, "%s/%s" % (PKG_TRACK, key),
                                    static_mesh_options()))
        log("コース %s: メッシュ %d 個" % (key, len(tasks)))
        imported += run_tasks(tasks)

    return imported


def import_generated_textures(root):
    """`Tracks/road_texture.py` が描いた白線・ひび割れを取り込む。

    **素材を探すのではなく自分で描いている。** 白線はコース幅に合って
    いなければ意味が無く、汎用のテクスチャでは幅 12 m のどこに引くかを
    決められない（`Tracks/road_texture.py` の冒頭）。
    """
    folder = os.path.join(root, "Tracks", "Export", "Textures")
    if not os.path.isdir(folder):
        # **黙って飛ばさない**（憲法ルール6）。無いなら理由が分かるように。
        unreal.log_error(
            "[ZN6 import] 生成テクスチャが無い: %s。"
            "先に `python Tracks/road_texture.py` を走らせること。" % folder)
        return []

    tasks = []
    for name in ("road_overlay_diff", "road_overlay_mask", "road_overlay_rough",
                 "kerb_diff", "kerb_rough"):
        path = os.path.join(folder, name + ".png")
        if os.path.isfile(path):
            tasks.append(build_task(path, PKG_TEXTURE))
        else:
            unreal.log_error("[ZN6 import] %s が無い" % path)

    log("生成テクスチャ %d 枚を取り込む" % len(tasks))
    return run_tasks(tasks)


def import_asset_pack(root, pack, required=True):
    """`Tracks/Assets/<pack>/manifest.json` に載っているものを取り込む。

    **PolyHaven 以外の出所も同じ形の manifest を書く**
    （`Tools/fetch_opengameart.py`）。取り込み側を出所ごとに分けると、
    増えるたびにここへ分岐が増える。

    @param required 無いときにエラーにするか。PolyHaven は必須、
                    それ以外は「まだ取得していない」があり得る。
    """
    base = os.path.join(root, "Tracks", "Assets", pack)
    manifest_path = os.path.join(base, "manifest.json")
    if not os.path.isfile(manifest_path):
        # **黙って飛ばさない**（憲法ルール6）。
        message = ("[ZN6 import] %s が無い。"
                   "Tools/fetch_%s.py を先に走らせること" % (manifest_path, pack))
        if required:
            unreal.log_error(message)
        else:
            unreal.log_warning(message)
        return []

    with open(manifest_path, encoding="utf-8") as handle:
        manifest = json.load(handle)

    model_tasks = []
    texture_tasks = []
    for asset_id, record in manifest.items():
        kind = record.get("kind", "models")
        folder = os.path.join(base, asset_id)

        if kind == "models":
            gltf = os.path.join(folder, record["gltf"])
            if not os.path.isfile(gltf):
                unreal.log_error("[ZN6 import] glTF が無い: %s" % gltf)
                continue
            # **glTF は Interchange 任せにする。** FbxImportUI は使えない
            model_tasks.append(build_task(gltf, "%s/%s" % (PKG_FOLIAGE, asset_id)))

        elif kind == "hdris":
            hdr = os.path.join(folder, record["file"])
            if os.path.isfile(hdr):
                texture_tasks.append(build_task(hdr, PKG_TEXTURE))

        else:  # textures
            for map_name, filename in record.get("maps", {}).items():
                path = os.path.join(folder, filename)
                if os.path.isfile(path):
                    texture_tasks.append(build_task(path, PKG_TEXTURE))

    log("%s: モデル %d 種 / テクスチャ %d 枚を取り込む"
        % (pack, len(model_tasks), len(texture_tasks)))
    return run_tasks(model_tasks) + run_tasks(texture_tasks)


def main():
    root = repo_root()
    log("repo = %s" % root)

    unreal.EditorAssetLibrary.make_directory(PKG_VEHICLE)
    unreal.EditorAssetLibrary.make_directory(PKG_TRACK)
    unreal.EditorAssetLibrary.make_directory(PKG_FOLIAGE)
    unreal.EditorAssetLibrary.make_directory(PKG_TEXTURE)

    imported = []
    imported += import_vehicle(root)
    imported += import_track(root)
    imported += import_asset_pack(root, "polyhaven")
    imported += import_asset_pack(root, "opengameart")
    imported += import_generated_textures(root)

    configure_textures()
    configure_generated_textures()
    enable_nanite_everywhere()

    log("取り込み完了: %d アセット" % len(imported))
    for path in sorted(imported):
        log("  " + path)


main()
