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
import datetime
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


# ---------------------------------------------------------------------------
# 実寸の測定
#
# **API の `dimensions` を実寸として使ってはいけない。**
# 2026-09-02 に実測して分かったこと: あれは「ファイルに入っている全部を
# 囲む箱」で、変種を横に並べてある資産では X が実物の何倍にもなる。
#
#   pine_tree_01        API 26.72 x 1.30 x 17.58 m
#                       実際  木が3本（幅 7.6 m）横並び。高さ 17.58 m だけが正しい
#   modular_electricity_poles  API の高さ 7.00 m / 実際は 10.04 m
#
# **高さ（API の Z）ですら合わないものがある。** だから落とした glTF の
# 頂点の min/max から測る。ここを間違えると「若木を拡大して大木のふりを
# する」のと同じことを、数字の側で繰り返すことになる。
# ---------------------------------------------------------------------------

def _matrix_multiply(a, b):
    return [[sum(a[i][k] * b[k][j] for k in range(4)) for j in range(4)]
            for i in range(4)]


def _node_matrix(node):
    """glTF ノードのローカル行列。`matrix` があればそれ、無ければ TRS。"""
    if "matrix" in node:
        m = node["matrix"]                 # glTF は列優先
        return [[m[0], m[4], m[8], m[12]],
                [m[1], m[5], m[9], m[13]],
                [m[2], m[6], m[10], m[14]],
                [m[3], m[7], m[11], m[15]]]

    tx, ty, tz = node.get("translation", (0.0, 0.0, 0.0))
    qx, qy, qz, qw = node.get("rotation", (0.0, 0.0, 0.0, 1.0))
    sx, sy, sz = node.get("scale", (1.0, 1.0, 1.0))

    rotation = [
        [1 - 2 * (qy * qy + qz * qz), 2 * (qx * qy - qz * qw), 2 * (qx * qz + qy * qw), 0.0],
        [2 * (qx * qy + qz * qw), 1 - 2 * (qx * qx + qz * qz), 2 * (qy * qz - qx * qw), 0.0],
        [2 * (qx * qz - qy * qw), 2 * (qy * qz + qx * qw), 1 - 2 * (qx * qx + qy * qy), 0.0],
        [0.0, 0.0, 0.0, 1.0],
    ]
    for row in range(3):
        rotation[row][0] *= sx
        rotation[row][1] *= sy
        rotation[row][2] *= sz
    rotation[0][3], rotation[1][3], rotation[2][3] = tx, ty, tz
    return rotation


def _apply(matrix, point):
    return [sum(matrix[i][k] * point[k] for k in range(3)) + matrix[i][3] for i in range(3)]


def _mesh_bounds(doc, mesh_index, matrix, box):
    """メッシュの各プリミティブの min/max（8隅）を変換して box に足し込む。"""
    for primitive in doc["meshes"][mesh_index].get("primitives", []):
        accessor = doc["accessors"][primitive["attributes"]["POSITION"]]
        low, high = accessor.get("min"), accessor.get("max")
        if not low or not high:
            continue
        for i in range(8):
            corner = [high[axis] if (i >> axis) & 1 else low[axis] for axis in range(3)]
            world = _apply(matrix, corner)
            for axis in range(3):
                box[0][axis] = min(box[0][axis], world[axis])
                box[1][axis] = max(box[1][axis], world[axis])


def _walk(doc, index, parent, box):
    node = doc["nodes"][index]
    matrix = _matrix_multiply(parent, _node_matrix(node))
    if "mesh" in node:
        _mesh_bounds(doc, node["mesh"], matrix, box)
    for child in node.get("children", []):
        _walk(doc, child, matrix, box)
    return matrix


def _empty_box():
    return [[1e30, 1e30, 1e30], [-1e30, -1e30, -1e30]]


def _size_zup(box):
    """glTF は Y 上。manifest には [幅, 奥行, 高さ] の順（Z 上）で書く。"""
    if box[0][0] > box[1][0]:
        return None
    dx, dy, dz = (box[1][axis] - box[0][axis] for axis in range(3))
    return [round(dx, 3), round(dz, 3), round(dy, 3)]


def _read_gltf_json(path: Path):
    """.gltf（JSON）と .glb（バイナリ）のどちらからも JSON を取り出す。"""
    if path.suffix.lower() != ".glb":
        return json.loads(path.read_text(encoding="utf-8"))

    data = path.read_bytes()
    if data[:4] != b"glTF":
        raise RuntimeError("GLB のマジックが違う: %s" % path)
    offset = 12                                    # ヘッダ 12 バイトの後
    while offset + 8 <= len(data):
        length = int.from_bytes(data[offset:offset + 4], "little")
        kind = data[offset + 4:offset + 8]
        body = data[offset + 8:offset + 8 + length]
        if kind == b"JSON":
            return json.loads(body.decode("utf-8"))
        offset += 8 + length
    raise RuntimeError("GLB に JSON チャンクが無い: %s" % path)


def measure_gltf(path: Path):
    """落とした glTF / GLB から実寸を測る。

    @return (全体の [幅, 奥行, 高さ] m, {トップレベルノード名: 同じ})

    **部品ごとにも出す。** 変種が横並びになっている資産（fir_tree_01 の
    a / b / c など）は、全体の箱を見ても1本の木の背丈が分からない。
    """
    doc = _read_gltf_json(path)
    roots = []
    scenes = doc.get("scenes") or []
    if scenes:
        roots = scenes[doc.get("scene", 0)].get("nodes", [])
    if not roots:
        roots = list(range(len(doc.get("nodes", []))))

    identity = [[1.0 if i == j else 0.0 for j in range(4)] for i in range(4)]
    whole = _empty_box()
    parts = {}
    for index in roots:
        box = _empty_box()
        _walk(doc, index, identity, box)
        size = _size_zup(box)
        if size is None:
            continue
        name = doc["nodes"][index].get("name") or ("node_%d" % index)
        parts[name] = size
        for axis in range(3):
            whole[0][axis] = min(whole[0][axis], box[0][axis])
            whole[1][axis] = max(whole[1][axis], box[1][axis])
    return _size_zup(whole), parts


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
            gltf_path = base / Path(entry["url"]).name
            written = download(entry["url"], gltf_path, entry.get("md5"))
            for rel, spec in entry.get("include", {}).items():
                written += download(spec["url"], base / rel, spec.get("md5"))
            whole, parts = measure_gltf(gltf_path)
            record.update({
                "resolution": resolution,
                "polycount": info.get("polycount"),
                "gltf": gltf_path.name,
                # **実測値。** API の dimensions ではない（measure_gltf の注記）。
                "size_m": whole,
                "size_source": "measured_from_gltf",
            })
            # **部品ごとの寸法は多いと読めない。** modular_* は 100 個を
            # 超える（modular_urban_apartments_facade で 147）。数が多い
            # ときは「いちばん背の高い部品」だけ残す。**配置側が知りたい
            # のは「1本の木が何 m か」であって、ボルトの寸法ではない。**
            record["size_m"] = whole
            if len(parts) > 1:
                tallest = max(parts.items(), key=lambda kv: kv[1][2])
                record["tallest_part"] = {"name": tallest[0], "size_m": tallest[1]}
                if len(parts) <= 12:
                    record["parts_m"] = parts
                else:
                    record["part_count"] = len(parts)
            print("  実寸: %.2f x %.2f x %.2f m（幅 x 奥行 x 高さ / 全体）" % tuple(whole))
            if len(parts) > 1:
                print("        部品 %d 個。最も高い %s = %.2f x %.2f x %.2f m"
                      % tuple([len(parts), tallest[0]] + tallest[1]))

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
        record["fetched"] = datetime.date.today().isoformat()
        manifest[asset_id] = record
        print("  -> %s (%.1f MB)" % (base.relative_to(REPO_ROOT), written / 1e6))

    DEST_ROOT.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print()
    print("manifest: %s (%d 件)" % (manifest_path.relative_to(REPO_ROOT), len(manifest)))
    return 0


def cmd_verify(args):
    """**置いてあるファイルを PolyHaven の md5 と突き合わせる。**

    `download()` は取得のたびに検証しているが、その結果を残していない。
    後から「このファイルは壊れていないか」「配布物と同じものか」を
    確かめる手段が無いと、`Docs/PHASE15_DATA_LICENCE.md` に md5 を
    書けない（憲法ルール2: 出典を書けないものは置かない）。

    ここで各ファイルの md5 を計算し、API の値と比べ、manifest に
    `md5` として書き戻す。**合わなかったものは manifest に書かない。**
    """
    manifest_path = DEST_ROOT / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    checked, mismatched, missing, unknown = 0, [], [], []
    for asset_id, record in sorted(manifest.items()):
        if record.get("trimmed"):
            # 変種を選んで作り直したもの。**配布物と中身が違うので、
            # 配布物の md5 と比べても合わない**（`trimmed.source_md5`
            # に配布物側の値が入っている）。
            continue
        kind = record.get("kind", "models")
        base = DEST_ROOT / asset_id
        try:
            files = get_json("%s/files/%s" % (API, asset_id))
        except Exception as exc:                              # noqa: BLE001
            unknown.append("%s: API %s" % (asset_id, exc))
            continue

        expected = {}
        if kind == "models":
            _, entry = gltf_entry(files, record.get("resolution", "1k"))
            if entry:
                expected[Path(entry["url"]).name] = entry.get("md5")
                for rel, spec in entry.get("include", {}).items():
                    expected[rel] = spec.get("md5")
        elif kind == "hdris":
            _, entry = hdri_entry(files, record.get("resolution", "4k"))
            if entry:
                expected[Path(entry["url"]).name] = entry.get("md5")
        else:
            for _, _, spec in texture_entries(files, record.get("resolution", "2k")):
                expected[Path(spec["url"]).name] = spec.get("md5")

        got = {}
        for rel, want in expected.items():
            path = base / rel
            if not path.is_file():
                missing.append("%s/%s" % (asset_id, rel))
                continue
            have = hashlib.md5(path.read_bytes()).hexdigest()
            checked += 1
            if want and have != want:
                mismatched.append("%s/%s (期待 %s / 実際 %s)" % (asset_id, rel, want, have))
            else:
                got[rel] = have
        if got and len(got) == len(expected):
            record["md5"] = got

    print("照合: %d ファイル / 不一致 %d / 欠落 %d"
          % (checked, len(mismatched), len(missing)))
    for line in mismatched + missing + unknown:
        print("  !! " + line, file=sys.stderr)
    if mismatched or missing or unknown:
        # **黙って通さない**（憲法ルール6）。
        return 1

    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("manifest に md5 を書いた: %s" % manifest_path.relative_to(REPO_ROOT))
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

    verify = sub.add_parser("verify", help="置いてあるファイルの md5 を照合し manifest に書く")
    verify.set_defaults(func=cmd_verify)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
