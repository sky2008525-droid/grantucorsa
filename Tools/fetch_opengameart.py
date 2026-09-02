#!/usr/bin/env python3
"""OpenGameArt から CC0 のモデルを取得し、glTF へ変換する.

    python Tools/fetch_opengameart.py            # 表に載っているもの全部
    python Tools/fetch_opengameart.py traffic_cone

**なぜ PolyHaven ではないのか**

`Tools/fetch_polyhaven.py` の方針（自分でモデリングせず CC0 を持ってくる）は
そのままだが、**PolyHaven には三角コーン（パイロン）が無い。**
2026-09-02 に API の models 521 件を全走査し、`cone` / `pylon` / `traffic`
のいずれにも一致しなかった（一致したのは concrete_road_barrier 2 件のみ）。

同じ日に見た他の候補:

| 候補 | 結果 |
|---|---|
| ambientCG | 三角コーンのモデルが無い（`q=cone` は地面マテリアル 1 件） |
| Poly Pizza | API に鍵が要る（`401 You need an API key`）。取得できない |
| Sketchfab | **ダウンロード可能な CC0 の三角コーンが 0 件。** CC-BY は 24 件あるが、取得に OAuth トークンが要り、鍵が無い |
| Kenney Racing Kit | 質感が合わず不採用済み（既定） |
| OpenGameArt | **CC0 の三角コーンがある。認証不要で取得できる** ← これ |

**表に無いものを勝手に足さない。** ライセンスの確認は URL を開いて
本文を読むところまでが作業で、それを `Docs/PHASE15_DATA_LICENCE.md` に
書き写して初めて完了する（憲法ルール2）。

## 容量

`Tools/fetch_polyhaven.py` と同じく md5 で検証する。**壊れたファイルを
置かない。** 後段の Blender が読めずに落ちたとき、原因が転送エラーだと
分からなくなる。
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import subprocess
import sys
import urllib.request
import zipfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DEST_ROOT = REPO_ROOT / "Tracks" / "Assets" / "opengameart"

HEADERS = {"User-Agent": "grantucorsa/1.0 (ZN6 digital twin; contact via repo)"}

#: 取得するもの。**md5 と取得日を必ず書く。**
#:
#: `target_*_m` は取り込み時に焼き込む実寸（`Blender/convert_prop.py`）。
#: **元モデルの寸法は作者の都合であって実寸ではない。**
ASSETS = {
    "traffic_cone": {
        "title": "Traffic cone",
        "author": "Savino",
        "license": "CC0",
        "source": "https://opengameart.org/content/traffic-cone",
        "file_url": "https://opengameart.org/sites/default/files/high.zip",
        "md5": "6aa7af159d0a931b03f84690193f0b1b",
        "entry": "high.obj",
        "fetched": "2026-09-02",
        # 日本で一般的な「H700 パイロン」（高さ 700 mm / 底面 380 mm 角）に
        # 合わせる。元モデルは 0.42 x 0.40 x 0.42 m で、**底面は実物どおり
        # だが背が半分近く低い。** 等倍で高さを合わせると底面が 0.74 m に
        # なり、明らかに太い。円錐は回転体なので縦に伸ばしても形は崩れない。
        # **これは景観であって計測対象ではない**（樹木の尺度と同じ扱い）。
        "target_width_m": 0.38,
        "target_height_m": 0.70,
    },
}

#: 変換に使う Blender。`Tools/build_tracks.sh` と同じ既定にしてある。
BLENDER = os.environ.get(
    "BLENDER", r"C:\Program Files\Blender Foundation\Blender 5.0\blender.exe")


def download(url: str, expect_md5: str) -> bytes:
    request = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(request, timeout=600) as response:
        data = response.read()
    got = hashlib.md5(data).hexdigest()
    if got != expect_md5:
        raise RuntimeError(
            "md5 が一致しない: {} (期待 {} / 実際 {})".format(url, expect_md5, got))
    return data


def fetch(key: str, record: dict) -> dict:
    folder = DEST_ROOT / key
    folder.mkdir(parents=True, exist_ok=True)

    data = download(record["file_url"], record["md5"])
    print("{}: {} bytes 取得".format(key, len(data)))

    names = []
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        for info in archive.infolist():
            if info.is_dir():
                continue
            # **配布物をそのまま残す。** 変換後の glb だけにすると、
            # 「元が何だったか」が追えなくなる（出典の検証ができない）。
            target = folder / Path(info.filename).name
            target.write_bytes(archive.read(info))
            names.append(target.name)
    print("   展開: {}".format(", ".join(sorted(names))))

    entry = folder / record["entry"]
    if not entry.is_file():
        raise RuntimeError("{}: {} が展開されなかった".format(key, record["entry"]))

    # **ライセンスをアセットの隣にも置く。** リポジトリを一部だけ
    # 切り出したときに、出典が離れて行方不明になるのを防ぐ。
    (folder / "LICENSE.txt").write_text(
        "{title}\n"
        "Author : {author}\n"
        "License: {license}\n"
        "Source : {source}\n"
        "File   : {file_url}\n"
        "md5    : {md5}\n"
        "Fetched: {fetched}\n"
        "\n"
        "取得と変換: Tools/fetch_opengameart.py\n"
        "変換の内容: Docs/PHASE15_DATA_LICENCE.md\n".format(**record),
        encoding="utf-8")

    glb = folder / (key + ".glb")
    convert(entry, glb, record)

    return {
        "name": record["title"],
        "authors": {record["author"]: "modeling"},
        "license": record["license"],
        "source": record["source"],
        "file_url": record["file_url"],
        "md5": record["md5"],
        "fetched": record["fetched"],
        "kind": "models",
        "gltf": glb.name,
        "original": record["entry"],
        "size_m": [record["target_width_m"], record["target_width_m"],
                   record["target_height_m"]],
        "bytes": glb.stat().st_size,
    }


def convert(source: Path, destination: Path, record: dict) -> None:
    if not Path(BLENDER).is_file():
        raise RuntimeError(
            "Blender が無い: {}。BLENDER=... を設定して実行すること".format(BLENDER))
    command = [
        BLENDER, "--background", "--python",
        str(REPO_ROOT / "Blender" / "convert_prop.py"), "--",
        str(source), str(destination),
        str(record["target_width_m"]), str(record["target_height_m"]),
    ]
    # **文字コードを明示する。** 既定は Windows の cp932 で、Blender が
    # 出す UTF-8 のログを読めずにスレッドごと落ちる（実際にそうなった）。
    result = subprocess.run(command, capture_output=True, text=True,
                            encoding="utf-8", errors="replace")
    for line in result.stdout.splitlines():
        if line.startswith("[convert]"):
            print("   " + line)
    if result.returncode != 0 or not destination.is_file():
        # **黙って進まない**（憲法ルール6）。glb が無いまま manifest を
        # 書くと、UE 側で「取り込めなかった」とだけ出る。
        sys.stderr.write(result.stdout[-4000:] + "\n" + result.stderr[-4000:] + "\n")
        raise RuntimeError("{} の変換に失敗した".format(source.name))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("keys", nargs="*", help="取得するもの（既定: 全部）")
    args = parser.parse_args()

    keys = args.keys or sorted(ASSETS)
    unknown = [key for key in keys if key not in ASSETS]
    if unknown:
        parser.error("知らないアセット: {}（ある: {}）".format(
            ", ".join(unknown), ", ".join(sorted(ASSETS))))

    DEST_ROOT.mkdir(parents=True, exist_ok=True)
    manifest_path = DEST_ROOT / "manifest.json"
    manifest = {}
    if manifest_path.is_file():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    for key in keys:
        manifest[key] = fetch(key, ASSETS[key])

    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print("manifest: {}".format(manifest_path))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
