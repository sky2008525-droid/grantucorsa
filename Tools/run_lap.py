#!/usr/bin/env python3
"""AIドライバーで Physics Test Track を走らせる.

    python3 Tools/run_lap.py
    python3 Tools/run_lap.py --laps 2 --csv Data/lap.csv --plot Data/lap.png
    python3 Tools/run_lap.py --open-diff      # LSD を外して比較する

**目標はラップタイムではなく「事故らず1周すること」**（SPEC_ZN6.md §8.3）。
タイムを縮める調整はしない（憲法ルール9）。
"""

from __future__ import annotations

import argparse
import math
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "Physics"))
sys.path.insert(0, str(REPO_ROOT / "Tracks"))

from driver import Driver, DriverConfig  # noqa: E402
from physics_test_track import physics_test_track  # noqa: E402
from telemetry import TelemetryLog  # noqa: E402
from units import GRAVITY_MPS2, mps_to_kmh  # noqa: E402
from vehicle import ControlInput, Vehicle, VehicleOutputs  # noqa: E402
from vehicle_data import VehicleData  # noqa: E402


def run(vehicle, track, laps=1, dt_s=0.002, max_time_s=400.0, config=None):
    driver = Driver(vehicle, track, config)
    state = vehicle.initial_state(speed_mps=5.0)
    outputs = VehicleOutputs()
    log = TelemetryLog()

    time_s = 0.0
    distance_m = 0.0
    lap_times = []
    last_index = 0
    lap_start_s = 0.0
    completed = 0

    while time_s < max_time_s and completed < laps:
        control = driver.control(state, outputs, dt_s)
        state, outputs = vehicle.step(state, control, dt_s)

        distance_m += state.vx_mps * dt_s
        time_s += dt_s
        log.record(time_s, distance_m, state, control, outputs, driver.telemetry)

        index = driver.telemetry.track_index
        if last_index > len(track.points) * 0.8 and index < len(track.points) * 0.2:
            completed += 1
            lap_times.append(time_s - lap_start_s)
            lap_start_s = time_s
        last_index = index

        if state.vx_mps < 0.5 and time_s > 5.0:
            break

    return log, lap_times, time_s, completed


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--laps", type=int, default=1)
    parser.add_argument("--dt", type=float, default=0.002)
    parser.add_argument("--csv", default=None)
    parser.add_argument("--plot", default=None)
    parser.add_argument("--open-diff", action="store_true",
                        help="LSD を外す（比較基準）")
    parser.add_argument("--no-traction-control", action="store_true",
                        help="安定化制御を全て外す（FR の破綻を見る）")
    parser.add_argument("--grip-margin", type=float, default=None,
                        help="コーナリングで使う mu の割合。既定 0.62（安全側）")
    args = parser.parse_args(argv)

    data = VehicleData()
    vehicle = Vehicle(data, use_lsd=not args.open_diff)
    track = physics_test_track()

    config = DriverConfig()
    if args.grip_margin is not None:
        config.corner_grip_margin = args.grip_margin
    if args.no_traction_control:
        config.slip_ratio_limit = 99.0          # スリップ率 TC
        config.grip_lateral_engage = 99.0       # 摩擦円リミッタ
        config.countersteer_gain = 0.0          # カウンターステア
        config.sideslip_warn_rad = 99.0         # スピン検出

    log, lap_times, total_s, completed = run(
        vehicle, track, laps=args.laps, dt_s=args.dt, config=config
    )

    print("=" * 66)
    print(" {} — {:.0f} m".format(track.name, track.length_m))
    print("=" * 66)
    print("  デフ            : {}".format("Open（比較基準）" if args.open_diff else "トルセンLSD"))
    print("  安定化制御       : {}".format(
        "なし" if args.no_traction_control else "TC + 摩擦円リミッタ + スピン検出"))
    print("  旋回余裕率       : {:.2f}".format(config.corner_grip_margin))
    print("  完走ラップ       : {} / {}".format(completed, args.laps))
    for i, t in enumerate(lap_times, 1):
        print("    Lap {}          : {:.2f} s".format(i, t))
    if completed < args.laps:
        print("  ** 1周できなかった **  経過 {:.1f}s".format(total_s))

    rows = log.rows
    print()
    print("  最高速度         : {:.1f} km/h".format(max(r["speed_kmh"] for r in rows)))
    print("  最大横G          : {:.2f} g".format(max(abs(r["ay_g"]) for r in rows)))
    print("  最大減速G        : {:.2f} g".format(abs(min(r["ax_g"] for r in rows))))
    print("  最大すべり角      : {:.1f} deg".format(max(abs(r["sideslip_deg"]) for r in rows)))
    print("  最大横ずれ        : {:.2f} m".format(max(abs(r["lateral_error_m"]) for r in rows)))
    tc = sum(1 for r in rows if r["traction_cut"] < 0.999) / len(rows)
    print("  TC 介入率        : {:.1f} %".format(tc * 100))

    print()
    print("-" * 66)
    print(" テレメトリ解析（異常挙動）")
    print("-" * 66)
    problems = log.detect_anomalies()
    if problems:
        for p in problems:
            print("  ! {}".format(p))
    else:
        print("  異常なし")
        print("    - スピン検出なし")
        print("    - 中心線からの逸脱なし")
        print("    - リアの急激なブレイクなし")
        print("    - タイヤμから出せない G が出ていない")

    print()
    print("-" * 66)
    print(" データの出所")
    print("-" * 66)
    print(data.provenance_report())

    if args.csv:
        log.write_csv(Path(args.csv))
        print("\n テレメトリ: {} ({} 行)".format(args.csv, len(rows)))
    if args.plot:
        _plot(track, log, Path(args.plot))
        print(" グラフ: {}".format(args.plot))

    return 0 if completed >= args.laps else 1


def _plot(track, log, out_path: Path) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    out_path.parent.mkdir(parents=True, exist_ok=True)
    rows = log.rows
    fig = plt.figure(figsize=(13, 9))

    ax_map = fig.add_subplot(2, 2, 1)
    ax_map.plot([p.x_m for p in track.points], [p.y_m for p in track.points],
                color="lightgrey", lw=6, solid_capstyle="round", label="centreline")
    sc = ax_map.scatter([r["distance_m"] for r in rows][:0], [], s=1)
    xs, ys, cs = [], [], []
    x = y = h = 0.0
    # 走行軌跡はテレメトリに座標を持っていないので、track_index から近似せず
    # 速度で色付けした中心線として描く
    idx_speed = {}
    for r in rows:
        idx_speed.setdefault(int(r["track_index"]), []).append(r["speed_kmh"])
    for i, p in enumerate(track.points):
        if i in idx_speed:
            xs.append(p.x_m); ys.append(p.y_m)
            cs.append(sum(idx_speed[i]) / len(idx_speed[i]))
    sc = ax_map.scatter(xs, ys, c=cs, cmap="viridis", s=8)
    fig.colorbar(sc, ax=ax_map, label="speed [km/h]")
    ax_map.set_aspect("equal")
    ax_map.set_title("Physics Test Track")
    ax_map.grid(alpha=0.3)

    t = [r["time_s"] for r in rows]

    ax_speed = fig.add_subplot(2, 2, 2)
    ax_speed.plot(t, [r["speed_kmh"] for r in rows], label="actual", color="#1f77b4")
    ax_speed.plot(t, [r["target_speed_kmh"] for r in rows], ls="--", lw=0.9,
                  label="target", color="#ff7f0e")
    ax_speed.set_ylabel("speed [km/h]"); ax_speed.legend(fontsize=8); ax_speed.grid(alpha=0.3)

    ax_g = fig.add_subplot(2, 2, 3)
    ax_g.plot(t, [r["ax_g"] for r in rows], label="ax", color="#d62728")
    ax_g.plot(t, [r["ay_g"] for r in rows], label="ay", color="#2ca02c")
    ax_g.set_ylabel("g"); ax_g.set_xlabel("time [s]")
    ax_g.legend(fontsize=8); ax_g.grid(alpha=0.3)

    ax_slip = fig.add_subplot(2, 2, 4)
    ax_slip.plot(t, [r["sideslip_deg"] for r in rows], label="sideslip", color="#9467bd")
    ax_slip.plot(t, [r["RR_slip_angle_deg"] for r in rows], lw=0.8,
                 label="RR slip angle", color="#8c564b")
    ax_slip.plot(t, [r["traction_cut"] * 10 for r in rows], lw=0.8,
                 label="TC cut x10", color="#e377c2")
    ax_slip.set_ylabel("deg"); ax_slip.set_xlabel("time [s]")
    ax_slip.legend(fontsize=8); ax_slip.grid(alpha=0.3)

    fig.tight_layout()
    fig.savefig(out_path, dpi=110)
    plt.close(fig)


if __name__ == "__main__":
    raise SystemExit(main())
