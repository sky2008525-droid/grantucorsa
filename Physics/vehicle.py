"""4輪車両モデル（平面3自由度 + 準静的荷重移動）.

`Docs/SPEC_ZN6.md` §6.5 の Phase 5。

状態:
    vx, vy   車体固定座標系の速度 [m/s]（x 前方、y 左方）
    r        ヨーレート [rad/s]
    X, Y, psi 地面固定座標系の位置と方位
    omega_i  各輪の回転速度 [rad/s]

**6自由度ではなく平面3自由度にした理由**

スプリングレート・ダンパー減衰力・スタビ径・サスペンションジオメトリが
すべて `unknown` である。上下・ロール・ピッチの**動特性**を入れると、
その全てを捏造することになる（憲法ルール1）。

代わりに荷重移動を**準静的**に扱う。これは加速度から一意に決まり、
仮定するのは前後ロール剛性配分ひとつだけで済む。
ロール角の時間応答は出ないが、**定常的な4輪の荷重は正しく出る**。

サスペンションのデータが取れたら Level 1（等価バネ・ダンパー）へ拡張すること。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from aero import Aerodynamics
from brake import Brakes
from differential import OpenDifferential, TorsenDifferential
from drivetrain import FORWARD_GEARS, Drivetrain
from engine import Engine
from tire import Tire
from units import GRAVITY_MPS2, rads_to_rpm
from vehicle_data import VehicleData

# 車輪の並び。FL=前左, FR=前右, RL=後左, RR=後右
WHEELS = ("FL", "FR", "RL", "RR")
FRONT_WHEELS = ("FL", "FR")
REAR_WHEELS = ("RL", "RR")


@dataclass
class ControlInput:
    throttle: float = 0.0
    brake: float = 0.0
    steer_rad: float = 0.0
    gear: str = "1"
    clutch_engaged: bool = True


@dataclass
class VehicleState:
    vx_mps: float = 0.0
    vy_mps: float = 0.0
    yaw_rate_rads: float = 0.0
    x_m: float = 0.0
    y_m: float = 0.0
    heading_rad: float = 0.0
    wheel_omega_rads: Dict[str, float] = field(default_factory=lambda: {w: 0.0 for w in WHEELS})

    @property
    def speed_mps(self) -> float:
        return math.hypot(self.vx_mps, self.vy_mps)

    @property
    def sideslip_rad(self) -> float:
        """車体すべり角 beta。大きいほどスピンに近い。"""
        return math.atan2(self.vy_mps, max(abs(self.vx_mps), 0.5))


@dataclass
class VehicleOutputs:
    """1ステップの内部量。テレメトリとデバッグ用。"""

    ax_mps2: float = 0.0
    ay_mps2: float = 0.0
    yaw_accel_rads2: float = 0.0
    engine_rpm: float = 0.0
    engine_torque_nm: float = 0.0
    tire_fz_n: Dict[str, float] = field(default_factory=dict)
    tire_fx_n: Dict[str, float] = field(default_factory=dict)
    tire_fy_n: Dict[str, float] = field(default_factory=dict)
    slip_ratio: Dict[str, float] = field(default_factory=dict)
    slip_angle_rad: Dict[str, float] = field(default_factory=dict)
    utilisation: Dict[str, float] = field(default_factory=dict)


class Vehicle:
    """ZN6（FR）の平面運動モデル。"""

    def __init__(self, data: VehicleData, use_lsd: bool = True) -> None:
        self.data = data

        self.engine = Engine(data)
        self.drivetrain = Drivetrain(data)
        self.brakes = Brakes(data)
        self.aero = Aerodynamics(data)
        self.differential = TorsenDifferential(data) if use_lsd else OpenDifferential()

        self.mass_kg = data.value("mass.curb_mass", "kg")
        self.izz_kgm2 = data.value("inertia.Izz", "kg*m^2")
        self.wheelbase_m = data.value("dimensions.wheelbase", "m")
        self.track_front_m = data.value("dimensions.track_front", "m")
        self.track_rear_m = data.value("dimensions.track_rear", "m")
        self.cg_height_m = data.value("inertia.cg_height", "m")
        self.lf_m = data.value("inertia.cg_longitudinal_from_front_axle", "m")
        self.lr_m = self.wheelbase_m - self.lf_m
        self.roll_dist_front = data.value(
            "suspension.roll_stiffness_distribution_front", "-"
        )
        self.crr = data.value("tires.rolling_resistance_coefficient", "-")
        self.wheel_inertia_kgm2 = data.value("tires.wheel_rotational_inertia", "kg*m^2")

        weight_n = self.mass_kg * GRAVITY_MPS2
        self.tire = Tire(data, nominal_load_n=weight_n / 4.0)
        self.wheel_radius_m = self.tire.effective_radius_m
        self.static_front_n = weight_n * self.lr_m / self.wheelbase_m
        self.static_rear_n = weight_n * self.lf_m / self.wheelbase_m

        # 車輪の位置（車体固定座標系。x 前方、y 左方）
        self._wheel_position: Dict[str, Tuple[float, float]] = {
            "FL": (self.lf_m, self.track_front_m / 2.0),
            "FR": (self.lf_m, -self.track_front_m / 2.0),
            "RL": (-self.lr_m, self.track_rear_m / 2.0),
            "RR": (-self.lr_m, -self.track_rear_m / 2.0),
        }

    # --- 荷重 -------------------------------------------------------------

    def wheel_loads_n(self, ax_mps2: float, ay_mps2: float) -> Dict[str, float]:
        """準静的な4輪の垂直荷重 [N]。

        前後: 加速で後軸へ。**FR なので駆動輪の荷重が増える。**
        左右: 前後ロール剛性配分で分配する。
        """
        longitudinal_transfer = self.mass_kg * ax_mps2 * self.cg_height_m / self.wheelbase_m
        front_total = self.static_front_n - longitudinal_transfer
        rear_total = self.static_rear_n + longitudinal_transfer

        lateral_front = (
            self.roll_dist_front * self.mass_kg * ay_mps2
            * self.cg_height_m / self.track_front_m
        )
        lateral_rear = (
            (1.0 - self.roll_dist_front) * self.mass_kg * ay_mps2
            * self.cg_height_m / self.track_rear_m
        )

        # ay が正（左向き加速 = 左旋回）のとき荷重は右へ移る
        loads = {
            "FL": front_total / 2.0 - lateral_front,
            "FR": front_total / 2.0 + lateral_front,
            "RL": rear_total / 2.0 - lateral_rear,
            "RR": rear_total / 2.0 + lateral_rear,
        }
        # 内輪が浮いたら負にせずゼロで止める（片輪浮き）
        return {w: max(fz, 0.0) for w, fz in loads.items()}

    # --- スリップ ---------------------------------------------------------

    def _wheel_velocity(self, state: VehicleState, wheel: str, steer_rad: float):
        """車輪位置での接地点速度を、車輪座標系で返す。"""
        x, y = self._wheel_position[wheel]
        vx = state.vx_mps - state.yaw_rate_rads * y
        vy = state.vy_mps + state.yaw_rate_rads * x

        if wheel in FRONT_WHEELS:
            cos_d, sin_d = math.cos(steer_rad), math.sin(steer_rad)
            vx, vy = vx * cos_d + vy * sin_d, -vx * sin_d + vy * cos_d
        return vx, vy

    # --- 1ステップ --------------------------------------------------------

    def step(self, state: VehicleState, control: ControlInput, dt_s: float):
        """状態を dt 進め、(新しい状態, 内部量) を返す。"""
        outputs = VehicleOutputs()

        # --- 前ステップの加速度から荷重を決める（準静的） ---
        # 反復せず1ステップ遅らせる。dt が十分小さければ差は無視できる。
        ax_prev = getattr(self, "_last_ax", 0.0)
        ay_prev = getattr(self, "_last_ay", 0.0)
        fz = self.wheel_loads_n(ax_prev, ay_prev)

        # --- エンジンと駆動系 ---
        rear_omega_mean = (
            state.wheel_omega_rads["RL"] + state.wheel_omega_rads["RR"]
        ) / 2.0
        engine_omega = self.drivetrain.engine_omega_rads(rear_omega_mean, control.gear)
        engine_rpm = rads_to_rpm(engine_omega)

        if control.clutch_engaged:
            engine_torque = self.engine.torque_nm(
                max(engine_omega, 0.0), control.throttle
            )
            axle_torque = self.drivetrain.wheel_torque_nm(engine_torque, control.gear)
        else:
            engine_torque = 0.0
            axle_torque = 0.0

        torque_rl, torque_rr = self.differential.split_torque_nm(
            axle_torque, state.wheel_omega_rads["RL"], state.wheel_omega_rads["RR"]
        )
        drive_torque = {"FL": 0.0, "FR": 0.0, "RL": torque_rl, "RR": torque_rr}

        brake_front, brake_rear = self.brakes.axle_torques_nm(control.brake)
        brake_torque = {
            "FL": brake_front / 2.0, "FR": brake_front / 2.0,
            "RL": brake_rear / 2.0, "RR": brake_rear / 2.0,
        }

        # --- 各輪のタイヤ力 ---
        forces_body: Dict[str, Tuple[float, float]] = {}
        new_omega: Dict[str, float] = {}

        for wheel in WHEELS:
            vx_w, vy_w = self._wheel_velocity(state, wheel, control.steer_rad)
            omega = state.wheel_omega_rads[wheel]

            kappa = Tire.slip_ratio(omega, self.wheel_radius_m, vx_w)
            alpha = Tire.slip_angle_rad(vy_w, vx_w)
            fx_w, fy_w = self.tire.forces_n(fz[wheel], kappa, alpha)

            # 転がり抵抗（進行方向と逆）
            if abs(vx_w) > 0.1:
                fx_w -= math.copysign(self.crr * fz[wheel], vx_w)

            # 車輪の回転運動
            inertia = self.wheel_inertia_kgm2
            if wheel in REAR_WHEELS and control.clutch_engaged:
                # エンジン慣性を後輪2本で分担
                inertia += self.drivetrain.reflected_inertia_at_wheel_kgm2(control.gear) / 2.0

            brake = math.copysign(brake_torque[wheel], omega) if abs(omega) > 0.1 else 0.0
            omega_dot = (drive_torque[wheel] - brake - fx_w * self.wheel_radius_m) / inertia
            omega_new = omega + omega_dot * dt_s

            # 制動でゼロを跨いだらロックさせる（逆回転させない）
            if control.brake > 0.0 and omega * omega_new < 0.0:
                omega_new = 0.0
            new_omega[wheel] = omega_new

            # 車輪座標系 -> 車体座標系
            if wheel in FRONT_WHEELS:
                cos_d, sin_d = math.cos(control.steer_rad), math.sin(control.steer_rad)
                fx_b = fx_w * cos_d - fy_w * sin_d
                fy_b = fx_w * sin_d + fy_w * cos_d
            else:
                fx_b, fy_b = fx_w, fy_w
            forces_body[wheel] = (fx_b, fy_b)

            outputs.tire_fz_n[wheel] = fz[wheel]
            outputs.tire_fx_n[wheel] = fx_w
            outputs.tire_fy_n[wheel] = fy_w
            outputs.slip_ratio[wheel] = kappa
            outputs.slip_angle_rad[wheel] = alpha
            limit = self.tire.max_longitudinal_force_n(fz[wheel])
            outputs.utilisation[wheel] = (
                math.hypot(fx_w, fy_w) / limit if limit > 1.0 else 0.0
            )

        # --- 車体の運動 ---
        sum_fx = sum(f[0] for f in forces_body.values())
        sum_fy = sum(f[1] for f in forces_body.values())
        sum_mz = sum(
            self._wheel_position[w][0] * forces_body[w][1]
            - self._wheel_position[w][1] * forces_body[w][0]
            for w in WHEELS
        )

        sum_fx -= math.copysign(self.aero.drag_force_n(state.vx_mps), state.vx_mps)

        # **加速度と状態微分を混同しないこと。**
        #   加速度（加速度計が読む値。摩擦円で制限される）  a = F / m
        #   状態微分（車体固定系なので回転項が入る）        vx_dot = ax + vy*r
        # スピン中は vx*r が大きく、vy_dot は摩擦限界をはるかに超えうる。
        # これを ay として記録すると「μ 1.1 で 2.8g」という偽の警告が出る。
        ax_mps2 = sum_fx / self.mass_kg
        ay_mps2 = sum_fy / self.mass_kg
        vx_dot = ax_mps2 + state.vy_mps * state.yaw_rate_rads
        vy_dot = ay_mps2 - state.vx_mps * state.yaw_rate_rads
        yaw_accel = sum_mz / self.izz_kgm2

        self._last_ax = ax_mps2
        self._last_ay = ay_mps2

        new_state = VehicleState(
            vx_mps=max(state.vx_mps + vx_dot * dt_s, 0.0),
            vy_mps=state.vy_mps + vy_dot * dt_s,
            yaw_rate_rads=state.yaw_rate_rads + yaw_accel * dt_s,
            x_m=state.x_m + (state.vx_mps * math.cos(state.heading_rad)
                             - state.vy_mps * math.sin(state.heading_rad)) * dt_s,
            y_m=state.y_m + (state.vx_mps * math.sin(state.heading_rad)
                             + state.vy_mps * math.cos(state.heading_rad)) * dt_s,
            heading_rad=state.heading_rad + state.yaw_rate_rads * dt_s,
            wheel_omega_rads=new_omega,
        )

        outputs.ax_mps2 = ax_mps2
        outputs.ay_mps2 = ay_mps2
        outputs.yaw_accel_rads2 = yaw_accel
        outputs.engine_rpm = engine_rpm
        outputs.engine_torque_nm = engine_torque
        return new_state, outputs

    # --- 補助 -------------------------------------------------------------

    def initial_state(self, speed_mps: float = 0.0) -> VehicleState:
        omega = speed_mps / self.wheel_radius_m
        return VehicleState(
            vx_mps=speed_mps,
            wheel_omega_rads={w: omega for w in WHEELS},
        )

    def gear_for_speed(self, speed_mps: float) -> str:
        gear = self.drivetrain.best_gear_for_speed(
            speed_mps, self.wheel_radius_m, self.engine.redline_rpm
        )
        return gear or FORWARD_GEARS[-1]
