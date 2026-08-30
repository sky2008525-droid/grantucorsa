"""`vehicle.json` の読み取り.

**物理コードが数値をハードコードしないための唯一の入口。**

このモジュールが守っていること（`.claude/rules/physics.md`）:

- `"unknown"` を読んだら例外を投げて止まる。デフォルト値で代用しない（憲法ルール14）
- 要求した単位と保存されている単位が違えば例外。定義済みの変換のみ通す（ルール5・13）
- 読んだ全パラメータの `confidence` を記録し、**結果の信頼度が入力の最小値を
  超えないようにする**

最後の点が重要。トルクカーブが `assumed` / 0.30 なら、そこから計算した
0-100km/h の信頼度も 0.30 を超えない。**結果だけを見て「実測と一致した」と
言えなくなる**のが狙い（`Docs/AGENT_TOPOLOGY.md` §3）。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from units import convert

UNKNOWN = "unknown"

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_VEHICLE_JSON = REPO_ROOT / "Vehicles" / "ZN6" / "vehicle.json"


class UnknownParameter(KeyError):
    """`"unknown"` の項目を読もうとした。

    **これを握りつぶしてデフォルト値を入れないこと。** 値が無いなら
    そのモデルはまだ動かせない、というのが正しい状態。
    """


class MissingParameter(KeyError):
    """パスがそもそも存在しない。"""


class UnitMismatch(ValueError):
    """要求した単位と保存されている単位が違う。"""


@dataclass(frozen=True)
class Param:
    """`vehicle.json` の1項目。"""

    path: str
    value: Any
    unit: Optional[str]
    source: str
    confidence: float
    minimum: Optional[float] = None
    maximum: Optional[float] = None
    method: Optional[str] = None
    note: Optional[str] = None

    @property
    def is_measured(self) -> bool:
        """一次資料または実測に基づくか。"""
        return self.source in ("official", "official_marketing", "measured", "secondary")


@dataclass
class VehicleData:
    """`vehicle.json` のラッパ。"""

    path: Path = DEFAULT_VEHICLE_JSON
    _raw: Dict[str, Any] = field(default_factory=dict, repr=False)
    _accessed: Dict[str, Param] = field(default_factory=dict, repr=False)

    def __post_init__(self) -> None:
        self.path = Path(self.path)
        with self.path.open(encoding="utf-8") as handle:
            self._raw = json.load(handle)

    # --- 生の取得 ---------------------------------------------------------

    def _node(self, dotted: str) -> Any:
        node: Any = self._raw
        for key in dotted.split("."):
            if not isinstance(node, dict) or key not in node:
                raise MissingParameter(
                    "{} は vehicle.json に存在しない".format(dotted)
                )
            node = node[key]
        return node

    def param(self, dotted: str) -> Param:
        """1項目を `Param` として取得し、アクセスを記録する。"""
        node = self._node(dotted)

        if node == UNKNOWN:
            raise UnknownParameter(
                "{} は unknown。値が取れるまでこのモデルは動かせない。\n"
                "  推測で埋めないこと（憲法ルール14）。出典を取るか、"
                "source='assumed' + method + confidence<=0.39 で明示的に置くこと。".format(dotted)
            )
        if not isinstance(node, dict) or "value" not in node:
            raise MissingParameter(
                "{} は測定ノードではない（value を持たない）".format(dotted)
            )

        param = Param(
            path=dotted,
            value=node["value"],
            unit=node.get("unit"),
            source=node.get("source", "unknown"),
            confidence=float(node.get("confidence", 0.0)),
            minimum=node.get("min"),
            maximum=node.get("max"),
            method=node.get("method"),
            note=node.get("note"),
        )
        self._accessed[dotted] = param
        return param

    # --- 数値の取得 -------------------------------------------------------

    def value(self, dotted: str, unit: str) -> float:
        """数値を取得する。**単位を必ず指定させる。**

        保存単位と一致しない場合、`units.CONVERSIONS` に定義があれば変換し、
        無ければ例外を投げる。
        """
        param = self.param(dotted)
        if param.unit is None:
            raise UnitMismatch(
                "{} は unit を持たない。単位付きで読めない。".format(dotted)
            )
        if not isinstance(param.value, (int, float)) or isinstance(param.value, bool):
            raise UnitMismatch(
                "{} の value は数値でない: {!r}".format(dotted, param.value)
            )
        try:
            return convert(float(param.value), param.unit, unit)
        except ValueError as exc:
            raise UnitMismatch(
                "{}: 保存単位 {!r} を要求単位 {!r} にできない。\n  {}".format(
                    dotted, param.unit, unit, exc
                )
            ) from exc

    def bounds(self, dotted: str, unit: str) -> Tuple[float, float]:
        """min/max を取得する。無い項目は (value, value)。

        Optimizer はこの範囲を超えて探索してはいけない。
        """
        param = self.param(dotted)
        if param.minimum is None or param.maximum is None:
            v = self.value(dotted, unit)
            return (v, v)
        return (
            convert(float(param.minimum), param.unit, unit),
            convert(float(param.maximum), param.unit, unit),
        )

    def curve(self, dotted: str, x_unit: str, y_unit: str) -> Tuple[List[float], List[float]]:
        """[[x, y], ...] 形式の曲線を取得する。

        トルクカーブ用。x の単位は `rpm_unit` フィールドから読む。
        """
        node = self._node(dotted)
        if node == UNKNOWN:
            raise UnknownParameter(
                "{} は unknown。\n"
                "  トルクカーブを2点（最大出力/最大トルク）だけで補間してはいけない。"
                "FA20 は 4,000rpm 付近に谷があり、2点補間では消える"
                "（Docs/ZN6_BASELINE.md）。".format(dotted)
            )
        param = self.param(dotted)
        pairs: Sequence[Sequence[float]] = param.value
        stored_x_unit = node.get("rpm_unit", x_unit)
        xs = [convert(float(x), stored_x_unit, x_unit) for x, _ in pairs]
        ys = [convert(float(y), param.unit, y_unit) for _, y in pairs]
        return xs, ys

    def text(self, dotted: str) -> str:
        """文字列の測定ノード（形式名など）を取得する。"""
        return str(self.param(dotted).value)

    def plain(self, dotted: str) -> Any:
        """測定ノードでない素の値を取得する。

        `identity.grade` のように、ブロック単位で source / confidence が
        付いている項目用。**数値には使わないこと**（単位検証を通らないため）。
        """
        node = self._node(dotted)
        if isinstance(node, dict) and "value" in node:
            raise MissingParameter(
                "{} は測定ノード。plain() ではなく param()/value() を使うこと".format(dotted)
            )
        if node == UNKNOWN:
            raise UnknownParameter("{} は unknown".format(dotted))
        return node

    # --- 信頼度の伝播 -----------------------------------------------------

    @property
    def accessed(self) -> Dict[str, Param]:
        """これまでに読んだ全項目。"""
        return dict(self._accessed)

    def weakest(self) -> Optional[Param]:
        """読んだ中で最も confidence が低い項目。

        **計算結果の信頼度はこれを超えない。**
        """
        if not self._accessed:
            return None
        return min(self._accessed.values(), key=lambda p: p.confidence)

    def result_confidence(self) -> float:
        """計算結果に付けてよい confidence の上限。"""
        weakest = self.weakest()
        return 0.0 if weakest is None else weakest.confidence

    def is_validatable(self, threshold: float = 0.40) -> bool:
        """この結果を Reality Validator の検証対象にしてよいか。

        `assumed`（0.0-0.39）の値が1つでも混ざっていたら False。
        実測との一致を主張できる状態ではない。
        """
        return self.result_confidence() >= threshold

    def provenance_report(self) -> str:
        """読んだ全項目を confidence 順に並べた出所レポート。"""
        if not self._accessed:
            return "（パラメータを1つも読んでいない）"

        lines = ["読んだパラメータ {} 件（confidence 昇順）".format(len(self._accessed)), ""]
        for param in sorted(self._accessed.values(), key=lambda p: (p.confidence, p.path)):
            unit = param.unit or "-"
            value = param.value
            shown = "curve[{}]".format(len(value)) if isinstance(value, list) else value
            lines.append(
                "  {:<5.2f} {:<20s} {:<46s} {} {}".format(
                    param.confidence, param.source, param.path, shown, unit
                )
            )

        weakest = self.weakest()
        lines += [
            "",
            "  結果に付けてよい confidence の上限: {:.2f}".format(self.result_confidence()),
            "  律速している項目: {} ({})".format(weakest.path, weakest.source),
        ]
        if not self.is_validatable():
            lines += [
                "",
                "  ** この結果は Reality Validator の検証対象にできない **",
                "  assumed 相当の入力が混ざっているため、実測との一致・不一致を",
                "  モデルの妥当性の証拠として扱えない。",
            ]
        return "\n".join(lines)
