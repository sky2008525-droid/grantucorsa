#!/usr/bin/env python3
"""0-100km/h 全開加速を計算し、結果と出所レポートを出す.

    python3 Tools/run_acceleration.py
    python3 Tools/run_acceleration.py --plot Data/acceleration.png
    python3 Tools/run_acceleration.py --shift-time 0.25 --launch-rpm 4000

**結果の数値だけを取り出して実測と比較しないこと。** 出所レポートが示すとおり、
現在の入力にはトルクカーブ（assumed / 0.30）が含まれており、
Reality Validator の検証対象にできない（issue #1 / #3）。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "Physics"))

from longitudinal import LongitudinalModel  # noqa: E402
from units import mps_to_kmh  # noqa: E402
from vehicle_data import VehicleData  # noqa: E402


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--vehicle", default=None, help="vehicle.json のパス")
    parser.add_argument("--shift-time", type=float, default=0.40,
                        help="変速時間 [s]。実測値のばらつきの主因の1つ")
    parser.add_argument("--launch-rpm", type=float, default=3500.0, help="発進回転数 [1/min]")
    parser.add_argument("--target", type=float, default=100.0, help="目標速度 [km/h]")
    parser.add_argument("--plot", default=None, help="グラフの出力先 (PNG)")
    args = parser.parse_args(argv)

    data = VehicleData(args.vehicle) if args.vehicle else VehicleData()
    model = LongitudinalModel(data, shift_time_s=args.shift_time, launch_rpm=args.launch_rpm)
    result = model.accelerate(target_kmh=args.target)

    print("=" * 66)
    print(" ZN6 前期 GT 6MT — 0-{:.0f} km/h 全開加速".format(args.target))
    print("=" * 66)

    if result.time_to_100_kmh_s is None:
        print("  目標速度に到達しなかった")
        return 1

    print("  到達時間          : {:.2f} s".format(result.time_to_100_kmh_s))
    print("  到達距離          : {:.1f} m".format(result.distance_at_100_kmh_m))
    print("  変速              : {} 回  {}".format(
        len(result.shift_points),
        "  ".join("{:.2f}s {}->{}".format(t, a, b) for t, a, b in result.shift_points)))
    print("  トラクション限界率 : {:.0f} %".format(result.traction_limited_fraction * 100))
    print()
    print("  測定条件（車両仕様ではない。実測値がばらつく主因）")
    print("    変速時間        : {:.2f} s".format(result.shift_time_s))
    print("    発進回転数      : {:.0f} rpm".format(result.launch_rpm))

    print()
    print("-" * 66)
    print(" Physics Validity（保存則と拘束条件）")
    print("-" * 66)
    problems = model.check_physics_validity(result)
    if problems:
        for p in problems:
            print("  違反: {}".format(p))
    else:
        print("  違反なし")
        print("    - 駆動力がタイヤ摩擦限界を超えていない")
        print("    - 後軸荷重が車重を超えていない")
        print("    - 駆動仕事率がエンジン最高出力を超えていない")

    print()
    print("-" * 66)
    print(" データの出所")
    print("-" * 66)
    print(data.provenance_report())

    print()
    print("=" * 66)
    if not result.validatable:
        print(" この結果は実測値との比較に使えない。")
        print(" assumed の入力が混ざっており、一致しても偶然、外れても")
        print(" モデルの誤りとは言えない。issue #1 / #3 を先に閉じること。")
    print("=" * 66)

    if args.plot:
        _plot(model, result, Path(args.plot))
        print("\n グラフ: {}".format(args.plot))

    return 0


def _plot(model, result, out_path: Path) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    out_path.parent.mkdir(parents=True, exist_ok=True)
    t = [s.time_s for s in result.samples]

    fig, axes = plt.subplots(4, 1, figsize=(9, 11), sharex=True)

    axes[0].plot(t, [mps_to_kmh(s.speed_mps) for s in result.samples], color="#1f77b4")
    axes[0].axhline(100, ls="--", lw=0.8, color="grey")
    axes[0].set_ylabel("speed [km/h]")
    axes[0].set_title(
        "ZN6 GT 6MT  0-100 km/h = {:.2f} s   (confidence {:.2f} - NOT validatable)".format(
            result.time_to_100_kmh_s, result.confidence))

    axes[1].plot(t, [s.accel_mps2 for s in result.samples], color="#d62728")
    axes[1].set_ylabel("accel [m/s^2]")

    axes[2].plot(t, [s.tractive_force_n for s in result.samples], label="tractive", color="#2ca02c")
    axes[2].plot(t, [s.traction_limit_n for s in result.samples], ls="--",
                 label="traction limit", color="#ff7f0e")
    axes[2].plot(t, [s.drag_force_n for s in result.samples], label="aero drag", color="#9467bd")
    axes[2].set_ylabel("force [N]")
    axes[2].legend(fontsize=8)

    axes[3].plot(t, [s.engine_rpm for s in result.samples], color="#8c564b")
    axes[3].axhline(model.redline_rpm, ls="--", lw=0.8, color="grey")
    axes[3].set_ylabel("engine [rpm]")
    axes[3].set_xlabel("time [s]")

    for ax in axes:
        ax.grid(alpha=0.3)
        for shift_t, _, _ in result.shift_points:
            ax.axvline(shift_t, color="grey", lw=0.6, alpha=0.6)

    fig.tight_layout()
    fig.savefig(out_path, dpi=120)
    plt.close(fig)


if __name__ == "__main__":
    raise SystemExit(main())
