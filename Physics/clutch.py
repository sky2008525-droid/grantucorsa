"""クラッチモデル（滑りを持つ）.

**bool の断続ではなく、回転差に応じてトルクを伝えるモデル。**

これが無いと以下が表現できない。

  - **クラッチ蹴り** — 切ってエンジンを空吹かしし、繋いだ瞬間に回転差ぶんの
    トルクを後輪に叩き込む。ドリフトの引き起こしに使う
  - 半クラッチ発進 — 回転差を保ったままトルクを渡す
  - エンスト — 車輪側にエンジンが引きずられて回転が落ちる

伝達トルク:

    T = clamp(k * (omega_engine - omega_gearbox), ±capacity)
    k = capacity / slip_scale

回転差が小さいときは線形（ばね的に両者を同期させる）、大きいときは容量で
頭打ちになる（滑っている状態）。`capacity` にはクラッチペダルの踏み量を掛ける。
"""

from __future__ import annotations

from vehicle_data import VehicleData

# 線形域と滑り域の境目 [rad/s]。
#
# **繋がったクラッチはほぼ剛体。** ここを大きくすると、繋いでいるのに
# エンジンだけが空転してしまう（容量飽和＝常時ホイールスピン）。
# 0.5 rad/s なら最大トルク 205 N*m を伝えるのに必要な回転差は約 3 rpm で、
# 実質ロックとみなせる。
#
# 代償として数値的に硬くなるので、エンジンの積分は半陰的に行う
# （Vehicle._integrate_engine）。陽解法だと発散する。
SLIP_SCALE_RADS = 0.5


class Clutch:
    def __init__(self, data: VehicleData) -> None:
        self.capacity_nm = data.value("transmission.clutch_capacity", "N*m")

    @property
    def stiffness_nm_per_rads(self) -> float:
        return self.capacity_nm / SLIP_SCALE_RADS

    def torque_nm(
        self, engagement: float, engine_omega_rads: float, gearbox_omega_rads: float
    ) -> float:
        """エンジンから駆動系へ伝わるトルク [N*m]。

        engagement: 0.0（完全に切る）- 1.0（完全に繋ぐ）
        戻り値の符号はエンジン側から見た抵抗トルク（正なら駆動側へ流れる）。
        """
        if not 0.0 <= engagement <= 1.0:
            raise ValueError("engagement は 0.0-1.0。受け取った値: {}".format(engagement))
        if engagement <= 0.0:
            return 0.0

        capacity = self.capacity_nm * engagement
        stiffness = capacity / SLIP_SCALE_RADS
        slip = engine_omega_rads - gearbox_omega_rads
        torque = stiffness * slip
        return max(min(torque, capacity), -capacity)

    def is_slipping(
        self, engagement: float, engine_omega_rads: float, gearbox_omega_rads: float
    ) -> bool:
        """容量いっぱいで滑っているか。"""
        if engagement <= 0.0:
            return True
        capacity = self.capacity_nm * engagement
        torque = self.torque_nm(engagement, engine_omega_rads, gearbox_omega_rads)
        return abs(torque) >= capacity * 0.999
