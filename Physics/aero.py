"""空力（抗力のみ）.

揚力係数は `unknown` のため扱わない。ダウンフォースをゼロとして扱うことは
「無視できる」という主張ではなく、**データが無いという事実の反映**である。
"""

from __future__ import annotations

from units import AIR_DENSITY_KGPM3
from vehicle_data import VehicleData


class Aerodynamics:
    def __init__(self, data: VehicleData, air_density_kgpm3: float = AIR_DENSITY_KGPM3) -> None:
        self.cd = data.value("aerodynamics.cd", "-")
        self.frontal_area_m2 = data.value("aerodynamics.frontal_area", "m^2")
        self.air_density_kgpm3 = air_density_kgpm3

    def drag_force_n(self, speed_mps: float) -> float:
        """進行方向と逆向きの抗力の大きさ [N]（常に正）。"""
        return 0.5 * self.air_density_kgpm3 * self.cd * self.frontal_area_m2 * speed_mps ** 2
