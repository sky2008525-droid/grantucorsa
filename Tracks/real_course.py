"""実在コースの GPS トレースと標高からコース定義を作る（Phase 15）。

    python Tracks/real_course.py Tracks/Import/course.gpx --width 12.0

**このリポジトリに実在コースのデータは1バイトも入っていない。**
理由と根拠は `Docs/PHASE15_DATA_LICENCE.md`。要点だけ:

- 国土地理院の DEM は測量法に基づく手続きが要る。**そのまま再配布できない**
- OpenStreetMap は ODbL。share-alike が派生データベースに伝播する
- 「ネットで拾った gpx」は出典ではない（憲法ルール2）

## ライセンス宣言を必須にしてある

入力ファイルと同じディレクトリに `licence.json` が要る。無ければ
`LicenceMissingError` で止まる。**既定値を作らない**（ルール1・14）。

```json
{
  "source": "自分で 2026-08-30 に走行して記録",
  "licence": "記録者本人",
  "attribution": "",
  "retrieved": "2026-08-30"
}
```

宣言は出力の `_meta.licence` にそのまま写る。**コース定義を見れば
出所が分かる**状態を保つため。

## 座標変換について

WGS84 の緯度経度を、コース中心を原点とする局所平面へ写す（等距円筒近似）。

    x = (lon - lon0) * cos(lat0) * R
    y = (lat - lat0) * R

**この近似はコースの大きさなら十分だが、無条件ではない。**
南北 10 km で東西方向に約 0.1% の縮尺誤差が出る（cos(lat) が緯度で
変わるため）。サーキット1周は長くても数 km なので、誤差は数十 cm。
`build_track` は範囲がこれを超えたら**警告ではなく失敗**する。
黙って歪んだコースを作るより、止まったほうがよい（ルール6）。

## 標高

2通り受け付ける。**どちらも無ければ標高なしで作る**（平らなコースになる）。

1. GPX の `<ele>` タグ。GPS の高さは水平位置より精度が悪い（数 m 級）
   ので、そのままだと路面が波打つ。移動平均で均す
2. ESRI ASCII グリッド（`.asc`）。gdal_translate や国土地理院の変換
   ツールが出せる**テキスト形式**なので、外部ライブラリ無しで読める

**GeoTIFF は読まない。** 読むには GDAL が要り、依存を増やす割に、
`gdal_translate -of AAIGrid` で `.asc` に落とせば済む。
"""

from __future__ import annotations

import argparse
import json
import math
import sys
import xml.etree.ElementTree as ElementTree
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

#: 地球半径 [m]（WGS84 の平均半径）。
#: **測地学的に正確な変換ではない。** 等距円筒近似に使うだけ。
EARTH_RADIUS_M = 6371008.8

#: 等距円筒近似を許す範囲 [m]。これを超えたら失敗する。
#: 10 km で東西方向に約 0.1%（10 m）の縮尺誤差が出る。
MAX_EXTENT_M = 10000.0

#: ライセンス宣言に必須の項目。**1つでも欠けたら止まる。**
REQUIRED_LICENCE_FIELDS = ("source", "licence", "attribution", "retrieved")


class LicenceMissingError(Exception):
    """ライセンス宣言が無い、または不完全。

    **既定値で埋めない。** 出所の分からないデータを、出所が分かるふりを
    して取り込むほうが害が大きい（`Docs/PHASE15_DATA_LICENCE.md`）。
    """


@dataclass(frozen=True)
class TracePoint:
    lat_deg: float
    lon_deg: float
    ele_m: Optional[float]


# --- ライセンス -------------------------------------------------------------


def read_licence(input_path: Path) -> Dict[str, str]:
    """入力と同じディレクトリの `licence.json` を読む。**無ければ止まる。**"""
    path = Path(input_path).resolve().parent / "licence.json"
    if not path.exists():
        raise LicenceMissingError(
            "ライセンス宣言が無い: {}\n"
            "実在コースのデータは出所によって再配布条件が違う。\n"
            "Docs/PHASE15_DATA_LICENCE.md を読み、次の形で作ること:\n"
            '  {{"source": "...", "licence": "...", '
            '"attribution": "...", "retrieved": "YYYY-MM-DD"}}'.format(path)
        )

    with open(path, encoding="utf-8") as handle:
        declared = json.load(handle)

    missing = [field for field in REQUIRED_LICENCE_FIELDS if field not in declared]
    if missing:
        raise LicenceMissingError(
            "{} に必須項目が無い: {}".format(path, ", ".join(missing)))

    # attribution は空でよい（自分で記録した場合など）が、
    # source と licence は空を認めない。**「不明」は宣言ではない。**
    for field in ("source", "licence", "retrieved"):
        if not str(declared[field]).strip():
            raise LicenceMissingError("{} の {} が空".format(path, field))

    return {field: declared[field] for field in REQUIRED_LICENCE_FIELDS}


# --- GPX --------------------------------------------------------------------


def read_gpx(path: Path) -> List[TracePoint]:
    """GPX のトラックポイントを読む。

    名前空間は宣言されているものを使う。**決め打ちしない**
    （GPX 1.0 と 1.1 で URI が違う）。
    """
    tree = ElementTree.parse(str(path))
    root = tree.getroot()

    namespace = ""
    if root.tag.startswith("{"):
        namespace = root.tag[: root.tag.index("}") + 1]

    points: List[TracePoint] = []
    for node in root.iter(namespace + "trkpt"):
        if "lat" not in node.attrib or "lon" not in node.attrib:
            raise ValueError("trkpt に lat/lon が無い")

        elevation = node.find(namespace + "ele")
        ele_m = None
        if elevation is not None and elevation.text:
            ele_m = float(elevation.text)

        points.append(TracePoint(
            lat_deg=float(node.attrib["lat"]),
            lon_deg=float(node.attrib["lon"]),
            ele_m=ele_m,
        ))

    if len(points) < 3:
        raise ValueError("トラックポイントが {} 個しかない".format(len(points)))
    return points


# --- 座標変換 ---------------------------------------------------------------


class LocalFrame:
    """緯度経度をコース中心の局所平面へ写す（等距円筒近似）。

    **測地学的に正しい投影ではない。** サーキット程度の広がりでのみ使う。
    """

    def __init__(self, lat0_deg: float, lon0_deg: float) -> None:
        self.lat0_deg = lat0_deg
        self.lon0_deg = lon0_deg
        self._cos_lat0 = math.cos(math.radians(lat0_deg))

    def to_local(self, lat_deg: float, lon_deg: float) -> Tuple[float, float]:
        """(x_m, y_m)。**x が東、y が北。**

        物理側の座標系（x 前方 / y 左）とは別物。コースの向きは
        走る方向で決まるので、ここでは地図の向きのまま返す。
        """
        x_m = math.radians(lon_deg - self.lon0_deg) * self._cos_lat0 * EARTH_RADIUS_M
        y_m = math.radians(lat_deg - self.lat0_deg) * EARTH_RADIUS_M
        return x_m, y_m


# --- 中心線 -----------------------------------------------------------------


def resample_centreline(xy: Sequence[Tuple[float, float]], spacing_m: float,
                        closed: bool) -> List[Tuple[float, float]]:
    """折れ線を等間隔に取り直す。

    GPS のログは間隔がまちまち（速度によって 1 点あたり数 cm 〜数十 m）。
    **そのまま使うと、直線区間だけ点が粗くなる。**
    """
    if spacing_m <= 0.0:
        raise ValueError("間隔が正でない: {}".format(spacing_m))

    points = list(xy)
    if closed:
        points.append(points[0])

    # 累積距離
    distances = [0.0]
    for (x0, y0), (x1, y1) in zip(points, points[1:]):
        distances.append(distances[-1] + math.hypot(x1 - x0, y1 - y0))

    total_m = distances[-1]
    if total_m <= spacing_m:
        raise ValueError("コースが短すぎる: {:.1f} m".format(total_m))

    out: List[Tuple[float, float]] = []
    index = 0
    steps = int(total_m // spacing_m)
    for step in range(steps):
        target = step * spacing_m
        while index + 1 < len(distances) and distances[index + 1] < target:
            index += 1
        span = distances[index + 1] - distances[index]
        ratio = 0.0 if span <= 0.0 else (target - distances[index]) / span
        x0, y0 = points[index]
        x1, y1 = points[index + 1]
        out.append((x0 + (x1 - x0) * ratio, y0 + (y1 - y0) * ratio))

    return out


def smooth(values: Sequence[float], window: int, closed: bool) -> List[float]:
    """移動平均。**GPS の高さは水平位置より精度が悪い**ので均す。

    窓は奇数にする（前後対称でないと中心線がずれる）。
    """
    if window <= 1:
        return list(values)
    if window % 2 == 0:
        window += 1

    half = window // 2
    count = len(values)
    out = []
    for index in range(count):
        total = 0.0
        used = 0
        for offset in range(-half, half + 1):
            position = index + offset
            if closed:
                position %= count
            elif position < 0 or position >= count:
                continue
            total += values[position]
            used += 1
        out.append(total / used)
    return out


def headings_and_curvature(xy: Sequence[Tuple[float, float]], spacing_m: float,
                           closed: bool) -> Tuple[List[float], List[float]]:
    """各点の方位 [rad] と曲率 [1/m]。

    曲率は方位の差分から出す。**折り返し（±pi をまたぐ）を必ず正す。**
    正さないと、直線区間で曲率が 2*pi/spacing の巨大な値になる。
    """
    count = len(xy)
    headings = []
    for index in range(count):
        nxt = (index + 1) % count if closed else min(index + 1, count - 1)
        prv = index if closed else index
        if not closed and index == count - 1:
            prv = count - 2
            nxt = count - 1
        x0, y0 = xy[prv]
        x1, y1 = xy[nxt]
        headings.append(math.atan2(y1 - y0, x1 - x0))

    curvature = []
    for index in range(count):
        nxt = (index + 1) % count if closed else min(index + 1, count - 1)
        delta = headings[nxt] - headings[index]
        # **-pi..pi へ畳む。** ここを忘れると直線が急コーナーになる。
        delta = (delta + math.pi) % (2.0 * math.pi) - math.pi
        curvature.append(delta / spacing_m)

    return headings, curvature


# --- ESRI ASCII グリッド -----------------------------------------------------


class AsciiGrid:
    """ESRI ASCII グリッド（`.asc`）。**テキストなので依存が要らない。**"""

    def __init__(self, path: Path) -> None:
        header: Dict[str, float] = {}
        rows: List[List[float]] = []

        with open(path, encoding="utf-8") as handle:
            for line in handle:
                parts = line.split()
                if not parts:
                    continue
                key = parts[0].lower()
                if key in ("ncols", "nrows", "xllcorner", "yllcorner",
                           "xllcenter", "yllcenter", "cellsize",
                           "nodata_value") and len(parts) == 2:
                    header[key] = float(parts[1])
                    continue
                rows.append([float(value) for value in parts])

        for required in ("ncols", "nrows", "cellsize"):
            if required not in header:
                raise ValueError("{} に {} が無い".format(path, required))

        self.ncols = int(header["ncols"])
        self.nrows = int(header["nrows"])
        self.cellsize = header["cellsize"]
        self.nodata = header.get("nodata_value", -9999.0)
        # xllcenter でも xllcorner でも受ける
        self.xll = header.get("xllcorner", header.get("xllcenter", 0.0))
        self.yll = header.get("yllcorner", header.get("yllcenter", 0.0))

        flat = [value for row in rows for value in row]
        if len(flat) != self.ncols * self.nrows:
            raise ValueError(
                "{} の格子数が合わない: {} 個（{}x{} を期待）".format(
                    path, len(flat), self.ncols, self.nrows))

        # ASCII グリッドは**上の行から**並ぶ
        self.values = [flat[row * self.ncols:(row + 1) * self.ncols]
                       for row in range(self.nrows)]

    def sample(self, lon_deg: float, lat_deg: float) -> Optional[float]:
        """最近傍。**範囲外や nodata では None を返す。**

        0 を返さない。標高 0 m は海面であって「不明」ではない。
        """
        col = int((lon_deg - self.xll) / self.cellsize)
        row = int((self.yll + self.nrows * self.cellsize - lat_deg) / self.cellsize)
        if not (0 <= col < self.ncols and 0 <= row < self.nrows):
            return None
        value = self.values[row][col]
        if value == self.nodata:
            return None
        return value


# --- 組み立て ---------------------------------------------------------------


def build_track(gpx_path: Path, name: str = None, width_m: float = 12.0,
                shoulder_m: float = 6.0, spacing_m: float = 1.0,
                closed: bool = True, elevation_grid: Path = None,
                elevation_smoothing: int = 21) -> dict:
    """GPX からコース定義（`physics_test_track.json` と同じ形）を作る。

    **ライセンス宣言が無ければ止まる。**
    """
    gpx_path = Path(gpx_path)
    licence = read_licence(gpx_path)          # ここで止まりうる
    trace = read_gpx(gpx_path)

    lat0 = sum(point.lat_deg for point in trace) / len(trace)
    lon0 = sum(point.lon_deg for point in trace) / len(trace)
    frame = LocalFrame(lat0, lon0)

    xy = [frame.to_local(point.lat_deg, point.lon_deg) for point in trace]

    # **近似が効く範囲かを確かめる。** 黙って歪んだコースを作らない。
    extent_x = max(x for x, _ in xy) - min(x for x, _ in xy)
    extent_y = max(y for _, y in xy) - min(y for _, y in xy)
    if max(extent_x, extent_y) > MAX_EXTENT_M:
        raise ValueError(
            "範囲が広すぎる（{:.0f} x {:.0f} m）。等距円筒近似の縮尺誤差が"
            "無視できない。適切な投影を使うこと".format(extent_x, extent_y))

    resampled = resample_centreline(xy, spacing_m, closed)
    headings, curvature = headings_and_curvature(resampled, spacing_m, closed)

    # --- 標高 ---
    grid = AsciiGrid(Path(elevation_grid)) if elevation_grid else None
    elevations: List[Optional[float]] = []

    for index in range(len(resampled)):
        # 取り直した点に対応する緯度経度を、元のトレースから最も近い点で拾う。
        # **点を作り直したので、標高も取り直す必要がある。**
        x_m, y_m = resampled[index]
        nearest = min(range(len(xy)),
                      key=lambda i: (xy[i][0] - x_m) ** 2 + (xy[i][1] - y_m) ** 2)

        value = None
        if grid is not None:
            value = grid.sample(trace[nearest].lon_deg, trace[nearest].lat_deg)
        if value is None:
            value = trace[nearest].ele_m
        elevations.append(value)

    known = [value for value in elevations if value is not None]
    if known:
        # 欠けたところは、両隣が分かっていないと埋められない。
        # **平均で埋めない。** 分からないところは分からないまま残し、
        # 基準面をずらすだけにする。
        base = sum(known) / len(known)
        filled = [base if value is None else value for value in elevations]
        smoothed = smooth(filled, elevation_smoothing, closed)
        z_values = [value - base for value in smoothed]
        elevation_source = "ascii_grid" if grid is not None else "gpx_ele"
        missing = sum(1 for value in elevations if value is None)
    else:
        z_values = [0.0] * len(resampled)
        elevation_source = "none"
        missing = len(elevations)

    points = []
    for index, ((x_m, y_m), z_m) in enumerate(zip(resampled, z_values)):
        points.append({
            "s_m": index * spacing_m,
            "x_m": x_m,
            "y_m": y_m,
            "z_m": z_m,
            "heading_rad": headings[index],
            "curvature_1pm": curvature[index],
            "label": "imported",
        })

    return {
        "_meta": {
            "generated_by": "Tracks/real_course.py",
            "source_file": gpx_path.name,
            # **出所をコース定義に埋め込む。** これを見れば分かる状態にする。
            "licence": licence,
            "projection": (
                "equirectangular about ({:.6f}, {:.6f}); "
                "測地学的に正確な投影ではない".format(lat0, lon0)),
            "elevation": {
                "source": elevation_source,
                "missing_points": missing,
                "smoothing_window": elevation_smoothing,
                "note": ("GPS の高さは水平位置より精度が悪い（数 m 級）。"
                         "移動平均で均してある。**実測の路面高さではない。**"),
            },
            "warning": (
                "実在コースのデータは出所によって再配布条件が違う。"
                "Docs/PHASE15_DATA_LICENCE.md を読むこと。"),
        },
        "name": name or gpx_path.stem,
        "length_m": len(resampled) * spacing_m,
        "width_m": width_m,
        "shoulder_m": shoulder_m,
        "tree_offset_m": width_m / 2.0 + shoulder_m + 4.0,
        "spacing_m": spacing_m,
        "closure": "closed" if closed else "open",
        "sections": [{"label": "imported", "s_start_m": 0.0,
                      "s_end_m": len(resampled) * spacing_m}],
        "points": points,
    }


def main(argv: Sequence[str] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("gpx", type=Path, help="GPS トレース（.gpx）")
    parser.add_argument("--out", type=Path, default=None,
                        help="出力先（既定: Tracks/Import/<名前>.json）")
    parser.add_argument("--name", default=None)
    parser.add_argument("--width", type=float, default=12.0, help="路面幅 [m]")
    parser.add_argument("--shoulder", type=float, default=6.0, help="路肩 [m]")
    parser.add_argument("--spacing", type=float, default=1.0, help="点の間隔 [m]")
    parser.add_argument("--open", action="store_true",
                        help="周回コースでない（始点と終点を繋がない）")
    parser.add_argument("--elevation", type=Path, default=None,
                        help="ESRI ASCII グリッド（.asc）")
    args = parser.parse_args(argv)

    try:
        track = build_track(
            args.gpx, name=args.name, width_m=args.width,
            shoulder_m=args.shoulder, spacing_m=args.spacing,
            closed=not args.open, elevation_grid=args.elevation)
    except LicenceMissingError as error:
        # **握りつぶさない。** 何が足りないかを見せて止まる。
        print(error, file=sys.stderr)
        return 2

    out = args.out or (args.gpx.parent / (track["name"] + ".json"))
    with open(out, "w", encoding="utf-8") as handle:
        json.dump(track, handle, ensure_ascii=False, indent=2)

    print("書き出した: {}".format(out))
    print("  全長 {:.1f} m / 点 {} 個 / 標高 {}".format(
        track["length_m"], len(track["points"]),
        track["_meta"]["elevation"]["source"]))
    print("  出典: {}".format(track["_meta"]["licence"]["source"]))
    print("  ライセンス: {}".format(track["_meta"]["licence"]["licence"]))
    if track["_meta"]["licence"]["attribution"]:
        print("  表示すべき帰属: {}".format(track["_meta"]["licence"]["attribution"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
