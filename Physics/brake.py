"""ブレーキモデル.

ディスク径・マスターシリンダー径・パッド摩擦係数はいずれも `unknown` のため、
油圧系からブレーキトルクを導くことはできない。

代わりに **総容量と前後配分** で扱う。実車のブレーキはタイヤの摩擦限界を
上回る能力を持つように設計される（だからロックする）ので、
**制動距離を決めるのはブレーキ容量ではなくタイヤ μ である。**
このモデル化で制動距離の妥当性は損なわれない。

ABS は未実装（`SPEC_ZN6.md` §6.2 の後段）。ロックしたらそのまま滑る。
"""

from __future__ import annotations

from typing import Tuple

from vehicle_data import VehicleData


class Brakes:
    def __init__(self, data: VehicleData) -> None:
        self.bias_front = data.value("brakes.brake_bias", "-")
        self.max_total_torque_nm = data.value("brakes.max_brake_torque_total", "N*m")

    def axle_torques_nm(self, pedal: float) -> Tuple[float, float]:
        """(前軸合計, 後軸合計) のブレーキトルク [N*m]（正の値）。

        pedal: 0.0 - 1.0
        """
        if not 0.0 <= pedal <= 1.0:
            raise ValueError("pedal は 0.0-1.0。受け取った値: {}".format(pedal))
        total = self.max_total_torque_nm * pedal
        return total * self.bias_front, total * (1.0 - self.bias_front)
