"""地形の高さ場と、斜面が車に与える力の検査.

**符号を推測で書かない。** 下り坂で前へ加速する、上り坂で減速する、という
向きは、間違えても「それらしく」動いてしまう。ここで機械的に縛る。
"""

from __future__ import annotations

import math
from pathlib import Path

import pytest

from terrain import Heightfield, body_gravity
from units import GRAVITY_MPS2
from vehicle import ControlInput, Vehicle
from vehicle_data import VehicleData

REPO_ROOT = Path(__file__).resolve().parent.parent
# **コースごとにフォルダが分かれている。** 検証用のコースを使う。
HEIGHTFIELD = (REPO_ROOT / "Tracks" / "Export" / "physics_test_track"
               / "heightfield.json")

pytestmark = pytest.mark.skipif(
    not HEIGHTFIELD.exists(),
    reason="heightfield.json が無い。Blender/build_track.py を先に実行すること",
)


@pytest.fixture(scope="module")
def field():
    return Heightfield(HEIGHTFIELD)


@pytest.fixture(scope="module")
def car():
    return Vehicle(VehicleData())


# --- 高さ場 -----------------------------------------------------------------


def test_走行域は平らである(field):
    """**このコースは縦断を持たない**（物理の基準コースなので平坦のまま）。

    したがって走行域の地面は一定でなければならない。一定でなければ、
    車が地面から浮く／埋まる（実際にそうなった）。

    **沈み込みの値そのものを書かない。** 以前は -0.05 m と直に書いて
    いたが、これは `Blender/build_track.py` の `GROUND_SINK_M` を
    別の場所に写した数字で、あちらを直すとここが落ちる（実際に落ちた）。
    見たいのは「一定であること」と「路面より少し下にあること」で、
    何センチかではない。
    """
    heights = []
    for x in range(-100, 420, 20):
        for y in range(0, 110, 10):
            heights.append(field.height_at(float(x), float(y)))

    assert max(heights) - min(heights) < 1e-6, (
        "走行域が平らでない: {:.4f} 〜 {:.4f} m".format(min(heights), max(heights)))

    sink = heights[0]
    assert -0.5 < sink < -0.001, (
        "地面が路面の {:.3f} m 下にある。0 以上だと路面が埋まり、"
        "深すぎると路肩が溝になる".format(sink))


def test_コースから離れると起伏がある(field):
    """遠景には起伏がある（無ければ「地形に沿う」検査に意味が無い）。"""
    heights = [
        field.height_at(x, y)
        for x in (-600.0, -500.0, 800.0, 900.0)
        for y in (-350.0, -250.0, 400.0, 500.0)
    ]
    assert max(heights) - min(heights) > 1.0, "遠景に起伏が無い: {}".format(heights)


def test_範囲外でも落ちない(field):
    """**地形の外へ出た車を落とさない。** 端の高さが続くとみなす。"""
    inside = field.height_at(field.x0_m, field.y0_m)
    outside = field.height_at(field.x0_m - 10000.0, field.y0_m - 10000.0)
    assert outside == pytest.approx(inside)
    assert math.isfinite(outside)


# --- 斜面の重力 -------------------------------------------------------------


def test_平地では重力の面内成分がゼロ():
    forward, left, normal_scale = body_gravity(0.0, 0.0, 0.0)
    assert forward == pytest.approx(0.0)
    assert left == pytest.approx(0.0)
    assert normal_scale == pytest.approx(1.0)


def test_下り坂では前へ加速する():
    """**進行方向が下がっていれば前向きの成分が出る。**

    dz/dx < 0 は「前方が低い」= 下り坂。
    """
    forward, _, _ = body_gravity(-0.20, 0.0, heading_rad=0.0)
    assert forward > 0.0, "下り坂なのに前向きの重力成分が {:.3f}".format(forward)

    uphill, _, _ = body_gravity(+0.20, 0.0, heading_rad=0.0)
    assert uphill < 0.0, "上り坂なのに前向きの重力成分が {:.3f}".format(uphill)
    assert uphill == pytest.approx(-forward, rel=1e-9)


def test_横傾斜では横向きの成分が出る():
    """dz/dy < 0 は「左が低い」。車体の左向きが正なので正の成分。"""
    _, left, _ = body_gravity(0.0, -0.20, heading_rad=0.0)
    assert left > 0.0


def test_方位を回すと前後と左右が入れ替わる():
    """**車体の向きを考慮していなければ、ここで落ちる。**"""
    forward, left, _ = body_gravity(-0.20, 0.0, heading_rad=0.0)
    turned_f, turned_l, _ = body_gravity(-0.20, 0.0, heading_rad=math.pi / 2.0)

    assert turned_f == pytest.approx(0.0, abs=1e-9)
    assert turned_l == pytest.approx(-forward, rel=1e-9)
    assert left == pytest.approx(0.0, abs=1e-9)


def test_傾きが大きいほど法線荷重が減る():
    _, _, flat = body_gravity(0.0, 0.0, 0.0)
    _, _, gentle = body_gravity(0.10, 0.0, 0.0)
    _, _, steep = body_gravity(0.40, 0.0, 0.0)
    assert flat > gentle > steep
    # 45 度なら cos45 = 0.707
    _, _, forty_five = body_gravity(1.0, 0.0, 0.0)
    assert forty_five == pytest.approx(math.cos(math.radians(45.0)), rel=1e-9)


def test_面内成分と法線成分がgに分解される():
    """**保存則の検査。** 分けた成分を合成すると g に戻る。"""
    for dzdx in (0.0, 0.15, 0.5, 1.2):
        forward, left, normal_scale = body_gravity(dzdx, 0.3, heading_rad=0.7)
        tangential = math.hypot(forward, left)
        normal = GRAVITY_MPS2 * normal_scale
        assert math.hypot(tangential, normal) == pytest.approx(GRAVITY_MPS2, rel=1e-9)


# --- 車両との結合 -----------------------------------------------------------


def test_坂道で止めた車が転がり出す(car):
    """**重力が効いていなければ、坂に置いても動かない。**

    これが「重力の概念が無い」状態そのもの。
    """
    state = car.initial_state(speed_mps=0.0, gear="1")
    control = ControlInput(gear="1", throttle=0.0, brake=0.0,
                           steer_rad=0.0, clutch=0.0, handbrake=0.0)

    forward, left, normal_scale = body_gravity(-0.15, 0.0, heading_rad=0.0)
    for _ in range(500):
        state, _ = car.step(state, control, 0.002,
                            slope_gx_mps2=forward, slope_gy_mps2=left,
                            normal_scale=normal_scale)

    assert state.vx_mps > 0.5, "下り坂で転がり出さない（vx = {:.3f}）".format(state.vx_mps)


def test_平地では地形を渡しても結果が変わらない():
    """**平地の既定値は、地形を入れる前と完全に一致する。**

    ここが変わると、既に検証済みの結果（0-100km/h、制動距離）が
    地形の実装で汚染されることになる。
    """
    def run(**kwargs):
        # **毎回新しい Vehicle を使う。** Vehicle は前ステップの加速度を
        # 内部に持ち越すので、使い回すと2回目が1回目の続きになる
        # （最初それで 8 桁目が食い違い、テストが落ちた）。
        car = Vehicle(VehicleData())
        state = car.initial_state(speed_mps=60.0 / 3.6, gear="3")
        control = ControlInput(gear="3", throttle=0.4, brake=0.0,
                               steer_rad=0.03, clutch=1.0, handbrake=0.0)
        for _ in range(300):
            state, outputs = car.step(state, control, 0.002, **kwargs)
        return state, outputs

    plain_state, plain_out = run()
    flat_state, flat_out = run(slope_gx_mps2=0.0, slope_gy_mps2=0.0, normal_scale=1.0)

    assert flat_state.vx_mps == plain_state.vx_mps
    assert flat_state.vy_mps == plain_state.vy_mps
    assert flat_state.yaw_rate_rads == plain_state.yaw_rate_rads
    for wheel in ("FL", "FR", "RL", "RR"):
        assert flat_out.tire_fz_n[wheel] == plain_out.tire_fz_n[wheel]


def test_上り坂では同じアクセルで加速が鈍る(car):
    def run(dzdx):
        state = car.initial_state(speed_mps=40.0 / 3.6, gear="2")
        control = ControlInput(gear="2", throttle=0.6, brake=0.0,
                               steer_rad=0.0, clutch=1.0, handbrake=0.0)
        forward, left, scale = body_gravity(dzdx, 0.0, heading_rad=0.0)
        for _ in range(500):
            state, _ = car.step(state, control, 0.002,
                                slope_gx_mps2=forward, slope_gy_mps2=left,
                                normal_scale=scale)
        return state.vx_mps

    flat = run(0.0)
    uphill = run(+0.12)
    downhill = run(-0.12)
    assert uphill < flat < downhill, (
        "坂の向きで加速が変わらない: 上り {:.2f} / 平地 {:.2f} / 下り {:.2f}"
        .format(uphill, flat, downhill)
    )
