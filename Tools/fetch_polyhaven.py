#!/usr/bin/env python3
"""PolyHaven から CC0 のモデル・HDRI・テクスチャを取得する.

    python3 Tools/fetch_polyhaven.py survey trees          # 候補と容量を調べる
    python3 Tools/fetch_polyhaven.py fetch <id> [<id>...]  # 取得する

**なぜ PolyHaven を使うか**

コース周辺の木や地面を自分でモデリングしない、という方針のため。
候補は Fab（Epic）と PolyHaven だが:

  - Fab は個人アカウントの GUI 操作が要り、スクリプトから取得できない
  - PolyHaven は API が認証不要で、**全て CC0**（帰属表示すら不要）

再現性のために PolyHaven を採る。**CC0 なのでライセンス上の制約が無く、
リポジトリへの同梱も配布も自由**（`Docs/SPEC_PHASE2_BACKLOG.md` §8 が
懸念していたライセンス問題が、この選択で消える）。

**容量に注意すること。** PolyHaven のモデルはフォトグラメトリで、
同じ「木」でもジオメトリが 2 MB のものと 208 MB のものがある
（jacaranda_tree の .bin は 1k テクスチャ指定でも 208 MB）。
survey で測ってから取ること。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DEST_ROOT = REPO_ROOT / "Tracks" / "Assets" / "polyhaven"

API = "https://api.polyhaven.com"

# **User-Agent を付けること。** 既定の python-urllib は 403 で弾かれる。
HEADERS = {"User-Agent": "grantucorsa/1.0 (ZN6 digital twin; contact via repo)"}


def get_json(url):
    request = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(request, timeout=120) as response:
        return json.load(response)


def download(url, dest: Path, expect_md5=None):
    dest.parent.mkdir(parents=True, exist_ok=True)
    request = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(request, timeout=600) as response:
        data = response.read()

    if expect_md5:
        got = hashlib.md5(data).hexdigest()
        if got != expect_md5:
            # **壊れたファイルを置かない。** 後段の Blender が読めずに
            # 落ちたとき、原因が転送エラーだと分からなくなる。
            raise RuntimeError(
                "md5 が一致しない: %s (期待 %s / 実際 %s)" % (url, expect_md5, got)
            )

    dest.write_bytes(data)
    return len(data)


# アセット種別ごとの取り出し方。
#
# **PolyHaven は種別で JSON の形が違う。**
#   models   -> files["gltf"][解像度]["gltf"]      （+ include に .bin とテクスチャ）
#   hdris    -> files["hdri"][解像度]["hdr"|"exr"]
#   textures -> files[マップ名][解像度][拡張子]     （マップが複数に分かれる）
ASSET_KINDS = ("models", "hdris", "textures")


def gltf_entry(files, resolution):
    """gltf の指定解像度エントリを返す。無ければ利用可能な最小のものへ落とす。"""
    gltf = files.get("gltf")
    if not gltf:
        return None, None
    if resolution in gltf:
        return resolution, gltf[resolution]["gltf"]
    available = sorted(gltf.keys())
    if not available:
        return None, None
    return available[0], gltf[available[0]]["gltf"]


def hdri_entry(files, resolution):
    """HDRI の指定解像度エントリを返す。**exr より hdr を優先**（UE が読める）。"""
    hdri = files.get("hdri")
    if not hdri:
        return None, None
    key = resolution if resolution in hdri else sorted(hdri.keys())[0]
    formats = hdri[key]
    for extension in ("hdr", "exr"):
        if extension in formats:
            return key, formats[extension]
    return None, None


def texture_entries(files, resolution):
    """テクスチャの各マップ（Diffuse / nor_gl / arm 等）を列挙する。

    **必要なマップだけを取る。** Displacement や AO 単体まで取ると容量が
    倍増するうえ、UE 側では arm（AO/Roughness/Metallic 合成）で足りる。

    **マップ名の表記が資産ごとに違う。** `Diffuse` と `diff` の両方が
    存在し、大文字小文字も揃っていない。一度これを見落として、
    **拡散マップ抜きの（＝真っ黒になる）テクスチャ一式を取得してしまった。**
    小文字化して突き合わせること。
    """
    wanted = {"diffuse", "diff", "nor_gl", "arm", "rough"}
    out = []
    for map_name, resolutions in files.items():
        if map_name.lower() not in wanted or not isinstance(resolutions, dict):
            continue
        key = resolution if resolution in resolutions else sorted(resolutions.keys())[0]
        formats = resolutions[key]
        for extension in ("jpg", "png", "exr"):
            if extension in formats:
                out.append((map_name, key, formats[extension]))
                break
    return out


def total_size(entry):
    size = entry.get("size", 0)
    size += sum(v.get("size", 0) for v in entry.get("include", {}).values())
    return size


def cmd_survey(args):
    assets = get_json("%s/assets?t=%s&c=%s" % (API, args.kind, args.category))
    print("カテゴリ '%s': %d 件" % (args.category, len(assets)))
    print()
    rows = []
    for asset_id, meta in assets.items():
        try:
            files = get_json("%s/files/%s" % (API, asset_id))
            if args.kind == "models":
                resolution, entry = gltf_entry(files, args.resolution)
                size = total_size(entry) if entry else None
            elif args.kind == "hdris":
                resolution, entry = hdri_entry(files, args.resolution)
                size = entry.get("size", 0) if entry else None
            else:
                entries = texture_entries(files, args.resolution)
                entry = entries or None
                resolution = args.resolution
                size = sum(spec.get("size", 0) for _, _, spec in entries)

            if entry is None:
                rows.append((float("inf"), asset_id, meta, "%s 無し" % args.kind))
                continue
            rows.append((size, asset_id, meta, resolution))
        except Exception as exc:                      # noqa: BLE001
            # **握りつぶさない。** どれが取れなかったかを出す。
            rows.append((float("inf"), asset_id, meta, "ERROR %s" % exc))

    print("%10s  %-26s %10s  %s" % ("容量", "id", "polycount", "名前 / 備考"))
    for size, asset_id, meta, note in sorted(rows, key=lambda r: r[0]):
        size_text = "----" if size == float("inf") else "%.1f MB" % (size / 1e6)
        print("%10s  %-26s %10s  %s [%s]"
              % (size_text, asset_id, meta.get("polycount"), meta.get("name"), note))
    return 0


def cmd_fetch(args):
    manifest_path = DEST_ROOT / "manifest.json"
    manifest = {}
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    for asset_id in args.ids:
        info = get_json("%s/info/%s" % (API, asset_id))
        files = get_json("%s/files/%s" % (API, asset_id))
        base = DEST_ROOT / asset_id
        record = {
            "name": info.get("name"),
            "authors": info.get("authors"),
            "license": "CC0",
            "source": "https://polyhaven.com/a/%s" % asset_id,
            "kind": args.kind,
        }

        if args.kind == "models":
            resolution, entry = gltf_entry(files, args.resolution)
            if entry is None:
                print("ERROR: %s に gltf が無い" % asset_id, file=sys.stderr)
                return 1
            size = total_size(entry)
            if size > args.max_bytes:
                print("SKIP: %s は %.1f MB で上限 %.1f MB を超える"
                      % (asset_id, size / 1e6, args.max_bytes / 1e6))
                continue
            print("取得中: %s (%s, %.1f MB)" % (asset_id, resolution, size / 1e6))
            written = download(entry["url"], base / Path(entry["url"]).name, entry.get("md5"))
            for rel, spec in entry.get("include", {}).items():
                written += download(spec["url"], base / rel, spec.get("md5"))
            record.update({
                "resolution": resolution,
                "polycount": info.get("polycount"),
                "gltf": Path(entry["url"]).name,
            })

        elif args.kind == "hdris":
            resolution, entry = hdri_entry(files, args.resolution)
            if entry is None:
                print("ERROR: %s に hdri が無い" % asset_id, file=sys.stderr)
                return 1
            print("取得中: %s (%s, %.1f MB)"
                  % (asset_id, resolution, entry.get("size", 0) / 1e6))
            name = Path(entry["url"]).name
            written = download(entry["url"], base / name, entry.get("md5"))
            record.update({"resolution": resolution, "file": name})

        else:  # textures
            entries = texture_entries(files, args.resolution)
            if not entries:
                print("ERROR: %s にテクスチャが無い" % asset_id, file=sys.stderr)
                return 1
            written = 0
            maps = {}
            for map_name, resolution, spec in entries:
                name = Path(spec["url"]).name
                written += download(spec["url"], base / name, spec.get("md5"))
                maps[map_name] = name
            print("取得中: %s (%d マップ, %.1f MB)" % (asset_id, len(maps), written / 1e6))
            record.update({"resolution": args.resolution, "maps": maps})

        record["bytes"] = written
        manifest[asset_id] = record
        print("  -> %s (%.1f MB)" % (base.relative_to(REPO_ROOT), written / 1e6))

    DEST_ROOT.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print()
    print("manifest: %s (%d 件)" % (manifest_path.relative_to(REPO_ROOT), len(manifest)))
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description="PolyHaven から CC0 アセットを取得する")
    sub = parser.add_subparsers(dest="command", required=True)

    survey = sub.add_parser("survey", help="候補と容量を調べる")
    survey.add_argument("category", default="trees", nargs="?")
    survey.add_argument("--kind", default="models", choices=ASSET_KINDS)
    survey.add_argument("--resolution", default="1k")
    survey.set_defaults(func=cmd_survey)

    fetch = sub.add_parser("fetch", help="取得する")
    fetch.add_argument("ids", nargs="+")
    fetch.add_argument("--kind", default="models", choices=ASSET_KINDS)
    fetch.add_argument("--resolution", default="1k")
    fetch.add_argument("--max-bytes", type=int, default=25_000_000,
                       help="1アセットあたりの上限。既定 25 MB")
    fetch.set_defaults(func=cmd_fetch)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
