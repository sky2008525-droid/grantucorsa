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
import shutil
import tempfile
import urllib.request
import zipfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DEST_ROOT = REPO_ROOT / "Tracks" / "Assets" / "opengameart"

sys.path.insert(0, str(Path(__file__).resolve().parent))
from fetch_polyhaven import measure_gltf                       # noqa: E402

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

#: 複数モデルが 1 つの zip に入っているもの（キット）。
#:
#: `ASSETS` は「1 zip = 1 モデル」の作りで、道路標識のようにキットで配布
#: されているものが入らない。**PolyHaven に道路標識・信号機が 1 つも無い**
#: （2026-09-02 に models 521 件を全走査。`Industrial & Infrastructure/
#: Traffic & Safety/Signs` は WetFloorSign_01 の 1 件だけ）ため、ここは
#: OpenGameArt に頼るしかない。
#:
#: ## `uniform` を書く理由（**ここが重要**）
#:
#: **Kenney のキットは実寸ではない。** 配布物のままだと信号機が 0.52 m、
#: 工業ビルが 1.47 m しかない（ミニチュアの寸法）。そのまま置くと
#: 「木だけ実寸で標識だけ模型」になる。
#:
#: 倍率は**推測せずに規格から出した**。同じキットに入っている 20 ft
#: コンテナ（`shipping-container-a`）の長辺が 0.823 で、**ISO 668 の
#: 1CC コンテナは 6.058 m**。よって 6.058 / 0.823 = **7.3609**。
#:
#: 裏取り: この倍率だと道路タイル（配布物で 1.00 角）が 7.36 m になる。
#: 日本の 2 車線道路は 1 車線 3.25〜3.5 m なので 6.5〜7.0 m で、**別の
#: 経路から出した数字と 1 割以内で合う。**
#:
#: **合わないところも書く。** コンテナの幅は 0.373 x 7.3609 = 2.75 m で、
#: ISO 668 の 2.438 m より 13% 太い。Kenney のコンテナは寸胴に作られて
#: いる。**キットの中の寸法の関係を保つほうを採り、等倍にしてある。**
#: 個々の物の寸法は実物と照合していない（`size_source` に書いてある）。
PACKS = {
    "kenney_city_roads": {
        "title": "City Kit (Roads) 2.1",
        "author": "Kenney",
        "license": "CC0",
        "source": "https://opengameart.org/content/city-kit-roads",
        "file_url": "https://opengameart.org/sites/default/files/kenney_city-kit-roads_0.zip",
        "md5": "746a3da5bd128c36c2d6cab633843152",
        "fetched": "2026-09-02",
        "uniform": 7.3609,
        "style": "low_poly",
        "texture": "Models/GLB format/Textures/colormap.png",
        # **道路タイルは取らない。** 路面は `Blender/build_track.py` が
        # 中心線から作っている（`Docs/PHASE15_DATA_LICENCE.md` §6.3 の
        # 縁石と同じ理由）。要るのは路側の物だけ。
        "members": [
            "road-sign-stop", "road-sign-street", "road-sign-warning",
            "road-sign-empty", "road-sign-empty-hanging",
            "road-sign-object-stop", "road-sign-object-street",
            "road-sign-object-warning",
            "sign-highway", "sign-highway-wide", "sign-highway-detailed",
            "traffic-light", "traffic-light-hanging",
            "traffic-light-object-vertical", "traffic-light-object-horizontal",
            "traffic-light-object-hanging",
            "construction-barrier", "construction-fence", "construction-cone",
            "construction-light",
            "light-square", "light-square-double", "light-curved",
            "light-curved-double",
            "bridge-pillar", "bridge-pillar-wide", "dumpster",
        ],
    },
    "kenney_city_industrial": {
        "title": "City Kit (Industrial) 2.0",
        "author": "Kenney",
        "license": "CC0",
        "source": "https://opengameart.org/content/city-kit-industrial",
        "file_url":
            "https://opengameart.org/sites/default/files/kenney_city-kit-industrial_2.0.zip",
        "md5": "eb88313b2bd444d63e614509fe518fba",
        "fetched": "2026-09-02",
        "uniform": 7.3609,          # 同じキット体系（作者が互換と明記）
        "style": "low_poly",
        "texture": "Models/GLB format/Textures/colormap.png",
        "members": [
            "building-a", "building-b", "building-c", "building-d",
            "building-e", "building-f", "building-g", "building-h",
            "building-i", "building-j", "building-k", "building-l",
            "building-m", "building-n", "building-o", "building-p",
            "building-q", "building-r", "building-s", "building-t",
            "shipping-container-a", "shipping-container-b", "shipping-container-c",
            "water-tower", "chimney-large", "chimney-medium", "chimney-small",
            "detail-tank", "detail-tank-large",
        ],
    },
    "traffic_road_assets": {
        "title": "Traffic Road Assets",
        "author": "milkandbanana",
        "license": "CC0",
        "source": "https://opengameart.org/content/traffic-road-assets",
        "file_url":
            "https://opengameart.org/sites/default/files/traffic_road_assets.zip",
        "md5": "509b6dfc569c7502da169b3ab1e77972",
        "fetched": "2026-09-02",
        # **このキットも実寸ではない。** 最初「実寸に近い」と書いたが、
        # 測ったら街灯が 44 単位・マンホールが 6 単位で、そのままでは
        # 街灯 44 m になる。
        #
        # 基準に取ったのは**三角コーン**（配布物で高さ 2.618 単位）。
        # このリポジトリは既に H700 パイロン（高さ 700 mm）を基準に
        # 決めてある（§6.2）ので、そこへ合わせる: 0.70 / 2.618 = 0.26738。
        #
        # 裏取りと**合わないところ**:
        #   街灯 44.05 -> 11.78 m    高速道路の照明柱は 10〜12 m。合う
        #   警告板 2.22 ->  0.59 m   実物の停止表示板は約 0.43 m。1.4 倍
        #   マンホール 6.01 -> 1.61 m 実物は 600 mm。**2.7 倍で合わない**
        # マンホールと消火栓は**採らない**。どちらも PolyHaven に実寸の
        # ものがある（water_manhole_cover 0.69 m / fire_hydrant 0.88 m）。
        "uniform": 0.26738,
        "style": "low_poly_textured",
        "glb_prefix": "Traffic Road Assets/GLB/All/",
        "members": [
            # **GLB 側に Plastic_Road_Block が無い**（FBX 側にはある）。
            # 名前を並べただけで確認しないと、ここで静かに 1 個減る。
            "Crush_Barrier", "Crush_Barrier_001",
            "Road_Block", "Road_Block_001", "Road_Block_004",
            "Chipped_Road_Block", "Small_Road_Block",
            "Wood_Road_Block_Large", "Wood_Road_Block_Medium",
            "Wood_Road_Block_Small",
            "Streetlights", "Streetlights_001",
            "Warning_Triangle",
            "Broken_Traffic_Cone", "Broken_Traffic_Cone_001",
        ],
    },
    "warehouse_32kda": {
        "title": "Warehouse building, low poly",
        "author": "32kda",
        "license": "CC0",
        "source": "https://opengameart.org/content/warehouse-building-low-poly",
        "file_url": "https://opengameart.org/sites/default/files/warehouse02.zip",
        "md5": "ef5045faa1971bc32e0ee2ec07b9ca13",
        "fetched": "2026-09-02",
        # 配布物が 23.8 x 52.0 x 8.2 m。**倉庫として妥当な実寸**なので
        # そのまま使う。サーキットのピット棟の代わりに置ける箱物。
        "uniform": None,
        "style": "low_poly_textured",
        "glb_prefix": "",
        "members": ["warehouse02"],
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


def fetch_pack(key: str, record: dict) -> dict:
    """1 つの zip に複数モデルが入っているキットを取り込む。

    **配布物の zip はリポジトリに残さない。** `ASSETS` 側（三角コーン）は
    720 KB なので原本を置いているが、こちらは 3〜54 MB あり、しかも要る
    のは中の一部だけ。代わりに `LICENSE.txt` と manifest に取得元 URL と
    md5 を書き、同じものを取り直せるようにしてある。
    """
    folder = DEST_ROOT / key
    folder.mkdir(parents=True, exist_ok=True)

    data = download(record["file_url"], record["md5"])
    print("{}: {} bytes 取得（md5 一致）".format(key, len(data)))

    staging = Path(tempfile.mkdtemp(prefix="oga_%s_" % key))
    files = {}
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            available = {info.filename for info in archive.infolist()}
            prefix = record.get("glb_prefix", "Models/GLB format/")

            texture = record.get("texture")
            if texture:
                # **テクスチャは相対パスのまま置く。** Kenney の GLB は
                # `Textures/colormap.png` を**外部参照**しており、位置を
                # 変えると Blender が読めずに真っ白なモデルになる。
                target = staging / "Textures" / Path(texture).name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(archive.read(texture))

            missing = [m for m in record["members"]
                       if prefix + m + ".glb" not in available]
            if missing:
                # **黙って飛ばさない**（憲法ルール6）。名前が変わったのに
                # 気づかず「数が減ったまま完了」になるのを防ぐ。
                raise RuntimeError("{}: zip に無い: {}".format(
                    key, ", ".join(missing)))

            for member in record["members"]:
                (staging / (member + ".glb")).write_bytes(
                    archive.read(prefix + member + ".glb"))

        for member in record["members"]:
            source = staging / (member + ".glb")
            destination = folder / (member + ".glb")
            convert(source, destination, record)
            whole, _ = measure_gltf(destination)
            files[destination.name] = whole
            print("   {:<34} {:.2f} x {:.2f} x {:.2f} m".format(
                destination.name, whole[0], whole[1], whole[2]))
    finally:
        shutil.rmtree(staging, ignore_errors=True)

    (folder / "LICENSE.txt").write_text(
        "{title}\n"
        "Author : {author}\n"
        "License: {license}\n"
        "Source : {source}\n"
        "File   : {file_url}\n"
        "md5    : {md5}\n"
        "Fetched: {fetched}\n"
        "\n"
        "取得と変換: Tools/fetch_opengameart.py（PACKS）\n"
        "変換の内容: zip から必要な GLB だけ取り出し、テクスチャを埋め込んで\n"
        "            書き出した。倍率 {uniform}。詳細は\n"
        "            Docs/PHASE15_DATA_LICENCE.md §6.5\n".format(
            **dict(record, uniform=record.get("uniform") or "1.0（配布物のまま）")),
        encoding="utf-8")

    return {
        "name": record["title"],
        "authors": {record["author"]: "modeling"},
        "license": record["license"],
        "source": record["source"],
        "file_url": record["file_url"],
        "md5": record["md5"],
        "fetched": record["fetched"],
        "kind": "models",
        "style": record["style"],
        # **複数モデル。** `import_assets.py` は `gltf_files` があれば
        # そちらを見る（無ければ従来どおり `gltf` 1 つ）。
        "gltf_files": sorted(files),
        "sizes_m": files,
        "uniform_scale": record.get("uniform"),
        "size_source": (
            "measured_from_gltf（ISO 668 の 20ft コンテナ 6.058 m を基準に "
            "キット全体へ等倍 {} を掛けた。個々の物の寸法は実物と照合して "
            "いない）".format(record["uniform"])
            if record.get("uniform") else
            "measured_from_gltf（配布物のまま。実物との照合はしていない）"),
        "bytes": sum((folder / name).stat().st_size for name in files),
    }


def convert(source: Path, destination: Path, record: dict) -> None:
    if not Path(BLENDER).is_file():
        raise RuntimeError(
            "Blender が無い: {}。BLENDER=... を設定して実行すること".format(BLENDER))
    if "target_width_m" in record:
        scale_args = [str(record["target_width_m"]), str(record["target_height_m"])]
    elif record.get("uniform"):
        scale_args = ["-", "-", "--uniform", str(record["uniform"])]
    else:
        scale_args = ["-", "-"]           # 配布物のまま
    command = [
        BLENDER, "--background", "--python",
        str(REPO_ROOT / "Blender" / "convert_prop.py"), "--",
        str(source), str(destination),
    ] + scale_args
    # **文字コードを明示する。** 既定は Windows の cp932 で、Blender が
    # 出す UTF-8 のログを読めずにスレッドごと落ちる（実際にそうなった）。
    result = subprocess.run(command, capture_output=True, text=True,
                            encoding="utf-8", errors="replace")
    for line in result.stdout.splitlines():
        if line.startswith("[convert]") and "!!" in line:
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

    known = dict(ASSETS)
    known.update(PACKS)
    keys = args.keys or sorted(known)
    unknown = [key for key in keys if key not in known]
    if unknown:
        parser.error("知らないアセット: {}（ある: {}）".format(
            ", ".join(unknown), ", ".join(sorted(known))))

    DEST_ROOT.mkdir(parents=True, exist_ok=True)
    manifest_path = DEST_ROOT / "manifest.json"
    manifest = {}
    if manifest_path.is_file():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    for key in keys:
        if key in PACKS:
            manifest[key] = fetch_pack(key, PACKS[key])
        else:
            manifest[key] = fetch(key, ASSETS[key])

    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print("manifest: {}".format(manifest_path))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
