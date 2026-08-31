"""樹木と世界境界の当たり判定の検査.

**符号と向きを推測で書かない。** 法線を逆にすると車が木へ吸い込まれるが、
「何か起きている」ようには見えるので、目視では気づけない。
"""

from __future__ import annotations

import math
from pathlib import Path

import pytest

from obstacles import (CollisionBody, ObstacleFeel, ObstacleField, circle_contact,
                       contact_impulse)
from vehicle import ControlInput, Vehicle, VehicleState
from vehicle_data import VehicleData

REPO_ROOT = Path(__file__).resolve().parent.parent
PLACEMENT = REPO_ROOT / "Tracks" / "Export" / "placement.json"


@pytest.fixture(scope="module")
def data():
    return VehicleData()


@pytest.fixture(scope="module")
def body(data):
    return CollisionBody.from_vehicle_data(data)


@pytest.fixture(scope="module")
def car(data):
    return Vehicle(data)


# --- 車体の外形 -------------------------------------------------------------


def test_車体の外形が公式寸法と一致する(body, data):
    """**外形をコードに書かない。** vehicle.json から来ていること。"""
    length_m = data.value("dimensions.length", "m")
    width_m = data.value("dimensions.width", "m")

    assert body.front_m + body.rear_m == pytest.approx(length_m, rel=1e-9)
    assert body.half_width_m * 2.0 == pytest.approx(width_m, rel=1e-9)
    # 重心は前寄りなので、前端までの距離は後端までより短い
    assert body.front_m < body.rear_m


# --- 接触の幾何 -------------------------------------------------------------


def test_離れていれば接触しない(body):
    assert circle_contact(body, 50.0, 0.0, 0.2) is None


def test_法線は障害物から車へ向く(body):
    """**逆にすると車が木へ吸い込まれる。**

    車の右側（y < 0）にある幹に触れたら、車は左（+y）へ押される。
    """
    hit = circle_contact(body, 0.0, -(body.half_width_m + 0.1), 0.2)
    assert hit is not None
    _, _, nx, ny, depth_m, engulfed = hit
    assert ny > 0.0, "右側の幹なのに法線が右向き（車が吸い込まれる）"
    assert depth_m == pytest.approx(0.1, rel=1e-9)
    assert not engulfed
    assert math.hypot(nx, ny) == pytest.approx(1.0, rel=1e-9)


def test_正面の幹では前方から押し戻される(body):
    hit = circle_contact(body, body.front_m + 0.05, 0.0, 0.2)
    assert hit is not None
    _, _, nx, ny, depth_m, _ = hit
    assert nx < 0.0, "前方の幹なのに法線が前向き"
    assert depth_m == pytest.approx(0.15, rel=1e-9)


def test_幹が車体の内側にあっても押し出す(body):
    """**黙って 0 を返さない。** dt が大き過ぎるとこうなる。"""
    hit = circle_contact(body, 0.0, 0.0, 0.2)
    assert hit is not None
    _, _, nx, ny, depth_m, engulfed = hit
    assert engulfed is True
    assert depth_m > 0.0
    assert math.hypot(nx, ny) == pytest.approx(1.0, rel=1e-9)


# --- 撃力 -------------------------------------------------------------------


def test_離れつつあるなら撃力を入れない():
    """**これが無いと、触れた物体に何ステップも撃力が入って弾き飛ばされる。**"""
    impulse_ns, closing_mps = contact_impulse(
        vx_mps=-5.0, vy_mps=0.0, yaw_rate_rads=0.0,
        px_m=2.0, py_m=0.0, nx=-1.0, ny=0.0,
        mass_kg=1230.0, izz_kgm2=2020.0, restitution=0.15)
    assert closing_mps > 0.0
    assert impulse_ns == 0.0


def test_正面衝突では減速する():
    mass_kg, izz_kgm2 = 1230.0, 2020.0
    impulse_ns, closing_mps = contact_impulse(
        vx_mps=10.0, vy_mps=0.0, yaw_rate_rads=0.0,
        px_m=2.0, py_m=0.0, nx=-1.0, ny=0.0,
        mass_kg=mass_kg, izz_kgm2=izz_kgm2, restitution=0.15)
    assert closing_mps < 0.0
    assert impulse_ns > 0.0

    # 中心を突いているのでヨーは出ず、前後だけ変わる
    new_vx = 10.0 + impulse_ns * (-1.0) / mass_kg
    assert new_vx < 10.0
    # 反発 0.15 なので跳ね返って後ろへ下がる
    assert new_vx == pytest.approx(-0.15 * 10.0, rel=1e-9)


def test_角でぶつかると回る():
    """接触点が重心からずれていれば、ヨーが出る。"""
    mass_kg, izz_kgm2 = 1230.0, 2020.0
    impulse_ns, _ = contact_impulse(
        vx_mps=10.0, vy_mps=0.0, yaw_rate_rads=0.0,
        px_m=2.0, py_m=0.9, nx=-1.0, ny=0.0,
        mass_kg=mass_kg, izz_kgm2=izz_kgm2, restitution=0.15)
    lever = 2.0 * 0.0 - 0.9 * (-1.0)
    yaw_change = impulse_ns * lever / izz_kgm2
    assert yaw_change > 0.0, "左前を当てたのに左へ回らない"


def test_角でぶつかると撃力が小さくなる():
    """**慣性項が効いていなければ、中心と角で同じ撃力になる。**"""
    centre_ns, _ = contact_impulse(10.0, 0.0, 0.0, 2.0, 0.0, -1.0, 0.0,
                                   1230.0, 2020.0, 0.15)
    corner_ns, _ = contact_impulse(10.0, 0.0, 0.0, 2.0, 0.9, -1.0, 0.0,
                                   1230.0, 2020.0, 0.15)
    assert corner_ns < centre_ns


def test_反発係数の範囲外を拒否する():
    with pytest.raises(ValueError):
        ObstacleFeel(restitution=1.5)
    with pytest.raises(ValueError):
        ObstacleFeel(restitution=-0.1)
    with pytest.raises(ValueError):
        ObstacleFeel(trunk_radius_per_scale_m=0.0)


# --- 集合としての解決 -------------------------------------------------------


def make_field(trees=(), bounds=(-1000.0, 1000.0, -1000.0, 1000.0)):
    return ObstacleField(trees=trees, bounds_m=bounds)


def test_何にも触れなければ状態が変わらない(body):
    """**当たり判定を入れる前と、結果がビット単位で一致すること。**

    ここが変わると、既に検証済みの結果が当たり判定の実装で汚染される。
    """
    field = make_field(trees=((500.0, 500.0, 0.2),))
    state = VehicleState(vx_mps=20.0, vy_mps=0.3, yaw_rate_rads=0.1,
                         x_m=0.0, y_m=0.0, heading_rad=0.4)

    resolved, contacts = field.resolve(state, body, 1230.0, 2020.0)

    assert contacts == []
    assert resolved is state, "触れていないのに状態を作り直している"


def test_木にぶつかると押し戻されて減速する(body):
    field = make_field(trees=((2.2, 0.0, 0.25),))
    state = VehicleState(vx_mps=15.0, x_m=0.0, y_m=0.0, heading_rad=0.0)

    resolved, contacts = field.resolve(state, body, 1230.0, 2020.0)

    assert len(contacts) == 1
    assert contacts[0].kind == "tree"
    assert contacts[0].depth_m > 0.0
    assert contacts[0].impulse_ns > 0.0
    assert resolved.vx_mps < state.vx_mps, "木に当たったのに減速していない"
    assert resolved.x_m < state.x_m, "木の方向へ押されている（法線が逆）"


def test_木をすり抜けない(body, car):
    """**走らせて、木の中に入らないこと。**

    幾何の単体検査だけでは「毎ステップ押し戻しているが結局めり込む」
    状態を検出できない。
    """
    tree_x_m, tree_y_m, radius_m = 30.0, 0.0, 0.3
    field = make_field(trees=((tree_x_m, tree_y_m, radius_m),))

    state = car.initial_state(speed_mps=80.0 / 3.6, gear="3")
    control = ControlInput(gear="3", throttle=1.0, brake=0.0,
                           steer_rad=0.0, clutch=1.0, handbrake=0.0)

    deepest_m = 0.0
    for _ in range(1500):
        state, _ = car.step(state, control, 0.002)
        state, contacts = field.resolve(state, body, car.mass_kg, car.izz_kgm2)
        for contact in contacts:
            deepest_m = max(deepest_m, contact.depth_m)

    # 車体の最近点が幹の中に入っていないこと
    dx = tree_x_m - state.x_m
    dy = tree_y_m - state.y_m
    cos_h, sin_h = math.cos(state.heading_rad), math.sin(state.heading_rad)
    local_x = dx * cos_h + dy * sin_h
    local_y = -dx * sin_h + dy * cos_h
    nearest_x = min(max(local_x, -body.rear_m), body.front_m)
    nearest_y = min(max(local_y, -body.half_width_m), body.half_width_m)
    distance_m = math.hypot(nearest_x - local_x, nearest_y - local_y)

    assert distance_m >= radius_m - 1e-6, (
        "幹にめり込んだまま（最近点まで {:.4f} m、半径 {:.4f} m）"
        .format(distance_m, radius_m)
    )
    assert deepest_m < 0.5, "1ステップのめり込みが大きすぎる: {:.3f} m".format(deepest_m)


def test_世界の外へ出られない(body, car):
    limit_m = 60.0
    field = make_field(bounds=(-limit_m, limit_m, -limit_m, limit_m))

    state = car.initial_state(speed_mps=100.0 / 3.6, gear="4")
    control = ControlInput(gear="4", throttle=1.0, brake=0.0,
                           steer_rad=0.0, clutch=1.0, handbrake=0.0)

    for _ in range(3000):
        state, _ = car.step(state, control, 0.002)
        state, _ = field.resolve(state, body, car.mass_kg, car.izz_kgm2)

    cos_h, sin_h = math.cos(state.heading_rad), math.sin(state.heading_rad)
    for corner_x_m, corner_y_m in body.corners():
        world_x_m = state.x_m + corner_x_m * cos_h - corner_y_m * sin_h
        world_y_m = state.y_m + corner_x_m * sin_h + corner_y_m * cos_h
        assert world_x_m <= limit_m + 1e-6, "東の境界を越えた: {:.3f}".format(world_x_m)
        assert world_x_m >= -limit_m - 1e-6
        assert world_y_m <= limit_m + 1e-6
        assert world_y_m >= -limit_m - 1e-6


def test_衝突でエネルギーが増えない(body):
    """**保存則の検査。** 反発係数が 1 未満なら運動エネルギーは減る。"""
    field = make_field(trees=((2.2, 0.4, 0.3),))
    mass_kg, izz_kgm2 = 1230.0, 2020.0
    state = VehicleState(vx_mps=25.0, vy_mps=1.0, yaw_rate_rads=0.2,
                         x_m=0.0, y_m=0.0, heading_rad=0.0)

    def energy(s):
        return (0.5 * mass_kg * (s.vx_mps ** 2 + s.vy_mps ** 2)
                + 0.5 * izz_kgm2 * s.yaw_rate_rads ** 2)

    resolved, contacts = field.resolve(state, body, mass_kg, izz_kgm2)
    assert contacts
    assert energy(resolved) < energy(state), "衝突で運動エネルギーが増えた"


# --- 実際の配置データ -------------------------------------------------------


@pytest.mark.skipif(not PLACEMENT.exists(), reason="placement.json が無い")
def test_配置データから読める():
    field = ObstacleField.from_placement(PLACEMENT)
    assert len(field.trees) > 100
    assert field.x1_m > field.x0_m
    for _, _, radius_m in field.trees:
        assert radius_m > 0.0


@pytest.mark.skipif(not PLACEMENT.exists(), reason="placement.json が無い")
def test_コース上には木が無い(body, car):
    """**コースを走っているだけで木に当たってはいけない。**"""
    field = ObstacleField.from_placement(PLACEMENT)

    # 中心線の直線区間（x 0..400、y=0）を舐める
    for x_m in range(0, 400, 5):
        probe = VehicleState(x_m=float(x_m), y_m=0.0, heading_rad=0.0)
        _, contacts = field.resolve(probe, body, car.mass_kg, car.izz_kgm2)
        assert not contacts, "メインストレート x={} で接触している".format(x_m)
