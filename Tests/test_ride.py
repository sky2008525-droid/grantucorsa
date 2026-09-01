"""上下・ピッチ・ロールと接地力の検査.

**「見た目が正しいか」では判定しない。** 接地は目で見ても分からない。
保存則と拘束条件で判定する（`.claude/rules/physics.md`）:

1. **静止した車の接地力の合計が車重に一致するか**（釣り合い）
2. **地面は押せるが引けないか**（接地力が負にならない）
3. **落とせば落ちるか**（重力が効いているか）
4. **定常状態で準静的モデルと前後の荷重移動が一致するか**
5. **段差で車輪が浮くか**（接地が切れるか）
"""

from __future__ import annotations

import math

import pytest

from ride import WHEELS, RideModel, RideState
from units import GRAVITY_MPS2
from vehicle import Vehicle
from vehicle_data import VehicleData

FLAT = {wheel: 0.0 for wheel in WHEELS}


@pytest.fixture(scope="module")
def data():
    return VehicleData()


@pytest.fixture(scope="module")
def ride(data):
    return RideModel(data)


@pytest.fixture(scope="module")
def car(data):
    return Vehicle(data)


# --- 諸元の読み方 -----------------------------------------------------------


def test_ばねとタイヤが直列になっている(ride, data):
    """**直列を忘れると剛性が1割ほど高く出る。**"""
    tyre_k = data.value("tires.vertical_stiffness", "N/m")
    for wheel in WHEELS:
        wheel_rate = ride.wheel_rate_n_per_m[wheel]
        expected = wheel_rate * tyre_k / (wheel_rate + tyre_k)
        assert ride.ride_rate_n_per_m[wheel] == pytest.approx(expected, rel=1e-12)
        # 直列なので、どちらの単体よりも柔らかい
        assert ride.ride_rate_n_per_m[wheel] < wheel_rate
        assert ride.ride_rate_n_per_m[wheel] < tyre_k


def test_モーションレシオが2乗で効く(ride, data):
    """**1乗にすると、たわみは合っても力が合わない。**"""
    for wheel, axle in (("FL", "front"), ("RL", "rear")):
        spring = data.value("suspension.spring_rate_" + axle, "N/m")
        ratio = data.value("suspension.motion_ratio_" + axle, "-")
        assert ride.wheel_rate_n_per_m[wheel] == pytest.approx(
            spring * ratio ** 2, rel=1e-12)


def test_乗り心地の固有振動数が乗用車の範囲(ride):
    """**妥当性の目安。** ここを大きく外れたら読み方が間違っている。

    これは実測との比較ではない（測っていない）。
    「物理的にあり得ない値を採用しない」ための検査。
    """
    for wheel in WHEELS:
        freq_hz = ride.natural_frequency_hz(wheel)
        assert 0.8 < freq_hz < 2.0, (
            "{} の上下固有振動数が {:.3f} Hz".format(wheel, freq_hz))

    # 後ろのほうが硬い（ばねレートが 33 > 22 N/mm）
    assert ride.natural_frequency_hz("RL") > ride.natural_frequency_hz("FL")


# --- 釣り合い ---------------------------------------------------------------


def test_静止した車の接地力の合計が車重(ride):
    """**釣り合っていなければ、車は勝手に浮くか沈む。**"""
    state, outputs = ride.settle(FLAT)
    total_n = sum(outputs.loads_n.values())
    assert total_n == pytest.approx(ride.mass_kg * GRAVITY_MPS2, rel=1e-9)


def test_平地の釣り合いが原点(ride):
    """静止した平地の姿勢を状態の原点にとってある。

    **ここがゼロでなければ、止まっている車が勝手に傾く。**
    実際、静荷重を `weight_distribution_front_pct` から出していたときは
    373 N*m のピッチモーメントが残り、前上がりに落ち着いた（issue #29）。
    """
    state, _ = ride.settle(FLAT)
    assert abs(state.heave_m) < 1e-9
    assert abs(state.pitch_rad) < 1e-9
    assert abs(state.roll_rad) < 1e-9


def test_静荷重が準静的モデルと一致する(ride, car):
    """**2つのモデルで静止時の軸重が違ったら、片方は間違っている。**"""
    _, outputs = ride.settle(FLAT)
    quasi_static = car.wheel_loads_n(0.0, 0.0)
    for wheel in WHEELS:
        assert outputs.loads_n[wheel] == pytest.approx(
            quasi_static[wheel], rel=1e-9), wheel


# --- 重力と接地 -------------------------------------------------------------


def test_持ち上げて放すと落ちる(ride):
    """**重力が効いているか。** 落ちなければ何かが支えている。"""
    lifted = RideState(heave_m=0.5)
    state = lifted
    for _ in range(20):
        state, outputs = ride.step(state, 0.001, FLAT)

    assert state.heave_rate_mps < 0.0, "落ちていない"
    assert state.heave_m < lifted.heave_m
    # 0.5 m 持ち上げれば、ばねは伸びきって4輪とも接地していない
    assert not any(outputs.contact.values()), "浮いているのに接地している"
    assert all(load == 0.0 for load in outputs.loads_n.values())
    assert state.airborne


def test_浮いている間は自由落下(ride):
    """**接地力が 0 なら、加速度は重力そのもの。**

    ここが g より小さいと、接地していないのに何かが支えている。
    """
    state = RideState(heave_m=1.0)
    before = state.heave_rate_mps
    state, outputs = ride.step(state, 0.001, FLAT)
    assert not any(outputs.contact.values())
    accel = (state.heave_rate_mps - before) / 0.001
    assert accel == pytest.approx(-GRAVITY_MPS2, rel=1e-9)


def test_落とすと跳ねてから釣り合いに戻る(ride):
    """**落ちて、車輪が地面に当たって、そこに留まる。**

    これが再現できていないと「地面に置いているだけ」になる。
    """
    state = RideState(heave_m=0.15)
    lowest_m = state.heave_m
    touched = False

    for _ in range(6000):        # 6 秒
        state, outputs = ride.step(state, 0.001, FLAT)
        lowest_m = min(lowest_m, state.heave_m)
        touched = touched or any(outputs.contact.values())

    assert touched, "一度も接地しなかった"
    assert lowest_m < 0.0, "沈み込んでいない（ばねが縮んでいない）"
    # 減衰があるので釣り合いへ戻る
    assert abs(state.heave_m) < 1e-3, "釣り合いに戻らない: {:.5f} m".format(state.heave_m)
    assert abs(state.heave_rate_mps) < 1e-2


def test_接地力は負にならない(ride):
    """**地面は押せるが引けない。**

    `max(0, ...)` を外すと、浮いた車輪が車体を下へ引っ張る。
    見た目には「なんとなく沈む」だけなので気づけない。
    """
    for heave_m in (-0.2, -0.05, 0.0, 0.05, 0.2, 0.5, 2.0):
        for pitch_rad in (-0.2, 0.0, 0.2):
            for roll_rad in (-0.2, 0.0, 0.2):
                state = RideState(heave_m=heave_m, pitch_rad=pitch_rad,
                                  roll_rad=roll_rad)
                loads, contact = ride.contact_loads_n(state, FLAT)
                for wheel in WHEELS:
                    assert loads[wheel] >= 0.0, (
                        "{} の接地力が {:.1f} N".format(wheel, loads[wheel]))
                    assert contact[wheel] == (loads[wheel] > 0.0)


def test_沈めるほど押し返しが強くなる(ride):
    """作用反作用。**縮んだぶんだけ押し返す。**"""
    previous = 0.0
    for depth_m in (0.0, 0.01, 0.02, 0.05):
        state = RideState(heave_m=-depth_m)
        loads, _ = ride.contact_loads_n(state, FLAT)
        total_n = sum(loads.values())
        assert total_n > previous, "沈めたのに押し返しが増えない"
        previous = total_n


# --- 地形 -------------------------------------------------------------------


def test_坂に置くと坂なりに傾く(ride):
    """上り坂（前が高い）に置いたら**機首が上がる**。

    符号が逆だと、上り坂で前のめりになる。UE 側で実際にそうなっていた。
    """
    lf = ride._position["FL"][0]
    lr = -ride._position["RL"][0]

    # 前上がりの斜面。前輪の下が高い
    slope = 0.10                     # 10%
    ground = {
        "FL": lf * slope, "FR": lf * slope,
        "RL": -lr * slope, "RR": -lr * slope,
    }
    state, outputs = ride.settle(ground)

    assert state.pitch_rad > 0.0, "上り坂で機首が下がっている"
    assert state.pitch_rad == pytest.approx(math.atan(slope), abs=0.01)
    assert all(outputs.contact.values()), "坂の上で車輪が浮いている"


def test_横に傾いた面で左が高ければ右へ傾く(ride):
    """**UE の正のロールは右下がり**（`AdvanceVisualAttitude` で確認済み）。"""
    half_front = ride._position["FL"][1]
    half_rear = ride._position["RL"][1]
    slope = 0.10

    ground = {
        "FL": half_front * slope, "FR": -half_front * slope,
        "RL": half_rear * slope, "RR": -half_rear * slope,
    }
    state, _ = ride.settle(ground)
    assert state.roll_rad > 0.0, "左が高いのに右へ傾かない"


def test_坂でも接地力の合計は車重(ride):
    """**傾いても、支えているのは同じ重さ。**

    ここでは重力の面直成分ではなく鉛直成分を見ている（このモデルは
    上下方向のみを解き、斜面に沿う運動は `Vehicle` 側が持つ）。
    """
    lf = ride._position["FL"][0]
    lr = -ride._position["RL"][0]
    for slope in (0.0, 0.05, 0.15):
        ground = {
            "FL": lf * slope, "FR": lf * slope,
            "RL": -lr * slope, "RR": -lr * slope,
        }
        _, outputs = ride.settle(ground)
        assert sum(outputs.loads_n.values()) == pytest.approx(
            ride.mass_kg * GRAVITY_MPS2, rel=1e-6)


def test_段差で車輪が浮く(ride):
    """**接地が切れること。**

    片輪だけ深い穴に落とすと、そこは地面に届かない。
    「浮く」が表現できないと、段差もギャップも無い世界になる。
    """
    ground = dict(FLAT)
    ground["FL"] = -1.0          # 左前だけ 1 m 落ちている

    state = RideState()
    lifted = False
    for _ in range(2000):
        state, outputs = ride.step(state, 0.001, ground)
        if not outputs.contact["FL"]:
            lifted = True

    assert lifted, "1 m の穴の上で左前が接地したまま"
    _, outputs = ride.step(state, 0.001, ground)
    assert outputs.loads_n["FL"] == 0.0
    # 残り3輪で支える
    assert sum(outputs.loads_n.values()) == pytest.approx(
        ride.mass_kg * GRAVITY_MPS2, rel=0.05)


# --- 荷重移動 ---------------------------------------------------------------


def test_定常の前後荷重移動が準静的モデルと一致する(ride, car):
    """**ここは厳密に一致すること。**

    準静的モデルの前後荷重移動 `m*ax*h/L` は、モーメントの釣り合いを
    解いた結果と同じものである。違っていたら、どちらかの導出が誤っている。
    """
    for ax_mps2 in (-6.0, -3.0, 0.0, 3.0, 6.0):
        state = RideState()
        for _ in range(20000):        # 20 秒。過渡が収まるまで
            state, outputs = ride.step(state, 0.001, FLAT, ax_mps2=ax_mps2)

        quasi = car.wheel_loads_n(ax_mps2, 0.0)
        front_ride = outputs.loads_n["FL"] + outputs.loads_n["FR"]
        front_quasi = quasi["FL"] + quasi["FR"]

        assert front_ride == pytest.approx(front_quasi, rel=1e-6), (
            "ax={:.1f}: 前軸 {:.2f} N vs 準静的 {:.2f} N".format(
                ax_mps2, front_ride, front_quasi)
        )


def test_加速すると機首が上がり後輪に荷重が乗る(ride):
    """**FR なので駆動輪に乗る**（FF とは逆）。"""
    state = RideState()
    for _ in range(20000):
        state, outputs = ride.step(state, 0.001, FLAT, ax_mps2=5.0)

    assert state.pitch_rad > 0.0, "加速したのに機首が上がらない"
    rear = outputs.loads_n["RL"] + outputs.loads_n["RR"]
    front = outputs.loads_n["FL"] + outputs.loads_n["FR"]
    assert rear > front * 0.9, "加速で後輪に荷重が乗っていない"


def test_左旋回で右輪に荷重が乗る(ride):
    """ay が正 = 左向き加速 = 左旋回。**荷重は外側（右）へ。**"""
    state = RideState()
    for _ in range(20000):
        state, outputs = ride.step(state, 0.001, FLAT, ay_mps2=5.0)

    assert state.roll_rad > 0.0, "左旋回で右へ傾かない"
    assert outputs.loads_n["FR"] > outputs.loads_n["FL"]
    assert outputs.loads_n["RR"] > outputs.loads_n["RL"]


def test_荷重移動に過渡がある(ride):
    """**準静的モデルには無いもの。** 瞬時には移らない。"""
    state = RideState()
    _, first = ride.step(state, 0.001, FLAT, ay_mps2=6.0)
    transferred_first = abs(first.loads_n["FR"] - first.loads_n["FL"])

    for _ in range(20000):
        state, settled = ride.step(state, 0.001, FLAT, ay_mps2=6.0)
    transferred_settled = abs(settled.loads_n["FR"] - settled.loads_n["FL"])

    assert transferred_first < transferred_settled * 0.1, (
        "1ステップ目で既に荷重が移りきっている（過渡が無い）")


def test_ロール剛性配分はばねから導出される(ride, data):
    """**`roll_stiffness_distribution_front`（assumed 0.6）に合わせない。**

    合わせにいくのは辻褄合わせ（憲法ルール3）。導出値と仮定値が違うことは、
    **隠さずここで数値として出す。**

    違う理由ははっきりしている: スタビライザーを含んでいない。
    前 18mm / 後 14mm で前が大幅に硬く、実車の配分を前寄りにしている。
    径は分かっているが、アーム長とレバー比（`suspension.geometry`）が
    `unknown` なのでロール剛性に直せない。
    """
    derived = ride.roll_stiffness_distribution_front
    assumed = data.value("suspension.roll_stiffness_distribution_front", "-")

    # 物理的にあり得る範囲であること
    assert 0.0 < derived < 1.0

    # ばねだけなら後ろが硬い（後ばね 33 > 前 22 N/mm）
    assert derived < 0.5, (
        "ばねだけの配分が前寄り（{:.4f}）。ばねレートの読み方を疑うこと"
        .format(derived))

    # **差は報告するが、合わせない。**
    print("\nロール剛性配分: ばねから導出 {:.4f} / vehicle.json の仮定 {:.4f}"
          " (差 {:+.4f})".format(derived, assumed, derived - assumed))


# --- 入力の検査 -------------------------------------------------------------


def test_dtが正でなければ止まる(ride):
    """**握りつぶさない**（憲法ルール6）。"""
    with pytest.raises(ValueError):
        ride.step(RideState(), 0.0, FLAT)
    with pytest.raises(ValueError):
        ride.step(RideState(), -0.001, FLAT)


def test_収束しなければ例外(ride):
    """**「収束しなかった」を黙って返さない。**

    平地の原点から始めると1ステップで収束してしまうので、
    1 m 落とすところから始める。
    """
    high = {wheel: -1.0 for wheel in WHEELS}
    with pytest.raises(RuntimeError):
        ride.settle(high, dt_s=0.001, max_steps=2)


def test_既定の走行が変わらない(data):
    """**ライドモデルを足しても、既存の結果が1ビットも動かないこと。**

    `Vehicle` はライドモデルを知らない。知っていたら、検証済みの
    0-100km/h やラップタイムが黙って変わる。
    """
    from vehicle import ControlInput

    control = ControlInput(gear="3", throttle=1.0, brake=0.0,
                           steer_rad=0.02, clutch=1.0, handbrake=0.0)

    def run():
        vehicle = Vehicle(data)
        state = vehicle.initial_state(speed_mps=60.0 / 3.6, gear="3")
        for _ in range(500):
            state, _ = vehicle.step(state, control, 0.002)
        return state

    first = run()
    second = run()
    assert first.vx_mps == second.vx_mps
    assert first.x_m == second.x_m
    assert first.yaw_rate_rads == second.yaw_rate_rads


# --- 接地力をタイヤへ繋ぐ ---------------------------------------------------


def test_接地力を渡さなければ結果が変わらない(data):
    """**繋いだこと自体で、検証済みの結果を動かさない。**"""
    from vehicle import ControlInput

    control = ControlInput(gear="3", throttle=1.0, brake=0.0,
                           steer_rad=0.04, clutch=1.0, handbrake=0.0)

    def run(pass_none):
        vehicle = Vehicle(data)
        state = vehicle.initial_state(speed_mps=70.0 / 3.6, gear="3")
        for _ in range(300):
            if pass_none:
                state, _ = vehicle.step(state, control, 0.002,
                                        contact_loads_n=None)
            else:
                state, _ = vehicle.step(state, control, 0.002)
        return state

    explicit = run(True)
    implicit = run(False)
    assert explicit.vx_mps == implicit.vx_mps
    assert explicit.vy_mps == implicit.vy_mps
    assert explicit.yaw_rate_rads == implicit.yaw_rate_rads


def test_浮いた車輪はタイヤ力を出さない(data):
    """**これが今まで出来ていなかった。**

    接地モデルが「その輪は地面を押していない」と言っているのに、
    タイヤは準静的な荷重（常に正）で力を出し続けていた。
    段差で跳ねてもタイヤが効いたままになる。
    """
    from vehicle import ControlInput

    vehicle = Vehicle(data)
    state = vehicle.initial_state(speed_mps=60.0 / 3.6, gear="3")
    control = ControlInput(gear="3", throttle=0.5, brake=0.0,
                           steer_rad=0.05, clutch=1.0, handbrake=0.0)

    # 左前だけ浮かせる
    quasi = vehicle.wheel_loads_n(0.0, 0.0)
    lifted = dict(quasi)
    lifted["FL"] = 0.0

    state, outputs = vehicle.step(state, control, 0.002, contact_loads_n=lifted)

    assert outputs.tire_fz_n["FL"] == 0.0
    assert outputs.tire_fx_n["FL"] == 0.0, "浮いた車輪が前後力を出している"
    assert outputs.tire_fy_n["FL"] == 0.0, "浮いた車輪が横力を出している"
    # 他の輪は出している
    assert abs(outputs.tire_fy_n["FR"]) > 0.0


def test_四輪とも浮いたらタイヤ力がゼロ(data):
    """飛んでいる車は曲がれない。"""
    from vehicle import ControlInput

    vehicle = Vehicle(data)
    state = vehicle.initial_state(speed_mps=80.0 / 3.6, gear="4")
    control = ControlInput(gear="4", throttle=1.0, brake=0.0,
                           steer_rad=0.20, clutch=1.0, handbrake=0.0)

    airborne = {wheel: 0.0 for wheel in WHEELS}
    before_yaw = state.yaw_rate_rads
    for _ in range(50):
        state, outputs = vehicle.step(state, control, 0.002,
                                      contact_loads_n=airborne)

    for wheel in WHEELS:
        assert outputs.tire_fx_n[wheel] == 0.0
        assert outputs.tire_fy_n[wheel] == 0.0
    # ヨーは増えない（空力モーメントは持っていない）
    assert abs(state.yaw_rate_rads - before_yaw) < 1e-9


def test_車輪が足りなければ止まる(data):
    """**黙って準静的へ戻さない**（憲法ルール6）。

    戻すと「接地モデルを使っているつもりで使えていない」状態に
    気づけない。
    """
    from vehicle import ControlInput

    vehicle = Vehicle(data)
    state = vehicle.initial_state(speed_mps=50.0 / 3.6, gear="2")
    control = ControlInput(gear="2", throttle=0.3, brake=0.0,
                           steer_rad=0.0, clutch=1.0, handbrake=0.0)

    with pytest.raises(ValueError):
        vehicle.step(state, control, 0.002, contact_loads_n={"FL": 3000.0})


def test_負の接地力を通さない(data):
    """地面は押せるが引けない。"""
    from vehicle import ControlInput

    vehicle = Vehicle(data)
    state = vehicle.initial_state(speed_mps=50.0 / 3.6, gear="2")
    control = ControlInput(gear="2", throttle=0.3, brake=0.0,
                           steer_rad=0.0, clutch=1.0, handbrake=0.0)

    negative = {wheel: -500.0 for wheel in WHEELS}
    _, outputs = vehicle.step(state, control, 0.002, contact_loads_n=negative)
    for wheel in WHEELS:
        assert outputs.tire_fz_n[wheel] == 0.0


def test_定常では接地モデルと準静的が同じ走りになる(data, ride):
    """**繋いでも、平地の定常走行は変わらないこと。**

    前後の荷重移動は既に一致することを確かめてある。ここでは
    「実際に走らせても大きく違わない」ことを見る。
    左右はばねから導出するぶん違うので、厳密一致は求めない。
    """
    from vehicle import ControlInput

    control = ControlInput(gear="3", throttle=0.6, brake=0.0,
                           steer_rad=0.0, clutch=1.0, handbrake=0.0)

    def run(use_ride):
        vehicle = Vehicle(data)
        model = RideModel(data)
        state = vehicle.initial_state(speed_mps=60.0 / 3.6, gear="3")
        ride_state, ride_out = model.settle(FLAT)
        loads = ride_out.loads_n

        for _ in range(500):
            state, outputs = vehicle.step(
                state, control, 0.002,
                contact_loads_n=loads if use_ride else None)
            ride_state, ride_out = model.step(
                ride_state, 0.002, FLAT,
                ax_mps2=outputs.ax_mps2, ay_mps2=outputs.ay_mps2)
            loads = ride_out.loads_n
        return state.vx_mps

    quasi = run(False)
    with_ride = run(True)
    assert with_ride == pytest.approx(quasi, rel=1e-3), (
        "直線加速で {:.4f} と {:.4f} が食い違う".format(with_ride, quasi))
