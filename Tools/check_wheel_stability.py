#!/usr/bin/env python3
"""車輪回転の積分が安定しているかを測る（issue #24 の検証）.

    python3 Tools/check_wheel_stability.py

## 何を測るか

issue #24 が報告した指標と同じもの: **発進 1.5 秒地点での前輪スリップ率の
振れ幅と符号反転回数。**

振動しているとき、スリップ率は period-2 のリミットサイクルに入り、毎ステップ
符号が反転する。振れ幅がゼロに近く、符号反転がほぼ起きなければ収束している。

## issue #24 時点（陽解法）の値

    刻み依存性                  初速依存性（dt=0.001）
    dt=0.004  0.437             0.0 m/s  0.769  反転 284/299
    dt=0.002  0.132             1.0 m/s  0.272  反転 298/299
    dt=0.001  0.000009          2.0 m/s  0.044  反転 199/299
    dt=0.0005 0.000004          3.0 m/s  0.001  反転   1/299

**dt=0.002 は run_lap.py の既定値**であり、そこで振動していた。
静止発進を安定させるには dt < 0.19 ms が要った。

## 合否

**しきい値をここに書かない。** 数値を出すだけにして、判断は読む人がする。
「安定した」の定義は使う刻みによって変わるため、固定のしきい値を置くと
条件を変えたときに意味を失う。
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "Physics"))

from vehicle import ControlInput, Vehicle          # noqa: E402
from vehicle_data import VehicleData               # noqa: E402

SETTLE_S = 1.5
SAMPLE_STEPS = 300


def measure(data, dt_s, initial_speed_mps, gear="1"):
    """(振れ幅, 符号反転回数, 標本数-1, 1.5s 後の vx) を返す。"""
    vehicle = Vehicle(data)
    state = vehicle.initial_state(speed_mps=initial_speed_mps, gear=gear)
    control = ControlInput(gear=gear, throttle=1.0, brake=0.0,
                           steer_rad=0.0, clutch=1.0, handbrake=0.0)

    settle_steps = int(SETTLE_S / dt_s)
    series = []
    for step in range(settle_steps + SAMPLE_STEPS):
        state, outputs = vehicle.step(state, control, dt_s)
        if step >= settle_steps:
            series.append(outputs.slip_ratio["FL"])

    amplitude = max(series) - min(series)
    flips = sum(1 for a, b in zip(series, series[1:]) if (a > 0.0) != (b > 0.0))
    return amplitude, flips, len(series) - 1, state.vx_mps


def main() -> int:
    # **VehicleData の読み込みは1回で済ませる。** 毎回作ると 1.5 秒かかる。
    data = VehicleData()

    print("=== 刻み依存性（初速 3.0 m/s）===")
    print("  %-10s %-14s %-14s" % ("dt [s]", "振れ幅", "符号反転"))
    for dt_s in (0.004, 0.002, 0.001):
        amplitude, flips, samples, _ = measure(data, dt_s, 3.0)
        print("  %-10.4f %-14.8f %d/%d" % (dt_s, amplitude, flips, samples))

    print()
    print("=== 初速依存性（dt = 0.002 = run_lap.py の既定値）===")
    print("  %-12s %-14s %-14s %s" % ("初速 [m/s]", "振れ幅", "符号反転", "1.5s後 vx"))
    for speed_mps in (0.0, 1.0, 2.0, 3.0):
        amplitude, flips, samples, vx = measure(data, 0.002, speed_mps)
        print("  %-12.1f %-14.8f %-14s %.3f"
              % (speed_mps, amplitude, "%d/%d" % (flips, samples), vx))

    print()
    print("=== 静止発進 dt=0.002（issue #24 が最悪と報告した条件）===")
    amplitude, flips, samples, vx = measure(data, 0.002, 0.0)
    print("  振れ幅 %.8f / 符号反転 %d/%d" % (amplitude, flips, samples))
    print("  （issue #24 時点: 振れ幅 0.769 / 符号反転 284/299）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
