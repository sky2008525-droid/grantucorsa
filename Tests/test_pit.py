# -*- coding: utf-8 -*-
"""ピットレーンの検査.

指摘: 「本物のサーキットはどのように配置しているのか（略）
**すくなくともピットくらいはあるってわかると思ったのですが**」

見るのは:

1. メインストレート（いちばん長い直線）に沿っているか
2. **コースの外側**に出ているか。内側だとインフィールドを潰す
3. 端がテーパーで本線に繋がっているか。繋がっていないと
   「横に浮いた板」に見える
4. **短いコースには作らないこと。** 幅 9 m の峠にピットレーンは無い
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import pytest

from pit import (MIN_PIT_STRAIGHT_M, PIT_LANE_WIDTH_M, PIT_WALL_THICKNESS_M,
                 garage_positions, plan_pit_lane)
from track_catalogue import CATALOGUE, build

REPO_ROOT = Path(__file__).resolve().parent.parent


def _points(key):
    track = build(key)
    return [
        {
            "s_m": p.s_m, "x_m": p.x_m, "y_m": p.y_m,
            "heading_rad": p.heading_rad, "curvature_1pm": p.curvature_1pm,
            "z_m": p.z_m,
        }
        for p in track.points
    ], track


@pytest.mark.parametrize("key", sorted(CATALOGUE))
def test_直線に沿っている(key):
    points, track = _points(key)
    spacing = points[1]["s_m"] - points[0]["s_m"]
    lane = plan_pit_lane(points, spacing)
    if lane is None:
        return

    # 区間の全点が直線であること
    for index in lane.indices:
        assert abs(points[index]["curvature_1pm"]) < 1e-9, (
            "{}: ピットレーンがコーナー上にある".format(key))

    assert lane.length_m * spacing >= MIN_PIT_STRAIGHT_M


@pytest.mark.parametrize("key", sorted(CATALOGUE))
def test_コースの外側に出る(key):
    """**内側に出すとインフィールドを潰す。**

    コース全体の重心が内側なので、レーンは重心と反対側にあるはず。
    """
    points, track = _points(key)
    spacing = points[1]["s_m"] - points[0]["s_m"]
    lane = plan_pit_lane(points, spacing)
    if lane is None:
        return

    cx = sum(p["x_m"] for p in points) / len(points)
    cy = sum(p["y_m"] for p in points) / len(points)

    middle = lane.indices[len(lane.indices) // 2]
    p = points[middle]
    heading = p["heading_rad"]
    nx = -math.sin(heading) * lane.side
    ny = math.cos(heading) * lane.side

    # レーン側へ向かうベクトルと、重心へ向かうベクトルが逆を向くこと
    to_centre = (cx - p["x_m"], cy - p["y_m"])
    assert nx * to_centre[0] + ny * to_centre[1] < 0.0, (
        "{}: ピットレーンがコースの内側に出ている".format(key))


@pytest.mark.parametrize("key", sorted(CATALOGUE))
def test_端がテーパーで本線に戻る(key):
    points, track = _points(key)
    spacing = points[1]["s_m"] - points[0]["s_m"]
    lane = plan_pit_lane(points, spacing)
    if lane is None:
        return

    count = len(lane.indices)
    assert lane.offset_at(0) == pytest.approx(0.0, abs=1e-9), (
        "入口が本線に繋がっていない")
    assert lane.offset_at(count - 1) == pytest.approx(0.0, abs=1e-9), (
        "出口が本線に繋がっていない")

    full = PIT_WALL_THICKNESS_M + PIT_LANE_WIDTH_M / 2.0
    assert lane.offset_at(count // 2) == pytest.approx(full), (
        "真ん中でレーンが本線から離れていない")

    # **単調に離れて、単調に戻ること。** 途中で寄ったり離れたりしない。
    first_half = [lane.offset_at(i) for i in range(count // 2)]
    assert all(b >= a - 1e-9 for a, b in zip(first_half, first_half[1:]))


def test_短いコースには作らない():
    """幅 9 m の峠にピットレーンがあったらおかしい。"""
    points, _ = _points("mountain_pass")
    spacing = points[1]["s_m"] - points[0]["s_m"]

    # 直線をすべてコーナーに書き換えると、ピットは作れなくなる
    bent = [dict(p, curvature_1pm=1.0 / 40.0) for p in points]
    assert plan_pit_lane(bent, spacing) is None


def test_建屋がレーンの外に並ぶ():
    points, _ = _points("technical_circuit")
    spacing = points[1]["s_m"] - points[0]["s_m"]
    lane = plan_pit_lane(points, spacing)
    assert lane is not None

    garages = garage_positions(lane, points, spacing)
    assert len(garages) >= 3, "建屋が {} 棟しかない".format(len(garages))

    # 建屋は本線から「壁 + レーン幅」だけ離れている
    expected = PIT_WALL_THICKNESS_M + PIT_LANE_WIDTH_M
    for garage in garages:
        index = min(range(len(points)),
                    key=lambda i: (points[i]["x_m"] - garage["x_m"]) ** 2
                    + (points[i]["y_m"] - garage["y_m"]) ** 2)
        p = points[index]
        distance = math.hypot(garage["x_m"] - p["x_m"], garage["y_m"] - p["y_m"])
        assert abs(distance - expected) < 1.5, (
            "建屋が本線から {:.1f} m（想定 {:.1f} m）".format(distance, expected))


def test_サーキットだけがピットを持つ():
    """**ピットビルを持つのはサーキットだけ。**

    峠にも都市高速にもピットは無い。
    """
    from environment import environment_for

    assert environment_for("technical_circuit").pit_building is not None
    for key in ("mountain_pass", "high_speed_ring", "physics_test_track"):
        assert environment_for(key).pit_building is None, key
