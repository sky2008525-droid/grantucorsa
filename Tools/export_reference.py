#!/usr/bin/env python3
"""Python 版の計算結果を JSON へ書き出す（UE5 版との突き合わせ用）.

`Docs/SPEC_ZN6.md` §10.3「Python 版と UE5 版の 0-100km/h が一致する」を
機械的に判定するための参照データを作る。

**なぜ数値をテストコードに直接書かないか**

C++ 側のテストに期待値をベタ書きすると、Python 側を変更したときに
C++ 側が古い値のまま通り続ける。どちらが正しいのか分からなくなる。
**Python 実装を唯一の基準とし、参照値はそこから生成する。**

使い方:

    python3 Tools/export_reference.py

出力先: Unreal/ZN6DigitalTwin/Reference/longitudinal_reference.json
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "Physics"))

from engine import Engine                      # noqa: E402
from longitudinal import LongitudinalModel     # noqa: E402
from vehicle import ControlInput, Vehicle      # noqa: E402
from vehicle_data import VehicleData           # noqa: E402

OUTPUT = REPO_ROOT / "Unreal" / "ZN6DigitalTwin" / "Reference" / "longitudinal_reference.json"

# トルクカーブを検査する回転数 [1/min]。
# **谷（4,000rpm 付近）とその前後を必ず含める。** ここが補間方式の差が
# 最も出る場所であり、単純な線形補間に落ちていたらここで検出できる
# （Docs/ZN6_BASELINE.md 罠③）。
TORQUE_PROBE_RPM = [
    700, 1000, 1234, 1500, 2000, 2500, 2650, 2800, 3000, 3100, 3200,
    3500, 3700, 3800, 3900, 4000, 4100, 4200, 4350, 4500, 4800,
    5000, 5250, 5500, 6000, 6200, 6400, 6500, 6600, 6800,
    7000, 7100, 7200, 7300, 7400,
]

# 加速シミュレーションの条件。**車両仕様ではなく測定手順のパラメータ**
# （Docs/DATA_SOURCE_POLICY.md §2）。C++ 側も同じ値を使わなければ比較にならない。
SHIFT_TIME_S = 0.25
LAUNCH_RPM = 3500.0
DT_S = 0.001


# --- 4輪モデルの参照シナリオ -------------------------------------------------
#
# **横力・ヨー・LSD・荷重移動を通る経路を選ぶこと。** 直進だけだと
# 4輪モデルのうち縦断モデルと重なる部分しか検査できない。
#
# 各シナリオは (名前, 初速[m/s], ギア, 制御, ステップ数, 刻み[s])。

# **刻みは 0.001s。0.002s を使わない。**
#
# 車輪回転の陽解法積分は数値的に硬く、dt >= 0.002s では前輪が毎ステップ
# 振動する（発進1.5秒地点でのスリップ率の振れ幅）:
#
#   dt=0.004  0.437      発散に近い
#   dt=0.002  0.132      振動している（run_lap.py の既定値）
#   dt=0.001  0.000009   収束
#   dt=0.0005 0.000004   収束（0.001 と同じ答え）
#
# 振動している状態は period-2 のリミットサイクルで、どちらの位相で
# サンプリングするかが浮動小数の微小差で変わる。**この領域で2つの実装を
# 突き合わせても、比較しているのは物理ではなくリミットサイクルの位相**に
# なってしまう。安定領域で比較しないと移植の正しさを判定できない。
#
# **これは移植で見つかった Python 側のバグであり、参照値の都合で刻みを
# 変えているのではない。** 詳細と影響範囲は issue を参照。
VEHICLE_TIMESTEP_S = 0.001

VEHICLE_SCENARIOS = [
    # 全開加速。トラクション限界・LSD・後軸への荷重移動が効く。
    #
    # **静止（0 m/s）から始めない。** 陽解法の安定条件は
    #
    #     dt < 2*I_wheel / (C_kappa * r^2) * max(|vx|, 0.5)
    #
    # で、**前輪は等価慣性が乗らないぶん軽く（I=1.2）、先に壊れる**。
    # スリップ率の分母が max(|vx|, 0.5) で下限を持つため、低速ほど
    # 実効剛性が上がり不安定になる。dt=0.001 での実測:
    #
    #   初速 0.0 m/s  振れ幅 0.769  符号反転 284/299 回   振動
    #   初速 1.0 m/s  振れ幅 0.272  符号反転 298/299 回   振動
    #   初速 2.0 m/s  振れ幅 0.044  符号反転 199/299 回   振動
    #   初速 2.6 m/s  振れ幅 0.001  符号反転   7/299 回   ほぼ収束
    #   初速 3.0 m/s  振れ幅 0.001  符号反転   1/299 回   収束
    #
    # 遷移点 2.6 m/s は上式からの予測 2.57 m/s と一致する。
    # **静止発進を安定させるには dt < 0.19 ms が要る**（run_lap.py の
    # 既定は 2 ms）。これは移植で見つかった Python 側のバグであり、
    # 参照値の都合ではない。詳細は issue を参照。
    ("accel_1st_from_3mps", 3.0, "1",
     {"throttle": 1.0, "brake": 0.0, "steer_rad": 0.0, "clutch": 1.0, "handbrake": 0.0},
     3000, VEHICLE_TIMESTEP_S),
    # 定常旋回に近い状態。横力・ヨー・左右の荷重移動・複合スリップが効く
    ("cornering_3rd", 60.0 / 3.6, "3",
     {"throttle": 0.30, "brake": 0.0, "steer_rad": 0.05, "clutch": 1.0, "handbrake": 0.0},
     3000, VEHICLE_TIMESTEP_S),
    # 全制動。前軸への荷重移動とロック挙動が効く
    ("braking_4th", 100.0 / 3.6, "4",
     {"throttle": 0.0, "brake": 1.0, "steer_rad": 0.0, "clutch": 1.0, "handbrake": 0.0},
     2000, VEHICLE_TIMESTEP_S),
    # パワーオンでの旋回。**FR のパワーオーバーステアが出る条件**
    ("power_on_oversteer_2nd", 45.0 / 3.6, "2",
     {"throttle": 1.0, "brake": 0.0, "steer_rad": 0.08, "clutch": 1.0, "handbrake": 0.0},
     2400, VEHICLE_TIMESTEP_S),
]

WHEELS = ("FL", "FR", "RL", "RR")


def state_snapshot(state, outputs):
    """比較に使う状態量を辞書にする。**丸めない。** 実装差を消してしまう。"""
    return {
        "vx_mps": state.vx_mps,
        "vy_mps": state.vy_mps,
        "yaw_rate_rads": state.yaw_rate_rads,
        "x_m": state.x_m,
        "y_m": state.y_m,
        "heading_rad": state.heading_rad,
        "engine_omega_rads": state.engine_omega_rads,
        "wheel_omega_rads": {w: state.wheel_omega_rads[w] for w in WHEELS},
        "ax_mps2": outputs.ax_mps2,
        "ay_mps2": outputs.ay_mps2,
        "yaw_accel_rads2": outputs.yaw_accel_rads2,
        "engine_torque_nm": outputs.engine_torque_nm,
        "clutch_torque_nm": outputs.clutch_torque_nm,
        "tire_fz_n": {w: outputs.tire_fz_n[w] for w in WHEELS},
        "tire_fx_n": {w: outputs.tire_fx_n[w] for w in WHEELS},
        "tire_fy_n": {w: outputs.tire_fy_n[w] for w in WHEELS},
        "slip_ratio": {w: outputs.slip_ratio[w] for w in WHEELS},
        "slip_angle_rad": {w: outputs.slip_angle_rad[w] for w in WHEELS},
    }


def run_vehicle_scenario(data, name, speed_mps, gear, control_kwargs, steps, dt_s):
    """4輪モデルを決められた入力で回し、途中と最後の状態を返す。"""
    vehicle = Vehicle(data)
    state = vehicle.initial_state(speed_mps=speed_mps, gear=gear)
    control = ControlInput(gear=gear, **control_kwargs)

    midpoint = steps // 2
    snapshots = {}
    outputs = None

    for step in range(steps):
        state, outputs = vehicle.step(state, control, dt_s)
        if step + 1 == midpoint:
            snapshots["midpoint"] = state_snapshot(state, outputs)

    snapshots["final"] = state_snapshot(state, outputs)
    return {
        "_note": "Vehicle.step() を同じ入力で {} ステップ回した結果".format(steps),
        "initial_speed_mps": speed_mps,
        "gear": gear,
        "control": control_kwargs,
        "steps": steps,
        "dt_s": dt_s,
        "midpoint_step": midpoint,
        "snapshots": snapshots,
    }


def main() -> int:
    data = VehicleData()
    engine = Engine(data)
    model = LongitudinalModel(data, shift_time_s=SHIFT_TIME_S, launch_rpm=LAUNCH_RPM)
    result = model.accelerate(dt_s=DT_S)

    if result.time_to_100_kmh_s is None:
        print("ERROR: 100km/h に到達しなかった。参照値を出力できない。", file=sys.stderr)
        return 1

    payload = {
        "_meta": {
            "generator": "Tools/export_reference.py",
            "purpose": (
                "Python 実装を基準として UE5(C++) 実装を突き合わせるための参照値。"
                "**手で編集しないこと。** Python 側を変えたら再生成する。"
            ),
            "vehicle_json": "Vehicles/ZN6/vehicle.json",
            "confidence": result.confidence,
            "validatable": result.validatable,
            "CRITICAL": (
                "confidence が低いのは Python 実装が間違っているという意味ではなく、"
                "入力データに assumed が混ざっているという意味。"
                "**この参照値は『2つの実装が同じ計算をしているか』の判定にのみ使う。**"
                "実車と一致しているかの判定には使えない（Docs/AGENT_TOPOLOGY.md §3）。"
            ),
        },
        "test_conditions": {
            "shift_time_s": SHIFT_TIME_S,
            "launch_rpm": LAUNCH_RPM,
            "dt_s": DT_S,
            "target_kmh": 100.0,
            "throttle": 1.0,
        },
        "torque_curve": {
            "_note": (
                "全開時のクランク軸トルク [N*m]。PCHIP（単調3次補間）で評価した値。"
                "**線形補間で置き換えると 4,000rpm 付近の谷の形が変わり、ここで落ちる。**"
            ),
            "rpm": TORQUE_PROBE_RPM,
            "wot_torque_nm": [engine.wot_torque_nm(float(r)) for r in TORQUE_PROBE_RPM],
        },
        "acceleration_0_100_kmh": {
            "time_s": result.time_to_100_kmh_s,
            "distance_m": result.distance_at_100_kmh_m,
            "shift_count": len(result.shift_points),
            "shift_points": [
                {"time_s": t, "from": before, "to": after}
                for t, before, after in result.shift_points
            ],
            "traction_limited_fraction": result.traction_limited_fraction,
        },
        "vehicle_scenarios": {
            name: run_vehicle_scenario(VehicleData(), name, speed, gear, control, steps, dt)
            for name, speed, gear, control, steps, dt in VEHICLE_SCENARIOS
        },
    }

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    with OUTPUT.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        handle.write("\n")

    print("書き出した: {}".format(OUTPUT.relative_to(REPO_ROOT)))
    print("  0-100km/h : {:.4f} s".format(result.time_to_100_kmh_s))
    print("  距離       : {:.2f} m".format(result.distance_at_100_kmh_m))
    print("  変速       : {} 回".format(len(result.shift_points)))
    print("  confidence : {:.2f}（実測比較には使えない）".format(result.confidence))
    print()
    print("  4輪モデルの参照シナリオ:")
    for name, scenario in payload["vehicle_scenarios"].items():
        final = scenario["snapshots"]["final"]
        print("    {:<24s} vx={:7.3f} m/s  vy={:+7.4f} m/s  r={:+8.5f} rad/s".format(
            name, final["vx_mps"], final["vy_mps"], final["yaw_rate_rads"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
