"""Day 1 の縦断モデルのテスト.

**ここでは「実測値と一致するか」を検証しない。** validation_targets が unknown で
あり、トルクカーブが assumed（confidence 0.30）だからである（issue #1 / #3）。

検証するのは以下だけ:
  - 保存則と拘束条件を破っていないこと
  - FR として挙動の向きが正しいこと
  - モデルが入力に対して正しく反応すること
"""

from __future__ import annotations

import copy
import json

import pytest

from longitudinal import LongitudinalModel
from units import GRAVITY_MPS2, mps_to_kmh
from vehicle_data import VehicleData


@pytest.fixture
def model():
    return LongitudinalModel(VehicleData())


def variant(tmp_path, name, mutate):
    """vehicle.json を一部だけ変えた VehicleData を作る。"""
    data = VehicleData()
    raw = copy.deepcopy(data._raw)
    mutate(raw)
    path = tmp_path / "{}.json".format(name)
    path.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
    return VehicleData(path)


# --- 成立性 ---------------------------------------------------------------


def test_100kmhに到達する(model):
    result = model.accelerate()
    assert result.time_to_100_kmh_s is not None, "100km/h に到達しない"


def test_保存則と拘束条件を破らない(model):
    """Physics Validity（SPEC_ZN6.md §8.4）。

    「数値が常識的に見えるか」ではなく、破ってはいけない条件で判定する。
    """
    result = model.accelerate()
    assert model.check_physics_validity(result) == []


def test_速度と距離が単調増加する(model):
    result = model.accelerate()
    speeds = [s.speed_mps for s in result.samples]
    distances = [s.distance_m for s in result.samples]
    assert distances == sorted(distances)
    # 変速中は減速するので速度の単調性は要求しない
    assert speeds[-1] > speeds[0]


def test_変速が発生し順序どおりである(model):
    result = model.accelerate()
    assert len(result.shift_points) >= 2
    for _, before, after in result.shift_points:
        assert int(after) == int(before) + 1


# --- FR であることの帰結 ---------------------------------------------------


def test_加速時に後軸荷重が静止時より増える(model):
    """FR なので駆動輪に荷重が乗る。FF とは逆向き（SPEC_ZN6.md §6.5）。"""
    static = model.static_rear_n
    assert model.rear_axle_load_n(4.0) > static
    assert model.rear_axle_load_n(-4.0) < static


def test_荷重移動を無視すると加速が遅くなる(tmp_path):
    """FR では荷重移動が駆動輪を有利にする。

    定荷重モデル（重心高ゼロ相当）は **過小評価側に外れる**。
    FF なら逆に過大評価になる。この向きが逆なら FR のモデル化が間違っている。
    """
    flat = variant(tmp_path, "no_transfer", lambda r: r["inertia"]["cg_height"].update(
        {"value": 0.0, "min": 0.0, "max": 0.0}))

    with_transfer = LongitudinalModel(VehicleData()).accelerate().time_to_100_kmh_s
    without_transfer = LongitudinalModel(flat).accelerate().time_to_100_kmh_s

    assert without_transfer > with_transfer, (
        "荷重移動を消したら速くなった。FR では駆動輪に荷重が乗るので遅くなるはず"
    )


def test_1速はトラクション限界に張り付く(model):
    """200PS / 1230kg の FR では、1速の駆動力はタイヤの摩擦限界を超える。"""
    result = model.accelerate()
    first_gear = [s for s in result.samples if s.gear == "1" and s.speed_mps > 1.0]
    assert first_gear
    limited = sum(1 for s in first_gear if s.traction_limited)
    assert limited / len(first_gear) > 0.8


# --- 入力への反応 ---------------------------------------------------------


def test_タイヤμを上げると速くなる(tmp_path):
    """トラクション限界が上がるため。

    **Optimizer が実測に合わせるために真っ先に触る場所**（憲法ルール9）。
    ここが効くという事実そのものが、μ を動かせば何とでもなることを意味する。
    """
    grippy = variant(tmp_path, "high_mu", lambda r: r["tires"]["friction_coefficient"].update(
        {"value": 1.30}))
    base = LongitudinalModel(VehicleData()).accelerate().time_to_100_kmh_s
    fast = LongitudinalModel(grippy).accelerate().time_to_100_kmh_s
    assert fast < base


def test_回転慣性を無視すると速くなる(tmp_path):
    """1速では等価質量が車重の 2-3 割に達する。落とすと実車より速くなる。"""
    light = variant(tmp_path, "no_inertia", lambda r: r["engine"]["rotational_inertia"].update(
        {"value": 0.001, "min": 0.001, "max": 0.001}))
    base = LongitudinalModel(VehicleData()).accelerate().time_to_100_kmh_s
    without = LongitudinalModel(light).accelerate().time_to_100_kmh_s
    assert without < base


def _wrong_final_drive(tmp_path):
    """G 6MT の 3.727 を GT に当ててしまった状態（ZN6_BASELINE.md 罠①）。"""
    def mutate(raw):
        raw["transmission"]["final_drive"]["value"] = 3.727
        raw["identity"]["grade"] = "G"
        raw["identity"]["transmission_type"] = "6AT"   # グレード検査を回避
    return variant(tmp_path, "wrong_final", mutate)


def test_ファイナルの取り違えは駆動力に約10パーセントの誤差を生む(tmp_path):
    """罠①の実体。トラクション限界に当たらない条件で駆動力を直接比べる。

    4.100 / 3.727 = 1.100 なので、駆動力の比もそうなるはず。
    """
    base = LongitudinalModel(VehicleData())
    wrong = LongitudinalModel(_wrong_final_drive(tmp_path))

    speed_mps = 60.0 / 3.6
    f_correct = base._tractive_force_n(speed_mps, "3", 1.0)
    f_wrong = wrong._tractive_force_n(speed_mps, "3", 1.0)

    assert f_wrong < f_correct
    assert f_correct / f_wrong == pytest.approx(4.100 / 3.727, rel=0.05)


def test_0_100だけを見るとファイナルの取り違えを見落とす(tmp_path):
    """**憲法ルール10「複数指標で比較する」の実証。**

    駆動力は約10%落ちるのに、0-100km/h は逆に **速くなる**。理由:

      ファイナル 4.100: 2速の頭打ちが 94.3km/h → 100km/h に 3速が要る（変速2回）
      ファイナル 3.727: 2速の頭打ちが 103.8km/h → 2速で届く（変速1回）

    変速1回ぶん（0.4s）の節約が、駆動力低下ぶんを上回る。さらに 1〜2速の
    約半分はトラクション限界に張り付いているため、駆動力が落ちても加速度が
    変わらない区間が長い。

    **単一指標の一致は、入力データの誤りを隠すどころか符号を反転させうる。**
    0-100km/h だけを合わせにいく検証は、この誤りを「合っている」と判定する。
    """
    base = LongitudinalModel(VehicleData()).accelerate()
    wrong = LongitudinalModel(_wrong_final_drive(tmp_path)).accelerate()

    # 誤りがあるのに 0-100 は速くなる
    assert wrong.time_to_100_kmh_s < base.time_to_100_kmh_s

    # 一方で変速回数が変わっている = 別の指標では検出できる
    assert len(wrong.shift_points) < len(base.shift_points)


def test_変速時間が結果に直接効く(model):
    """変速時間 0.2s の違いは、5回変速すれば 1.0s の差になる。

    公開されている 0-100km/h の実測値がばらつく主因の1つ（issue #1）。
    """
    quick = LongitudinalModel(VehicleData(), shift_time_s=0.15).accelerate()
    slow = LongitudinalModel(VehicleData(), shift_time_s=0.60).accelerate()
    assert slow.time_to_100_kmh_s > quick.time_to_100_kmh_s


# --- 信頼度の伝播 ---------------------------------------------------------


def test_結果の信頼度が入力の最小値を超えない(model):
    result = model.accelerate()
    weakest = min(p.confidence for p in model.data.accessed.values())
    assert result.confidence == pytest.approx(weakest)


def test_この結果は検証対象にできない(model):
    """トルクカーブが assumed（confidence 0.30）である限り、

    実測との一致・不一致をモデルの妥当性の証拠として扱ってはいけない。
    **issue #3 が閉じるまでこのテストは通り続けるべき。**
    通らなくなったら、それは実測ベースのデータが入ったということ。
    """
    result = model.accelerate()
    assert not result.validatable
    assert result.confidence < 0.40


def test_到達時間が物理的にあり得ない値でない(model):
    """検証ではなく、桁が壊れていないことの確認。

    200PS / 1230kg / FR が 3秒を切ることも 20秒かかることも物理的にない。
    """
    result = model.accelerate()
    assert 4.0 < result.time_to_100_kmh_s < 15.0
