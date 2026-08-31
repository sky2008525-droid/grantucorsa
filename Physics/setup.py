"""車のセッティング（車高・アライメント・ばね・ブレーキバイアス）。

## この層の約束

**スライダーは全部、物理に効くものだけを置く。**

動かしても何も変わらない項目を画面に出さない。出すなら「効かない」と
書く。効いているふりをするのは、数値を捏造するのと同じ性質の嘘である
（憲法ルール1・2の精神）。

だから `UNSUPPORTED` に「今のモデルでは効かないもの」を理由つきで並べて
ある。UI はこれを読んで、灰色で理由を出せばよい。

## 効くもの

| 項目 | どう効くか |
|---|---|
| 車高 | 重心高が変わる -> 荷重移動が変わる |
| トー | 各輪のスリップ角に定数が乗る |
| キャンバー | キャンバー推力が横力に足される |
| ばねレート | 接地モデルのコーナー剛性 -> ロールと過渡 |
| 減衰比 | 接地モデルの減衰 -> 過渡の収まり方 |
| ブレーキバイアス | 前後の制動トルク配分 |

## 既定は「何も変えない」

`CarSetup()` は全項目が中立で、**そのときの結果は今までとビット単位で
一致する**（`Tests/test_setup.py` で検査）。セッティング機能を足したこと
自体で検証済みの結果が動かないようにするため。

## 変更できる範囲

`vehicle.json` の `min` / `max` を超えない（憲法の権限表）。
範囲が書かれていない項目は、**このファイルに範囲を書いて根拠を残す。**

車高だけは扱いが違う。`inertia.cg_height` は `official_marketing` で
Level 1（範囲内のみ・理由必須）だが、ここで動かすのは**基準値そのもの
ではなく、そこからの差**である。車高を 20mm 下げれば重心も下がる、
という物理をモデルに入れているだけで、ZN6 の諸元を書き換えてはいない。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict, List, Tuple

WHEELS = ("FL", "FR", "RL", "RR")
FRONT_WHEELS = ("FL", "FR")
LEFT_WHEELS = ("FL", "RL")


#: 今のモデルでは効かない項目。**理由つきで並べる。**
#:
#: UI はこれを読んで「なぜ触れないか」を出す。項目を黙って消すと、
#: 「このゲームにはこの調整が無い」のか「実装が抜けている」のかが
#: 区別できない。
UNSUPPORTED: Dict[str, str] = {
    "tyre_pressure":
        "タイヤモデルに空気圧の入力が無い。圧力による剛性・μの変化を"
        "測った資料も無く、入れれば数値の捏造になる。",
    "aero_downforce":
        "aerodynamics.lift_coefficient_front / _rear が unknown。"
        "ダウンフォースが計算できないので、ウイング角を置いても効かない。",
    "caster":
        "suspension.geometry が unknown。キャスターからキャンバー変化や"
        "セルフアライニングトルクを出せない。",
    "roll_centre":
        "同じく geometry が unknown。車高を変えるとロールセンタも動くが、"
        "その量が計算できない。**車高の効果は重心高の変化だけ**に留めてある。",
    "anti_roll_bar":
        "arb_front / arb_rear は径（18mm / 14mm）が分かっているが、"
        "アーム長とレバー比が unknown なのでロール剛性に直せない。"
        "接地モデルはスタビライザーを含んでいない。",
    "differential":
        "differential.preload / accel_lock_ratio / decel_lock_ratio は"
        "すべて assumed。動かせはするが、実車のトルセンの特性ではない"
        "ので、セッティングとしては出さない。",
    "gear_ratio":
        "公式ギア比は Level 0（変更禁止）。ZN6 の諸元そのものなので、"
        "セッティングで動かすものではない。",
}


@dataclass(frozen=True)
class Range:
    """調整できる範囲。**UI はここから目盛りを作る。**"""

    low: float
    high: float
    default: float
    unit: str
    label: str
    note: str = ""

    def clamp(self, value: float) -> float:
        return min(max(value, self.low), self.high)

    def contains(self, value: float) -> bool:
        return self.low - 1e-12 <= value <= self.high + 1e-12


class SetupLimits:
    """調整範囲。`vehicle.json` の min/max を超えない。"""

    def __init__(self, data) -> None:
        self.data = data

        # --- 車高 ---
        #
        # 下げ側は最低地上高（official 0.130 m）で頭打ちにする。
        # **地面に擦る車高を選べるようにしない。**
        # 上げ側は純正から +20mm まで（それ以上は実車で現実的でない）。
        clearance_m = data.value("dimensions.ground_clearance", "m")
        self.ride_height = Range(
            low=-min(0.060, clearance_m - 0.060), high=0.020, default=0.0,
            unit="m", label="車高（純正からの差）",
            note="下げると重心が下がる。**下限は最低地上高 {:.0f}mm から"
                 "60mm を残した位置。** ロールセンタの変化は計算できないので"
                 "含んでいない（UNSUPPORTED の roll_centre）。"
                 .format(clearance_m * 1000))

        # --- アライメント ---
        #
        # **範囲の根拠は vehicle.json に無い。** 実車の調整範囲を測った
        # 資料が無いので、ここで決めて理由を残す。ZN6 の純正キャンバーは
        # 資料が取れていないため、既定は 0（＝何も足さない）。
        self.camber_front = Range(
            low=math.radians(-4.0), high=math.radians(1.0), default=0.0,
            unit="rad", label="キャンバー（前）",
            note="負が内側倒し。**既定 0 は「純正値」ではなく「何も足さない」。**"
                 "ZN6 の純正キャンバーの出典が取れていないので、基準を 0 に"
                 "置いてそこからの差として扱う。範囲はストラットの一般的な"
                 "調整幅で、実車で測ったものではない。")
        self.camber_rear = Range(
            low=math.radians(-4.0), high=math.radians(1.0), default=0.0,
            unit="rad", label="キャンバー（後）", note=self.camber_front.note)

        self.toe_front = Range(
            low=math.radians(-0.5), high=math.radians(0.5), default=0.0,
            unit="rad", label="トー（前・片輪）",
            note="正がトーイン（前が内向き）。片輪あたりの角度。"
                 "**既定 0 は「純正値」ではなく「何も足さない」。**")
        self.toe_rear = Range(
            low=math.radians(-0.5), high=math.radians(0.5), default=0.0,
            unit="rad", label="トー（後・片輪）", note=self.toe_front.note)

        # --- ばねと減衰 ---
        #
        # **vehicle.json の min/max を超えない。** ばねレートは estimated で
        # 範囲が書いてあるので、それを倍率に直して使う。
        self.spring_front = self._scale_range(
            "suspension.spring_rate_front", "N/m", "ばねレート（前）")
        self.spring_rear = self._scale_range(
            "suspension.spring_rate_rear", "N/m", "ばねレート（後）")
        self.damping_front = self._scale_range(
            "suspension.damping_ratio_front", "-", "減衰比（前）")
        self.damping_rear = self._scale_range(
            "suspension.damping_ratio_rear", "-", "減衰比（後）")

        # --- ブレーキバイアス ---
        self.brake_bias = self._absolute_range(
            "brakes.brake_bias", "-", "ブレーキバイアス（前）")

    def _scale_range(self, path: str, unit: str, label: str) -> Range:
        """`min`/`max` を基準値に対する倍率にした範囲。

        倍率で持つのは、UI で「純正比 90%」と出せるようにするため。
        **範囲そのものは `vehicle.json` から来る。**
        """
        param = self.data.param(path)
        base = self.data.value(path, unit)
        low = param.minimum if param.minimum is not None else base
        high = param.maximum if param.maximum is not None else base
        if low == high:
            # 範囲が書かれていないなら動かさない。**勝手に広げない。**
            return Range(1.0, 1.0, 1.0, "-", label,
                         note="{} に min/max が無いので調整できない。".format(path))
        return Range(low / base, high / base, 1.0, "-", label,
                     note="{} の min/max（{:.4g}〜{:.4g} {}）を倍率にしたもの。"
                          .format(path, low, high, unit))

    def _absolute_range(self, path: str, unit: str, label: str) -> Range:
        param = self.data.param(path)
        base = self.data.value(path, unit)
        low = param.minimum
        high = param.maximum
        if low is None or high is None:
            # assumed なら Level 3（完全探索）だが、それでも**物理的に
            # あり得る範囲**には収める。
            low, high = 0.50, 0.90
            note = ("{} に min/max が無い（source={}）。前 50〜90% は"
                    "物理的にあり得る範囲として、ここで決めた。"
                    .format(path, param.source))
        else:
            note = "{} の min/max。".format(path)
        return Range(low, high, base, unit, label, note=note)

    def all_ranges(self) -> Dict[str, Range]:
        """UI が読む調整項目の一覧。"""
        return {
            "ride_height_m": self.ride_height,
            "camber_front_rad": self.camber_front,
            "camber_rear_rad": self.camber_rear,
            "toe_front_rad": self.toe_front,
            "toe_rear_rad": self.toe_rear,
            "spring_scale_front": self.spring_front,
            "spring_scale_rear": self.spring_rear,
            "damping_scale_front": self.damping_front,
            "damping_scale_rear": self.damping_rear,
            "brake_bias": self.brake_bias,
        }


@dataclass(frozen=True)
class CarSetup:
    """1台ぶんのセッティング。

    **既定値は「何も変えない」。** そのときの結果は、セッティング機能を
    入れる前とビット単位で一致する。
    """

    ride_height_m: float = 0.0
    """純正からの車高差 [m]。**負が下げ。**"""

    camber_front_rad: float = 0.0
    camber_rear_rad: float = 0.0
    """キャンバー [rad]。**負が内側倒し**（自動車の慣習）。"""

    toe_front_rad: float = 0.0
    toe_rear_rad: float = 0.0
    """トー [rad]（片輪あたり）。**正がトーイン。**"""

    spring_scale_front: float = 1.0
    spring_scale_rear: float = 1.0
    damping_scale_front: float = 1.0
    damping_scale_rear: float = 1.0
    """純正比の倍率 [-]。"""

    brake_bias: float = None
    """前ブレーキの配分 [-]。`None` なら `vehicle.json` の値をそのまま使う。"""

    def is_default(self) -> bool:
        """**何も変えていないか。** 検証はこの状態で行う（ルール18）。"""
        return (self.ride_height_m == 0.0
                and self.camber_front_rad == 0.0 and self.camber_rear_rad == 0.0
                and self.toe_front_rad == 0.0 and self.toe_rear_rad == 0.0
                and self.spring_scale_front == 1.0 and self.spring_scale_rear == 1.0
                and self.damping_scale_front == 1.0 and self.damping_scale_rear == 1.0
                and self.brake_bias is None)

    def validate(self, limits: SetupLimits) -> List[str]:
        """範囲外の項目を並べて返す。**空なら妥当。**

        例外を投げないのは、UI がまとめて赤く出せるようにするため。
        ただし `Vehicle` 側は範囲外を受け取ったら止まる。
        """
        problems = []
        ranges = limits.all_ranges()
        for name, allowed in ranges.items():
            value = getattr(self, name)
            if value is None:
                continue
            if not allowed.contains(value):
                problems.append(
                    "{}（{}）が範囲外: {:.4g} は {:.4g}〜{:.4g} に入らない"
                    .format(allowed.label, name, value, allowed.low, allowed.high))
        return problems

    def clamped(self, limits: SetupLimits) -> "CarSetup":
        """範囲に収めた複製。**UI のスライダー用。**"""
        ranges = limits.all_ranges()
        values = {}
        for name, allowed in ranges.items():
            value = getattr(self, name)
            values[name] = value if value is None else allowed.clamp(value)
        return CarSetup(**values)

    # --- 物理への写像 -----------------------------------------------------

    def wheel_toe_rad(self, wheel: str) -> float:
        """その車輪の静的な向き [rad]。**車体座標系での符号に直す。**

        トーインは「前が内側を向く」こと。左輪では右（負のヨー向き）、
        右輪では左（正）になる。**符号を逆にすると、直進で car が
        片側へ引っ張られる。**
        """
        toe = self.toe_front_rad if wheel in FRONT_WHEELS else self.toe_rear_rad
        return -toe if wheel in LEFT_WHEELS else toe

    def wheel_camber_lean_rad(self, wheel: str) -> float:
        """キャンバーを**車体座標系の傾き**に直す [rad]。正が左へ倒れる。

        自動車の慣習では、キャンバーは車輪ごとに測り、負が内側倒し。
        左輪の内側は右（-y）、右輪の内側は左（+y）なので、同じ「負の
        キャンバー」でも倒れる向きは左右で逆になる。

        **ここを揃えないと、負のキャンバーで車が横に走り出す。**
        直進では左右のキャンバー推力が打ち消し合うのが正しい。
        """
        camber = (self.camber_front_rad if wheel in FRONT_WHEELS
                  else self.camber_rear_rad)
        return camber if wheel in LEFT_WHEELS else -camber

    def cg_height_m(self, baseline_m: float) -> float:
        """セッティング後の重心高 [m]。

        **車高を下げたぶん、そのまま重心が下がるとしている。**
        実際にはバネ下（車輪・ハブ・ブレーキ）は下がらないので、
        重心の下がりはこれより小さい。バネ下重量が `unknown` なので
        配分できず、**効果を大きめに見積もる側**に倒してある。
        """
        return baseline_m + self.ride_height_m

    def describe(self) -> str:
        """人が読む要約。**画面と保存ファイルの両方で使う。**"""
        if self.is_default():
            return "純正（何も変更していない）"
        parts = []
        if self.ride_height_m != 0.0:
            parts.append("車高 {:+.0f}mm".format(self.ride_height_m * 1000))
        for label, value in (("前キャンバー", self.camber_front_rad),
                             ("後キャンバー", self.camber_rear_rad)):
            if value != 0.0:
                parts.append("{} {:+.2f}deg".format(label, math.degrees(value)))
        for label, value in (("前トー", self.toe_front_rad),
                             ("後トー", self.toe_rear_rad)):
            if value != 0.0:
                parts.append("{} {:+.2f}deg".format(label, math.degrees(value)))
        for label, value in (("前ばね", self.spring_scale_front),
                             ("後ばね", self.spring_scale_rear),
                             ("前減衰", self.damping_scale_front),
                             ("後減衰", self.damping_scale_rear)):
            if value != 1.0:
                parts.append("{} {:.0f}%".format(label, value * 100))
        if self.brake_bias is not None:
            parts.append("ブレーキ前 {:.0f}%".format(self.brake_bias * 100))
        return " / ".join(parts)
