"""コース一覧の検査.

**「走れるか」を数値で見る。** 形が気に入るかは主観だが、
以下は主観ではない:

1. **閉じているか。** 開いていると周回にならず、終端で中心線が
   始点へ飛ぶ（路面メッシュに巨大な三角形が出る）
2. **曲率が飛んでいないか。** 直線とコーナーを角で繋ぐと、そこで
   ステアリングが不連続になり車が跳ねる
3. **車が曲がれる半径か。** 最小回転半径より小さいコーナーは通れない
4. **実在コースを名乗っていないか**（`Docs/PHASE15_DATA_LICENCE.md`）
"""

from __future__ import annotations

import math

import pytest

from physics_test_track import closure_error
from track_catalogue import (CATALOGUE, ClosureError, build,
                             solve_closed_track, summary)
from vehicle_data import VehicleData


@pytest.fixture(scope="module")
def data():
    return VehicleData()


ALL_KEYS = sorted(CATALOGUE)


def test_一覧が空でない():
    assert len(CATALOGUE) >= 4, "コースが少ない（面白みが無い）"


@pytest.mark.parametrize("key", ALL_KEYS)
def test_閉じている(key):
    """**開いていると周回にならない。**

    路面メッシュは最後の点を先頭へ繋ぐので、ずれが大きいと
    スタートラインに大きな裂け目ができる。
    """
    track = build(key)
    position_error, heading_error = closure_error(track)

    spacing = track.points[1].s_m - track.points[0].s_m
    assert position_error < 1.6 * spacing, (
        "{}: 位置が {:.3f} m ずれている".format(key, position_error))
    assert abs(heading_error) < math.radians(0.5), (
        "{}: 方位が {:.4f} rad ずれている".format(key, heading_error))


@pytest.mark.parametrize("key", ALL_KEYS)
def test_点が等間隔(key):
    track = build(key)
    spacing = track.points[1].s_m - track.points[0].s_m
    for previous, current in zip(track.points, track.points[1:]):
        step = math.hypot(current.x_m - previous.x_m, current.y_m - previous.y_m)
        assert step == pytest.approx(spacing, rel=0.02)


@pytest.mark.parametrize("key", ALL_KEYS)
def test_曲率が飛ばない(key):
    """**直線とコーナーを角で繋ぐと、そこで車が跳ねる。**

    円弧の入口では曲率が 0 から 1/R へ一段で変わる。これは設計どおりで、
    実車もクロソイドを挟まなければ同じ。ここで見るのは
    「**一段の大きさが車の限界を超えていないか**」。

    ステアリングは有限の速さでしか切れないので、1 点（1 m）で
    要求される舵角の変化が現実的な範囲に収まっている必要がある。
    """
    track = build(key)
    spacing = track.points[1].s_m - track.points[0].s_m

    worst = 0.0
    for previous, current in zip(track.points, track.points[1:]):
        worst = max(worst, abs(current.curvature_1pm - previous.curvature_1pm))

    # 1/R の最大の飛びが 1/20 m^-1 を超えないこと（R=20 m のコーナー入口）
    assert worst <= 1.0 / 19.0, (
        "{}: 曲率が 1 点で {:.4f} 1/m 飛ぶ（間隔 {:.1f} m）"
        .format(key, worst, spacing))


@pytest.mark.parametrize("key", ALL_KEYS)
def test_車が曲がれる半径(key, data):
    """**最小回転半径より小さいコーナーは物理的に通れない。**"""
    turning_radius_m = data.value("dimensions.min_turning_radius", "m")

    track = build(key)
    curvatures = [abs(p.curvature_1pm) for p in track.points
                  if p.curvature_1pm != 0.0]
    assert curvatures, "{}: コーナーが1つも無い".format(key)

    tightest_m = 1.0 / max(curvatures)
    assert tightest_m > turning_radius_m, (
        "{}: 最小 R が {:.1f} m で、最小回転半径 {:.1f} m を下回る"
        .format(key, tightest_m, turning_radius_m))


@pytest.mark.parametrize("key", ALL_KEYS)
def test_幅と長さが現実的(key):
    track = build(key)
    assert 6.0 <= track.width_m <= 20.0, "{}: 幅 {:.1f} m".format(key, track.width_m)
    assert 300.0 <= track.length_m <= 8000.0, (
        "{}: 全長 {:.1f} m".format(key, track.length_m))


def test_性格が分かれている():
    """**同じような形を並べても走り分けにならない。**

    最小 R と全長で、はっきり違うものが揃っていること。
    """
    tightest = {}
    lengths = {}
    for key in ALL_KEYS:
        track = build(key)
        curvatures = [abs(p.curvature_1pm) for p in track.points
                      if p.curvature_1pm != 0.0]
        tightest[key] = 1.0 / max(curvatures)
        lengths[key] = track.length_m

    # いちばんきついコースと、いちばん緩いコースが 3 倍以上違う
    assert max(tightest.values()) / min(tightest.values()) > 3.0, (
        "最小 R がどれも似ている: {}".format(
            {k: round(v) for k, v in tightest.items()}))
    # 全長も 3 倍以上違う
    assert max(lengths.values()) / min(lengths.values()) > 3.0, (
        "全長がどれも似ている: {}".format({k: round(v) for k, v in lengths.items()}))


def test_実在コースを名乗らない():
    """**ライセンス上の判断**（`Docs/PHASE15_DATA_LICENCE.md`）。

    レイアウトを模していないのだから、名前でも模しているように
    見せないこと。
    """
    forbidden = ("首都高", "つくば", "鈴鹿", "富士", "筑波", "菅生",
                 "suzuka", "tsukuba", "fuji", "nurburgring", "spa",
                 "monza", "silverstone", "laguna", "shuto")
    for key in ALL_KEYS:
        name = build(key).name.lower()
        for word in forbidden:
            assert word.lower() not in name and word.lower() not in key.lower(), (
                "{}: 実在コースの名前を含む（{}）".format(key, word))


# --- 閉合を解く仕組み -------------------------------------------------------


def test_総旋回角が360度でなければ止まる():
    """**向きが一周していなければ、直線をどう伸ばしても閉じない。**"""
    segments = [
        ("free", 0.0, "a"),
        ("arc", 30.0, 90.0, "corner"),
        ("free", 0.0, "b"),
        ("straight", 50.0, "c"),
    ]
    with pytest.raises(ClosureError):
        solve_closed_track(segments, "Not Closed")


def test_自由直線が2本でなければ止まる():
    """位置の閉合は x と y の 2 条件。1 本では足りない。"""
    segments = [
        ("free", 0.0, "a"),
        ("arc", 30.0, 360.0, "loop"),
    ]
    with pytest.raises(ClosureError):
        solve_closed_track(segments, "One Free")


def test_平行な自由直線を拒否する():
    """**同じ向きの 2 本では閉合を解けない**（det = 0）。"""
    segments = [
        ("free", 0.0, "a"),
        ("arc", 40.0, 180.0, "u turn"),
        ("straight", 100.0, "back"),
        ("arc", 40.0, 180.0, "u turn 2"),
        ("free", 0.0, "b"),          # 一周して先頭と同じ向き
    ]
    with pytest.raises(ClosureError):
        solve_closed_track(segments, "Parallel")


def test_知らないコース名で止まる():
    with pytest.raises(KeyError):
        build("nurburgring")


def test_要約が読める():
    for key in ALL_KEYS:
        text = summary(build(key))
        assert "全長" in text and "最小R" in text
