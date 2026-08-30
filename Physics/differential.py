"""デファレンシャル（トルセンLSD）.

基準車両の GT はトルセンLSD を標準装備する。CL1版仕様書の
「初期は Open Diff、次に LSD」という段階論は ZN6 では実車と乖離するため、
**LSD を本実装、Open Diff を比較基準として残す**（`Docs/SPEC_ZN6.md` §6.2）。

FR + LSD の効き方は FF と逆向きになる。コーナー脱出のパワーオンで
**内輪から外輪へトルクが移り、オーバーステアを助長する**。
FF の「アンダーを消す」用途とは意味が違う。
"""

from __future__ import annotations

import math
from typing import Tuple

from vehicle_data import VehicleData

# ロックの立ち上がりを滑らかにする回転差のスケール [rad/s]。
# これを小さくすると数値的に硬くなり、積分が不安定になる。
_LOCK_SMOOTHING_RADS = 1.5


class OpenDifferential:
    """比較基準。左右へ常に等分。"""

    def split_torque_nm(
        self, total_torque_nm: float, omega_left_rads: float, omega_right_rads: float
    ) -> Tuple[float, float]:
        half = total_torque_nm / 2.0
        return half, half


class TorsenDifferential:
    """トルク感応式 LSD.

    回転差に応じて、**速い側から遅い側へ**トルクを移す。
    移せる量は プリロード + ロック率 * 入力トルク で頭打ちになる。
    """

    def __init__(self, data: VehicleData) -> None:
        self.preload_nm = data.value("differential.preload", "N*m")
        self.accel_lock_ratio = data.value("differential.accel_lock_ratio", "-")
        self.decel_lock_ratio = data.value("differential.decel_lock_ratio", "-")

    def split_torque_nm(
        self, total_torque_nm: float, omega_left_rads: float, omega_right_rads: float
    ) -> Tuple[float, float]:
        lock_ratio = (
            self.accel_lock_ratio if total_torque_nm >= 0.0 else self.decel_lock_ratio
        )
        capacity_nm = self.preload_nm + lock_ratio * abs(total_torque_nm)

        # 速い側が正になるようにとる
        omega_difference = omega_left_rads - omega_right_rads
        transfer_nm = capacity_nm * math.tanh(omega_difference / _LOCK_SMOOTHING_RADS)

        half = total_torque_nm / 2.0
        return half - transfer_nm, half + transfer_nm
