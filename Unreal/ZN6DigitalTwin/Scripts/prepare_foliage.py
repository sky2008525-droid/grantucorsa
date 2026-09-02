# -*- coding: utf-8 -*-
"""樹木メッシュを描ける状態にする。

    UnrealEditor-Cmd.exe <uproject> \
        -ExecutePythonScript=".../prepare_foliage.py" -unattended -nosplash

## 何を直すのか

**峠の木が幹だけの棒に見えていた。** 画面を撮って初めて分かった。

原因は **Nanite**。glTF から取り込まれた樹木は Nanite が有効になっており、
**Nanite が扱えないマテリアルのセクションは描かれずに消える。**
葉のマテリアルは glTF で `alphaMode = BLEND`（半透明）なので、
葉のセクションだけが丸ごと落ちていた。幹と枝は不透明なので残る。
これが「棒だけの木」の正体である。

切り分けの順序（**推測で直さない**）:

| 見たこと | 分かったこと |
|---|---|
| 葉のマテリアルの親が `MI_Default_Blend_DS` | 半透明として取り込まれている |
| 葉のベースカラーだけが PNG | アルファは**ある**（幹・枝は JPEG） |
| `fir_tree_01_twig` が 446,074 三角形 | 葉の**面もある**（木の面の 88%） |
| Masked に上書きしても変わらない | マテリアルの問題ではない |
| メッシュが `nanite = True` | **ここ** |
| Nanite を切ったら葉が出た | 確定 |

## この script がすること

1. **Nanite を切る。** 葉が描かれるようになる
2. **LOD を作る。** Nanite を切ると LOD が無い（取り込み時は LOD 1 段）。
   峠には木が 4307 本あり、1 本 50 万面のまま全部描くのは無理
3. **レイトレーシングの対象から外す。** 有効なままだと
   「RAY TRACING GEOMETRY - ALWAYS RESIDENT MEMORY EXCEEDS 20% OF THE
   BUDGET (319 MiB / 400 MiB)」が画面に出る。**葉が反射に映ることより、
   警告が出ないことのほうが大事**（映っても誰も見ない）
"""

import unreal

PKG_FOLIAGE = "/Game/ZN6/Foliage"


def log(message):
    unreal.log("[ZN6 foliage] " + message)


def build_lods(subsystem, mesh):
    """LOD を 3 段作る。**Nanite を切ったので自分で用意する。**

    削減率と画面占有率は演出値（憲法ルール18）。遠くの木の枝ぶりを
    誰も数えないので、思い切って落としてよい。
    """
    options = unreal.EditorScriptingMeshReductionOptions()
    options.set_editor_property("auto_compute_lod_screen_size", True)
    settings = []
    for percent in (1.0, 0.35, 0.12, 0.04):
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

    nanite_off = 0
    ray_off = 0
    lods = 0
    saved = 0

    for data in registry.get_assets_by_path(PKG_FOLIAGE, recursive=True):
        asset = data.get_asset()
        if not isinstance(asset, unreal.StaticMesh):
            continue

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

        if asset.get_num_lods() < 2:
            if build_lods(subsystem, asset):
                lods += 1
                touched = True

        if touched:
            unreal.EditorAssetLibrary.save_asset(asset.get_path_name(),
                                                 only_if_is_dirty=False)
            saved += 1

    log("Nanite を切った %d / レイトレを外した %d / LOD を作った %d "
        "（保存 %d 個）" % (nanite_off, ray_off, lods, saved))


main()
