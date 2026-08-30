"""Physics Test Track — 物理検証用コース.

`Docs/SPEC_ZN6.md` §8.2 が要求する要素:

  直線 / 強ブレーキ / ヘアピン / S字 / 段差

**段差は含めない。** 上下方向の動特性を持たないため（スプリングレート・
ダンパー減衰力が `unknown`）、段差を置いても車体は何も反応しない。
「あるのに効かない」状態はモデルの限界を隠すので、**入れないことで
限界を可視にしておく**。サスペンションのデータが取れたら追加すること。

コースは中心線のポリラインとして持つ。各点に曲率を持たせ、
ドライバーはそこから目標速度を決める。
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import List, Optional, Tuple


@dataclass
class TrackPoint:
    s_m: float          # 始点からの距離
    x_m: float
    y_m: float
    heading_rad: float
    curvature_1pm: float  # 曲率 [1/m]。左旋回が正
    label: str = ""


class Track:
    def __init__(self, points: List[TrackPoint], name: str, width_m: float = 12.0) -> None:
        self.points = points
        self.name = name
        self.width_m = width_m

    @property
    def length_m(self) -> float:
        return self.points[-1].s_m

    def nearest_index(self, x_m: float, y_m: float, hint: int = 0, window: int = 400) -> int:
        """(x, y) に最も近い点の添字。

        hint の周辺だけを探すので、走行中は O(window) で済む。
        """
        n = len(self.points)
        best_i, best_d = hint, float("inf")
        for offset in range(-window // 4, window):
            i = (hint + offset) % n
            p = self.points[i]
            d = (p.x_m - x_m) ** 2 + (p.y_m - y_m) ** 2
            if d < best_d:
                best_d, best_i = d, i
        return best_i

    def point_ahead(self, index: int, distance_m: float) -> TrackPoint:
        """index から distance_m 先の点（周回するのでラップアラウンドする）。"""
        spacing = self.points[1].s_m - self.points[0].s_m
        steps = max(int(distance_m / spacing), 1)
        return self.points[(index + steps) % len(self.points)]

    def max_curvature_ahead(self, index: int, distance_m: float) -> float:
        """先読み区間の最大曲率。ブレーキングポイントの判断に使う。"""
        spacing = self.points[1].s_m - self.points[0].s_m
        steps = max(int(distance_m / spacing), 1)
        return max(
            abs(self.points[(index + k) % len(self.points)].curvature_1pm)
            for k in range(steps + 1)
        )

    def lateral_error_m(self, index: int, x_m: float, y_m: float) -> float:
        """中心線からの横ずれ [m]。左が正。"""
        p = self.points[index]
        dx, dy = x_m - p.x_m, y_m - p.y_m
        return -dx * math.sin(p.heading_rad) + dy * math.cos(p.heading_rad)


class _Builder:
    def __init__(self, spacing_m: float = 1.0) -> None:
        self.spacing_m = spacing_m
        self.points: List[TrackPoint] = []
        self._x = 0.0
        self._y = 0.0
        self._heading = 0.0
        self._s = 0.0

    def _emit(self, curvature: float, label: str) -> None:
        self.points.append(
            TrackPoint(self._s, self._x, self._y, self._heading, curvature, label)
        )

    def straight(self, length_m: float, label: str = "straight") -> "_Builder":
        steps = max(int(round(length_m / self.spacing_m)), 1)
        for _ in range(steps):
            self._emit(0.0, label)
            self._x += self.spacing_m * math.cos(self._heading)
            self._y += self.spacing_m * math.sin(self._heading)
            self._s += self.spacing_m
        return self

    def arc(self, radius_m: float, angle_deg: float, label: str = "corner") -> "_Builder":
        """radius_m の円弧を angle_deg 分。angle_deg が正で左旋回。"""
        angle_rad = math.radians(angle_deg)
        length = abs(angle_rad) * radius_m
        steps = max(int(round(length / self.spacing_m)), 1)
        curvature = math.copysign(1.0 / radius_m, angle_rad)
        d_heading = angle_rad / steps
        for _ in range(steps):
            self._emit(curvature, label)
            self._x += self.spacing_m * math.cos(self._heading)
            self._y += self.spacing_m * math.sin(self._heading)
            self._heading += d_heading
            self._s += self.spacing_m
        return self

    def build(self, name: str) -> Track:
        return Track(self.points, name)


def physics_test_track(spacing_m: float = 1.0) -> Track:
    """検証用の**閉じた**周回コース。

      1. メインストレート 400m — 全開加速。0-100km/h と高速域
      2. ヘアピン R=25 (180deg)  — 直前が強ブレーキ区間。低速旋回と LSD
      3. 脱出ストレート 100m     — **FR のパワーオーバーステアが出やすい**
      4. S字 R=60 (-60/+60deg)   — 左右の切り返し。過渡応答
      5. バックストレート 250m   — 再加速
      6. 大回りの 180deg R=55    — 高速の定常旋回。横G の上限
      7. 短いストレートで閉じる

    **閉合の条件**

    総旋回角が 360deg であること、かつ始点に戻ること。これを満たさないと
    周回にならず、終端で中心線が始点へ飛ぶため「巨大な横ずれ」として現れる。

    幾何:
      総旋回角 = 180 (ヘアピン) + (-60 + 60) (S字) + 180 (大回り) = 360 deg  OK
      y 方向   = 2*25 (ヘアピン) + 60 (S字の横移動) - 2*Rb = 0  ->  Rb = 55
      x 方向   = L1 - L2 - 103.92 (S字) - L3 + L4 = 0

    `assert_closed()` で数値的にも確認している。
    """
    main_straight = 400.0
    exit_straight = 100.0
    s_curve_x_shift = 2.0 * 60.0 * math.sin(math.radians(60.0))   # 103.92 m
    back_straight = 250.0
    closing_straight = back_straight + exit_straight + s_curve_x_shift - main_straight

    b = _Builder(spacing_m)
    b.straight(main_straight, "main straight")
    b.arc(25.0, 180.0, "hairpin")
    b.straight(exit_straight, "exit straight")
    b.arc(60.0, -60.0, "S1 right")
    b.arc(60.0, 60.0, "S2 left")
    b.straight(back_straight, "back straight")
    b.arc(55.0, 180.0, "sweeper")
    b.straight(closing_straight, "start line")
    return b.build("Physics Test Track")


def closure_error(track: Track) -> Tuple[float, float]:
    """(位置のずれ [m], 方位のずれ [rad])。

    閉じた周回であることの検査。ずれが大きいと、周回の終端で中心線が
    始点へ飛び、ドライバーが追従不能になる。
    """
    first, last = track.points[0], track.points[-1]
    spacing = track.points[1].s_m - track.points[0].s_m
    # 最終点から1ステップ進んだ位置が始点に一致するはず
    end_x = last.x_m + spacing * math.cos(last.heading_rad)
    end_y = last.y_m + spacing * math.sin(last.heading_rad)
    position_error = math.hypot(end_x - first.x_m, end_y - first.y_m)

    heading_error = (last.heading_rad - first.heading_rad) % (2.0 * math.pi)
    if heading_error > math.pi:
        heading_error -= 2.0 * math.pi
    return position_error, heading_error
