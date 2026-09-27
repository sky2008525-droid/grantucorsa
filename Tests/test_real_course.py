"""実在コースの取り込みの検査（Phase 15）.

**実データを1バイトも置かない。** テストの中で合成した GPX を使う。
理由は `Docs/PHASE15_DATA_LICENCE.md`。出所の分からないトレースを
リポジトリに入れないための決まりを、テスト自身も守る。

検査するのは:

1. **ライセンス宣言が無ければ止まるか**（既定値で埋めないこと）
2. 座標変換が測った距離と合うか
3. **曲率の折り返しを正しているか**（直線が急コーナーにならないこと）
4. 標高が無いときに 0 をでっち上げていないか
"""

from __future__ import annotations

import json
import math

import pytest

from real_course import (AsciiGrid, LicenceMissingError, LocalFrame,
                         build_track, headings_and_curvature, read_gpx,
                         read_licence, resample_centreline)

GOOD_LICENCE = {
    "source": "テストの中で合成したトレース",
    "licence": "このリポジトリ",
    "attribution": "",
    "retrieved": "2026-09-01",
}


def write_gpx(path, points):
    """(lat, lon, ele) の並びから GPX を書く。"""
    body = "\n".join(
        '      <trkpt lat="{:.9f}" lon="{:.9f}">{}</trkpt>'.format(
            lat, lon,
            "" if ele is None else "<ele>{:.3f}</ele>".format(ele))
        for lat, lon, ele in points)

    path.write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<gpx version="1.1" creator="test" '
        'xmlns="http://www.topografix.com/GPX/1/1">\n'
        '  <trk><trkseg>\n' + body + '\n'
        '  </trkseg></trk>\n</gpx>\n', encoding="utf-8")
    return path


def make_circle(tmp_path, radius_m=200.0, count=360, lat0=35.37, lon0=138.73,
                elevation=None, with_licence=True):
    """半径 `radius_m` の円周を走ったトレース。**曲率が既知。**"""
    metres_per_deg = 111195.0
    points = []
    for index in range(count):
        angle = 2.0 * math.pi * index / count
        dy = radius_m * math.sin(angle)
        dx = radius_m * math.cos(angle)
        lat = lat0 + dy / metres_per_deg
        lon = lon0 + dx / (metres_per_deg * math.cos(math.radians(lat0)))
        ele = None if elevation is None else elevation(angle)
        points.append((lat, lon, ele))

    gpx = write_gpx(tmp_path / "course.gpx", points)
    if with_licence:
        (tmp_path / "licence.json").write_text(
            json.dumps(GOOD_LICENCE, ensure_ascii=False), encoding="utf-8")
    return gpx


# --- ライセンス -------------------------------------------------------------


def test_ライセンス宣言が無ければ止まる(tmp_path):
    """**既定値で埋めない**（憲法ルール1・14）。

    出所の分からないデータを、分かるふりをして取り込むほうが害が大きい。
    """
    gpx = make_circle(tmp_path, with_licence=False)
    with pytest.raises(LicenceMissingError) as error:
        build_track(gpx)
    assert "licence.json" in str(error.value)


def test_必須項目が欠けたら止まる(tmp_path):
    gpx = make_circle(tmp_path)
    for missing in ("source", "licence", "attribution", "retrieved"):
        partial = {k: v for k, v in GOOD_LICENCE.items() if k != missing}
        (tmp_path / "licence.json").write_text(
            json.dumps(partial, ensure_ascii=False), encoding="utf-8")
        with pytest.raises(LicenceMissingError) as error:
            read_licence(gpx)
        assert missing in str(error.value)


def test_出所が空なら止まる(tmp_path):
    """**「不明」は宣言ではない。** 空文字を通すと宣言の意味が無くなる。"""
    gpx = make_circle(tmp_path)
    for field in ("source", "licence", "retrieved"):
        blank = dict(GOOD_LICENCE, **{field: "   "})
        (tmp_path / "licence.json").write_text(
            json.dumps(blank, ensure_ascii=False), encoding="utf-8")
        with pytest.raises(LicenceMissingError):
            read_licence(gpx)


def test_帰属が空でも通る(tmp_path):
    """自分で記録した場合、表示すべき帰属は無い。"""
    gpx = make_circle(tmp_path)
    licence = read_licence(gpx)
    assert licence["attribution"] == ""
    assert licence["source"]


def test_出所がコース定義に残る(tmp_path):
    """**コース定義を見れば出所が分かること。**"""
    gpx = make_circle(tmp_path)
    track = build_track(gpx)
    assert track["_meta"]["licence"] == GOOD_LICENCE


# --- 座標変換 ---------------------------------------------------------------


def test_局所座標が実距離と合う():
    """**緯度1度は約111 km。** 桁が違えばコース全体の縮尺が狂う。"""
    frame = LocalFrame(35.37, 138.73)
    x_m, y_m = frame.to_local(35.37 + 1.0 / 111195.0, 138.73)
    assert y_m == pytest.approx(1.0, rel=0.01), "北へ1mのはずが {:.4f}".format(y_m)
    assert abs(x_m) < 1e-9

    # 経線は緯度が上がると詰まる。**cos(lat) を忘れると東西が伸びる。**
    x_m, _ = frame.to_local(35.37, 138.73 + 1.0 / 111195.0)
    assert x_m == pytest.approx(math.cos(math.radians(35.37)), rel=0.01)


def test_原点は中心付近(tmp_path):
    gpx = make_circle(tmp_path, radius_m=200.0)
    track = build_track(gpx)
    xs = [point["x_m"] for point in track["points"]]
    ys = [point["y_m"] for point in track["points"]]
    assert abs(sum(xs) / len(xs)) < 5.0
    assert abs(sum(ys) / len(ys)) < 5.0


def test_広すぎる範囲を拒否する(tmp_path):
    """**黙って歪んだコースを作らない。**"""
    gpx = make_circle(tmp_path, radius_m=20000.0, count=720)
    with pytest.raises(ValueError) as error:
        build_track(gpx)
    assert "広すぎる" in str(error.value)


# --- 中心線 -----------------------------------------------------------------


def test_取り直した点が等間隔(tmp_path):
    """GPS のログは間隔がまちまち。**そのままだと直線区間が粗くなる。**"""
    gpx = make_circle(tmp_path, radius_m=200.0)
    track = build_track(gpx, spacing_m=2.0)

    points = track["points"]
    gaps = [math.hypot(b["x_m"] - a["x_m"], b["y_m"] - a["y_m"])
            for a, b in zip(points, points[1:])]
    for gap in gaps:
        assert gap == pytest.approx(2.0, rel=0.02)


def test_円の全長が2πrに近い(tmp_path):
    radius_m = 200.0
    gpx = make_circle(tmp_path, radius_m=radius_m, count=720)
    track = build_track(gpx, spacing_m=1.0)
    assert track["length_m"] == pytest.approx(2.0 * math.pi * radius_m, rel=0.01)


def test_円の曲率が1除するrに近い(tmp_path):
    """**曲率が合っていれば、方位も座標も合っている。**"""
    radius_m = 200.0
    gpx = make_circle(tmp_path, radius_m=radius_m, count=720)
    track = build_track(gpx, spacing_m=1.0)

    curvatures = [abs(point["curvature_1pm"]) for point in track["points"]]
    mean = sum(curvatures) / len(curvatures)
    assert mean == pytest.approx(1.0 / radius_m, rel=0.05), (
        "曲率 {:.6f} 1/m（1/{:.0f} = {:.6f} を期待）".format(
            mean, radius_m, 1.0 / radius_m)
    )


def test_直線の曲率がゼロ():
    """**±pi の折り返しを正していないと、直線が急コーナーになる。**

    方位が -pi と +pi を行き来する向き（西向き）の直線で起きる。
    """
    # 西向き（-x 方向）の直線。方位が ±pi の境目にあたる。
    straight = [(-float(index), 0.0) for index in range(200)]
    headings, curvature = headings_and_curvature(straight, 1.0, closed=False)

    largest = max(abs(value) for value in curvature)
    assert largest < 1e-9, "直線の曲率が {:.6f} 1/m".format(largest)


def test_短すぎるコースを拒否する():
    with pytest.raises(ValueError):
        resample_centreline([(0.0, 0.0), (1.0, 0.0)], spacing_m=10.0, closed=False)


# --- 標高 -------------------------------------------------------------------


def test_標高が無ければ平らでsourceがnone(tmp_path):
    """**0 をでっち上げない。** 標高が無いことを記録に残す。"""
    gpx = make_circle(tmp_path, elevation=None)
    track = build_track(gpx)
    assert track["_meta"]["elevation"]["source"] == "none"
    assert all(point["z_m"] == 0.0 for point in track["points"])
    assert track["_meta"]["elevation"]["missing_points"] == len(track["points"])


def test_gpxの標高を読む(tmp_path):
    """1周で 10 m 上下する円。**均しても山と谷が残ること。**"""
    gpx = make_circle(tmp_path, radius_m=300.0, count=720,
                      elevation=lambda angle: 500.0 + 10.0 * math.sin(angle))
    track = build_track(gpx, spacing_m=1.0)

    assert track["_meta"]["elevation"]["source"] == "gpx_ele"
    zs = [point["z_m"] for point in track["points"]]
    # 基準面を引いてあるので平均は 0 付近
    assert abs(sum(zs) / len(zs)) < 0.5
    # 振幅は移動平均で少し減るが、消えはしない
    assert 15.0 < (max(zs) - min(zs)) < 20.0


def test_標高を均している(tmp_path):
    """**GPS の高さは数 m 級の誤差を持つ。** 均さないと路面が波打つ。"""
    def noisy(angle):
        # 1点おきに ±2 m 跳ねる（GPS の高さのばらつきを模したもの）
        return 500.0 + (2.0 if int(angle * 1000) % 2 else -2.0)

    gpx = make_circle(tmp_path, radius_m=300.0, count=720, elevation=noisy)
    smoothed = build_track(gpx, spacing_m=1.0, elevation_smoothing=21)
    raw = build_track(gpx, spacing_m=1.0, elevation_smoothing=1)

    def roughness(track):
        zs = [point["z_m"] for point in track["points"]]
        return sum(abs(b - a) for a, b in zip(zs, zs[1:])) / len(zs)

    assert roughness(smoothed) < roughness(raw) / 2.0


# --- ESRI ASCII グリッド -----------------------------------------------------


def test_asciiグリッドを読む(tmp_path):
    path = tmp_path / "dem.asc"
    path.write_text(
        "ncols 3\nnrows 2\nxllcorner 138.0\nyllcorner 35.0\n"
        "cellsize 0.5\nNODATA_value -9999\n"
        "10 20 30\n40 50 -9999\n", encoding="utf-8")

    grid = AsciiGrid(path)
    assert grid.ncols == 3 and grid.nrows == 2

    # 上の行が北。左下角が (138.0, 35.0)、格子 0.5 度。
    # 経度 138.0-138.5 / 138.5-139.0 / 139.0-139.5 が列 0/1/2、
    # 緯度 35.5-36.0 が行 0（北）、35.0-35.5 が行 1（南）。
    assert grid.sample(138.25, 35.75) == 10.0   # 北西 = 10
    assert grid.sample(139.25, 35.75) == 30.0   # 北東 = 30
    assert grid.sample(138.75, 35.25) == 50.0   # 南の中央 = 50

    # **nodata と範囲外では None。** 0 を返さない（標高 0 m は海面）。
    assert grid.sample(139.25, 35.25) is None, "NODATA を値として返している"
    assert grid.sample(200.0, 35.5) is None
    assert grid.sample(138.25, 20.0) is None


def test_格子数が合わなければ止まる(tmp_path):
    path = tmp_path / "broken.asc"
    path.write_text("ncols 3\nnrows 2\ncellsize 1\n10 20 30\n", encoding="utf-8")
    with pytest.raises(ValueError) as error:
        AsciiGrid(path)
    assert "格子数" in str(error.value)


def test_グリッドの標高を優先する(tmp_path):
    """グリッドがあれば GPX の高さより優先する（測量値のほうが正確）。"""
    gpx = make_circle(tmp_path, radius_m=200.0, count=360,
                      elevation=lambda angle: 100.0)

    grid_path = tmp_path / "dem.asc"
    grid_path.write_text(
        "ncols 2\nnrows 2\nxllcorner 138.0\nyllcorner 35.0\n"
        "cellsize 1.0\nNODATA_value -9999\n"
        "700 700\n700 700\n", encoding="utf-8")

    track = build_track(gpx, elevation_grid=grid_path)
    assert track["_meta"]["elevation"]["source"] == "ascii_grid"


# --- 出力の形 ---------------------------------------------------------------


def test_既存のコース定義と同じ形(tmp_path):
    """`Tracks/physics_test_track.json` を読む側がそのまま使えること。"""
    gpx = make_circle(tmp_path)
    track = build_track(gpx)

    for key in ("name", "length_m", "width_m", "shoulder_m", "spacing_m",
                "closure", "sections", "points"):
        assert key in track, "{} が無い".format(key)

    for key in ("s_m", "x_m", "y_m", "heading_rad", "curvature_1pm", "label"):
        assert key in track["points"][0], "点に {} が無い".format(key)


def test_gpxの点が少なすぎたら止まる(tmp_path):
    gpx = write_gpx(tmp_path / "short.gpx", [(35.0, 138.0, None)])
    with pytest.raises(ValueError):
        read_gpx(gpx)
