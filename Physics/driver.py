"""AIドライバー（PID ベース）.

`Docs/SPEC_ZN6.md` §8.3 の Phase 6。**目標は「事故らず1周する」こと。**
ラップタイムは目標にしない（憲法ルール9）。

## FF 前提の元仕様書から変えた点

FF はアンダーステア主体で、限界を超えても「曲がらない」方向に破綻する。
目標値追従の PID だけでも1周できる。

**FR はパワーオーバーステアを起こす。** コーナー脱出でスロットルを開けすぎると
後輪が縦力で飽和し、横力を失ってスピンする。したがって以下が要る:

  1. トラクションコントロール — 後輪スリップ率の監視とスロットル制限
  2. スピン検出 — 車体すべり角とオーバーステア指標
  3. カウンターステアとスロットル戻し

これを入れずに PID だけで走らせると、コーナー脱出のたびにスピンして
「1周する」に到達しない。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import List, Optional

from drivetrain import FORWARD_GEARS
from units import GRAVITY_MPS2, rads_to_rpm
from vehicle import ControlInput, VehicleOutputs, VehicleState, Vehicle
from physics_test_track import Track


@dataclass
class DriverConfig:
    """ドライバーの調整値。**車両の仕様ではない。**

    `vehicle.json` に入れないのはそのため。ここを動かしてラップタイムを
    縮めても、それは車が速くなったことを意味しない。
    """

    # 縦方向 PID
    speed_kp: float = 0.55
    speed_ki: float = 0.06
    speed_kd: float = 0.02

    # 操舵（Pure Pursuit）
    # 先読み距離。**短くすること。**
    # 長いと S字（R=60 が 63m ずつ）で先読み点が次のコーナーに入ってしまい、
    # 手前のコーナーを切り遅れて外へふくらむ。掃引の結果、
    # base=4 / per_speed=0.20 で横ずれ最大 1.57m（コース幅 12m）。
    lookahead_base_m: float = 4.0
    lookahead_per_speed_s: float = 0.20
    # 横ずれ補正（Stanley 形式）。**速度で割ることが重要。**
    # 固定ゲインだと高速で舵が効きすぎ、直線で蛇行が発散する。
    lateral_error_gain: float = 0.60
    yaw_damping_s: float = 0.10

    # コーナリング速度
    # μ のうち旋回に使う割合。**目標はラップタイムではなく事故らず1周すること**
    # （SPEC_ZN6.md §8.3）。限界の 100% を狙うと、荷重移動と複合スリップで
    # 実際の余裕は想定より小さくなり、FR では即スピンにつながる。
    corner_grip_margin: float = 0.62
    braking_decel_mps2: float = 8.0    # 減速の見込み
    lookahead_corner_m: float = 200.0
    min_corner_speed_mps: float = 8.0
    max_speed_mps: float = 69.4          # 250 km/h。直線で目標を無限大にしない

    # トラクションコントロール
    slip_ratio_limit: float = 0.14
    traction_cut_gain: float = 5.0

    # 摩擦円の残り容量によるスロットル制限（FR で最も重要）
    # **旋回で横力に使っている分がある時だけ**効かせる。直線では
    # 後輪は加速で摩擦円の 70-90% を普通に使うので、そこで絞ると走れない。
    grip_lateral_engage: float = 0.25  # 横方向の使用率がこれを超えたら介入する
    grip_total_limit: float = 0.85     # 後輪の合力を摩擦円の 85% に抑える

    # ペダル操作の速度制限 [1/s]。人間は 0 から 1 へ瞬時に踏まない
    throttle_rate_per_s: float = 4.0

    # スピン検出
    sideslip_warn_rad: float = math.radians(3.0)
    sideslip_spin_rad: float = math.radians(22.0)
    countersteer_gain: float = 0.85
    oversteer_margin_rad: float = math.radians(2.0)

    # 変速
    upshift_fraction: float = 0.97     # レブリミットに対する割合
    downshift_rpm: float = 3200.0
    shift_time_s: float = 0.35

    max_steer_rad: float = math.radians(33.0)


@dataclass
class DriverTelemetry:
    target_speed_mps: float = 0.0
    lookahead_m: float = 0.0
    lateral_error_m: float = 0.0
    sideslip_rad: float = 0.0
    oversteer_rad: float = 0.0
    traction_cut: float = 0.0
    countersteer_rad: float = 0.0
    spin_detected: bool = False
    shifting: bool = False
    track_index: int = 0


class Driver:
    def __init__(self, vehicle: Vehicle, track: Track, config: Optional[DriverConfig] = None) -> None:
        self.vehicle = vehicle
        self.track = track
        self.config = config or DriverConfig()

        self._integral = 0.0
        self._previous_error = 0.0
        self._previous_throttle = 0.0
        self._gear = "1"
        self._shift_timer_s = 0.0
        self._track_index = 0
        self.telemetry = DriverTelemetry()

    # --- 目標速度 ---------------------------------------------------------

    def _corner_speed_mps(self, curvature_1pm: float) -> float:
        """曲率から定常旋回の限界速度を出す。

        v = sqrt(mu_eff * g / kappa)。μ は荷重感度を無視した公称値を使い、
        余裕率を掛ける。**限界ぎりぎりを狙わない**（目標は1周すること）。
        """
        if abs(curvature_1pm) < 1e-6:
            return 1e9
        mu = self.vehicle.tire.mu0 * self.config.corner_grip_margin
        return math.sqrt(mu * GRAVITY_MPS2 / abs(curvature_1pm))

    def _target_speed_mps(self, index: int) -> float:
        """先読みして、減速が間に合う最大速度を返す。

        各先読み点のコーナー速度 v_c に対し、そこまでの距離 d を使って
        今出せる速度は sqrt(v_c^2 + 2*a*d)。その最小値を採る。
        """
        cfg = self.config
        spacing = self.track.points[1].s_m - self.track.points[0].s_m
        steps = max(int(cfg.lookahead_corner_m / spacing), 1)

        best = cfg.max_speed_mps
        for k in range(steps + 1):
            point = self.track.points[(index + k) % len(self.track.points)]
            corner = self._corner_speed_mps(point.curvature_1pm)
            if corner >= 1e8:
                continue
            distance = k * spacing
            allowed = math.sqrt(corner ** 2 + 2.0 * cfg.braking_decel_mps2 * distance)
            best = min(best, allowed)

        return max(min(best, cfg.max_speed_mps), cfg.min_corner_speed_mps)

    # --- 操舵 -------------------------------------------------------------

    def _steer_rad(self, state: VehicleState, index: int) -> float:
        """Pure Pursuit + 横ずれ補正。"""
        cfg = self.config
        lookahead = cfg.lookahead_base_m + cfg.lookahead_per_speed_s * state.vx_mps
        target = self.track.point_ahead(index, lookahead)

        dx = target.x_m - state.x_m
        dy = target.y_m - state.y_m
        local_x = dx * math.cos(state.heading_rad) + dy * math.sin(state.heading_rad)
        local_y = -dx * math.sin(state.heading_rad) + dy * math.cos(state.heading_rad)

        distance = max(math.hypot(local_x, local_y), 1e-3)
        steer = math.atan2(2.0 * self.vehicle.wheelbase_m * local_y, distance ** 2)

        # 横ずれ補正。速度で割ることで、高速ほど舵角が小さくなる。
        # atan なので大きな誤差でも舵角が飽和し、暴れない。
        lateral_error = self.track.lateral_error_m(index, state.x_m, state.y_m)
        steer -= math.atan2(cfg.lateral_error_gain * lateral_error, max(state.vx_mps, 3.0))

        # ヨーレートのダンピング。振動の立ち上がりを抑える
        steer -= cfg.yaw_damping_s * state.yaw_rate_rads

        self.telemetry.lookahead_m = lookahead
        self.telemetry.lateral_error_m = lateral_error
        return steer

    # --- FR 固有の安定化 ---------------------------------------------------

    def _oversteer_rad(self, outputs: VehicleOutputs) -> float:
        """後輪スリップ角 - 前輪スリップ角。正なら オーバーステア方向。"""
        if not outputs.slip_angle_rad:
            return 0.0
        front = max(abs(outputs.slip_angle_rad.get(w, 0.0)) for w in ("FL", "FR"))
        rear = max(abs(outputs.slip_angle_rad.get(w, 0.0)) for w in ("RL", "RR"))
        return rear - front

    def _grip_budget_scale(self, outputs: VehicleOutputs) -> float:
        """後輪の摩擦円の使用率からスロットル上限を決める (0.0-1.0)。

        **FR のパワーオーバーステアを防ぐ本体。**

        タイヤが出せる力は mu*Fz の円に収まる。旋回で横力に使っている分だけ
        縦力に回せる余地が減る。旋回中に全開にすると後輪は縦力で円を使い切り、
        **横力を失って一気に流れる**（SPEC_ZN6.md §6.3）。

        ただし **直線では介入しない**。後輪は直線加速でも摩擦円の 70-90% を
        普通に使うので、そこで絞ると加速できなくなる。直線の空転は
        スリップ率ベースの TC（_traction_cut）が受け持つ。

        判定:
          横方向の使用率 u_lat が grip_lateral_engage 未満  -> 介入しない
          合力の使用率 u_total が grip_total_limit を超える -> limit/u_total に絞る
        """
        if not outputs.utilisation or not outputs.tire_fy_n:
            return 1.0
        cfg = self.config

        worst_total = 0.0
        worst_lateral = 0.0
        for wheel in ("RL", "RR"):
            fz = outputs.tire_fz_n.get(wheel, 0.0)
            limit = self.vehicle.tire.max_longitudinal_force_n(fz)
            if limit < 1.0:
                continue
            worst_total = max(worst_total, outputs.utilisation.get(wheel, 0.0))
            worst_lateral = max(worst_lateral, abs(outputs.tire_fy_n.get(wheel, 0.0)) / limit)

        if worst_lateral < cfg.grip_lateral_engage:
            return 1.0
        if worst_total <= cfg.grip_total_limit:
            return 1.0
        return max(cfg.grip_total_limit / worst_total, 0.0)

    def _traction_cut(self, outputs: VehicleOutputs) -> float:
        """後輪スリップ率に応じたスロットル制限係数 (0.0-1.0)。

        **FR の駆動輪は後輪。** ここを見ずに全開にすると、コーナー脱出で
        後輪が縦力に飽和し、横力を失ってスピンする。
        """
        if not outputs.slip_ratio:
            return 1.0
        cfg = self.config
        worst = max(outputs.slip_ratio.get(w, 0.0) for w in ("RL", "RR"))
        excess = worst - cfg.slip_ratio_limit
        if excess <= 0.0:
            return 1.0
        return max(1.0 - cfg.traction_cut_gain * excess, 0.0)

    # --- 変速 -------------------------------------------------------------

    def _update_gear(self, state: VehicleState, dt_s: float) -> bool:
        """必要なら変速する。変速中は True を返す（クラッチが切れている）。"""
        cfg = self.config
        if self._shift_timer_s > 0.0:
            self._shift_timer_s -= dt_s
            return True

        wheel_omega = state.vx_mps / self.vehicle.wheel_radius_m
        rpm = rads_to_rpm(self.vehicle.drivetrain.engine_omega_rads(wheel_omega, self._gear))
        index = FORWARD_GEARS.index(self._gear)

        if rpm >= self.vehicle.engine.redline_rpm * cfg.upshift_fraction and index < len(FORWARD_GEARS) - 1:
            self._gear = FORWARD_GEARS[index + 1]
            self._shift_timer_s = cfg.shift_time_s
            return True

        if rpm <= cfg.downshift_rpm and index > 0:
            self._gear = FORWARD_GEARS[index - 1]
            self._shift_timer_s = cfg.shift_time_s
            return True

        return False

    # --- 本体 -------------------------------------------------------------

    def control(self, state: VehicleState, outputs: VehicleOutputs, dt_s: float) -> ControlInput:
        cfg = self.config
        telemetry = self.telemetry

        self._track_index = self.track.nearest_index(state.x_m, state.y_m, self._track_index)
        telemetry.track_index = self._track_index

        target_speed = self._target_speed_mps(self._track_index)
        telemetry.target_speed_mps = target_speed

        # --- 縦方向 PID ---
        # アンチワインドアップ: 出力が飽和している間は積分を進めない。
        # これが無いと、直線で目標速度に届かない間に積分が飽和し、
        # コーナー手前で減速指令に切り替わるのが遅れる。
        error = target_speed - state.vx_mps
        derivative = (error - self._previous_error) / dt_s if dt_s > 0 else 0.0
        self._previous_error = error

        raw = cfg.speed_kp * error + cfg.speed_ki * self._integral + cfg.speed_kd * derivative
        saturated = raw > 1.0 or raw < -2.0
        if not saturated or (raw > 1.0 and error < 0.0) or (raw < -2.0 and error > 0.0):
            self._integral = max(min(self._integral + error * dt_s, 20.0), -20.0)
            raw = cfg.speed_kp * error + cfg.speed_ki * self._integral + cfg.speed_kd * derivative

        throttle = max(min(raw, 1.0), 0.0)
        brake = max(min(-raw * 0.5, 1.0), 0.0)

        # --- 操舵 ---
        steer = self._steer_rad(state, self._track_index)

        # --- FR の安定化 ---
        sideslip = state.sideslip_rad
        oversteer = self._oversteer_rad(outputs)
        telemetry.sideslip_rad = sideslip
        telemetry.oversteer_rad = oversteer

        spinning = (
            abs(sideslip) > cfg.sideslip_warn_rad
            and oversteer > cfg.oversteer_margin_rad
        )
        telemetry.spin_detected = abs(sideslip) > cfg.sideslip_spin_rad

        countersteer = 0.0
        if spinning:
            # **カウンターステアの符号**
            #
            #   beta < 0 = 速度ベクトルが機首より右 = 左旋回でオーバーステア
            #   立て直すには右へ切る (delta < 0)
            #   → 前輪横力が右向き → ヨーモーメントが左回転を打ち消す
            #   したがって beta < 0 のとき delta < 0、すなわち +gain * beta
            #
            # 以前は -gain * beta としており **符号が逆だった**。滑っている方向へ
            # 舵を足すため、車が回り続けたまま速度ベクトルが追いつくだけになる。
            # 実験では舵を当てないより悪く、残留ヨーレートが 12 deg/s 残った
            # （正しい符号では 0.0 deg/s まで収束する）。
            countersteer = cfg.countersteer_gain * sideslip
            steer += countersteer
            # 後輪の縦力を抜いて横力を戻す
            severity = min(abs(sideslip) / cfg.sideslip_spin_rad, 1.0)
            throttle *= max(1.0 - severity, 0.0)
            brake = 0.0   # スピン中にブレーキを踏むと悪化する
        telemetry.countersteer_rad = countersteer

        traction_cut = self._traction_cut(outputs) * self._grip_budget_scale(outputs)
        telemetry.traction_cut = traction_cut
        throttle *= traction_cut

        # ペダルの操作速度を制限する。0 -> 1 を瞬時にやると、
        # 後輪が縦力に飽和するまでの猶予が無くなる
        max_change = cfg.throttle_rate_per_s * dt_s
        throttle = max(
            min(throttle, self._previous_throttle + max_change),
            self._previous_throttle - max_change,
        )
        self._previous_throttle = throttle

        steer = max(min(steer, cfg.max_steer_rad), -cfg.max_steer_rad)

        # --- 変速 ---
        shifting = self._update_gear(state, dt_s)
        telemetry.shifting = shifting
        if shifting:
            throttle = 0.0

        return ControlInput(
            throttle=throttle,
            brake=brake,
            steer_rad=steer,
            gear=self._gear,
            clutch_engaged=not shifting,
        )
