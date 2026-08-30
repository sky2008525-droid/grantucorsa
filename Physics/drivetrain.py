"""駆動系（FR）.

    Engine -> Clutch -> Gearbox -> Propeller Shaft -> Final Drive / Diff
           -> Drive Shaft -> 後輪

**FF との違い**: ギアボックスとファイナルの間にプロペラシャフトが入る
（`Docs/SPEC_ZN6.md` §6.2）。プロペラシャフト単体の慣性は `unknown` のため、
現状はエンジン慣性に含めて扱っている。分離できるデータが取れたら独立させること。
"""

from __future__ import annotations

from typing import Dict, Optional

from vehicle_data import VehicleData

FORWARD_GEARS = ["1", "2", "3", "4", "5", "6"]


class Drivetrain:
    def __init__(self, data: VehicleData) -> None:
        self._data = data

        self.gear_ratios: Dict[str, float] = {
            g: data.value("transmission.gear_ratios.{}".format(g), "-")
            for g in FORWARD_GEARS + ["R"]
        }
        self.final_drive = data.value("transmission.final_drive", "-")
        self.efficiency = data.value("transmission.drivetrain_efficiency", "-")
        self.engine_inertia_kgm2 = data.value("engine.rotational_inertia", "kg*m^2")

        self._check_final_drive_variant()

    def _check_final_drive_variant(self) -> None:
        """基準車両のグレードとファイナルの整合を確認する。

        ZN6 のファイナルは単一値ではない。G 6MT のみ 3.727、
        GT / GT"Limited" / 6AT 全車は 4.100。取り違えると約10%の駆動力誤差が入り、
        0-100km/h の検証で原因不明のズレとして現れる（`Docs/ZN6_BASELINE.md` 罠①）。
        """
        grade = self._data.plain("identity.grade")
        transmission = self._data.plain("identity.transmission_type")

        if grade in ("GT", 'GT"Limited"') and abs(self.final_drive - 4.100) > 1e-6:
            raise ValueError(
                "グレード {} のファイナルは 4.100 のはずだが {} が入っている。"
                "G 6MT の値（3.727）と取り違えていないか確認すること"
                "（Docs/ZN6_BASELINE.md 罠①）。".format(grade, self.final_drive)
            )
        if grade == "G" and transmission == "6MT" and abs(self.final_drive - 4.100) < 1e-6:
            raise ValueError(
                "G 6MT のファイナルは 3.727（オープンデフ）。"
                "トルセンLSD を選択した場合のみ 4.100。どちらか明示すること。"
            )

    # --- 比 ---------------------------------------------------------------

    def total_ratio(self, gear: str) -> float:
        """エンジン回転 / 車輪回転 の総減速比。"""
        return self.gear_ratios[gear] * self.final_drive

    def engine_omega_rads(self, wheel_omega_rads: float, gear: str) -> float:
        return wheel_omega_rads * self.total_ratio(gear)

    def wheel_omega_rads(self, engine_omega_rads: float, gear: str) -> float:
        return engine_omega_rads / self.total_ratio(gear)

    # --- トルクと慣性 -----------------------------------------------------

    def wheel_torque_nm(self, engine_torque_nm: float, gear: str) -> float:
        """駆動輪に届くトルク [N*m]（後輪合計）。

        効率は駆動側にのみ掛ける。エンジンブレーキ（負のトルク）に効率を
        掛けると、損失が車を加速させる向きに働いてしまう。
        """
        ratio = self.total_ratio(gear)
        if engine_torque_nm >= 0.0:
            return engine_torque_nm * ratio * self.efficiency
        return engine_torque_nm * ratio / self.efficiency

    def reflected_inertia_at_wheel_kgm2(self, gear: str) -> float:
        """エンジン回転慣性を車輪軸に換算した値 [kg*m^2]。

        I_wheel = I_engine * ratio^2。**1速では ratio ~= 14.9 なので
        ratio^2 ~= 222 倍**になり、無視できない。等価質量に直すと数十 kg 相当。
        これを落とすと発進加速が実車より速くなる。
        """
        ratio = self.total_ratio(gear)
        return self.engine_inertia_kgm2 * ratio * ratio

    def equivalent_mass_kg(self, gear: str, wheel_radius_m: float) -> float:
        """回転慣性を並進質量に換算した値 [kg]。"""
        return self.reflected_inertia_at_wheel_kgm2(gear) / (wheel_radius_m ** 2)

    # --- 変速 -------------------------------------------------------------

    def best_gear_for_speed(
        self, speed_mps: float, wheel_radius_m: float,
        redline_rpm: float, shift_margin: float = 0.97,
    ) -> Optional[str]:
        """その速度で回転数がレブリミットを超えない最も低いギア。

        最も低い（=最も減速比が大きい）ギアを選ぶことで駆動力が最大になる。
        """
        from units import rads_to_rpm

        wheel_omega = speed_mps / wheel_radius_m
        limit = redline_rpm * shift_margin
        for gear in FORWARD_GEARS:
            rpm = rads_to_rpm(self.engine_omega_rads(wheel_omega, gear))
            if rpm <= limit:
                return gear
        return None
