"""エンジンモデル（FA20）.

トルクカーブは `vehicle.json` から読む。**コードに数値を書かない。**

内部の扱い:
    T_indicated(rpm) = T_wot(rpm) + T_friction(omega)
    T_net(rpm, throttle) = throttle * T_indicated(rpm) - T_friction(omega)

こうすると throttle=1 で T_wot に、throttle=0 で -T_friction（エンジンブレーキ）に
なる。**T_wot を測定値としてそのまま使いつつ、摩擦を二重に引かない**ための形。
"""

from __future__ import annotations

import math
from typing import List

from units import rads_to_rpm, rpm_to_rads
from vehicle_data import VehicleData


class Engine:
    def __init__(self, data: VehicleData) -> None:
        self._data = data

        rpm, torque = data.curve("engine.torque_curve", "1/min", "N*m")
        if len(rpm) < 3:
            raise ValueError(
                "トルクカーブの点数が {} 個しかない。"
                "2点（最大出力/最大トルク）だけで補間してはいけない。"
                "FA20 は 4,000rpm 付近に谷がある（Docs/ZN6_BASELINE.md）。".format(len(rpm))
            )
        self._rpm: List[float] = list(rpm)
        self._torque_nm: List[float] = list(torque)

        self.redline_rpm = data.value("engine.redline", "1/min")
        self.idle_rpm = data.value("engine.idle_rpm", "1/min")
        self.friction_coeff_nms = data.value("engine.friction_model", "N*m*s")
        self.inertia_kgm2 = data.value("engine.rotational_inertia", "kg*m^2")

        self._interp = self._build_interpolator()

    # --- 補間 -------------------------------------------------------------

    def _build_interpolator(self):
        """単調3次補間（PCHIP）。

        通常の3次スプラインだと、4,000rpm の谷の前後でオーバーシュートして
        存在しない山や谷を作る。PCHIP はデータ点の間で振動しない。
        **谷の形状を勝手に増幅しないことが重要。**
        """
        try:
            from scipy.interpolate import PchipInterpolator
        except ImportError as exc:  # pragma: no cover
            raise ImportError(
                "scipy が要る。./Tools/setup.sh を実行すること。"
            ) from exc
        return PchipInterpolator(self._rpm, self._torque_nm, extrapolate=False)

    def wot_torque_nm(self, rpm: float) -> float:
        """全開時のクランク軸トルク [N*m]。カーブの範囲外は端点で保持する。"""
        lo, hi = self._rpm[0], self._rpm[-1]
        if rpm <= lo:
            return self._torque_nm[0]
        if rpm >= hi:
            return self._torque_nm[-1]
        return float(self._interp(rpm))

    # --- トルク -----------------------------------------------------------

    def friction_torque_nm(self, omega_rads: float) -> float:
        """内部摩擦・ポンピングロスによる抵抗トルク [N*m]（正の値）。"""
        return self.friction_coeff_nms * abs(omega_rads)

    def torque_nm(self, omega_rads: float, throttle: float) -> float:
        """クランク軸の正味トルク [N*m]。

        throttle: 0.0 - 1.0
        """
        if not 0.0 <= throttle <= 1.0:
            raise ValueError("throttle は 0.0-1.0。受け取った値: {}".format(throttle))

        rpm = rads_to_rpm(omega_rads)
        friction = self.friction_torque_nm(omega_rads)

        if rpm >= self.redline_rpm:
            # レブリミッター。燃料カットで駆動トルクは消え、摩擦だけが残る
            return -friction

        indicated = self.wot_torque_nm(rpm) + friction
        return throttle * indicated - friction

    def power_w(self, omega_rads: float, throttle: float = 1.0) -> float:
        """出力 [W]。"""
        return self.torque_nm(omega_rads, throttle) * omega_rads

    # --- 検査 -------------------------------------------------------------

    def peak_power_w(self):
        """(最高出力 [W], その回転数 [1/min]) を返す。

        公式値（147 kW / 7,000rpm）と突き合わせるための検査用。
        """
        best_w, best_rpm = -math.inf, 0.0
        rpm = self._rpm[0]
        while rpm <= self._rpm[-1]:
            w = self.wot_torque_nm(rpm) * rpm_to_rads(rpm)
            if w > best_w:
                best_w, best_rpm = w, rpm
            rpm += 10.0
        return best_w, best_rpm

    def peak_torque_nm(self):
        """(最大トルク [N*m], その回転数 [1/min])。"""
        best_t, best_rpm = -math.inf, 0.0
        rpm = self._rpm[0]
        while rpm <= self._rpm[-1]:
            t = self.wot_torque_nm(rpm)
            if t > best_t:
                best_t, best_rpm = t, rpm
            rpm += 10.0
        return best_t, best_rpm
