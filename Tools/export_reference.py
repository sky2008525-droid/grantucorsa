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
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
