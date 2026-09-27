#!/usr/bin/env python3
"""PolyHaven の**重い樹木**を、変種を選んで取り込む.

    python Tools/fetch_polyhaven_trees.py            # 表に載っているもの全部
    python Tools/fetch_polyhaven_trees.py fir_tree_01

## なぜ `Tools/fetch_polyhaven.py` と別なのか

**大木のファイルが桁違いに重いから。** 2026-09-02 に PolyHaven の
models 521 件を全走査して分かったこと（`Docs/PHASE15_DATA_LICENCE.md` §6.4）:

| id | 実寸の高さ | 1k glTF の容量 |
|---|---|---|
| `pine_tree_01` | 17.6 m（3本入り） | **958 MB** |
| `fir_tree_01` | 18.9 / 14.1 / 14.5 m | **478 MB** |
| `pine_sapling_medium` | 11.5 / 9.1 / 7.0 m | **264 MB** |
| `jacaranda_tree` | 19.2 m | 214 MB |

`fetch_polyhaven.py` は落としたものをそのまま置く作りで、これをやると
リポジトリが 1 ファイルで 1 GB 近く増える。

**中身を見ると、1 ファイルに木が 3 本入っている。** `fir_tree_01` は
18.93 m / 14.06 m / 14.52 m の 3 本が横並びで、いちばん軽い c だけなら
505,494 三角形（約 36 MB）で済む。

そこで**残す木を名前で選んでから GLB に書き出す**。

- **頂点は 1 つも書き換えない。** 間引き（Decimate）はしていない
- **拡大縮小もしない。** PolyHaven のフォトグラメトリは既に実寸で、
  引き伸ばすと「若木を大木のふりをさせる」元の問題に戻る
- 落とすのは「どの木をリポジトリに入れるか」だけ

## 残さないもの

配布物の .gltf / .bin / テクスチャは**リポジトリに置かない**（重いので、
それが目的）。代わりに `LICENSE.txt` と manifest に**取得元 URL と md5**
を書き、同じものを後から取り直せるようにしてある。
`Tools/fetch_opengameart.py` は原本を残しているが、あれは 720 KB だから
できることで、478 MB では同じ判断にならない。

## md5

`fetch_polyhaven.py` と同じく、**落とした 1 ファイルごとに API の md5 と
突き合わせる**（`download()` をそのまま使う）。壊れたファイルを置かない。
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from fetch_polyhaven import (                       # noqa: E402
    API, DEST_ROOT, REPO_ROOT, download, get_json, gltf_entry, measure_gltf,
)

#: 変換に使う Blender。`Tools/fetch_opengameart.py` と同じ既定。
BLENDER = os.environ.get(
    "BLENDER", r"C:\Program Files\Blender Foundation\Blender 5.0\blender.exe")

#: 取り込む樹木と、**残す変種の名前**。
#:
#: 名前は配布物の glTF のノード名そのまま。`--keep` は前方一致で照合する。
#: **「なぜその 1 本か」を書くこと。** 後から見て「全部入れなかった理由」が
#: 分からないと、容量の判断が再現できない。
ASSETS = {
    "fir_tree_01": {
        # a=18.93 m(4,176,819 tri) / b=14.06 m(2,300,624 tri) / c=14.52 m(505,494 tri)
        # **c は a とほぼ同じ背丈で、三角形が 1/8。** 針葉樹 10 m 以上という
        # 要求は c だけで満たせる。a と b を足すと +444 MB になる。
        "keep": ["fir_tree_01_c_LOD0"],
        "why": "14.52 m のモミ。同アセットの 3 本のうち、背丈あたりの容量が最も軽い",
    },
    "pine_sapling_medium": {
        # a=11.49 m(2,662,894 tri) / b=9.12 m(1,872,357 tri) / c=7.01 m(1,502,888 tri)
        # **名前は sapling（若木）だが実寸 11.5 m ある。** PolyHaven の
        # 命名は樹齢の話で、背丈の話ではない。fir_tree_01 とは別種（マツ）
        # なので、針葉樹が 1 種類だけにならないよう a を入れる。
        "keep": ["pine_sapling_medium_a_LOD0"],
        "why": "11.49 m のマツ。fir_tree_01（モミ）と樹種を分けるため",
    },
}


def blender_convert(source: Path, destination: Path, keep):
    if not Path(BLENDER).is_file():
        raise RuntimeError(
            "Blender が無い: {}。BLENDER=... を設定して実行すること".format(BLENDER))
    command = [
        BLENDER, "--background", "--python",
        str(REPO_ROOT / "Blender" / "convert_prop.py"), "--",
        str(source), str(destination),
        "-", "-",                       # 尺度は変えない（元が実寸）
        "--keep", ",".join(keep),
    ]
    # **文字コードを明示する**（`fetch_opengameart.py` と同じ理由。既定の
    # cp932 では Blender の UTF-8 ログを読めずにスレッドごと落ちる）。
    result = subprocess.run(command, capture_output=True, text=True,
                            encoding="utf-8", errors="replace")
    for line in result.stdout.splitlines():
        if line.startswith("[convert]"):
            print("   " + line)
    if result.returncode != 0 or not destination.is_file():
        # **黙って進まない**（憲法ルール6）。
        sys.stderr.write(result.stdout[-4000:] + "\n" + result.stderr[-4000:] + "\n")
        raise RuntimeError("{} の変換に失敗した".format(source.name))


def fetch(asset_id: str, plan: dict, resolution: str) -> dict:
    info = get_json("%s/info/%s" % (API, asset_id))
    files = get_json("%s/files/%s" % (API, asset_id))
    resolution, entry = gltf_entry(files, resolution)
    if entry is None:
        raise RuntimeError("%s に gltf が無い" % asset_id)

    staging = Path(tempfile.mkdtemp(prefix="ph_%s_" % asset_id))
    try:
        gltf_path = staging / Path(entry["url"]).name
        source_bytes = download(entry["url"], gltf_path, entry.get("md5"))
        for rel, spec in entry.get("include", {}).items():
            source_bytes += download(spec["url"], staging / rel, spec.get("md5"))
        print("%s: 配布物 %.1f MB を取得（一時領域 %s）"
              % (asset_id, source_bytes / 1e6, staging))

        whole, parts = measure_gltf(gltf_path)
        print("   配布物の実寸: %.2f x %.2f x %.2f m / 変種 %d 個"
              % (whole[0], whole[1], whole[2], len(parts)))
        for name in sorted(parts):
            mark = "残す" if any(name.startswith(k) for k in plan["keep"]) else "捨てる"
            print("     %-4s %-34s %.2f x %.2f x %.2f m"
                  % (mark, name, parts[name][0], parts[name][1], parts[name][2]))

        missing = [k for k in plan["keep"] if not any(n.startswith(k) for n in parts)]
        if missing:
            # **黙って進まない。** ノード名が変わったのに気づかず、
            # 「なぜか空の glb」を置くのを防ぐ。
            raise RuntimeError(
                "%s: --keep %s に一致するノードが配布物に無い（ある: %s）"
                % (asset_id, ", ".join(missing), ", ".join(sorted(parts))))

        folder = DEST_ROOT / asset_id
        folder.mkdir(parents=True, exist_ok=True)
        glb = folder / (asset_id + ".glb")
        blender_convert(gltf_path, glb, plan["keep"])
    finally:
        shutil.rmtree(staging, ignore_errors=True)

    kept_whole, kept_parts = measure_gltf(glb)
    print("   書き出し後の実寸: %.2f x %.2f x %.2f m (%.1f MB)"
          % (kept_whole[0], kept_whole[1], kept_whole[2], glb.stat().st_size / 1e6))

    record = {
        "name": info.get("name"),
        "authors": info.get("authors"),
        "license": "CC0",
        "source": "https://polyhaven.com/a/%s" % asset_id,
        "kind": "models",
        "resolution": resolution,
        "polycount": info.get("polycount"),
        "gltf": glb.name,
        "size_m": kept_whole,
        "size_source": "measured_from_gltf",
        "fetched": datetime.date.today().isoformat(),
        "bytes": glb.stat().st_size,
        # **選別したことを manifest に残す。** 「配布物と中身が違う」ことが
        # ファイルを見ただけで分かるようにする（憲法ルール2）。
        "trimmed": {
            "kept": plan["keep"],
            "why": plan["why"],
            "source_size_m": whole,
            "source_parts_m": parts,
            "source_bytes": source_bytes,
            "source_gltf": Path(entry["url"]).name,
            "source_md5": entry.get("md5"),
            "modification": "変種の選別のみ。頂点の間引き・拡大縮小はしていない",
        },
    }
    if len(kept_parts) > 1:
        record["parts_m"] = kept_parts

    # **ライセンスをアセットの隣にも置く**（`fetch_opengameart.py` と同じ）。
    (folder / "LICENSE.txt").write_text(
        "{name}\n"
        "Author : {authors}\n"
        "License: CC0\n"
        "Source : {source}\n"
        "File   : {url}\n"
        "md5    : {md5}\n"
        "Fetched: {fetched}\n"
        "\n"
        "取得と変換: Tools/fetch_polyhaven_trees.py\n"
        "変換の内容: 配布物に入っている {total} 本のうち {kept} だけを残して GLB 化した。\n"
        "            頂点の間引き（Decimate）と拡大縮小はしていない。\n"
        "            詳細は Docs/PHASE15_DATA_LICENCE.md §6.4\n".format(
            name=info.get("name"),
            authors=", ".join("%s (%s)" % (k, v) for k, v in (info.get("authors") or {}).items()),
            source=record["source"], url=entry["url"], md5=entry.get("md5"),
            fetched=record["fetched"], total=len(parts), kept=", ".join(plan["keep"])),
        encoding="utf-8")
    return record


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("ids", nargs="*", help="取り込むもの（既定: 全部）")
    parser.add_argument("--resolution", default="1k")
    args = parser.parse_args(argv)

    ids = args.ids or sorted(ASSETS)
    unknown = [i for i in ids if i not in ASSETS]
    if unknown:
        parser.error("表に無い: %s（ある: %s）"
                     % (", ".join(unknown), ", ".join(sorted(ASSETS))))

    manifest_path = DEST_ROOT / "manifest.json"
    manifest = {}
    if manifest_path.is_file():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    for asset_id in ids:
        manifest[asset_id] = fetch(asset_id, ASSETS[asset_id], args.resolution)

    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("manifest: %s (%d 件)" % (manifest_path.relative_to(REPO_ROOT), len(manifest)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
