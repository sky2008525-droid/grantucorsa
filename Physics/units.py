"""単位変換を1箇所に集める.

憲法ルール5「SI単位系を内部計算の標準とする」/ ルール13「単位を明示する」。

**式の中に /3.6 や *9.80665 を散らさないこと。** 単位の取り違えは、
物理的にあり得ない結果としてではなく「もっともらしい間違った結果」として現れる。
これが最も見つけにくい。

`vehicle.json` は一次資料の表記に近い形で保存してある（rpm、L）。
SI への変換はここでのみ行う。
"""

from __future__ import annotations

import math

# --- 定数 -----------------------------------------------------------------

GRAVITY_MPS2 = 9.80665
"""標準重力加速度 [m/s^2]。"""

AIR_DENSITY_KGPM3 = 1.225
"""海面上・15degC の空気密度 [kg/m^3]。ISA 標準大気。

**測定条件を変えるなら明示すること。** 気温が変われば空力抗力が変わり、
0-100km/h の実測値がばらつく原因の1つになる。
"""

# --- 変換係数 -------------------------------------------------------------

_KMH_PER_MPS = 3.6
_RADS_PER_RPM = 2.0 * math.pi / 60.0
_M3_PER_LITRE = 1.0e-3
_W_PER_PS = 735.49875


def kmh_to_mps(kmh: float) -> float:
    return kmh / _KMH_PER_MPS


def mps_to_kmh(mps: float) -> float:
    return mps * _KMH_PER_MPS


def rpm_to_rads(rpm: float) -> float:
    """エンジン回転数 [1/min] を角速度 [rad/s] へ。"""
    return rpm * _RADS_PER_RPM


def rads_to_rpm(rads: float) -> float:
    return rads / _RADS_PER_RPM


def litre_to_m3(litre: float) -> float:
    return litre * _M3_PER_LITRE


def ps_to_watt(ps: float) -> float:
    """PS を W へ。**日本の諸元表は PS 表記だが計算には使わない。**"""
    return ps * _W_PER_PS


def watt_to_ps(watt: float) -> float:
    return watt / _W_PER_PS


# --- `vehicle.json` の単位から SI への変換表 --------------------------------
#
# キー: (保存されている単位, 要求された単位)
# 値:   変換関数
#
# ここに無い組み合わせは変換しない（単位が一致しなければ例外を投げる）。
# **暗黙の変換を増やさないこと。** 変換できてしまうと取り違えに気づけなくなる。

CONVERSIONS = {
    ("1/min", "rad/s"): rpm_to_rads,
    ("rad/s", "1/min"): rads_to_rpm,
    ("L", "m^3"): litre_to_m3,
    ("m^3", "L"): lambda x: x / _M3_PER_LITRE,
    ("km/h", "m/s"): kmh_to_mps,
    ("m/s", "km/h"): mps_to_kmh,
}


def convert(value: float, from_unit: str, to_unit: str) -> float:
    """単位変換。変換できない組み合わせは ValueError。"""
    if from_unit == to_unit:
        return value
    key = (from_unit, to_unit)
    if key not in CONVERSIONS:
        raise ValueError(
            "{!r} から {!r} への変換は定義されていない。"
            "暗黙の変換を増やす前に、本当に同じ物理量か確認すること。".format(
                from_unit, to_unit
            )
        )
    return CONVERSIONS[key](value)
