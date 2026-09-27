"""タイヤモデル（Fiala / ブラシ）.

`Docs/SPEC_ZN6.md` §6.3 の段階的実装のうち **初期段階**。
Pacejka の係数を当てずに、物理的に意味のある少数のパラメータから構築する。

実装しているもの:
  - 荷重感度（μ は垂直荷重とともに低下する）
  - **複合スリップ（Combined Slip）**

複合スリップを最初から入れているのは FR だから。FF では駆動と操舵が前輪に
集中するため複合スリップは前輪の問題だが、**FR では後輪の複合スリップが
パワーオーバーステアの発生条件そのもの**であり、「後期に実装」に回せない
（`Docs/SPEC_ZN6.md` §6.3）。

まだ入っていないもの: キャンバー変化、緩和長、温度・摩耗。
"""

from __future__ import annotations

import math
from typing import Tuple

from vehicle_data import VehicleData


class Tire:
    def __init__(self, data: VehicleData, nominal_load_n: float,
                 read_camber: bool = False) -> None:
        self.mu0 = data.value("tires.friction_coefficient", "-")
        self.load_sensitivity_per_n = data.value("tires.load_sensitivity", "1/N")
        self.cornering_stiffness_per_load = data.value(
            "tires.cornering_stiffness_per_load", "1/rad"
        )
        self.longitudinal_stiffness_per_load = data.value(
            "tires.longitudinal_stiffness_per_load", "-"
        )
        # キャンバー推力の係数。**キャンバーを使うときだけ読む。**
        #
        # `VehicleData` は読んだ全パラメータの confidence を記録し、結果の
        # 信頼度をその最小値で頭打ちにする。この項目は assumed / 0.10 なので、
        # **常に読むとキャンバー 0 の走行まで信頼度 0.10 に落ちる。**
        # 効いていないパラメータで信頼度を下げるのは、依存関係の嘘になる。
        #
        # 使わないときは None にしておき、**それでキャンバーを渡されたら
        # 止まる**（黙って 0 として扱わない）。
        self.camber_stiffness_per_load = (
            data.value("tires.camber_stiffness_per_load", "1/rad")
            if read_camber else None
        )
        self.effective_radius_m = data.value("tires.effective_radius", "m")
        self.nominal_load_n = nominal_load_n

    # --- 摩擦係数 ---------------------------------------------------------

    def mu(self, fz_n: float) -> float:
        """垂直荷重に依存する摩擦係数.

        mu(Fz) = mu0 * (1 - k * (Fz - Fz_nominal))

        **荷重が増えるほど μ は下がる。** これが無いと荷重移動の効果が
        正しく出ない。FR の加速時は後輪荷重が増えるが、μ の低下により
        駆動力の増加は荷重の増加ほどには大きくならない。
        """
        if fz_n <= 0.0:
            return 0.0
        mu = self.mu0 * (1.0 - self.load_sensitivity_per_n * (fz_n - self.nominal_load_n))
        return max(mu, 0.05)

    # --- 力 ---------------------------------------------------------------

    def forces_n(
        self, fz_n: float, slip_ratio: float, slip_angle_rad: float,
        camber_lean_rad: float = 0.0,
    ) -> Tuple[float, float]:
        """(縦力 Fx, 横力 Fy) [N] を返す。

        ブラシモデルの飽和則:
            F = mu*Fz * (3z - 3z^2 + z^3)   (z = F_linear / (3*mu*Fz) <= 1)
            F = mu*Fz                        (z > 1)

        線形域では F -> F_linear に一致し、飽和すると摩擦円 mu*Fz で頭打ちになる。
        力の向きは線形力のベクトル方向を保つので、**摩擦円の拘束が
        縦横で自動的に共有される**（これが複合スリップ）。
        """
        if fz_n <= 0.0:
            return 0.0, 0.0

        mu = self.mu(fz_n)
        f_max = mu * fz_n
        if f_max <= 0.0:
            return 0.0, 0.0

        c_kappa = self.longitudinal_stiffness_per_load * fz_n
        c_alpha = self.cornering_stiffness_per_load * fz_n

        fx_linear = c_kappa * slip_ratio
        fy_linear = -c_alpha * math.tan(slip_angle_rad)

        # キャンバー推力。**倒れた向きへ押す。**
        #
        # 飽和の前に足すので摩擦円を共有する（キャンバーで得た横力も
        # 限界を消費する）。後から足すと**摩擦円を超える横力が出る。**
        #
        # `camber_lean_rad` は車体座標系の傾き（正が左倒し）で、
        # 自動車の慣習のキャンバー角ではない。左右で符号が変わるので、
        # 変換は `setup.CarSetup.wheel_camber_lean_rad` が持っている。
        if camber_lean_rad != 0.0:
            if self.camber_stiffness_per_load is None:
                raise ValueError(
                    "キャンバーを渡されたが camber_stiffness_per_load を"
                    "読んでいない。Tire(..., read_camber=True) で作ること")
            fy_linear += self.camber_stiffness_per_load * fz_n * camber_lean_rad

        f_linear = math.hypot(fx_linear, fy_linear)
        if f_linear < 1e-9:
            return 0.0, 0.0

        z = f_linear / (3.0 * f_max)
        if z < 1.0:
            f_total = f_max * (3.0 * z - 3.0 * z * z + z ** 3)
        else:
            f_total = f_max

        scale = f_total / f_linear
        return fx_linear * scale, fy_linear * scale

    def longitudinal_slope_n_per_slip(
        self, fz_n: float, slip_ratio: float, slip_angle_rad: float,
        camber_lean_rad: float = 0.0,
    ) -> float:
        """その動作点での dFx/dkappa [N]（接線剛性）。

        **車輪回転を半陰的に積分するために要る**（issue #24）。

        飽和則 F = mu*Fz*(3z - 3z^2 + z^3) を f_linear で微分すると

            dF/df_linear = (1 - z)^2

        なので、接線剛性は線形域の c_kappa をこの係数で縮めたものになる。
        飽和しきる（z >= 1）と 0。

        **線形域の c_kappa をそのまま使ってはいけない。** 飽和している
        ときの実際の勾配はずっと小さく、それを使うと半陰的な積分が
        過剰に減衰する。実際、発進 2 秒後の速度が刻みによって
        3.13 / 4.19 m/s と食い違った。
        """
        if fz_n <= 0.0:
            return 0.0

        mu = self.mu(fz_n)
        f_max = mu * fz_n
        if f_max <= 0.0:
            return 0.0

        c_kappa = self.longitudinal_stiffness_per_load * fz_n
        c_alpha = self.cornering_stiffness_per_load * fz_n

        fx_linear = c_kappa * slip_ratio
        fy_linear = -c_alpha * math.tan(slip_angle_rad)

        # **forces_n と同じ動作点で微分すること。** ここでキャンバーを
        # 落とすと、力と接線剛性が別の点の値になり、半陰的な積分の
        # 減衰が合わなくなる。
        if camber_lean_rad != 0.0:
            if self.camber_stiffness_per_load is None:
                raise ValueError(
                    "キャンバーを渡されたが camber_stiffness_per_load を"
                    "読んでいない。Tire(..., read_camber=True) で作ること")
            fy_linear += self.camber_stiffness_per_load * fz_n * camber_lean_rad

        f_linear = math.hypot(fx_linear, fy_linear)

        z = f_linear / (3.0 * f_max)
        if z >= 1.0:
            return 0.0
        return c_kappa * (1.0 - z) ** 2

    def max_longitudinal_force_n(self, fz_n: float) -> float:
        """その荷重で出せる縦力の上限 [N]（摩擦円の半径）。"""
        return self.mu(fz_n) * max(fz_n, 0.0)

    # --- スリップ量 -------------------------------------------------------

    @staticmethod
    def slip_ratio(wheel_omega_rads: float, radius_m: float, contact_speed_mps: float) -> float:
        """スリップ率 kappa = (omega*r - v) / max(|v|, eps)。

        駆動時は正、制動時は負。
        """
        wheel_speed = wheel_omega_rads * radius_m
        denominator = max(abs(contact_speed_mps), 0.5)
        return (wheel_speed - contact_speed_mps) / denominator

    @staticmethod
    def slip_angle_rad(lateral_speed_mps: float, longitudinal_speed_mps: float) -> float:
        """スリップ角 alpha = atan(vy / |vx|)。"""
        return math.atan2(lateral_speed_mps, max(abs(longitudinal_speed_mps), 0.5))
