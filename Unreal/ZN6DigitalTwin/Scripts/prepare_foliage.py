# -*- coding: utf-8 -*-
"""樹木・小物のメッシュを描ける／軽い状態にする。

    UnrealEditor-Cmd.exe <uproject> \
        -ExecutePythonScript=".../prepare_foliage.py" -unattended -nosplash

## 1. Nanite を切る（葉が出ない問題）

**峠の木が幹だけの棒に見えていた。** 画面を撮って初めて分かった。

原因は **Nanite**。glTF から取り込まれた樹木は Nanite が有効で、
**Nanite が扱えないマテリアルのセクションは描かれずに消える。**
葉のマテリアルは glTF で `alphaMode = BLEND`（半透明）なので、
葉のセクションだけが丸ごと落ちていた。幹と枝は不透明なので残る。

## 2. LOD を作る（重い問題）

Nanite を切ると LOD が無くなる（取り込み時は 1 段だけ）。
峠には木が 4307 本あり、1 本 50 万面のまま全部描くのは無理である。
**Nanite を切った時点で LOD は必須になった。**

### 全部にやらない

最初は `/Game/ZN6/Foliage` の 1001 メッシュ全部に掛けて、
**33 分を超えて打ち切られた。** 実際に置いているのは 40 種ほどで、
しかも重いのはそのうちの数種（木）だけである。

  - `placement.json`（4コース）に出てくる種類だけ
  - そのうち**面数がしきい値を超えるものだけ**

に絞る。草 1 本に LOD は要らない。

## 3. レイトレーシングの対象から外す

有効なままだと「RAY TRACING GEOMETRY - ALWAYS RESIDENT MEMORY EXCEEDS
20% OF THE BUDGET」が画面に出る。葉が反射に映ることより、警告が
出ないことのほうが大事。
"""

import json
import os

import unreal

PKG_FOLIAGE = "/Game/ZN6/Foliage"

#: LOD を作る面数のしきい値。**これ以下は作らない。**
#:
#: 草や小石に LOD を足しても描画は軽くならず、生成と保存の時間だけ
#: 掛かる（1 メッシュあたり 10 秒近い）。
LOD_TRIANGLE_THRESHOLD = 8000

#: LOD ごとの面の残し方。**遠くの木の枝ぶりは誰も数えない。**
#: 演出値（憲法ルール18）。
LOD_PERCENT = (1.0, 0.30, 0.10, 0.03)


def log(message):
    unreal.log("[ZN6 foliage] " + message)


def repo_root():
    return os.path.abspath(os.path.join(
        unreal.Paths.project_dir(), "..", ".."))


def used_asset_ids():
    """4コースの placement.json に出てくる種類。**置いていない物は触らない。**"""
    root = repo_root()
    export = os.path.join(root, "Tracks", "Export")
    names = set()
    if not os.path.isdir(export):
        return names
    for key in os.listdir(export):
        path = os.path.join(export, key, "placement.json")
        if not os.path.isfile(path):
            continue
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
        names.update(data.get("species", []))
        names.update(data.get("prop_kinds", []))
        for tree in data.get("trees", []):
            names.add(tree.get("species"))
        for prop in data.get("props", []):
            names.add(prop.get("kind"))
    names.discard(None)
    return names


def triangle_count(asset_data):
    """アセットレジストリのタグから面数を取る。**メッシュを読み込まずに済む。**"""
    for tag in ("Triangles", "TrianglesCount"):
        value = asset_data.get_tag_value(tag)
        if value:
            try:
                return int(str(value).split()[0])
            except ValueError:
                pass
    return None


def build_lods(subsystem, mesh):
    options = unreal.EditorScriptingMeshReductionOptions()
    options.set_editor_property("auto_compute_lod_screen_size", True)
    settings = []
    for percent in LOD_PERCENT:
        entry = unreal.EditorScriptingMeshReductionSettings()
        entry.set_editor_property("percent_triangles", percent)
        settings.append(entry)
    options.set_editor_property("reduction_settings", settings)
    try:
        subsystem.set_lods(mesh, options)
        return True
    except Exception as error:
        unreal.log_warning("[ZN6 foliage] LOD を作れない %s: %s"
                           % (mesh.get_name(), error))
        return False


def main():
    subsystem = unreal.get_editor_subsystem(unreal.StaticMeshEditorSubsystem)
    registry = unreal.AssetRegistryHelpers.get_asset_registry()

    used = used_asset_ids()
    log("置いている種類 %d 件" % len(used))
    if not used:
        unreal.log_error("[ZN6 foliage] placement.json を読めない。"
                         "先に ./Tools/build_tracks.sh")
        return

    nanite_off = 0
    ray_off = 0
    lods = 0
    saved = 0
    skipped_small = 0

    for data in registry.get_assets_by_path(PKG_FOLIAGE, recursive=True):
        if str(data.asset_class_path.asset_name) != "StaticMesh":
            continue

        # `/Game/ZN6/Foliage/<asset_id>/...` の <asset_id> を見る
        parts = str(data.package_name).split("/")
        if len(parts) < 5 or parts[4] not in used:
            continue

        triangles = triangle_count(data)
        asset = data.get_asset()
        touched = False

        settings = asset.get_editor_property("nanite_settings")
        if settings.get_editor_property("enabled"):
            settings.set_editor_property("enabled", False)
            asset.set_editor_property("nanite_settings", settings)
            nanite_off += 1
            touched = True

        try:
            if asset.get_editor_property("support_ray_tracing"):
                asset.set_editor_property("support_ray_tracing", False)
                ray_off += 1
                touched = True
        except Exception:
            pass

        # **重いものだけ LOD を作る。**
        if asset.get_num_lods() < 2:
            if triangles is not None and triangles < LOD_TRIANGLE_THRESHOLD:
                skipped_small += 1
            elif build_lods(subsystem, asset):
                lods += 1
                touched = True
                log("LOD: %s（%s 面）" % (asset.get_name(), triangles))

        if touched:
            unreal.EditorAssetLibrary.save_asset(asset.get_path_name(),
                                                 only_if_is_dirty=False)
            saved += 1

    log("Nanite を切った %d / レイトレを外した %d / LOD を作った %d "
        "/ 小さいので飛ばした %d（保存 %d 個）"
        % (nanite_off, ray_off, lods, skipped_small, saved))


main()
