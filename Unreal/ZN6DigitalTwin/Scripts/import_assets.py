"""Blender と PolyHaven が出したアセットを UE5 へ取り込む（UE Python）.

    UnrealEditor-Cmd.exe <uproject> -run=pythonscript
        -script="Unreal/ZN6DigitalTwin/Scripts/import_assets.py"

**GUI の操作手順を README に書かない。** Blender 側と同じ方針で、
取り込みも自動化する。手順書にすると、誰が何回やっても同じ結果になる
保証が無くなる。

取り込むもの:

  Vehicles/ZN6/Export/*.fbx      車体（ボディ + 4輪）
  Tracks/Export/*.fbx            路面・地面
  Tracks/Assets/polyhaven/*      樹木（glTF）・テクスチャ・HDRI

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


def enable_nanite_everywhere():
    """取り込んだ StaticMesh すべてで Nanite を有効にする。

    **取り込みオプションではなく後段で設定する。** FBX と glTF で
    オプションの与え方が違い（前者は FbxImportUI、後者は Interchange の
    パイプライン）、片方だけ設定して**もう片方が静かに素通りする**のを
    避けたい。結果に対して一律に掛けるほうが確実。

    PolyHaven の樹木は 40 万〜84 万ポリゴンあり、525 本を素で置くと
    描画が破綻する。Nanite はまさにこの用途のもの。
    """
    changed = 0
    for path in unreal.EditorAssetLibrary.list_assets(PKG_ROOT, recursive=True):
        asset = unreal.EditorAssetLibrary.load_asset(path)
        if not isinstance(asset, unreal.StaticMesh):
            continue
        settings = asset.get_editor_property("nanite_settings")
        if settings.enabled:
            continue
        settings.enabled = True
        asset.set_editor_property("nanite_settings", settings)
        unreal.EditorAssetLibrary.save_asset(path, only_if_is_dirty=False)
        changed += 1
    log("Nanite を有効化: %d メッシュ" % changed)
    return changed


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

    log("車体 %d パーツを取り込む" % len(tasks))
    return run_tasks(tasks)


def import_track(root):
    export_dir = os.path.join(root, "Tracks", "Export")
    tasks = []
    for name in ("TrackRoad.fbx", "TrackGround.fbx"):
        path = os.path.join(export_dir, name)
        if not os.path.isfile(path):
            unreal.log_error("[ZN6 import] コースメッシュが無い: %s" % path)
            continue
        tasks.append(build_task(path, PKG_TRACK, static_mesh_options()))

    log("コースメッシュ %d 個を取り込む" % len(tasks))
    return run_tasks(tasks)


def import_polyhaven(root):
    base = os.path.join(root, "Tracks", "Assets", "polyhaven")
    with open(os.path.join(base, "manifest.json"), encoding="utf-8") as handle:
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

    log("樹木 %d 種 / テクスチャ %d 枚を取り込む"
        % (len(model_tasks), len(texture_tasks)))
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
    imported += import_polyhaven(root)

    enable_nanite_everywhere()

    log("取り込み完了: %d アセット" % len(imported))
    for path in sorted(imported):
        log("  " + path)


main()
