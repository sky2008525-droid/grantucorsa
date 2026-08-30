"""Phase 4 — 物理モデル単体のテスト（Engine / Drivetrain / Tire / Aero / Brake）."""

from __future__ import annotations

import math

import pytest

from aero import Aerodynamics
from brake import Brakes
from drivetrain import Drivetrain
from engine import Engine
from tire import Tire
from units import GRAVITY_MPS2, rads_to_rpm, rpm_to_rads, watt_to_ps
from vehicle_data import UnitMismatch, UnknownParameter, VehicleData


@pytest.fixture
def data():
    return VehicleData()


# --- Engine ---------------------------------------------------------------


def test_トルクカーブが公式2点を再現する(data):
    """公式値: 最大トルク 205 N*m / 6,400-6,600rpm、最高出力 147 kW / 7,000rpm。

    補間後の極大の位置ではなく、**公式2点の再現精度**で検証する。
    極大を厳密に 7,000rpm へ置くことは公式値どうしの非整合により不可能
    （Vehicles/ZN6/vehicle.json の torque_curve note を参照）。
    """
    engine = Engine(data)

    assert engine.wot_torque_nm(6400) == pytest.approx(205.0, rel=0.01)
    assert engine.wot_torque_nm(6600) == pytest.approx(205.0, rel=0.01)

    power_at_7000 = engine.wot_torque_nm(7000) * rpm_to_rads(7000)
    assert power_at_7000 == pytest.approx(147_000.0, rel=0.01)
    assert watt_to_ps(power_at_7000) == pytest.approx(200.0, rel=0.02)


def test_4000rpm付近のトルクの谷が消えていない(data):
    """FA20 の特性。2点補間するとこれが消え、中回転域が実車より速くなる。

    そして**その誤差を Optimizer が別のパラメータで吸収してしまう**
    （Docs/ZN6_BASELINE.md）。
    """
    engine = Engine(data)
    dip = engine.wot_torque_nm(4000)
    before = engine.wot_torque_nm(3000)
    after = engine.wot_torque_nm(5000)

    assert dip < before, "3,000rpm より 4,000rpm のトルクが低くない = 谷が無い"
    assert dip < after, "5,000rpm より 4,000rpm のトルクが低くない = 谷が無い"


def test_補間がデータ点間で振動しない(data):
    """PCHIP は単調区間でオーバーシュートしない。

    通常の3次スプラインだと谷の前後で存在しない山を作る。
    """
    engine = Engine(data)
    peak = max(engine._torque_nm)
    trough = min(engine._torque_nm)
    for rpm in range(1000, 7401, 25):
        t = engine.wot_torque_nm(rpm)
        assert trough - 0.5 <= t <= peak + 0.5, "{}rpm で {} N*m は元データの範囲外".format(rpm, t)


def test_点数が少ないカーブを拒否する(tmp_path, data):
    """2点補間の禁止をコードで強制する。"""
    import copy, json

    raw = copy.deepcopy(data._raw)
    raw["engine"]["torque_curve"]["value"] = [[6500, 205.0], [7000, 200.5]]
    path = tmp_path / "two_point.json"
    path.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")

    with pytest.raises(ValueError, match="2点"):
        Engine(VehicleData(path))


def test_全開でトルクカーブに一致し全閉でエンジンブレーキになる(data):
    engine = Engine(data)
    omega = rpm_to_rads(5000)
    assert engine.torque_nm(omega, 1.0) == pytest.approx(engine.wot_torque_nm(5000), rel=1e-9)
    assert engine.torque_nm(omega, 0.0) < 0.0


def test_レブリミットで駆動トルクが消える(data):
    engine = Engine(data)
    over = rpm_to_rads(engine.redline_rpm + 100)
    assert engine.torque_nm(over, 1.0) < 0.0


def test_スロットルの範囲外を拒否する(data):
    engine = Engine(data)
    with pytest.raises(ValueError):
        engine.torque_nm(rpm_to_rads(3000), 1.5)


# --- Drivetrain -----------------------------------------------------------


def test_GTのファイナルが4100であることを強制する(tmp_path, data):
    """G 6MT の 3.727 と取り違えると約10%の駆動力誤差（ZN6_BASELINE.md 罠①）。"""
    import copy, json

    raw = copy.deepcopy(data._raw)
    raw["transmission"]["final_drive"]["value"] = 3.727
    path = tmp_path / "wrong_final.json"
    path.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")

    with pytest.raises(ValueError, match="4.100"):
        Drivetrain(VehicleData(path))


def test_総減速比が単調減少する(data):
    dt = Drivetrain(data)
    ratios = [dt.total_ratio(g) for g in ["1", "2", "3", "4", "5", "6"]]
    assert ratios == sorted(ratios, reverse=True)


def test_5速が直結でファイナルと一致する(data):
    dt = Drivetrain(data)
    assert dt.total_ratio("5") == pytest.approx(dt.final_drive)


def test_エンジン慣性が低速ギアで無視できない大きさになる(data):
    """1速では総比^2 が 220 倍になり、等価質量が車重の 2-3 割に達する。

    これを落とすと発進加速が実車より速くなる。
    """
    dt = Drivetrain(data)
    radius = data.value("tires.effective_radius", "m")
    mass = data.value("mass.curb_mass", "kg")

    first = dt.equivalent_mass_kg("1", radius)
    sixth = dt.equivalent_mass_kg("6", radius)

    assert first > sixth * 10
    assert 0.15 * mass < first < 0.45 * mass


def test_エンジンブレーキに効率を掛けて加速させない(data):
    """負のトルクに効率を掛けると、損失が車を前に押す向きに働く。"""
    dt = Drivetrain(data)
    assert dt.wheel_torque_nm(-100.0, "3") < -100.0 * dt.total_ratio("3")


# --- Tire -----------------------------------------------------------------


@pytest.fixture
def tire(data):
    weight = data.value("mass.curb_mass", "kg") * GRAVITY_MPS2
    return Tire(data, nominal_load_n=weight / 4.0)


def test_摩擦係数が荷重とともに下がる(tire):
    """荷重感度。これが無いと荷重移動の効果が正しく出ない。"""
    assert tire.mu(5000.0) < tire.mu(3000.0) < tire.mu(1500.0)


def test_摩擦円を超えない(tire):
    """複合スリップ。縦横の合力が mu*Fz を超えてはいけない。"""
    fz = 3500.0
    limit = tire.mu(fz) * fz
    for kappa in (-0.5, -0.1, 0.0, 0.1, 0.3, 1.0):
        for alpha in (-0.4, -0.1, 0.0, 0.1, 0.4):
            fx, fy = tire.forces_n(fz, kappa, alpha)
            assert math.hypot(fx, fy) <= limit * 1.001


def test_線形域では剛性どおりの力が出る(tire):
    fz = 3000.0
    kappa = 0.005
    fx, _ = tire.forces_n(fz, kappa, 0.0)
    expected = tire.longitudinal_stiffness_per_load * fz * kappa
    assert fx == pytest.approx(expected, rel=0.05)


def test_縦スリップを増やすと横力が減る(tire):
    """複合スリップの核心。FR ではコーナー脱出のパワーオンで後輪の横力が

    奪われ、パワーオーバーステアになる（SPEC_ZN6.md §6.3）。
    """
    fz = 3500.0
    alpha = 0.10
    _, fy_pure = tire.forces_n(fz, 0.0, alpha)
    _, fy_combined = tire.forces_n(fz, 0.30, alpha)
    assert abs(fy_combined) < abs(fy_pure)


def test_荷重ゼロで力が出ない(tire):
    assert tire.forces_n(0.0, 0.2, 0.1) == (0.0, 0.0)


def test_スリップ率の符号(tire):
    """kappa = (omega*r - v) / |v|。駆動で正、制動で負、自由転動でゼロ。"""
    # 車速 10 m/s、有効半径 0.3 m → 自由転動は omega = 33.33 rad/s
    assert Tire.slip_ratio(40.0, 0.3, 10.0) > 0    # 12.0 m/s 相当 > 10 → 駆動
    assert Tire.slip_ratio(33.3333, 0.3, 10.0) == pytest.approx(0.0, abs=1e-3)
    assert Tire.slip_ratio(20.0, 0.3, 10.0) < 0    # 6.0 m/s 相当 < 10 → 制動
    assert Tire.slip_ratio(0.0, 0.3, 10.0) == pytest.approx(-1.0)  # ロック


# --- Aero / Brake ---------------------------------------------------------


def test_抗力が速度の2乗に比例する(data):
    aero = Aerodynamics(data)
    assert aero.drag_force_n(40.0) == pytest.approx(4.0 * aero.drag_force_n(20.0))


def test_抗力に全高ではなくルーフ高由来の投影面積を使う(data):
    """全高 1.320m はアンテナを含む。投影面積に使うと過大になる

    （Docs/ZN6_BASELINE.md 罠②）。
    """
    aero = Aerodynamics(data)
    roof = data.value("dimensions.roof_height", "m")
    width = data.value("dimensions.width", "m")
    assert aero.frontal_area_m2 < width * roof


def test_ブレーキ配分が前寄りである(data):
    brakes = Brakes(data)
    front, rear = brakes.axle_torques_nm(1.0)
    assert front > rear


def test_ブレーキペダルの範囲外を拒否する(data):
    with pytest.raises(ValueError):
        Brakes(data).axle_torques_nm(1.2)


# --- データドロップ由来の逆算チェック -------------------------------------


def test_ギア比と有効半径の逆算が実車と一致する(data):
    """**縦断モデルはタイヤ有効半径 1個で全速度域がスケールする。**

    6速(0.767) x ファイナル(4.100) と R_e を組んで 100km/h のときのエンジン回転数が
    実車の 6速100km/h ≒ 2,700rpm と一致するか。データドロップの
    `tires.effective_radius` の validation_note が要求している回帰テスト。

    ここがずれていると、加速も最高速も全速度域で一様にずれる。**しかも
    「なんとなく遅い/速い」としてしか現れないので、タイヤμを触って辻褄を
    合わせたくなる**（憲法ルール9 が禁じる事故の入口）。
    """
    from units import rads_to_rpm

    drivetrain = Drivetrain(data)
    radius = data.value("tires.effective_radius", "m")

    speed_mps = 100.0 / 3.6
    wheel_omega = speed_mps / radius
    rpm = rads_to_rpm(drivetrain.engine_omega_rads(wheel_omega, "6"))

    assert rpm == pytest.approx(2700.0, rel=0.03), (
        "6速100km/h で {:.0f} rpm。実車は約 2,700rpm。"
        "有効半径かギア比が疑わしい".format(rpm)
    )


def test_トルクカーブが公式アンカーと出力の整合を保つ(data):
    """データドロップのカーブは 全25点で トルク x omega = 出力 が一致する。

    補間後の最高出力は 147.05kW @ 7,025rpm（定格 147kW @ 7,000rpm）。
    """
    engine = Engine(data)
    peak_w, peak_rpm = engine.peak_power_w()
    assert peak_w == pytest.approx(147_000.0, rel=0.01)
    assert peak_rpm == pytest.approx(7000.0, rel=0.02)


def test_タイヤμがスキッドパッド実測に紐づいている(data):
    """μ は assumed（作り話）ではなく、実測 0.82g から較正した値。

    **スキッドパッドは較正の入力になったので、以後これを検証指標として
    使ってはいけない（循環論法）。**
    """
    param = data.param("tires.friction_coefficient")
    assert param.source == "estimated"
    assert "skidpad" in (param.method or "")
    assert 0.95 <= param.value <= 1.06
