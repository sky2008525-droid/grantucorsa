"""最小の縦断（1自由度）モデル.

`Docs/HANDOFF.md` §5 の Day 1 用。**エンドツーエンドの検証ループを1本通すための最小構成。**

  - タイヤは Fiala の縦方向のみ（横力なし）
  - サスペンションなし。荷重移動は準静的
  - 空力は抗力のみ

**FR であることが効く点**: 加速時の荷重移動は駆動輪（後輪）に **乗る**。
FF とは逆で、トラクション限界が速度とともに緩む方向に働く。
荷重移動を無視した定荷重モデルは、FR では発進加速を **過小評価** する
（`Docs/SPEC_ZN6.md` §6.5）。

荷重移動と加速度は互いに依存するため、各ステップで不動点反復して解く。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

from aero import Aerodynamics
from drivetrain import FORWARD_GEARS, Drivetrain
from engine import Engine
from tire import Tire
from units import GRAVITY_MPS2, kmh_to_mps, mps_to_kmh, rads_to_rpm
from vehicle_data import VehicleData


@dataclass
class Sample:
    time_s: float
    speed_mps: float
    distance_m: float
    accel_mps2: float
    gear: str
    engine_rpm: float
    tractive_force_n: float
    traction_limit_n: float
    drag_force_n: float
    rear_load_n: float
    traction_limited: bool


@dataclass
class AccelerationResult:
    """加速シミュレーションの結果。

    `confidence` と `validatable` を必ず持たせる。**数値だけを取り出して
    実測と比較させない**ため（`Docs/AGENT_TOPOLOGY.md` §3）。
    """

    time_to_100_kmh_s: Optional[float]
    distance_at_100_kmh_m: Optional[float]
    samples: List[Sample] = field(default_factory=list)
    shift_points: List[Tuple[float, str, str]] = field(default_factory=list)
    confidence: float = 0.0
    validatable: bool = False
    limiting_parameter: str = ""
    shift_time_s: float = 0.0
    launch_rpm: float = 0.0

    @property
    def traction_limited_fraction(self) -> float:
        """トラクション限界に張り付いていたサンプルの割合。

        FR + 200PS では低速側でこれが高くなる。**タイヤμを動かすと直接ここが
        変わる**ので、Optimizer がμを触った影響を見るのに使える。
        """
        if not self.samples:
            return 0.0
        return sum(1 for s in self.samples if s.traction_limited) / len(self.samples)


class LongitudinalModel:
    """1自由度の縦断加速モデル。"""

    def __init__(
        self,
        data: VehicleData,
        shift_time_s: float = 0.40,
        launch_rpm: float = 3500.0,
    ) -> None:
        """
        shift_time_s / launch_rpm は **車両の仕様ではなくドライバーと測定手順の
        パラメータ**。`vehicle.json` に入れていないのはそのため。

        **公開されている 0-100km/h の実測値がばらつく主因の1つがここ。**
        変速時間が 0.2s 違えば、5回変速すれば 1.0s 変わる。出典を比較するときは
        測定条件を必ず確認すること（issue #1）。
        """
        self.data = data
        self.shift_time_s = shift_time_s
        self.launch_rpm = launch_rpm

        self.engine = Engine(data)
        self.drivetrain = Drivetrain(data)
        self.aero = Aerodynamics(data)

        self.mass_kg = data.value("mass.curb_mass", "kg")
        self.wheelbase_m = data.value("dimensions.wheelbase", "m")
        self.cg_height_m = data.value("inertia.cg_height", "m")
        self.front_fraction = data.value("mass.weight_distribution_front_pct", "-")
        self.crr = data.value("tires.rolling_resistance_coefficient", "-")

        # 静的な軸荷重
        weight_n = self.mass_kg * GRAVITY_MPS2
        self.static_front_n = weight_n * self.front_fraction
        self.static_rear_n = weight_n * (1.0 - self.front_fraction)

        # タイヤ1本あたりの公称荷重（荷重感度の基準点）
        self.tire = Tire(data, nominal_load_n=weight_n / 4.0)
        self.wheel_radius_m = self.tire.effective_radius_m

        self.redline_rpm = self.engine.redline_rpm

    # --- 荷重移動 ---------------------------------------------------------

    def rear_axle_load_n(self, accel_mps2: float) -> float:
        """加速度に応じた後軸荷重 [N]。

        FR なので **加速すると駆動輪の荷重が増える**。
        """
        transfer_n = self.mass_kg * accel_mps2 * self.cg_height_m / self.wheelbase_m
        return max(self.static_rear_n + transfer_n, 0.0)

    # --- 1ステップ --------------------------------------------------------

    def _resistance_n(self, speed_mps: float) -> float:
        drag = self.aero.drag_force_n(speed_mps)
        rolling = self.crr * self.mass_kg * GRAVITY_MPS2
        return drag + rolling

    def _tractive_force_n(self, speed_mps: float, gear: str, throttle: float) -> float:
        """エンジンが出せる駆動力 [N]（トラクション限界は考慮しない）。"""
        wheel_omega = speed_mps / self.wheel_radius_m
        engine_omega = self.drivetrain.engine_omega_rads(wheel_omega, gear)
        engine_torque = self.engine.torque_nm(engine_omega, throttle)
        wheel_torque = self.drivetrain.wheel_torque_nm(engine_torque, gear)
        return wheel_torque / self.wheel_radius_m

    def _solve_acceleration(
        self, speed_mps: float, gear: str, throttle: float, launching: bool
    ) -> Tuple[float, float, float, bool]:
        """(加速度, 駆動力, トラクション限界, 限界に当たったか) を返す。

        荷重移動 -> 後軸荷重 -> μ -> トラクション限界 -> 加速度 -> 荷重移動
        の循環を不動点反復で解く。
        """
        resistance = self._resistance_n(speed_mps)
        engine_force = self._tractive_force_n(speed_mps, gear, throttle)
        equivalent_mass = self.mass_kg + self.drivetrain.equivalent_mass_kg(
            gear, self.wheel_radius_m
        )

        accel = 0.0
        traction_limit = 0.0
        drive_force = 0.0
        for _ in range(12):
            rear_load = self.rear_axle_load_n(accel)
            # 後輪2本ぶん。μ は荷重依存なので1本あたりの荷重で評価する
            traction_limit = 2.0 * self.tire.max_longitudinal_force_n(rear_load / 2.0)

            if launching:
                # クラッチが滑っている間は、エンジンを launch_rpm 付近に保てるため
                # トラクション限界まで使えるとみなす（理想的な発進）。
                # 実際の発進はこれより遅い。0-100km/h の実測がばらつく一因（issue #1）。
                drive_force = traction_limit
            else:
                drive_force = min(engine_force, traction_limit)

            new_accel = (drive_force - resistance) / equivalent_mass
            if abs(new_accel - accel) < 1e-6:
                accel = new_accel
                break
            accel = new_accel

        limited = launching or engine_force > traction_limit
        return accel, drive_force, traction_limit, limited

    # --- シミュレーション -------------------------------------------------

    def accelerate(
        self,
        target_kmh: float = 100.0,
        dt_s: float = 0.001,
        max_time_s: float = 60.0,
        throttle: float = 1.0,
    ) -> AccelerationResult:
        """静止から目標速度までの全開加速。"""
        target_mps = kmh_to_mps(target_kmh)

        speed_mps = 0.0
        distance_m = 0.0
        time_s = 0.0
        gear = "1"
        shift_remaining_s = 0.0

        samples: List[Sample] = []
        shift_points: List[Tuple[float, str, str]] = []
        time_to_target: Optional[float] = None
        distance_at_target: Optional[float] = None

        # クラッチ完全接続まで（1速で launch_rpm に達する速度）
        lockup_speed_mps = (
            self.launch_rpm * (2.0 * math.pi / 60.0)
            / self.drivetrain.total_ratio("1")
            * self.wheel_radius_m
        )

        while time_s < max_time_s:
            launching = speed_mps < lockup_speed_mps

            if shift_remaining_s > 0.0:
                # 変速中は駆動力ゼロ。抵抗だけが効く
                accel = -self._resistance_n(speed_mps) / self.mass_kg
                drive_force = 0.0
                traction_limit = 0.0
                limited = False
                shift_remaining_s -= dt_s
            else:
                # レブリミットに達したらシフトアップ
                wheel_omega = speed_mps / self.wheel_radius_m
                rpm = rads_to_rpm(self.drivetrain.engine_omega_rads(wheel_omega, gear))
                if rpm >= self.redline_rpm and gear != FORWARD_GEARS[-1]:
                    next_gear = FORWARD_GEARS[FORWARD_GEARS.index(gear) + 1]
                    shift_points.append((time_s, gear, next_gear))
                    gear = next_gear
                    shift_remaining_s = self.shift_time_s
                    continue

                accel, drive_force, traction_limit, limited = self._solve_acceleration(
                    speed_mps, gear, throttle, launching
                )

            wheel_omega = speed_mps / self.wheel_radius_m
            rpm = rads_to_rpm(self.drivetrain.engine_omega_rads(wheel_omega, gear))

            samples.append(
                Sample(
                    time_s=time_s,
                    speed_mps=speed_mps,
                    distance_m=distance_m,
                    accel_mps2=accel,
                    gear=gear,
                    engine_rpm=max(rpm, self.engine.idle_rpm),
                    tractive_force_n=drive_force,
                    traction_limit_n=traction_limit,
                    drag_force_n=self.aero.drag_force_n(speed_mps),
                    rear_load_n=self.rear_axle_load_n(accel),
                    traction_limited=limited,
                )
            )

            if time_to_target is None and speed_mps >= target_mps:
                time_to_target = time_s
                distance_at_target = distance_m
                break

            speed_mps += accel * dt_s
            speed_mps = max(speed_mps, 0.0)
            distance_m += speed_mps * dt_s
            time_s += dt_s

        weakest = self.data.weakest()
        return AccelerationResult(
            time_to_100_kmh_s=time_to_target,
            distance_at_100_kmh_m=distance_at_target,
            samples=samples,
            shift_points=shift_points,
            confidence=self.data.result_confidence(),
            validatable=self.data.is_validatable(),
            limiting_parameter="" if weakest is None else weakest.path,
            shift_time_s=self.shift_time_s,
            launch_rpm=self.launch_rpm,
        )

    # --- 検査 -------------------------------------------------------------

    def check_physics_validity(self, result: AccelerationResult) -> List[str]:
        """保存則と拘束条件を破っていないか検査する。

        「数値が常識的に見えるか」では判定しない（`Docs/SPEC_ZN6.md` §8.4）。
        """
        problems: List[str] = []
        weight_n = self.mass_kg * GRAVITY_MPS2

        for s in result.samples:
            if s.tractive_force_n > s.traction_limit_n + 1.0:
                problems.append(
                    "t={:.3f}s: 駆動力 {:.0f}N がタイヤ摩擦限界 {:.0f}N を超えている".format(
                        s.time_s, s.tractive_force_n, s.traction_limit_n
                    )
                )
                break

        for s in result.samples:
            if s.rear_load_n > weight_n + 1.0:
                problems.append(
                    "t={:.3f}s: 後軸荷重 {:.0f}N が車重 {:.0f}N を超えている"
                    "（前輪が浮いた状態を超えている）".format(s.time_s, s.rear_load_n, weight_n)
                )
                break

        for s in result.samples:
            if s.speed_mps > 0.5 and s.tractive_force_n > 0:
                mechanical_power = s.tractive_force_n * s.speed_mps
                engine_limit = self.engine.peak_power_w()[0]
                if mechanical_power > engine_limit * 1.02:
                    problems.append(
                        "t={:.3f}s: 駆動仕事率 {:.1f}kW がエンジン最高出力 {:.1f}kW を"
                        "超えている".format(
                            s.time_s, mechanical_power / 1000.0, engine_limit / 1000.0
                        )
                    )
                    break

        return problems
