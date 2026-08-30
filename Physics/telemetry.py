"""テレメトリ記録（CSV）.

`Docs/SPEC_ZN6.md` §8.3。Speed / RPM / Throttle / Steering / G / Slip Angle 等を
記録し、異常挙動（**リアの急激なブレイク**）を検出できるようにする。
"""

from __future__ import annotations

import csv
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

from units import GRAVITY_MPS2, mps_to_kmh
from vehicle import WHEELS, ControlInput, VehicleOutputs, VehicleState

COLUMNS = [
    "time_s", "distance_m", "track_index",
    "speed_kmh", "vx_mps", "vy_mps", "yaw_rate_rads", "sideslip_deg",
    "ax_g", "ay_g",
    "throttle", "brake", "steer_deg", "gear", "engine_rpm",
    "target_speed_kmh", "lateral_error_m", "traction_cut", "spin_detected",
] + [
    "{}_{}".format(w, k)
    for w in WHEELS
    for k in ("fz_n", "fx_n", "fy_n", "slip_ratio", "slip_angle_deg", "utilisation")
]


@dataclass
class TelemetryLog:
    rows: List[Dict[str, float]] = field(default_factory=list)

    def record(
        self, time_s: float, distance_m: float, state: VehicleState,
        control: ControlInput, outputs: VehicleOutputs, driver_telemetry,
    ) -> None:
        import math

        row: Dict[str, float] = {
            "time_s": round(time_s, 4),
            "distance_m": round(distance_m, 3),
            "track_index": driver_telemetry.track_index,
            "speed_kmh": mps_to_kmh(state.vx_mps),
            "vx_mps": state.vx_mps,
            "vy_mps": state.vy_mps,
            "yaw_rate_rads": state.yaw_rate_rads,
            "sideslip_deg": math.degrees(state.sideslip_rad),
            "ax_g": outputs.ax_mps2 / GRAVITY_MPS2,
            "ay_g": outputs.ay_mps2 / GRAVITY_MPS2,
            "throttle": control.throttle,
            "brake": control.brake,
            "steer_deg": math.degrees(control.steer_rad),
            "gear": int(control.gear),
            "engine_rpm": outputs.engine_rpm,
            "target_speed_kmh": mps_to_kmh(driver_telemetry.target_speed_mps),
            "lateral_error_m": driver_telemetry.lateral_error_m,
            "traction_cut": driver_telemetry.traction_cut,
            "spin_detected": int(driver_telemetry.spin_detected),
        }
        for w in WHEELS:
            row["{}_fz_n".format(w)] = outputs.tire_fz_n.get(w, 0.0)
            row["{}_fx_n".format(w)] = outputs.tire_fx_n.get(w, 0.0)
            row["{}_fy_n".format(w)] = outputs.tire_fy_n.get(w, 0.0)
            row["{}_slip_ratio".format(w)] = outputs.slip_ratio.get(w, 0.0)
            row["{}_slip_angle_deg".format(w)] = math.degrees(outputs.slip_angle_rad.get(w, 0.0))
            row["{}_utilisation".format(w)] = outputs.utilisation.get(w, 0.0)
        self.rows.append(row)

    def write_csv(self, path: Path) -> None:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=COLUMNS)
            writer.writeheader()
            writer.writerows(self.rows)

    # --- 異常検出 ---------------------------------------------------------

    def detect_anomalies(self) -> List[str]:
        """テレメトリから異常挙動を拾う。

        SPEC_ZN6.md §8.3「AIが100周分のログを解析し、異常挙動
        （例: リアの急激なブレイク）を検出する」の最初の実装。
        """
        problems: List[str] = []
        if not self.rows:
            return ["テレメトリが空"]

        spins = [r for r in self.rows if r["spin_detected"]]
        if spins:
            problems.append(
                "スピン検出が {} サンプル。最初は t={:.2f}s（すべり角 {:.1f}deg）".format(
                    len(spins), spins[0]["time_s"], spins[0]["sideslip_deg"]
                )
            )

        worst_error = max(abs(r["lateral_error_m"]) for r in self.rows)
        if worst_error > 6.0:
            problems.append("中心線からの最大横ずれ {:.1f} m（コース幅を超えている）".format(worst_error))

        # リアの急激なブレイク: 後輪の横力が短時間で大きく落ちる
        for i in range(1, len(self.rows)):
            prev, cur = self.rows[i - 1], self.rows[i]
            drop = abs(prev["RR_fy_n"]) + abs(prev["RL_fy_n"]) - (
                abs(cur["RR_fy_n"]) + abs(cur["RL_fy_n"])
            )
            if drop > 3000.0 and abs(cur["ay_g"]) > 0.3:
                problems.append(
                    "t={:.2f}s でリアの横力が {:.0f}N 急減（リアのブレイク）".format(
                        cur["time_s"], drop
                    )
                )
                break

        for r in self.rows:
            if abs(r["ax_g"]) > 1.6 or abs(r["ay_g"]) > 1.6:
                problems.append(
                    "t={:.2f}s で G が過大（ax={:.2f}g ay={:.2f}g）。"
                    "タイヤμ {:.2f} では出せない".format(
                        r["time_s"], r["ax_g"], r["ay_g"], 1.1
                    )
                )
                break

        return problems
