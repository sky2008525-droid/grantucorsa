"""Phase 5-7 — 4輪モデル / AIドライバー / テストコースのテスト."""

from __future__ import annotations

import math

import pytest

from differential import OpenDifferential, TorsenDifferential
from driver import Driver, DriverConfig
from physics_test_track import closure_error, physics_test_track
from telemetry import TelemetryLog
from units import GRAVITY_MPS2, mps_to_kmh
from vehicle import FRONT_WHEELS, REAR_WHEELS, WHEELS, ControlInput, Vehicle, VehicleOutputs
from vehicle_data import VehicleData


@pytest.fixture(scope="module")
def data():
    return VehicleData()


@pytest.fixture
def car(data):
    return Vehicle(data)


@pytest.fixture(scope="module")
def track():
    return physics_test_track()


# --- コース ---------------------------------------------------------------


def test_コースが閉じている(track):
    """**閉じていないと周回にならない。**

    終端で中心線が始点へ飛び、「巨大な横ずれ」として現れる。
    総旋回角 = 180(ヘアピン) + 0(S字) + 180(大回り) = 360 deg。
    """
    position_error, heading_error = closure_error(track)
    assert position_error < 1.5, "始点に戻っていない（ずれ {:.2f} m）".format(position_error)
    assert abs(heading_error) < math.radians(1.0)


def test_必要な要素が揃っている(track):
    labels = {p.label for p in track.points}
    assert "main straight" in labels     # 全開加速
    assert "hairpin" in labels           # 強ブレーキ + 低速旋回
    assert "S1 right" in labels and "S2 left" in labels   # 切り返し
    assert "sweeper" in labels           # 高速旋回


def test_段差を含めていない(track):
    """上下方向の動特性を持たないため、段差を置いても車体が反応しない。

    「あるのに効かない」状態はモデルの限界を隠すので入れない
    （Tracks/physics_test_track.py の冒頭）。
    """
    assert not any("bump" in p.label or "段差" in p.label for p in track.points)


def test_曲率が半径と一致する(track):
    for point in track.points:
        if point.label == "hairpin":
            assert abs(1.0 / point.curvature_1pm) == pytest.approx(25.0, rel=0.01)
        if point.label == "S1 right":
            assert point.curvature_1pm < 0, "S1 は右旋回なので曲率は負"
        if point.label == "S2 left":
            assert point.curvature_1pm > 0, "S2 は左旋回なので曲率は正"


# --- 荷重移動 -------------------------------------------------------------


def test_静止時の荷重が重量配分と一致する(car):
    loads = car.wheel_loads_n(0.0, 0.0)
    total = sum(loads.values())
    assert total == pytest.approx(car.mass_kg * GRAVITY_MPS2, rel=1e-6)
    front = loads["FL"] + loads["FR"]
    assert front / total == pytest.approx(car.static_front_n / total, rel=1e-6)


def test_加速で後軸に荷重が乗る(car):
    """FR。FF とは逆向き（SPEC_ZN6.md §6.5）。"""
    static = car.wheel_loads_n(0.0, 0.0)
    accelerating = car.wheel_loads_n(4.0, 0.0)
    assert accelerating["RL"] + accelerating["RR"] > static["RL"] + static["RR"]
    assert accelerating["FL"] + accelerating["FR"] < static["FL"] + static["FR"]


def test_制動で前軸に荷重が乗る(car):
    static = car.wheel_loads_n(0.0, 0.0)
    braking = car.wheel_loads_n(-8.0, 0.0)
    assert braking["FL"] + braking["FR"] > static["FL"] + static["FR"]


def test_左旋回で荷重が右へ移る(car):
    loads = car.wheel_loads_n(0.0, 0.8 * GRAVITY_MPS2)
    assert loads["FR"] > loads["FL"]
    assert loads["RR"] > loads["RL"]


def test_荷重の合計が保存する(car):
    for ax in (-8.0, 0.0, 5.0):
        for ay in (-8.0, 0.0, 8.0):
            loads = car.wheel_loads_n(ax, ay)
            if all(v > 1.0 for v in loads.values()):   # 片輪浮きしていない範囲
                assert sum(loads.values()) == pytest.approx(
                    car.mass_kg * GRAVITY_MPS2, rel=1e-6
                )


def test_荷重が負にならない(car):
    """片輪が浮いても負荷重にしない。負にすると摩擦力が逆向きに出る。"""
    loads = car.wheel_loads_n(0.0, 3.0 * GRAVITY_MPS2)
    assert all(v >= 0.0 for v in loads.values())


# --- 平面運動 -------------------------------------------------------------


def test_直進では横運動もヨーも生じない(car):
    """モデルの左右対称性。ここが崩れていたら符号のバグがある。"""
    state = car.initial_state(20.0)
    for _ in range(500):
        state, _ = car.step(state, ControlInput(throttle=1.0, gear="3"), 0.002)
    assert abs(state.vy_mps) < 1e-9
    assert abs(state.yaw_rate_rads) < 1e-9


def test_左に切ると左へヨーする(car):
    state = car.initial_state(20.0)
    for _ in range(300):
        state, _ = car.step(
            state, ControlInput(throttle=0.2, steer_rad=math.radians(4.0), gear="3"), 0.002
        )
    assert state.yaw_rate_rads > 0.0


def test_加速度が摩擦円を超えない(car):
    """**加速度と状態微分を混同しない。**

    車体固定系の vy_dot にはヨー由来の項が入るため、スピン中は摩擦限界を
    はるかに超えうる。outputs に入れるのは a = F/m の方。
    """
    state = car.initial_state(25.0)
    mu_limit = car.tire.mu0 * GRAVITY_MPS2 * 1.15
    for _ in range(400):
        state, outputs = car.step(
            state, ControlInput(throttle=1.0, steer_rad=math.radians(6.0), gear="3"), 0.002
        )
        assert math.hypot(outputs.ax_mps2, outputs.ay_mps2) < mu_limit


def test_ブレーキで減速する(car):
    state = car.initial_state(30.0)
    for _ in range(500):
        state, _ = car.step(state, ControlInput(brake=1.0, gear="4"), 0.002)
    assert state.vx_mps < 30.0


# --- デファレンシャル -----------------------------------------------------


def test_オープンデフは常に等分する():
    diff = OpenDifferential()
    assert diff.split_torque_nm(1000.0, 50.0, 30.0) == (500.0, 500.0)


def test_LSDは速い側から遅い側へトルクを移す(data):
    """トルセンはトルク感応式。

    FR + LSD ではコーナー脱出のパワーオンで内輪から外輪へトルクが移り、
    **オーバーステアを助長する方向に働く**（SPEC_ZN6.md §6.2）。
    FF の「アンダーを消す」用途とは効き方が逆。
    """
    diff = TorsenDifferential(data)
    left, right = diff.split_torque_nm(1000.0, omega_left_rads=60.0, omega_right_rads=40.0)
    assert left < right, "速く回っている左輪のトルクが減っていない"
    assert left + right == pytest.approx(1000.0)


def test_LSDは回転差がなければ等分する(data):
    diff = TorsenDifferential(data)
    left, right = diff.split_torque_nm(1000.0, 50.0, 50.0)
    assert left == pytest.approx(right)


# --- AIドライバー ---------------------------------------------------------


def run_lap(vehicle, track, config=None, dt_s=0.004, max_time_s=200.0):
    driver = Driver(vehicle, track, config)
    state = vehicle.initial_state(5.0)
    outputs = VehicleOutputs()
    log = TelemetryLog()
    time_s = 0.0
    last_index = 0
    while time_s < max_time_s:
        control = driver.control(state, outputs, dt_s)
        state, outputs = vehicle.step(state, control, dt_s)
        time_s += dt_s
        log.record(time_s, 0.0, state, control, outputs, driver.telemetry)
        index = driver.telemetry.track_index
        if last_index > len(track.points) * 0.8 and index < len(track.points) * 0.2:
            return time_s, log
        last_index = index
        if state.vx_mps < 0.5 and time_s > 5.0:
            return None, log
    return None, log


def test_AIドライバーが1周する(car, track):
    """**第1完成目標。** 灰色の ZN6 が物理的に妥当な動きで1周する。

    ラップタイムは目標にしない（憲法ルール9）。
    """
    lap_time, log = run_lap(car, track)
    assert lap_time is not None, "1周できなかった"
    assert log.detect_anomalies() == []


def test_1周中コース内に留まる(car, track):
    lap_time, log = run_lap(car, track)
    assert lap_time is not None
    worst = max(abs(r["lateral_error_m"]) for r in log.rows)
    assert worst < track.width_m / 2.0


def test_1周中スピンしない(car, track):
    lap_time, log = run_lap(car, track)
    assert lap_time is not None
    worst = max(abs(r["sideslip_deg"]) for r in log.rows)
    assert worst < 15.0, "すべり角 {:.1f} deg。FR の破綻に近い".format(worst)


def test_安定化制御を外すとFRは破綻する(car, track):
    """**SPEC_ZN6.md §8.3 の主張の実証。**

    FF はアンダーステア主体で、限界を超えても「曲がらない」方向に破綻する。
    PID の目標値追従だけでも1周できる。

    FR はパワーオーバーステアを起こす。コーナー脱出でスロットルを開けすぎると
    後輪が縦力で飽和し、横力を失ってスピンする。

    同じ攻め方（μ の 85%）で制御の有無だけを比べる。
    """
    aggressive = DriverConfig(corner_grip_margin=0.85)
    with_control = run_lap(Vehicle(car.data), track, aggressive)

    no_control = DriverConfig(
        corner_grip_margin=0.85,
        slip_ratio_limit=99.0,        # スリップ率 TC
        grip_lateral_engage=99.0,     # 摩擦円リミッタ
        countersteer_gain=0.0,        # カウンターステア
        sideslip_warn_rad=99.0,       # スピン検出
    )
    _, log_without = run_lap(Vehicle(car.data), track, no_control)

    worst_without = max(abs(r["sideslip_deg"]) for r in log_without.rows)
    assert worst_without > 45.0, (
        "安定化制御なしでもすべり角が {:.1f} deg にしかならない。"
        "FR のモデル化が甘い可能性がある".format(worst_without)
    )


def test_攻めるほど速いとは限らない(car, track):
    """**憲法ルール9 が禁じる最適化がなぜ無意味かの実例。**

    旋回余裕率を 0.62 -> 0.85 に上げると、コーナリング速度は上がるが
    スピンと立て直しの時間で相殺され、**ラップタイムはむしろ悪化する**。
    ラップタイムを目標に調整しても、車が速くなったことにはならない。
    """
    safe, _ = run_lap(Vehicle(car.data), track, DriverConfig(corner_grip_margin=0.62))
    aggressive, log = run_lap(
        Vehicle(car.data), track, DriverConfig(corner_grip_margin=0.85)
    )
    assert safe is not None
    if aggressive is not None:
        assert aggressive > safe


# --- テレメトリ -----------------------------------------------------------


def test_テレメトリが必要な項目を記録する(car, track):
    _, log = run_lap(car, track)
    row = log.rows[0]
    for key in ("speed_kmh", "engine_rpm", "throttle", "steer_deg", "ax_g", "ay_g",
                "sideslip_deg", "RL_slip_ratio", "RR_slip_angle_deg", "FL_fz_n"):
        assert key in row


def test_異常検出が過大なGを拾う():
    """検出器そのものの検査。これが動かないと異常を見逃す。"""
    log = TelemetryLog()
    log.rows = [{
        "time_s": 1.0, "ax_g": 2.5, "ay_g": 0.1, "spin_detected": 0,
        "lateral_error_m": 0.0, "sideslip_deg": 0.0,
        "RR_fy_n": 0.0, "RL_fy_n": 0.0,
    }]
    assert any("G が過大" in p for p in log.detect_anomalies())
