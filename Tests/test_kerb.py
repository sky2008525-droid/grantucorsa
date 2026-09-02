"""縁石とパイロンを「どこに置くか」の検査.

**メッシュは検査しない。** メッシュを作るのは `Blender/build_track.py` で、
bpy が要る。ここで縛るのは `Tracks/kerb.py` の区間の決め方だけ。

守らせたいのは 2 つ。

1. **直線に縁石を敷かない。** 敷くと赤白の帯が延々続き、コーナーが
   どこか分からなくなる
2. **設計上のコーナーを取りこぼさない。** しきい値を上げすぎると、
   高速コース（R = 120〜150 m）の縁石が全部消える。消えても
   「縁石を実装した」とは言えてしまうので、機械的に止める
"""

from __future__ import annotations

import math

import pytest

from kerb import (CONE_EXIT_FRACTION, KERB_CURVATURE_1PM, KERB_HEIGHT_M,
                  KERB_LEAD_M, KERB_STRIPES_PER_TILE, KERB_TILE_LENGTH_M,
                  KERB_WIDTH_M, corner_exit_indices, kerb_spans)
from track_catalogue import CATALOGUE, build

SPACING_M = 1.0


@pytest.fixture(scope="module")
def tracks():
    return {key: build(key, SPACING_M) for key in sorted(CATALOGUE)}


def curvatures(track):
    return [point.curvature_1pm for point in track.points]


# --- 区間の決め方 -------------------------------------------------------------


@pytest.mark.parametrize("key", sorted(CATALOGUE))
def test_縁石は必ず1本以上できる(tracks, key):
    """周回コースなので、必ずコーナーがある。"""
    spans = kerb_spans(curvatures(tracks[key]), SPACING_M)
    assert spans, "{}: 縁石の区間が 0 本".format(key)


@pytest.mark.parametrize("key", sorted(CATALOGUE))
def test_縁石はコーナーを全て覆う(tracks, key):
    """**しきい値を上げすぎて拾い落としていないか。**

    `kerb_spans()` はコーナーを前後へ伸ばすので、コーナーの点は
    1 つ残らず区間に入っていなければならない。
    """
    values = curvatures(tracks[key])
    covered = set()
    for span in kerb_spans(values, SPACING_M):
        covered.update(span)

    missed = [index for index, value in enumerate(values)
              if abs(value) >= KERB_CURVATURE_1PM and index not in covered]
    assert not missed, "{}: コーナーなのに縁石が無い点が {} 個".format(key, len(missed))


@pytest.mark.parametrize("key", sorted(CATALOGUE))
def test_長い直線の真ん中には縁石が無い(tracks, key):
    """**直線に敷かない。**

    コーナーの前後 `KERB_LEAD_M` は意図して伸ばしてあるので、
    「直線の点に 1 つも掛からない」ではなく「伸ばした量より内側に
    入り込んでいない」で見る。
    """
    values = curvatures(tracks[key])
    count = len(values)
    covered = set()
    for span in kerb_spans(values, SPACING_M):
        covered.update(span)

    lead = int(round(KERB_LEAD_M / SPACING_M))
    for index, value in enumerate(values):
        if abs(value) >= KERB_CURVATURE_1PM or index not in covered:
            continue
        # 直線なのに覆われている点は、必ず lead 以内にコーナーがある
        near = any(abs(values[(index + offset) % count]) >= KERB_CURVATURE_1PM
                   for offset in range(-lead, lead + 1))
        assert near, (
            "{}: 直線の点 {} に縁石がある（近くにコーナーが無い）".format(key, index))


def test_周回の閉合をまたぐコーナーが2本に割れない():
    """**始点がコーナーの中にある場合**、区間が末尾と先頭に割れてはいけない。

    割れると、スタートラインの上で縁石が途切れて見える。
    """
    # 全周がコーナー（半径一定の円）に近い並びを作る
    values = [1.0 / 50.0] * 40 + [0.0] * 20 + [1.0 / 50.0] * 40
    spans = kerb_spans(values, SPACING_M)
    assert len(spans) == 1, "末尾と先頭のコーナーが繋がっていない: {} 本".format(len(spans))
    assert 0 in spans[0] and len(values) - 1 in spans[0]


def test_全部が直線なら縁石は無い():
    assert kerb_spans([0.0] * 50, SPACING_M) == []


def test_全部がコーナーなら1本になる():
    spans = kerb_spans([1.0 / 30.0] * 50, SPACING_M)
    assert len(spans) == 1
    assert len(spans[0]) == 50


# --- パイロン -----------------------------------------------------------------


@pytest.mark.parametrize("key", sorted(CATALOGUE))
def test_パイロンはコーナーの中にしか置かない(tracks, key):
    """直線や、コーナーの手前には置かない。"""
    values = curvatures(tracks[key])
    for index in corner_exit_indices(values):
        assert abs(values[index]) >= KERB_CURVATURE_1PM, (
            "{}: 直線の点 {} が立ち上がり扱いになっている".format(key, index))


@pytest.mark.parametrize("key", sorted(CATALOGUE))
def test_パイロンは立ち上がり側に寄っている(tracks, key):
    """**コーナーの後半だけ。** 進入側に置くとブレーキの目標物に見える。"""
    values = curvatures(tracks[key])
    corner = [index for index, value in enumerate(values)
              if abs(value) >= KERB_CURVATURE_1PM]
    exits = corner_exit_indices(values)
    assert exits, "{}: 立ち上がりが 1 点も無い".format(key)
    # 取りすぎていない（コーナー全体の割合と大きく離れない）
    ratio = len(exits) / len(corner)
    assert ratio == pytest.approx(CONE_EXIT_FRACTION, abs=0.10), (
        "{}: 立ち上がりの割合が {:.2f}（設計 {:.2f}）".format(
            key, ratio, CONE_EXIT_FRACTION))


# --- 寸法 ---------------------------------------------------------------------


def test_縁石の寸法が現実の範囲にある():
    """**当たり判定が無いので高くしない。**

    物理は平面3自由度で、車は z = 0 を走り続ける。高い縁石を置いても
    車はすり抜けるので、見た目に厚みがある以上の高さに意味が無い。
    実際のサーキットの縁石も 5〜7 cm。
    """
    assert 0.03 <= KERB_HEIGHT_M <= 0.08
    assert 0.4 <= KERB_WIDTH_M <= 1.6


def test_縞の間隔が現実の範囲にある():
    """1 ブロックが 0.5〜1.5 m。実際のサーキットの縞に合わせる。"""
    block_m = KERB_TILE_LENGTH_M / KERB_STRIPES_PER_TILE
    assert 0.5 <= block_m <= 1.5
    # 赤白が交互なので、1 枚のタイルに入る縞は偶数でなければ繋がらない
    assert KERB_STRIPES_PER_TILE % 2 == 0


def test_しきい値がコース設計の隙間にある(tracks):
    """**設計上のコーナーを全て拾い、直線を 1 点も拾わない**位置にあるか。

    `Tracks/kerb.py` がしきい値 180 m を選んだ根拠そのもの。
    コース定義の半径を変えたらここで落ちる。
    """
    tightest_straight = 0.0
    loosest_corner = math.inf
    for track in tracks.values():
        for point in track.points:
            value = abs(point.curvature_1pm)
            if value == 0.0:
                tightest_straight = max(tightest_straight, value)
            else:
                loosest_corner = min(loosest_corner, value)

    assert tightest_straight < KERB_CURVATURE_1PM < loosest_corner, (
        "しきい値 {:.5f} が直線 {:.5f} とコーナー {:.5f} の間に無い".format(
            KERB_CURVATURE_1PM, tightest_straight, loosest_corner))
