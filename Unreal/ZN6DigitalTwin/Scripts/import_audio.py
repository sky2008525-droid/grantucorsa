"""合成した音のループを UE に取り込む（UE Python、Phase 14）。

    python Audio/synth.py           # 先に WAV を作る
    UnrealEditor-Cmd.exe <uproject> \
        -ExecutePythonScript="Unreal/ZN6DigitalTwin/Scripts/import_audio.py"

**取り込む前に `Audio/synth.py` を走らせること。** `Audio/Generated/` は
`.gitignore` に入っていて、リポジトリには WAV が無い。無い状態で走らせると
「0 本取り込んだ」で終わる（**成功として扱わない**。エラーを出す）。

## ループ再生を必ず有効にすること

`USoundWave` は既定でワンショット。ループにしないと、エンジン音が1秒で
止まり、**「音が出ない」ではなく「たまに鳴る」という分かりにくい壊れ方**を
する。取り込み後に `looping = True` を立てて保存する。

## 圧縮しない

`SoundWave.compression_quality` を上げないと、48 kHz のループが
つなぎ目でわずかに歪む。合成側でループ点を合わせてある努力が無駄になる。
"""

import json
import os

import unreal

PKG_AUDIO = "/Game/ZN6/Audio"


def log(message):
    unreal.log("[ZN6 audio] " + message)


def repo_root():
    return os.path.abspath(os.path.join(
        unreal.Paths.convert_relative_path_to_full(unreal.Paths.project_dir()),
        "..", ".."))


def generated_dir(root):
    return os.path.join(root, "Audio", "Generated")


def read_manifest(root):
    """どの段が何 rpm かの対応表。**無ければ止まる。**"""
    path = os.path.join(generated_dir(root), "manifest.json")
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def import_wavs(root, names):
    tasks = []
    for name in names:
        filename = os.path.join(generated_dir(root), name + ".wav")
        if not os.path.exists(filename):
            unreal.log_error("[ZN6 audio] WAV が無い: %s" % filename)
            continue

        task = unreal.AssetImportTask()
        task.set_editor_property("filename", filename)
        task.set_editor_property("destination_path", PKG_AUDIO)
        task.set_editor_property("destination_name", name)
        task.set_editor_property("automated", True)
        task.set_editor_property("replace_existing", True)
        task.set_editor_property("save", True)
        tasks.append(task)

    if not tasks:
        return []

    unreal.AssetToolsHelpers.get_asset_tools().import_asset_tasks(tasks)

    imported = []
    for task in tasks:
        paths = list(task.get_editor_property("imported_object_paths") or [])
        if not paths:
            unreal.log_error("[ZN6 audio] 取り込めなかった: %s"
                             % task.get_editor_property("filename"))
            continue
        imported += paths
    return imported


def make_loopable(paths):
    """**全部ループにする。** ワンショットのままだと1秒で音が止まる。"""
    changed, failed = 0, []
    for path in paths:
        asset = unreal.EditorAssetLibrary.load_asset(path.split(".")[0])
        if not isinstance(asset, unreal.SoundWave):
            failed.append(path)
            continue

        asset.set_editor_property("looping", True)
        # 圧縮でつなぎ目が歪むのを避ける。合成側でループ点を合わせてある。
        asset.set_editor_property("compression_quality", 100)
        unreal.EditorAssetLibrary.save_loaded_asset(asset, only_if_is_dirty=False)

        # **設定できたか確かめる。** 立てたつもりで立っていない事故があった
        # （Nanite で同じことをやった。フラグは立つがデータが無かった）。
        if not asset.get_editor_property("looping"):
            failed.append(path)
        else:
            changed += 1

    log("ループ設定: %d 本" % changed)
    if failed:
        unreal.log_error("[ZN6 audio] ループにできなかった: %s" % ", ".join(failed))
    return len(failed) == 0


def main():
    root = repo_root()
    log("repo = %s" % root)

    manifest = read_manifest(root)
    if manifest is None:
        # **「0 本取り込んだ」を成功にしない**（憲法ルール16）。
        unreal.log_error(
            "[ZN6 audio] Audio/Generated/manifest.json が無い。"
            "先に `python Audio/synth.py` を走らせること。")
        return

    names = list(manifest["files"])
    log("取り込む: %d 本（%d Hz）" % (len(names), manifest["sample_rate_hz"]))

    unreal.EditorAssetLibrary.make_directory(PKG_AUDIO)
    imported = import_wavs(root, names)

    if len(imported) != len(names):
        unreal.log_error("[ZN6 audio] %d/%d 本しか取り込めていない"
                         % (len(imported), len(names)))

    make_loopable(imported)

    log("取り込み完了: %d アセット" % len(imported))
    log("**この音は FA20 の音ではない。** 手続き合成（Audio/synth.py）。")


main()
