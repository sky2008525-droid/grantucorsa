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


# --- 独立検証（μ を較正していない指標での照合）----------------------------


def _braking_distance_m(car, initial_speed_mps, dt_s=0.001):
    state = car.initial_state(initial_speed_mps)
    distance = 0.0
    time_s = 0.0
    while state.vx_mps > 0.3 and time_s < 15.0:
        state, _ = car.step(state, ControlInput(brake=1.0, gear="4"), dt_s)
        distance += state.vx_mps * dt_s
        time_s += dt_s
    return distance


def test_制動距離が実測と一致する_独立検証(car):
    """**μ を較正していない指標での照合。いまのところ唯一の独立検証。**

    タイヤμはスキッドパッド（横方向 0.82g）に較正してある。制動はそこに
    合わせていないので、一致すれば独立した裏付けになる。

    実測: 60-0mph = 123 ft (37.49 m) / Edmunds。
    ただし出典が1つなので `weak`。DATA_SOURCE_POLICY.md §5 により、
    **これを根拠に Level 2/3 パラメータを変更してはいけない。** 照合のみ。
    """
    MPH = 0.44704
    distance = _braking_distance_m(car, 60 * MPH)
    assert distance == pytest.approx(37.49, rel=0.06), (
        "60-0mph が {:.2f} m。実測は 37.49 m (123 ft)".format(distance)
    )


def test_等方のμが旋回と制動を同時に再現する(car, data):
    """**旋回と制動で μ から実現 g への変換が違う。**

    実測の横 0.82g と 縦 0.978g を直接比べると比 1.19 になり、
    「タイヤの摩擦特性が円ではなく楕円だ」と解釈したくなる。**これは誤り**
    （issue #21 として起票し、検証の結果クローズした）。

      旋回: 外輪に荷重が集中し、荷重感度で μ が落ちる。内輪はほぼ寄与しない
            → 実現できる横 g は μ より大幅に低い
      制動: 荷重は前後に移るが4輪とも接地荷重が残る
            → 実現できる縦 g は μ に近い

    等方の μ=1.007 ひとつが、旋回 0.820g と 制動 0.985g を同時に再現する。
    縦μを 1.19 倍にすると制動距離が実測より 15% 短くなり、**改悪になる。**

    **実測された g どうしを直接比べてタイヤの性質を推定してはいけない。**
    """
    MPH = 0.44704
    distance = _braking_distance_m(car, 60 * MPH)
    decel_g = (60 * MPH) ** 2 / (2 * distance) / GRAVITY_MPS2

    mu = data.value("tires.friction_coefficient", "-")
    assert 0.93 < decel_g / mu < 1.02, "制動では実現 g が μ に近いはず"
    # 横方向は較正済みの 0.82g。μ に対する比は制動よりずっと低い
    assert 0.82 / mu < 0.86, "旋回では実現 g が μ よりかなり低いはず"


# --- サイドブレーキ -------------------------------------------------------


def test_サイドブレーキは後輪だけをロックする(car):
    """フットブレーキと違い前輪には効かない。

    **後輪だけをロックさせて横力を消す**ため、車が回り始める。
    ドリフトの引き起こしに使う操作。
    """
    state = car.initial_state(20.0, gear="3")
    for _ in range(500):
        state, _ = car.step(
            state, ControlInput(throttle=0.2, steer_rad=math.radians(4), gear="3"), 0.001
        )
    front_before = state.wheel_omega_rads["FL"]

    for _ in range(500):
        state, outputs = car.step(
            state,
            ControlInput(steer_rad=math.radians(4), gear="3", handbrake=1.0),
            0.001,
        )

    assert state.wheel_omega_rads["RL"] < 1.0, "後輪がロックしていない"
    assert state.wheel_omega_rads["FL"] > front_before * 0.5, "前輪まで止まっている"
    assert outputs.slip_ratio["RL"] < -0.8, "後輪が滑っていない"


def test_サイドブレーキで車が回り始める(car):
    """後輪の横力が消えるのでヨーレートが増える。"""
    state = car.initial_state(20.0, gear="3")
    for _ in range(500):
        state, _ = car.step(
            state, ControlInput(throttle=0.2, steer_rad=math.radians(4), gear="3"), 0.001
        )
    yaw_before = abs(state.yaw_rate_rads)
    sideslip_before = abs(state.sideslip_rad)

    for _ in range(700):
        state, _ = car.step(
            state, ControlInput(steer_rad=math.radians(4), gear="3", handbrake=1.0), 0.001
        )

    assert abs(state.yaw_rate_rads) > yaw_before * 1.5
    assert abs(state.sideslip_rad) > sideslip_before * 3.0


def test_サイドブレーキの引き量の範囲外を拒否する(data):
    from brake import Brakes

    with pytest.raises(ValueError):
        Brakes(data).handbrake_axle_torque_nm(1.5)


# --- クラッチ -------------------------------------------------------------


def test_繋がっているとエンジンと変速機入力が一致する(car):
    """完全に繋がったクラッチはロック。回転差ゼロ。"""
    state = car.initial_state(15.0, gear="2")
    for _ in range(500):
        state, outputs = car.step(state, ControlInput(throttle=0.5, gear="2"), 0.001)
    assert abs(outputs.clutch_slip_rads) < 1e-6
    assert outputs.clutch_torque_nm == pytest.approx(outputs.engine_torque_nm, rel=1e-6)


def test_クラッチを切るとエンジンが空吹かしする(car):
    """**エンジンが独立した回転状態を持っていることの検査。**

    以前はエンジン回転を車輪速度から逆算していたため、クラッチを切っても
    回転が上がらなかった。クラッチ蹴りが表現できない原因。
    """
    state = car.initial_state(15.0, gear="2")
    for _ in range(300):
        state, outputs = car.step(state, ControlInput(throttle=0.4, gear="2"), 0.001)
    rpm_engaged = outputs.engine_rpm

    for _ in range(250):
        state, outputs = car.step(
            state, ControlInput(throttle=1.0, gear="2", clutch=0.0), 0.001
        )

    assert outputs.engine_rpm > rpm_engaged * 1.4, "空吹かしできていない"
    assert outputs.clutch_torque_nm == 0.0, "切っているのにトルクが伝わっている"


def test_クラッチ蹴りでエンジン最大トルクを超える力が後輪に入る(car):
    """**クラッチ蹴りの本質。**

    空吹かしで溜めた回転エネルギーが、繋いだ瞬間に後輪へ叩き込まれる。
    伝わるトルクはエンジンの最大トルク(205 N*m)を超える。
    """
    state = car.initial_state(15.0, gear="2")
    for _ in range(300):
        state, _ = car.step(state, ControlInput(throttle=0.4, gear="2"), 0.001)
    for _ in range(250):
        state, _ = car.step(state, ControlInput(throttle=1.0, gear="2", clutch=0.0), 0.001)

    peak_torque = 0.0
    peak_slip_ratio = 0.0
    for i in range(150):
        engagement = min(i / 80.0, 1.0)
        state, outputs = car.step(
            state, ControlInput(throttle=1.0, gear="2", clutch=engagement), 0.001
        )
        peak_torque = max(peak_torque, abs(outputs.clutch_torque_nm))
        peak_slip_ratio = max(peak_slip_ratio, outputs.slip_ratio["RL"])

    assert peak_torque > 205.0, "エンジン最大トルクを超えていない = 慣性が効いていない"
    assert peak_slip_ratio > 0.05, "後輪が滑っていない"


def test_時間刻みを変えても結果が変わらない(data, track):
    """**数値の健全性。**

    ロック判定を回転差で行っていたとき、刻みを 0.002 -> 0.004 に変えるだけで
    ラップが 55s -> 82s になった。刻みが粗いと回転差が判定値を超えて
    スリップ扱いになるためで、物理ではなく数値の問題だった。
    """
    times = []
    for dt_s in (0.002, 0.004):
        lap_time, _ = run_lap(Vehicle(data), track, dt_s=dt_s)
        assert lap_time is not None
        times.append(lap_time)
    assert times[0] == pytest.approx(times[1], rel=0.03)
