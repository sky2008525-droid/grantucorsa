"""Tools/validate_vehicle.py のテスト.

このテストが守っているもの:
  - 憲法の層1（コミット前ゲート）が意図どおり通す／止めること
  - 実物の Vehicles/ZN6/vehicle.json がエラー0件であり続けること（回帰）

物理モデルはまだ実装していないため、Tests/ にはこれしかない。
Phase 4 で Physics/ のテストが加わる。
"""

import json
from pathlib import Path

import pytest

import validate_vehicle as vv

REPO_ROOT = Path(__file__).resolve().parent.parent
VEHICLE_JSON = REPO_ROOT / "Vehicles" / "ZN6" / "vehicle.json"


def check(node, path="test.node"):
    """1ノードを検証して Report を返す。"""
    report = vv.Report()
    vv.validate_node(path, node, report)
    return report


def errors(node):
    return [m for _, m in check(node).errors]


def warnings(node):
    return [m for _, m in check(node).warnings]


VALID = {"value": 2.570, "unit": "m", "source": "official", "confidence": 1.0}


# --- 正常系 ---------------------------------------------------------------


def test_正しいノードはエラーも警告も出ない():
    report = check(VALID)
    assert report.errors == []
    assert report.warnings == []


def test_文字列の値にはunitを要求しない():
    node = {"value": "マクファーソンストラット式", "source": "official", "confidence": 1.0}
    assert errors(node) == []


def test_無次元はunitがハイフン():
    node = {"value": 3.626, "unit": "-", "source": "official", "confidence": 1.0}
    assert errors(node) == []


# --- 必須フィールド -------------------------------------------------------


@pytest.mark.parametrize("missing", ["source", "confidence"])
def test_必須フィールドの欠落を検出する(missing):
    node = dict(VALID)
    del node[missing]
    assert any(missing in m for m in errors(node))


def test_数値なのにunitが無いとエラー():
    node = {"value": 2.570, "source": "official", "confidence": 1.0}
    assert any("unit" in m for m in errors(node))


def test_valueがnullならエラー():
    node = {"value": None, "unit": "m", "source": "official", "confidence": 1.0}
    assert any("null" in m for m in errors(node))


# --- source -------------------------------------------------------------


def test_未定義のsourceを弾く():
    node = dict(VALID, source="web")
    assert any("未定義" in m for m in errors(node))


def test_6分類すべてを受け付ける():
    """official_marketing と secondary は CL1版の4分類には無かった（SPEC_ZN6.md §5.2）。"""
    assert set(vv.SOURCE_CONFIDENCE_BANDS) == {
        "official", "official_marketing", "measured",
        "secondary", "estimated", "assumed",
    }


@pytest.mark.parametrize("source", ["estimated", "assumed"])
def test_推定値にはmethodが必須(source):
    low, high = vv.SOURCE_CONFIDENCE_BANDS[source]
    node = dict(VALID, source=source, confidence=high)
    assert any("method" in m for m in errors(node))
    node["method"] = "geometric_calculation"
    assert errors(node) == []


# --- confidence の帯 -----------------------------------------------------


def test_帯を上に外れるのはエラー():
    """過大申告は防ぎたい事故。"""
    node = dict(VALID, source="assumed", confidence=0.9, method="x")
    assert any("過大申告" in m for m in errors(node))


def test_帯を下に外れるのは警告どまり():
    """慎重側に申告することを禁じると、嘘をつく方向に圧力がかかる。

    実データの例:
      tires.size       = secondary / 0.6  諸元表で裏が取れていない
      brakes.rear_type は照合で official / 1.0 に修正済み
    """
    node = {"value": "215/45R17", "source": "secondary", "confidence": 0.6}
    assert errors(node) == []
    assert any("下回る" in m for m in warnings(node))


def test_confidenceが範囲外ならエラー():
    assert any("0.0–1.0" in m for m in errors(dict(VALID, confidence=1.5)))


# --- unit ----------------------------------------------------------------


def test_非SI単位を弾く():
    assert any("SI" in m for m in errors(dict(VALID, unit="mm")))


def test_許容した非SI単位は警告どまり():
    node = {"value": 1.998, "unit": "L", "source": "official", "confidence": 1.0}
    assert errors(node) == []
    assert any("SI ではない" in m for m in warnings(node))


# --- min / max -----------------------------------------------------------


def test_minとmaxは両方揃える():
    assert any("両方" in m for m in errors(dict(VALID, min=2.5)))


def test_valueがminmaxの外ならエラー():
    node = dict(VALID, min=2.0, max=2.5)
    assert any("範囲外" in m for m in errors(node))


def test_minがmaxより大きいとエラー():
    node = dict(VALID, value=2.2, min=2.5, max=2.0)
    assert any("min" in m and "max" in m for m in errors(node))


# --- unknown -------------------------------------------------------------


def test_unknownは検証をすり抜ける():
    """不明な値は "unknown" と書く（憲法ルール14）。中途半端に埋めさせない。"""
    data = {"tires": {"effective_radius": "unknown", "size": dict(VALID)}}
    found = dict(vv.walk_measurement_nodes(data))
    assert "tires.size" in found
    assert "tires.effective_radius" not in found


def test_下線始まりのフィールドは検証対象外():
    data = {"engine": {"_note": "注記", "displacement": dict(VALID)}}
    assert list(dict(vv.walk_measurement_nodes(data))) == ["engine.displacement"]


# --- Level 0 -------------------------------------------------------------


def base():
    return {
        "engine": {"displacement": {"value": 1.998}},
        "dimensions": {"wheelbase": {"value": 2.570}},
        "mass": {"curb_mass": {"value": 1230}},
        "transmission": {
            "gear_ratios": {"1": {"value": 3.626}, "2": {"value": 2.188}},
            "final_drive": {
                "value": 4.100,
                "variants": {"G_6MT_open_diff": 3.727, "GT_GTLimited_all": 4.100},
            },
        },
    }


def level0(old, new):
    report = vv.Report()
    vv.compare_level0(old, new, report)
    return report


def test_変更が無ければ通る():
    assert level0(base(), base()).errors == []


@pytest.mark.parametrize("path,value", [
    (("engine", "displacement"), 2.000),
    (("dimensions", "wheelbase"), 2.600),
    (("mass", "curb_mass"), 1200),
])
def test_Level0_の値の変更を止める(path, value):
    new = base()
    node = new
    for k in path:
        node = node[k]
    node["value"] = value
    assert len(level0(base(), new).errors) == 1


def test_ギア比の変更を止める():
    new = base()
    new["transmission"]["gear_ratios"]["1"]["value"] = 3.500
    errs = level0(base(), new).errors
    assert len(errs) == 1
    assert errs[0][0] == "transmission.gear_ratios.1.value"


def test_ファイナルの値の変更を止める():
    new = base()
    new["transmission"]["final_drive"]["value"] = 3.727
    assert len(level0(base(), new).errors) == 1


def test_ファイナルのvariantの変更を止める():
    """ZN6 のファイナルは単一値ではない（ZN6_BASELINE.md 罠①）。

    value だけを監視していても variant の取り違えは検出できない。
    G 6MT のみ 3.727、GT/GT"Limited"/6AT 全車は 4.100。
    取り違えると約10%の駆動力誤差が入り、Optimizer がタイヤμを不当に触る。
    """
    new = base()
    del new["transmission"]["final_drive"]["variants"]["G_6MT_open_diff"]
    errs = level0(base(), new).errors
    assert len(errs) == 1
    assert errs[0][0] == "transmission.final_drive.variants"


def test_Level0_の削除を止める():
    new = base()
    del new["dimensions"]["wheelbase"]
    assert any("削除" in m for _, m in level0(base(), new).errors)


def test_Level0_以外の変更は通る():
    """note の追記や unit の付与でコミットが止まってはいけない。"""
    old, new = base(), base()
    new["transmission"]["final_drive"]["note"] = "説明を追記"
    new["dimensions"]["wheelbase"]["unit"] = "m"
    assert level0(old, new).errors == []


def test_保護対象の一覧がSPECと一致する():
    assert vv.LEVEL0_PATHS == [
        "engine.displacement.value",
        "dimensions.wheelbase.value",
        "transmission.gear_ratios.*.value",
        "transmission.final_drive.value",
        "transmission.final_drive.variants",
        "mass.curb_mass.value",
    ]


# --- 実データの回帰テスト -------------------------------------------------


def test_実物のvehicle_jsonがエラー0件であり続ける():
    report = vv.Report()
    vv.validate_schema(json.loads(VEHICLE_JSON.read_text(encoding="utf-8")),
                       str(VEHICLE_JSON), report)
    assert report.errors == [], "vehicle.json がスキーマ違反: {}".format(report.errors)


def test_基準車両がGTの前提を保つ():
    """基準車両を変えたら vehicle.json を作り直すこと（ZN6_BASELINE.md）。

    ギア比・ファイナル・車重・装備が連動して変わるため、
    部分的な差し替えは必ず不整合を生む。
    """
    d = json.loads(VEHICLE_JSON.read_text(encoding="utf-8"))
    assert d["identity"]["grade"] == "GT"
    assert d["identity"]["transmission_type"] == "6MT"
    assert d["identity"]["drivetrain"] == "FR"
    # GT のファイナルは 4.100。3.727 は G 6MT（オープンデフ）用
    assert d["transmission"]["final_drive"]["value"] == 4.100
    # 3Dスケール補正の基準はルーフ高。全高1.320はアンテナ込み
    assert d["dimensions"]["roof_height"]["value"] == 1.285
