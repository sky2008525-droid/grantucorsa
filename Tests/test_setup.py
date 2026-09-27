"""セッティングの検査.

**スライダーが本当に効くかを確かめる。**

セッティング画面でいちばんやってはいけないのは、動かしても何も変わらない
項目を置くことである。数値を捏造するのと同じ性質の嘘になる。
だからここでは、項目ごとに**「動かしたら物理が変わる」ことを実測する。**

同時に、その逆も確かめる:
**既定（何も変えない）では、結果がビット単位で以前と一致すること。**
セッティング機能を足したこと自体で検証済みの結果が動いてはいけない。
"""

from __future__ import annotations

import math

import pytest

from ride import WHEELS, RideModel, RideState
from setup import UNSUPPORTED, CarSetup, SetupLimits
from vehicle import ControlInput, Vehicle
from vehicle_data import VehicleData

FLAT = {wheel: 0.0 for wheel in WHEELS}


@pytest.fixture(scope="module")
def data():
    return VehicleData()


@pytest.fixture(scope="module")
def limits(data):
    return SetupLimits(data)


def drive(data, setup, steps=600, steer_rad=0.0, throttle=1.0, gear="3",
          speed_kmh=80.0):
    """同じ入力で走らせて、終わりの状態を返す。"""
    vehicle = Vehicle(data, setup=setup)
    state = vehicle.initial_state(speed_mps=speed_kmh / 3.6, gear=gear)
    control = ControlInput(gear=gear, throttle=throttle, brake=0.0,
                           steer_rad=steer_rad, clutch=1.0, handbrake=0.0)
    outputs = None
    for _ in range(steps):
        state, outputs = vehicle.step(state, control, 0.002)
    return state, outputs


# --- 既定は何も変えない -----------------------------------------------------


def test_既定のセッティングは中立(limits):
    setup = CarSetup()
    assert setup.is_default()
    assert setup.validate(limits) == []
    assert setup.describe() == "純正（何も変更していない）"


def test_既定なら走行結果がビット単位で一致する(data):
    """**セッティング機能を足したこと自体で結果が動かないこと。**

    `CarSetup()` を明示的に渡した場合と、渡さなかった場合が一致する。
    """
    implicit, _ = drive(data, None, steer_rad=0.03)
    explicit, _ = drive(data, CarSetup(), steer_rad=0.03)

    assert implicit.vx_mps == explicit.vx_mps
    assert implicit.vy_mps == explicit.vy_mps
    assert implicit.yaw_rate_rads == explicit.yaw_rate_rads
    assert implicit.x_m == explicit.x_m
    assert implicit.y_m == explicit.y_m
    assert implicit.heading_rad == explicit.heading_rad


def test_既定ではキャンバー係数を読まない(data):
    """**効いていない値で結果の信頼度を下げない。**

    `camber_stiffness_per_load` は assumed / 0.10。常に読むと、
    キャンバー 0 の走行まで信頼度が 0.10 に落ちる。
    """
    plain = Vehicle(data)
    assert plain.tire.camber_stiffness_per_load is None

    cambered = Vehicle(data, setup=CarSetup(camber_front_rad=math.radians(-2.0)))
    assert cambered.tire.camber_stiffness_per_load is not None


def test_係数を読まずにキャンバーを渡したら止まる(data):
    """**黙って 0 として扱わない**（憲法ルール6）。"""
    tire = Vehicle(data).tire
    with pytest.raises(ValueError):
        tire.forces_n(3000.0, 0.0, 0.0, math.radians(-2.0))
    with pytest.raises(ValueError):
        tire.longitudinal_slope_n_per_slip(3000.0, 0.0, 0.0, math.radians(-2.0))


# --- 範囲 -------------------------------------------------------------------


def test_調整範囲はvehicle_jsonのmin_maxを超えない(data, limits):
    """**憲法の権限表を破らない。**"""
    for path, name in (("suspension.spring_rate_front", "spring_scale_front"),
                       ("suspension.spring_rate_rear", "spring_scale_rear")):
        param = data.param(path)
        allowed = limits.all_ranges()[name]
        base = data.value(path, "N/m")
        assert allowed.low * base == pytest.approx(param.minimum, rel=1e-12)
        assert allowed.high * base == pytest.approx(param.maximum, rel=1e-12)


def test_車高の下限が地上高を残す(data, limits):
    """**地面に擦る車高を選べるようにしない。**"""
    clearance_m = data.value("dimensions.ground_clearance", "m")
    lowest = limits.ride_height.low
    assert clearance_m + lowest >= 0.059, (
        "最低地上高が {:.3f} m しか残らない".format(clearance_m + lowest))


def test_範囲外を検出する(limits):
    problems = CarSetup(ride_height_m=-0.5,
                        camber_front_rad=math.radians(-30.0)).validate(limits)
    assert len(problems) == 2
    assert any("車高" in problem for problem in problems)


def test_範囲に丸められる(limits):
    clamped = CarSetup(ride_height_m=-0.5).clamped(limits)
    assert clamped.ride_height_m == limits.ride_height.low
    assert clamped.validate(limits) == []


def test_効かない項目に理由が書いてある():
    """**「無い」と「実装が抜けている」を区別できるようにする。**"""
    assert UNSUPPORTED
    for name, reason in UNSUPPORTED.items():
        assert len(reason) > 20, "{} の理由が短すぎる".format(name)
        # 理由は「なぜ出せないか」を具体的に言っていること。
        # 「対応していません」だけでは、実装漏れと区別できない。
        assert ("unknown" in reason or "無い" in reason
                or "Level 0" in reason or "assumed" in reason), name


# --- 車高 -------------------------------------------------------------------


def test_車高を下げると重心が下がる(data):
    baseline = Vehicle(data)
    lowered = Vehicle(data, setup=CarSetup(ride_height_m=-0.030))

    assert lowered.cg_height_m == pytest.approx(
        baseline.cg_height_m - 0.030, rel=1e-12)
    # **基準値そのものは書き換えていない**
    assert lowered.cg_height_baseline_m == baseline.cg_height_baseline_m


def test_車高を下げると荷重移動が減る(data):
    """重心が下がれば、同じ加速度でも移る荷重が減る。**これが車高の効果。**"""
    baseline = Vehicle(data)
    lowered = Vehicle(data, setup=CarSetup(ride_height_m=-0.030))

    def transfer(vehicle):
        loads = vehicle.wheel_loads_n(5.0, 0.0)
        return (loads["RL"] + loads["RR"]) - (loads["FL"] + loads["FR"])

    assert transfer(lowered) < transfer(baseline)


def test_車高が接地モデルにも効く(data):
    baseline = RideModel(data)
    lowered = RideModel(data, CarSetup(ride_height_m=-0.030))
    assert lowered.cg_height_m == pytest.approx(baseline.cg_height_m - 0.030)


# --- トー -------------------------------------------------------------------


def test_トーの符号が左右で逆(data):
    """**同じトーインでも、左右で向きは逆。**

    揃えてしまうと、直進で車が片側へ曲がっていく。
    """
    setup = CarSetup(toe_front_rad=math.radians(0.2),
                     toe_rear_rad=math.radians(0.1))
    assert setup.wheel_toe_rad("FL") == -setup.wheel_toe_rad("FR")
    assert setup.wheel_toe_rad("RL") == -setup.wheel_toe_rad("RR")
    # 前後で量が違う
    assert abs(setup.wheel_toe_rad("FL")) > abs(setup.wheel_toe_rad("RL"))


def test_トーインでも直進する(data):
    """**左右のトーは打ち消し合う。** 片側へ流れたら符号が揃っている。"""
    straight, _ = drive(data, CarSetup(toe_front_rad=math.radians(0.3),
                                       toe_rear_rad=math.radians(0.2)),
                        steer_rad=0.0, throttle=0.3)
    assert abs(straight.y_m) < 0.05, "トーインで横に流れた: {:.4f} m".format(straight.y_m)
    assert abs(straight.yaw_rate_rads) < 1e-3


def test_トーが走りを変える(data):
    """**動かしたら物理が変わること。** 変わらなければ飾りのスライダー。"""
    baseline, _ = drive(data, CarSetup(), steer_rad=0.05)
    toed, _ = drive(data, CarSetup(toe_rear_rad=math.radians(0.4)),
                    steer_rad=0.05)
    assert toed.yaw_rate_rads != baseline.yaw_rate_rads
    assert abs(toed.yaw_rate_rads - baseline.yaw_rate_rads) > 1e-4


def test_後輪トーインで曲がりにくくなる(data):
    """後輪トーインは直進安定側。**旋回が鈍る。**"""
    baseline, _ = drive(data, CarSetup(), steer_rad=0.05, throttle=0.3)
    toed, _ = drive(data, CarSetup(toe_rear_rad=math.radians(0.4)),
                    steer_rad=0.05, throttle=0.3)
    assert abs(toed.yaw_rate_rads) < abs(baseline.yaw_rate_rads)


# --- キャンバー -------------------------------------------------------------


def test_キャンバーの倒れ向きが左右で逆(data):
    """負のキャンバーは左右とも内側倒し。**車体座標では向きが逆になる。**"""
    setup = CarSetup(camber_front_rad=math.radians(-2.0))
    assert setup.wheel_camber_lean_rad("FL") == -setup.wheel_camber_lean_rad("FR")
    # 左輪の内側は -y（右）
    assert setup.wheel_camber_lean_rad("FL") < 0.0


def test_負のキャンバーでも直進する(data):
    """**左右のキャンバー推力は打ち消し合う。**

    符号を揃えてしまうと、直進で横に走り出す。
    """
    straight, _ = drive(data, CarSetup(camber_front_rad=math.radians(-3.0),
                                       camber_rear_rad=math.radians(-2.0)),
                        steer_rad=0.0, throttle=0.3)
    assert abs(straight.y_m) < 0.05, (
        "キャンバーで横に流れた: {:.4f} m".format(straight.y_m))


def test_キャンバー推力が摩擦円を超えない(data):
    """**飽和の前に足していること。**

    後から足すと、横力が mu*Fz を超える。
    """
    vehicle = Vehicle(data, setup=CarSetup(camber_front_rad=math.radians(-4.0)))
    tire = vehicle.tire
    fz_n = 4000.0
    limit_n = tire.mu(fz_n) * fz_n

    for alpha_deg in (-20.0, -5.0, 0.0, 5.0, 20.0):
        for kappa in (-0.5, 0.0, 0.5):
            fx, fy = tire.forces_n(fz_n, kappa, math.radians(alpha_deg),
                                   math.radians(-4.0))
            total = math.hypot(fx, fy)
            assert total <= limit_n * (1.0 + 1e-9), (
                "摩擦円を超えた: {:.1f} N > {:.1f} N".format(total, limit_n))


def test_キャンバーが旋回を変える(data):
    baseline, _ = drive(data, CarSetup(), steer_rad=0.06, throttle=0.4)
    cambered, _ = drive(data, CarSetup(camber_front_rad=math.radians(-3.0)),
                        steer_rad=0.06, throttle=0.4)
    assert abs(cambered.yaw_rate_rads - baseline.yaw_rate_rads) > 1e-4


# --- ばねと減衰 -------------------------------------------------------------


def test_ばねを硬くするとロールが減る(data):
    """**接地モデルに効くこと。**"""
    def settled_roll(setup):
        model = RideModel(data, setup)
        state = RideState()
        for _ in range(20000):
            state, _ = model.step(state, 0.001, FLAT, ay_mps2=5.0)
        return abs(state.roll_rad)

    soft = settled_roll(CarSetup(spring_scale_front=0.87, spring_scale_rear=0.82))
    stiff = settled_roll(CarSetup(spring_scale_front=1.18, spring_scale_rear=1.15))
    assert stiff < soft, "ばねを硬くしてもロールが減らない"


def test_減衰を強くすると収まりが早い(data):
    """**過渡の違いが出ること。**"""
    def overshoot(setup):
        model = RideModel(data, setup)
        state = RideState(heave_m=0.05)
        lowest = 0.0
        for _ in range(3000):
            state, _ = model.step(state, 0.001, FLAT)
            lowest = min(lowest, state.heave_m)
        return abs(lowest)

    light = overshoot(CarSetup(damping_scale_front=0.7, damping_scale_rear=0.7))
    heavy = overshoot(CarSetup(damping_scale_front=1.5, damping_scale_rear=1.5))
    assert heavy < light, "減衰を強くしても行き過ぎが減らない"


def test_ばねを変えても静荷重は変わらない(data):
    """**支えている重さは同じ。** ばねが変わるのは姿勢と過渡だけ。"""
    stiff = RideModel(data, CarSetup(spring_scale_front=1.18))
    _, outputs = stiff.settle(FLAT)
    total_n = sum(outputs.loads_n.values())
    assert total_n == pytest.approx(stiff.mass_kg * 9.80665, rel=1e-6)


# --- ブレーキバイアス -------------------------------------------------------


def test_ブレーキバイアスが効く(data):
    baseline = Vehicle(data)
    biased = Vehicle(data, setup=CarSetup(brake_bias=0.58))
    assert biased.brakes.bias_front == 0.58
    assert baseline.brakes.bias_front != 0.58


def test_ブレーキバイアスを前寄りにすると前輪が先にロックする(data):
    """**動かしたら挙動が変わること。**"""
    def front_slip(bias):
        # **強く踏まない。** brakes.max_brake_torque_total は 12000 N*m
        # （assumed）と大きく、0.3 も踏めば前輪はロックして滑り率が 1.0 で
        # 頭打ちになる。そうなると配分の違いが消えて、両方 1.0 になる
        # （最初それで検査が通らなかった）。線形域で比べる。
        vehicle = Vehicle(data, setup=CarSetup(brake_bias=bias))
        state = vehicle.initial_state(speed_mps=100.0 / 3.6, gear="4")
        control = ControlInput(gear="4", throttle=0.0, brake=0.15,
                               steer_rad=0.0, clutch=0.0, handbrake=0.0)
        worst = 0.0
        for _ in range(200):
            state, outputs = vehicle.step(state, control, 0.002)
            worst = max(worst, abs(outputs.slip_ratio["FL"]))
        return worst

    front_biased = front_slip(0.72)
    rear_biased = front_slip(0.58)
    assert front_biased > rear_biased, (
        "前寄り {:.4f} が後ろ寄り {:.4f} を上回らない".format(front_biased, rear_biased))


# --- 説明 -------------------------------------------------------------------


def test_変更点が読める形で出る():
    setup = CarSetup(ride_height_m=-0.025,
                     camber_front_rad=math.radians(-2.5),
                     brake_bias=0.6)
    text = setup.describe()
    assert "-25mm" in text
    assert "-2.50deg" in text
    assert "60%" in text
    assert not setup.is_default()
