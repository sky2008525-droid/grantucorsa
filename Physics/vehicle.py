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
from clutch import Clutch
from differential import OpenDifferential, TorsenDifferential
from drivetrain import FORWARD_GEARS, Drivetrain
from engine import Engine
from setup import CarSetup
from tire import Tire
from units import GRAVITY_MPS2, rads_to_rpm, rpm_to_rads
from vehicle_data import VehicleData

# 車輪の並び。FL=前左, FR=前右, RL=後左, RR=後右
WHEELS = ("FL", "FR", "RL", "RR")

# これ以下の回転差ならクラッチはロックしているとみなす [rad/s]
# （約 20 rpm。実車のクラッチは繋がれば回転差ゼロ）
LOCK_TOLERANCE_RADS = 2.0
FRONT_WHEELS = ("FL", "FR")
REAR_WHEELS = ("RL", "RR")


@dataclass
class ControlInput:
    throttle: float = 0.0
    brake: float = 0.0
    steer_rad: float = 0.0
    gear: str = "1"
    clutch: float = 1.0
    """0.0 = 完全に切る / 1.0 = 完全に繋ぐ。**bool ではない。**

    途中の値が半クラッチ。クラッチ蹴り（切って空吹かし -> 繋ぐ）を表現するのに要る。
    """
    handbrake: float = 0.0
    """サイドブレーキの引き量 0.0-1.0。**後輪のみ**に効く。"""

    @property
    def clutch_engaged(self) -> bool:
        return self.clutch > 0.5


@dataclass
class VehicleState:
    vx_mps: float = 0.0
    vy_mps: float = 0.0
    yaw_rate_rads: float = 0.0
    x_m: float = 0.0
    y_m: float = 0.0
    heading_rad: float = 0.0
    wheel_omega_rads: Dict[str, float] = field(default_factory=lambda: {w: 0.0 for w in WHEELS})
    engine_omega_rads: float = 0.0
    """**エンジンの回転は独立した状態変数。**

    以前は車輪速度から逆算していたため、クラッチを切ってもエンジンが空吹かし
    できず、繋いでもトルクの叩き込みが起きなかった。"""

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
    clutch_torque_nm: float = 0.0
    clutch_slip_rads: float = 0.0
    tire_fz_n: Dict[str, float] = field(default_factory=dict)
    tire_fx_n: Dict[str, float] = field(default_factory=dict)
    tire_fy_n: Dict[str, float] = field(default_factory=dict)
    slip_ratio: Dict[str, float] = field(default_factory=dict)
    slip_angle_rad: Dict[str, float] = field(default_factory=dict)
    utilisation: Dict[str, float] = field(default_factory=dict)


class Vehicle:
    """ZN6（FR）の平面運動モデル。"""

    def __init__(self, data: VehicleData, use_lsd: bool = True,
                 setup: "CarSetup" = None) -> None:
        self.data = data

        # **既定は「何も変えない」。** そのときの結果は、セッティング機能を
        # 入れる前とビット単位で一致する（Tests/test_setup.py で検査）。
        self.setup = setup if setup is not None else CarSetup()

        self.engine = Engine(data)
        self.drivetrain = Drivetrain(data)
        self.brakes = Brakes(data)
        if self.setup.brake_bias is not None:
            self.brakes.bias_front = self.setup.brake_bias
        self.clutch = Clutch(data)
        self.aero = Aerodynamics(data)
        self.differential = TorsenDifferential(data) if use_lsd else OpenDifferential()

        self.mass_kg = data.value("mass.curb_mass", "kg")
        self.izz_kgm2 = data.value("inertia.Izz", "kg*m^2")
        self.wheelbase_m = data.value("dimensions.wheelbase", "m")
        self.track_front_m = data.value("dimensions.track_front", "m")
        self.track_rear_m = data.value("dimensions.track_rear", "m")
        # 車高を下げれば重心も下がる。**基準値そのものは書き換えない。**
        self.cg_height_baseline_m = data.value("inertia.cg_height", "m")
        self.cg_height_m = self.setup.cg_height_m(self.cg_height_baseline_m)
        self.lf_m = data.value("inertia.cg_longitudinal_from_front_axle", "m")
        self.lr_m = self.wheelbase_m - self.lf_m
        self.roll_dist_front = data.value(
            "suspension.roll_stiffness_distribution_front", "-"
        )
        self.crr = data.value("tires.rolling_resistance_coefficient", "-")
        self.wheel_inertia_kgm2 = data.value("tires.wheel_rotational_inertia", "kg*m^2")
        self.engine_inertia_kgm2 = data.value("engine.rotational_inertia", "kg*m^2")
        self.idle_omega_rads = rpm_to_rads(data.value("engine.idle_rpm", "1/min"))

        weight_n = self.mass_kg * GRAVITY_MPS2
        # **キャンバーを使うときだけ、その係数を読む。**
        # 常に読むと、キャンバー 0 の走行まで結果の信頼度が
        # assumed の 0.10 に落ちる（効いていない値で信頼度を下げない）。
        uses_camber = (self.setup.camber_front_rad != 0.0
                       or self.setup.camber_rear_rad != 0.0)
        self.tire = Tire(data, nominal_load_n=weight_n / 4.0,
                         read_camber=uses_camber)
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

    def wheel_loads_n(self, ax_mps2: float, ay_mps2: float,
                      normal_scale: float = 1.0) -> Dict[str, float]:
        """準静的な4輪の垂直荷重 [N]。

        前後: 加速で後軸へ。**FR なので駆動輪の荷重が増える。**
        左右: 前後ロール剛性配分で分配する。

        `normal_scale` は斜面での法線荷重の係数（平地なら 1.0）。
        傾いた面では垂直荷重が mg*cos(傾き) になる。

        **ax / ay は加速度計が読む値（タイヤ力/質量）を渡すこと。**
        斜面で停車していると、タイヤ力が重力と釣り合って ax = g*sin(傾き)
        になり、**坂の下側の軸へ荷重が移る**という正しい結果が出る。
        重力を別に足すと二重に数えることになる。
        """
        longitudinal_transfer = self.mass_kg * ax_mps2 * self.cg_height_m / self.wheelbase_m
        front_total = self.static_front_n * normal_scale - longitudinal_transfer
        rear_total = self.static_rear_n * normal_scale + longitudinal_transfer

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

    def wheel_steer_rad(self, wheel: str, steer_rad: float) -> float:
        """その車輪が向いている角度 [rad]。操舵にトーを足したもの。

        **後輪にも角度が付きうる。** 後輪トーを入れられるようにしてある。
        既定（トー 0）では後輪はちょうど 0 になり、以前と同じ計算になる。
        """
        base = steer_rad if wheel in FRONT_WHEELS else 0.0
        return base + self.setup.wheel_toe_rad(wheel)

    def _wheel_velocity(self, state: VehicleState, wheel: str, steer_rad: float):
        """車輪位置での接地点速度を、車輪座標系で返す。"""
        x, y = self._wheel_position[wheel]
        vx = state.vx_mps - state.yaw_rate_rads * y
        vy = state.vy_mps + state.yaw_rate_rads * x

        # **角度がちょうど 0 なら回さない。** 回転を通すと丸めで最下位
        # ビットが動きうる。既定のセッティングで以前と完全に一致させる
        # ため、ここで分岐する。
        angle = self.wheel_steer_rad(wheel, steer_rad)
        if angle != 0.0:
            cos_d, sin_d = math.cos(angle), math.sin(angle)
            vx, vy = vx * cos_d + vy * sin_d, -vx * sin_d + vy * cos_d
        return vx, vy

    # --- 1ステップ --------------------------------------------------------

    def step(self, state: VehicleState, control: ControlInput, dt_s: float,
             slope_gx_mps2: float = 0.0, slope_gy_mps2: float = 0.0,
             normal_scale: float = 1.0,
             contact_loads_n: Dict[str, float] = None):
        """状態を dt 進め、(新しい状態, 内部量) を返す。

        `slope_*` は斜面が車体に与える重力成分 [m/s^2]（車体固定系）、
        `normal_scale` は法線荷重の係数。**既定は平地**で、そのときの
        結果は地形を入れる前と完全に一致する（参照値が変わらない）。

        地形の値は `Physics/terrain.py` が高さ場から求める。
        **描画メッシュからは読まない**（憲法ルール4）。

        `contact_loads_n` を渡すと、垂直荷重を準静的な式ではなく
        **`Physics/ride.py` が力の釣り合いで解いた接地力**にする。

        これが無いと、**浮いた車輪のグリップが消えない。** 接地モデルは
        「その輪は地面を押していない」と言っているのに、タイヤは
        準静的な荷重（常に正）で力を出し続ける。段差で跳ねても
        タイヤが効いたままになり、飛んでいる車が曲がれてしまう。

        **既定は None で、そのときは今までと1ビットも変わらない。**
        検証済みの結果を、接続そのもので動かさないため。

        呼ぶ側は前ステップの接地力を渡す（1ステップ遅れ）。ride は
        vehicle の加速度を要るので、同時には解けない。dt が十分小さければ
        差は無視できる（荷重を1ステップ遅らせるのと同じ理屈）。
        """
        outputs = VehicleOutputs()

        # --- 垂直荷重 ---
        if contact_loads_n is None:
            # 前ステップの加速度から荷重を決める（準静的）。
            # 反復せず1ステップ遅らせる。dt が十分小さければ差は無視できる。
            ax_prev = getattr(self, "_last_ax", 0.0)
            ay_prev = getattr(self, "_last_ay", 0.0)
            fz = self.wheel_loads_n(ax_prev, ay_prev, normal_scale)
        else:
            missing = [w for w in WHEELS if w not in contact_loads_n]
            if missing:
                # **黙って準静的へ戻さない**（憲法ルール6）。
                # 戻すと「接地モデルを使っているつもりで使えていない」
                # 状態に気づけない。
                raise ValueError(
                    "contact_loads_n に {} が無い".format(", ".join(missing)))
            # **負を通さない。** 地面は押せるが引けない。
            fz = {w: max(contact_loads_n[w], 0.0) for w in WHEELS}

        # --- エンジンとクラッチ ---
        #
        # **エンジンは独立した回転状態を持つ。** 車輪から逆算しない。
        #   I_e * domega/dt = T_engine(omega, throttle) - T_clutch
        # クラッチは回転差に応じてトルクを伝え、容量で頭打ちになる。
        #
        # 数値的に硬いのでエンジンだけ細かく刻む。
        rear_omega_mean = (
            state.wheel_omega_rads["RL"] + state.wheel_omega_rads["RR"]
        ) / 2.0
        gearbox_omega = self.drivetrain.engine_omega_rads(rear_omega_mean, control.gear)

        engine_omega, clutch_torque, engine_torque, clutch_locked = self._integrate_engine(
            state.engine_omega_rads, gearbox_omega, control, dt_s
        )
        engine_rpm = rads_to_rpm(engine_omega)
        axle_torque = self.drivetrain.wheel_torque_nm(clutch_torque, control.gear)

        torque_rl, torque_rr = self.differential.split_torque_nm(
            axle_torque, state.wheel_omega_rads["RL"], state.wheel_omega_rads["RR"]
        )
        drive_torque = {"FL": 0.0, "FR": 0.0, "RL": torque_rl, "RR": torque_rr}

        brake_front, brake_rear = self.brakes.axle_torques_nm(control.brake)
        # サイドブレーキは**後輪のみ**。後輪をロックさせて横力を消す
        brake_rear += self.brakes.handbrake_axle_torque_nm(control.handbrake)
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
            camber_lean = self.setup.wheel_camber_lean_rad(wheel)
            fx_w, fy_w = self.tire.forces_n(fz[wheel], kappa, alpha, camber_lean)

            # 転がり抵抗（進行方向と逆）
            if abs(vx_w) > 0.1:
                fx_w -= math.copysign(self.crr * fz[wheel], vx_w)

            # 車輪の回転運動
            # ロック中はエンジンと車輪が一体で回るので、エンジン慣性を
            # 車輪軸へ換算して足す（1速では総比^2 = 約221倍になり支配的）。
            # 滑っている間はエンジンが切り離されているので足さない。
            inertia = self.wheel_inertia_kgm2
            if wheel in REAR_WHEELS and clutch_locked:
                inertia += self.drivetrain.reflected_inertia_at_wheel_kgm2(control.gear) / 2.0

            brake = math.copysign(brake_torque[wheel], omega) if abs(omega) > 0.1 else 0.0
            omega_dot = (drive_torque[wheel] - brake - fx_w * self.wheel_radius_m) / inertia

            # 半陰的に積分する（issue #24）。
            #
            # **陽解法だと低速で毎ステップ振動する。** fx は omega に
            # 依存する（kappa = (omega*r - v)/max(|v|,0.5)）のに、その
            # 依存を陽に扱っていたため。安定条件は
            #
            #     dt < 2*I / (C_kappa * r^2) * max(|v|, 0.5)
            #
            # で、スリップ率の分母が下限 0.5 を持つぶん低速ほど実効剛性が
            # 上がり、静止発進には dt < 0.19 ms が要った（既定は 2 ms）。
            #
            # fx の omega 依存を線形化して陰的に解く:
            #
            #     d = dt/I * (T - r*fx(omega))
            #     omega_new = omega + d / (1 + dt*r*k/I)      k = d(fx)/d(omega)
            #
            # k はその動作点での**接線剛性**を使う。線形域の c_kappa を
            # そのまま使うと、飽和している発進時に過剰減衰し、刻みに
            # よって速度が食い違った（2秒後 3.13 / 4.19 m/s）。
            #
            # **定常解は陽解法と同じ。** 分母は増分に掛かるだけで、
            # 増分がゼロになる条件（T = r*fx）を変えない。したがって
            # 収束していた結果（0-100km/h 等）は動かず、振動していた
            # 領域だけが直る。
            d_fx_d_kappa = self.tire.longitudinal_slope_n_per_slip(
                fz[wheel], kappa, alpha, camber_lean
            )
            d_fx_d_omega = d_fx_d_kappa * self.wheel_radius_m / max(abs(vx_w), 0.5)
            damping = 1.0 + dt_s * self.wheel_radius_m * d_fx_d_omega / inertia
            omega_new = omega + omega_dot * dt_s / damping

            # 制動でゼロを跨いだらロックさせる（逆回転させない）
            if control.brake > 0.0 and omega * omega_new < 0.0:
                omega_new = 0.0
            new_omega[wheel] = omega_new

            # 車輪座標系 -> 車体座標系。**速度を回したのと同じ角度で戻す。**
            # 別の角度を使うと、力と速度の向きが食い違って仕事が合わなくなる。
            angle = self.wheel_steer_rad(wheel, control.steer_rad)
            if angle != 0.0:
                cos_d, sin_d = math.cos(angle), math.sin(angle)
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
        # **重力は状態微分にだけ足す。** ax_mps2 は加速度計が読む値
        # （タイヤ力/質量）のままにしておく。そうしないと荷重移動が
        # 二重に数えられる（wheel_loads_n のコメント参照）。
        vx_dot = ax_mps2 + slope_gx_mps2 + state.vy_mps * state.yaw_rate_rads
        vy_dot = ay_mps2 + slope_gy_mps2 - state.vx_mps * state.yaw_rate_rads
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
            engine_omega_rads=engine_omega,
        )

        outputs.ax_mps2 = ax_mps2
        outputs.ay_mps2 = ay_mps2
        outputs.yaw_accel_rads2 = yaw_accel
        outputs.engine_rpm = engine_rpm
        outputs.engine_torque_nm = engine_torque
        outputs.clutch_torque_nm = clutch_torque
        outputs.clutch_slip_rads = engine_omega - gearbox_omega
        return new_state, outputs

    # --- エンジンとクラッチの積分 -------------------------------------------

    def _integrate_engine(self, engine_omega, gearbox_omega, control, dt_s, substeps=4):
        """エンジン回転を dt 進め、(新回転, クラッチトルク, エンジントルク, ロック中か) を返す。

        **ロック／スリップを切り替える。** これは車両シミュレーションの標準手法。

          ロック中: クラッチは剛体。エンジン回転は変速機入力に拘束される。
                    エンジンの慣性は駆動系を通じて車輪側に反映される。
          スリップ中: エンジンは独立した状態を持ち、クラッチは容量ぶんだけ伝える。

        剛なバネで両者を繋いだまま陽解法で解くと、エンジンと車輪が
        2質量系として発振する（実際にクラッチトルクが毎ステップ ±容量で
        振動した）。ロック時に拘束へ切り替えることでこれを避ける。
        """
        capacity = self.clutch.capacity_nm * control.clutch

        # **完全に繋がっていればロック。** 実車のクラッチは容量が
        # エンジン最大トルクの 1.3-1.8 倍あるので、繋がっていれば滑らない。
        #
        # 回転差でロック判定していたときは、時間刻みを 0.002 -> 0.004 に
        # 変えるだけでラップが 55s -> 82s になった（刻みが粗いと回転差が
        # 判定値を超えてスリップ扱いになるため）。踏み量で判定すれば
        # 時間刻みに依存しない。
        locked = control.clutch > 0.95

        if locked:
            # 拘束。エンジンは変速機入力と一体で回る
            engine_omega = max(gearbox_omega, self.idle_omega_rads)
            engine_torque = self.engine.torque_nm(engine_omega, control.throttle)
            # エンジンが出したトルクはそのままクラッチを通る。
            # 慣性による抵抗は、車輪側に反映した等価慣性が受け持つ。
            clutch_torque = engine_torque
            if abs(clutch_torque) > capacity:
                clutch_torque = math.copysign(capacity, clutch_torque)
            return engine_omega, clutch_torque, engine_torque, True

        # --- 滑っている（切っている / 半クラッチ / 回転差が大きい）---
        inertia = self.engine_inertia_kgm2
        sub_dt = dt_s / substeps
        clutch_torque = 0.0
        engine_torque = 0.0
        for _ in range(substeps):
            engine_torque = self.engine.torque_nm(max(engine_omega, 0.0), control.throttle)
            if capacity <= 0.0:
                clutch_torque = 0.0          # 完全に切れている。空吹かし
            else:
                # **回転差に比例。容量で頭打ち。**
                # 以前は常に容量いっぱいを掛けていたため、エンジンがわずかに
                # 遅いだけで -310 N*m の制動が入り、車が極端に遅くなった。
                stiffness = capacity / LOCK_TOLERANCE_RADS
                clutch_torque = stiffness * (engine_omega - gearbox_omega)
                clutch_torque = max(min(clutch_torque, capacity), -capacity)
            engine_omega += (engine_torque - clutch_torque) / inertia * sub_dt
            engine_omega = max(engine_omega, self.idle_omega_rads)

        return engine_omega, clutch_torque, engine_torque, False

    # --- 補助 -------------------------------------------------------------

    def initial_state(self, speed_mps: float = 0.0, gear: str = "1") -> VehicleState:
        omega = speed_mps / self.wheel_radius_m
        engine_omega = max(
            self.drivetrain.engine_omega_rads(omega, gear), self.idle_omega_rads
        )
        return VehicleState(
            vx_mps=speed_mps,
            wheel_omega_rads={w: omega for w in WHEELS},
            engine_omega_rads=engine_omega,
        )

    def gear_for_speed(self, speed_mps: float) -> str:
        gear = self.drivetrain.best_gear_for_speed(
            speed_mps, self.wheel_radius_m, self.engine.redline_rpm
        )
        return gear or FORWARD_GEARS[-1]
