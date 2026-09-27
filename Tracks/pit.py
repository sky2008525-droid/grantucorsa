# -*- coding: utf-8 -*-
"""ピットレーンをどこに引くかを決める。

**「すくなくともピットくらいはあるってわかると思ったのですが」**
という指摘への答えの前半。ここは「どこに」だけを決め、メッシュは
`Blender/build_track.py` の `build_pit_lane()` が作る。

---

## なぜ手続きで作るのか

ユーザーの方針は「自分でモデリングせず外からアセットを使う」だが、
**ピットレーンは道路である。** 路面と同じで、コースの形が決まれば
形が決まる。外から持ってきた「ピットレーンのモデル」を置いても、
このコースの直線の長さにも曲率にも合わない。

一方、**ピットビル（建屋）は建築物**なので外部アセットを置く
（`Tracks/environment.py` の `pit_building`）。

---

## どこに引くか

実際のサーキットのピットレーンは、**メインストレートに並行して、
コースの外側**にある。したがって:

1. いちばん長い直線を探す（そこがメインストレート）
2. その区間の**外側**へ、ピットウォールぶん離してレーンを引く
3. 入口と出口はテーパーで本線へ繋ぐ

「外側」は、その直線の左右どちらがコースの内側でないか、で決まる。
**コース全体の重心が中心線のどちら側にあるか**で判定する。
重心の側が内側である。

---

## 寸法の根拠

| 項目 | 値 | 根拠 |
|---|---|---|
| ピットレーン幅 | 12.0 m | **unknown。** FIA の付属書には最低幅の定めがあるが条文を確認していない。10〜15 m の桁にあることは確か |
| ピットウォール厚 | 1.0 m | 同上。人が立てる幅 |
| ガレージの間口 | 12.0 m | 同上。車 1 台と作業スペース |
| テーパー長 | 60 m | 同上 |

**どれも実測ではない**（憲法ルール1・2）。「その種類の施設として
不自然でない値」であって、実在サーキットの図面から取った値ではない。
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import List, Optional, Sequence


#: ピットレーンの幅 [m]。**unknown**（上の表を参照）。
PIT_LANE_WIDTH_M = 12.0

#: 本線とピットレーンの間の壁の厚み [m]。
PIT_WALL_THICKNESS_M = 1.0

#: ピットウォールの高さ [m]。**1.0 m。** 人が寄りかかる高さ。
PIT_WALL_HEIGHT_M = 1.05

#: 入口・出口のテーパーの長さ [m]。
PIT_TAPER_M = 60.0

#: ガレージの間口 [m]。建屋をこの間隔で並べる。
PIT_GARAGE_PITCH_M = 12.0

#: ピットレーンとして使う直線の、最低の長さ [m]。
#: **これより短いコースにはピットを作らない。**
MIN_PIT_STRAIGHT_M = 150.0


@dataclass
class PitLane:
    """ピットレーンの区間。

    `indices` は中心線の添字（本線に沿った並び）。`side` は本線から
    見てどちら側か（+1 が左、-1 が右。物理の座標系で Y が左）。
    """

    indices: List[int]
    side: float
    #: 入口側・出口側のテーパーに使う点数。
    taper_points: int

    @property
    def length_m(self) -> float:
        return float(len(self.indices))

    def offset_at(self, position: int) -> float:
        """区間の `position` 番目で、本線からどれだけ離すか [m]。

        **端はテーパーで 0 に戻す。** 戻さないと、レーンが本線の横に
        いきなり現れて、繋がっていない板に見える。
        """
        full = (PIT_WALL_THICKNESS_M + PIT_LANE_WIDTH_M / 2.0)
        count = len(self.indices)
        if self.taper_points <= 0:
            return full
        if position < self.taper_points:
            t = position / self.taper_points
        elif position >= count - self.taper_points:
            t = (count - 1 - position) / self.taper_points
        else:
            return full
        t = max(0.0, min(1.0, t))
        return full * (t * t * (3.0 - 2.0 * t))          # smoothstep


def _straight_spans(curvature: Sequence[float],
                    spacing_m: float) -> List[List[int]]:
    """曲率がほぼ 0 の連続区間。**直線の一覧。**"""
    spans: List[List[int]] = []
    current: List[int] = []
    for index, k in enumerate(curvature):
        if abs(k) < 1e-9:
            current.append(index)
        else:
            if current:
                spans.append(current)
            current = []
    if current:
        spans.append(current)
    return spans


def _outward_side(xs: Sequence[float], ys: Sequence[float],
                  span: Sequence[int], headings: Sequence[float]) -> float:
    """その直線から見て、コースの外側はどちらか（+1 左 / -1 右）。

    **コース全体の重心が内側**である。重心が左にあれば外側は右。
    """
    cx = sum(xs) / len(xs)
    cy = sum(ys) / len(ys)

    middle = span[len(span) // 2]
    heading = headings[middle]
    # 左手法線
    nx = -math.sin(heading)
    ny = math.cos(heading)

    to_centre_x = cx - xs[middle]
    to_centre_y = cy - ys[middle]
    # 重心が左手側にあれば内積が正 -> 内側が左 -> 外側は右
    return -1.0 if (nx * to_centre_x + ny * to_centre_y) > 0.0 else 1.0


def plan_pit_lane(points, spacing_m: float) -> Optional[PitLane]:
    """いちばん長い直線の外側にピットレーンを引く。

    直線が短すぎるコースでは `None` を返す。**無理に作らない。**
    幅 9 m の峠にピットレーンがあったらおかしい。
    """
    curvature = [p["curvature_1pm"] for p in points]
    xs = [p["x_m"] for p in points]
    ys = [p["y_m"] for p in points]
    headings = [p["heading_rad"] for p in points]

    spans = _straight_spans(curvature, spacing_m)
    if not spans:
        return None

    longest = max(spans, key=len)
    if len(longest) * spacing_m < MIN_PIT_STRAIGHT_M:
        return None

    side = _outward_side(xs, ys, longest, headings)
    taper = int(PIT_TAPER_M / spacing_m)
    # テーパーが区間の半分を超えるなら、平らな部分が残らない。
    taper = min(taper, max(1, len(longest) // 3))
    return PitLane(indices=list(longest), side=side, taper_points=taper)


def garage_positions(lane: PitLane, points, spacing_m: float):
    """ガレージ（建屋）を置く位置と向きを返す。

    レーンのさらに外側に、間口ぶんの間隔で並べる。
    """
    full = PIT_WALL_THICKNESS_M + PIT_LANE_WIDTH_M
    step = max(int(PIT_GARAGE_PITCH_M / spacing_m), 1)
    out = []
    count = len(lane.indices)
    for position in range(lane.taper_points, count - lane.taper_points, step):
        index = lane.indices[position]
        p = points[index]
        heading = p["heading_rad"]
        nx = -math.sin(heading) * lane.side
        ny = math.cos(heading) * lane.side
        out.append({
            "x_m": p["x_m"] + nx * full,
            "y_m": p["y_m"] + ny * full,
            "z_m": p.get("z_m", 0.0),
            # 建屋は本線と平行に、レーン側を向ける
            "yaw_rad": heading,
        })
    return out
