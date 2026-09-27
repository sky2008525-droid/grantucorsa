# -*- coding: utf-8 -*-
"""ニュートラルと後退のテスト.

**H パターンシフターは「今どの段か」を絶対値で送ってくる。**
そこには当然 N と R がある。前進6段しか受け取れないモデルには
シフターを繋げない。

ここで見るのは、次のどれも「それらしく見える」では済まない点:

1. **ニュートラルでは動力が通らない。** クラッチペダルを戻していても、
   歯車が噛んでいないのでトルクの通り道が無い
2. **ニュートラルでもエンジンは回る。** 空吹かしができる
3. **後退では車輪が逆へ回る。** 公表比 3.437 は大きさであって、
   そのまま使うと後退で前へ進む
"""

from __future__ import annotations

import math

import pytest

from drivetrain import NEUTRAL, REVERSE, SELECTABLE_GEARS, Drivetrain
from units import rads_to_rpm
from vehicle import ControlInput, Vehicle
from vehicle_data import VehicleData


@pytest.fixture(scope="module")
def data():
    return VehicleData()


@pytest.fixture
def car(data):
    return Vehicle(data)


# --- 減速比 ---------------------------------------------------------------


def test_ニュートラルに減速比は無い(data):
    """**0 を返さない。**

    0 を返すと「比が 0 の段」として計算が通ってしまい、
    エンジン回転が 0 に張り付くなどの形で間違いが黙って進む。
    """
    with pytest.raises(ValueError):
        Drivetrain(data).total_ratio(NEUTRAL)


def test_後退は負の比(data):
    """公表値 3.437 は**大きさ**。向きはリバースアイドラが決める。

    `vehicle.json` の値は official なので負号を書き込まず、
    符号はモデル側で付ける（憲法ルール1・2）。
    """
    drivetrain = Drivetrain(data)
    ratio = drivetrain.total_ratio(REVERSE)
    assert ratio < 0.0, "後退の比が正だと、後退に入れて前へ進む"
    assert ratio == pytest.approx(-3.437 * drivetrain.final_drive)

    # 元データは大きさのまま
    assert drivetrain.gear_ratios[REVERSE] == pytest.approx(3.437)


def test_慣性は符号に依らない(data):
    """換算慣性は比の2乗。**負でも正の値が出ること。** """
    drivetrain = Drivetrain(data)
    assert drivetrain.reflected_inertia_at_wheel_kgm2(REVERSE) > 0.0


def test_選べない段を拒否する():
    for bad in ("7", "0", "n", "r", "", "D"):
        with pytest.raises(ValueError):
            ControlInput(gear=bad)
    for good in SELECTABLE_GEARS:
        ControlInput(gear=good)          # 例外にならないこと


# --- ニュートラル ---------------------------------------------------------


def _run(car, control, seconds=1.5, dt=0.002, state=None):
    state = state if state is not None else car.initial_state()
    steps = int(round(seconds / dt))
    outputs = None
    for _ in range(steps):
        state, outputs = car.step(state, control, dt)
    return state, outputs


def test_ニュートラルでは加速しない(car):
    """**クラッチを繋いだまま全開にしても車は動かない。**

    これが動くようなら、噛んでいない歯車を通してトルクが流れている。
    """
    control = ControlInput(throttle=1.0, gear=NEUTRAL, clutch=1.0)
    state, _ = _run(car, control, seconds=2.0)

    assert abs(state.vx_mps) < 0.05, (
        "ニュートラルで {:.3f} m/s まで動いた".format(state.vx_mps))


def test_ニュートラルで空吹かしできる(car):
    """**動力が通らない = エンジンが止まる、ではない。**

    切り離されたエンジンは自分の慣性だけで回るので、むしろ速く吹け上がる。
    """
    control = ControlInput(throttle=1.0, gear=NEUTRAL, clutch=1.0)
    start = car.initial_state()
    state, _ = _run(car, control, seconds=1.0, state=start)

    assert rads_to_rpm(state.engine_omega_rads) > rads_to_rpm(start.engine_omega_rads) + 1000.0, (
        "空吹かしで回転が上がらない（{:.0f} rpm）".format(
            rads_to_rpm(state.engine_omega_rads)))


def test_ニュートラルでは駆動トルクが0(car):
    control = ControlInput(throttle=1.0, gear=NEUTRAL, clutch=1.0)
    _, outputs = _run(car, control, seconds=0.5)
    assert outputs.clutch_torque_nm == pytest.approx(0.0, abs=1e-9)


def test_走行中にニュートラルへ入れると惰行する(car):
    """**駆動も engine brake も無くなる。**

    転がり抵抗と空気抵抗だけが残るので、ゆっくり減速すること。
    急に止まったら、切り離したはずのエンジンがまだ効いている。
    """
    state = car.initial_state()
    state.vx_mps = 20.0
    for wheel in state.wheel_omega_rads:
        state.wheel_omega_rads[wheel] = 20.0 / car.wheel_radius_m
    state.engine_omega_rads = 250.0

    before = state.vx_mps
    state, _ = _run(car, ControlInput(gear=NEUTRAL, clutch=1.0), seconds=2.0,
                    state=state)

    lost = before - state.vx_mps
    assert 0.0 < lost < 3.0, "2 秒で {:.2f} m/s 減った（惰行にしては急）".format(lost)


# --- 後退 -----------------------------------------------------------------


def test_後退で後ろへ進む(car):
    """**符号を落とすとここで前へ進む。**"""
    control = ControlInput(throttle=0.5, gear=REVERSE, clutch=1.0)
    state, _ = _run(car, control, seconds=2.0)

    assert state.vx_mps < -0.5, (
        "後退に入れて {:.3f} m/s（正なら前へ進んでいる）".format(state.vx_mps))


def test_後退でもエンジンは正転する(car):
    """車輪が逆へ回っても、エンジンは逆回転しない。"""
    control = ControlInput(throttle=0.5, gear=REVERSE, clutch=1.0)
    state, _ = _run(car, control, seconds=2.0)

    assert state.engine_omega_rads > 0.0
    assert state.wheel_omega_rads["RL"] < 0.0, "後輪が逆へ回っていない"


def test_後退は1速より遅い(car, data):
    """比が 3.437 と 3.626 で近いので**速度も近い**。

    ここが大きく違うなら、比の使い方かクラッチの扱いを間違えている。
    """
    forward, _ = _run(car, ControlInput(throttle=0.5, gear="1", clutch=1.0),
                      seconds=2.0)
    backward, _ = _run(car, ControlInput(throttle=0.5, gear=REVERSE, clutch=1.0),
                       seconds=2.0)

    assert abs(backward.vx_mps) == pytest.approx(abs(forward.vx_mps), rel=0.12)


# --- 既存の挙動を壊していないこと -----------------------------------------


def test_前進はまだ前へ進む(car):
    """分岐を足すときの事故は「ついでに前進も変えてしまう」こと。

    厳密な回帰は既存のテスト群と `Tools/export_reference.py` の参照値が
    担うので、ここでは**向き**だけを見る。
    """
    state, outputs = _run(car, ControlInput(throttle=1.0, gear="1", clutch=1.0),
                          seconds=1.0)

    assert math.isfinite(state.vx_mps) and state.vx_mps > 0.5
    assert outputs.clutch_torque_nm > 0.0


def test_停車中に後ろへずり下がらない(car):
    """**前後速度の 0 での切り上げを外したので、ここが要になる。**

    切り上げていた間は、停車中に 1速でクラッチを繋ぐと閉じスロットルの
    負トルクが車を後ろへ押していたのが見えていなかった。速度を切り上げず、
    かつ下がらないこと（アイドル制御が支える）。
    """
    for label, control in (
        ("ブレーキ", ControlInput(brake=1.0, gear="1", clutch=0.0)),
        ("1速クラッチ接", ControlInput(gear="1", clutch=1.0)),
        ("何もしない", ControlInput(gear="1", clutch=0.0)),
        ("ニュートラル", ControlInput(gear=NEUTRAL, clutch=1.0)),
    ):
        state, _ = _run(car, control, seconds=3.0)
        assert state.vx_mps > -0.02, (
            "{}: 平地で 3 秒に {:.3f} m/s 後ろへ下がった".format(label, state.vx_mps))
