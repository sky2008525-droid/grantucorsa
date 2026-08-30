#!/usr/bin/env python3
"""vehicle.json のスキーマ検証と Level 0 パラメータの変更検出.

憲法（Docs/SPEC_ZN6.md §5.2 / §5.3）の層1を実装する。
標準ライブラリのみを使う。venv の外から git フックに呼ばれても動く必要があるため。

使い方:
    # スキーマ検証
    python3 Tools/validate_vehicle.py Vehicles/ZN6/vehicle.json

    # Level 0 パラメータの変更検出（旧 → 新）
    python3 Tools/validate_vehicle.py --level0 old.json new.json

    # 標準入力から読む（git フックが `git show :path` を渡すため）
    git show :Vehicles/ZN6/vehicle.json | python3 Tools/validate_vehicle.py -

終了コード: 0 = 問題なし / 1 = エラーあり / 2 = 使い方の誤り
"""

from __future__ import annotations

import argparse
import json
import sys

# ---------------------------------------------------------------------------
# 憲法の定義（Docs/SPEC_ZN6.md §5.2）
# ---------------------------------------------------------------------------

# source と confidence の帯。帯を外れたら「分類と信頼度が食い違っている」ことになる。
SOURCE_CONFIDENCE_BANDS = {
    "official":           (0.90, 1.00),
    "calculated":         (0.60, 0.95),
    "official_marketing": (0.70, 0.89),
    "measured":           (0.70, 0.89),
    "measured_shape":     (0.40, 0.60),
    "secondary":          (0.70, 0.89),
    "estimated":          (0.40, 0.69),
    "assumed":            (0.00, 0.39),
}

# `calculated` と `measured_shape` は 2026-08-30 のデータドロップで追加した。
#
#   calculated     既知の値からの**厳密な計算**。モデル上の仮定を含まない。
#                  例: タイヤ無負荷半径 = リム径 + 2*サイドウォール（サイズから一意）
#                      7,000rpm のトルク = 公式最高出力 / 角速度
#                  estimated と分ける理由: estimated は「こういうモデルだと仮定すると」が
#                  入るが、calculated は入力さえ正しければ一意に決まる。
#                  confidence は入力の confidence を超えてはいけない。
#
#   measured_shape 実測から**形状だけ**を取り、絶対値は公式アンカーに正規化したもの。
#                  例: FA20 のトルクカーブの 4,000rpm の谷（ダイノ実測の記述由来だが、
#                      絶対値は公式の 205N*m / 147kW に合わせてある）
#                  measured と分ける理由: その量そのものを測った値ではないため。

# method が必須の source（推定方法を記録する / 憲法ルール4）
METHOD_REQUIRED_SOURCES = {"estimated", "assumed", "calculated", "measured_shape"}

# SI 単位と、無次元を表す "-"。
SI_UNITS = {
    "-",                                    # 無次元（比・係数）
    "m", "m^2", "m^3",                      # 長さ・面積・体積
    "kg", "kg*m^2",                         # 質量・慣性モーメント
    "s",                                    # 時間
    "N", "N*m", "N/m", "N*s/m",             # 力・トルク・バネ定数・減衰係数
    "N*m*s", "N*m/rad",                     # 回転減衰係数・トルク剛性
    "1/N", "1/rad",                         # 荷重感度・正規化コーナリング剛性
    "W", "J", "Pa",                         # 仕事率・エネルギー・圧力
    "rad", "rad/s", "1/s",                  # 角度・角速度・周波数
    "m/s", "m/s^2",                         # 速度・加速度
    "K",                                    # 温度
}

# SI ではないが、この分野で慣用的に使われ、変換が一意に定まるもの。
# 使うたびに理由を残す。無条件に増やさないこと。
ACCEPTED_NON_SI_UNITS = {
    "L": "排気量・タンク容量は L 表記が一次資料の標準。1 L = 1e-3 m^3 で一意に変換できる",
    "persons": "乗車定員は個数であり物理量ではない。単位名を残す方が読み間違いを防げる",
    "1/min": "エンジン回転数。ISO 標準表記 min^-1。一次資料は例外なく rpm 表記で、rad/s で保存すると照合できない。omega = rpm * 2*pi/60 で一意に変換でき、変換は Physics/units.py の 1 箇所に集約してある",
}

# ---------------------------------------------------------------------------
# Level 0（絶対変更禁止 / Docs/SPEC_ZN6.md §5.3）
# ---------------------------------------------------------------------------
# ワイルドカード "*" は任意の1階層にマッチする。
#
# final_drive は value だけでなく variants も監視する。
# ZN6 のファイナルは単一値ではない（G 6MT のみ 3.727、他 4.100）ため、
# 値だけを見ていても variant の取り違えは検出できない。
# 根拠: Docs/ZN6_BASELINE.md 罠①
LEVEL0_PATHS = [
    "engine.displacement.value",
    "dimensions.wheelbase.value",
    "transmission.gear_ratios.*.value",
    "transmission.final_drive.value",
    "transmission.final_drive.variants",
    "mass.curb_mass.value",
]

UNKNOWN = "unknown"


# ---------------------------------------------------------------------------


class Report:
    def __init__(self) -> None:
        self.errors = []
        self.warnings = []

    def error(self, path, message):
        self.errors.append((path, message))

    def warn(self, path, message):
        self.warnings.append((path, message))

    def print(self, label):
        for path, msg in self.errors:
            print("  ERROR  {:<48s} {}".format(path, msg))
        for path, msg in self.warnings:
            print("  WARN   {:<48s} {}".format(path, msg))
        n_e, n_w = len(self.errors), len(self.warnings)
        if n_e == 0 and n_w == 0:
            print("  OK     {} — 問題なし".format(label))
        else:
            print("  ---- {}: エラー {} 件 / 警告 {} 件".format(label, n_e, n_w))
        return n_e == 0


def is_measurement_node(obj):
    """value キーを持つ dict を「測定ノード」とみなす。"""
    return isinstance(obj, dict) and "value" in obj


def walk_measurement_nodes(obj, path=""):
    """測定ノードを (path, node) で列挙する。unknown はスキップする。"""
    if obj == UNKNOWN:
        return
    if is_measurement_node(obj):
        yield path, obj
        return
    if isinstance(obj, dict):
        for key, value in obj.items():
            # _meta / _note のような下線始まりの注記フィールドは検証対象外
            if key.startswith("_"):
                continue
            child = "{}.{}".format(path, key) if path else key
            for item in walk_measurement_nodes(value, child):
                yield item


def validate_node(path, node, report):
    value = node.get("value")

    if value is None:
        report.error(path, "value が null。不明なら項目全体を \"unknown\" にすること")
        return

    # --- source ---------------------------------------------------------
    source = node.get("source")
    if source is None:
        report.error(path, "source が無い（憲法ルール2）")
    elif source not in SOURCE_CONFIDENCE_BANDS:
        report.error(path, "source={!r} は未定義。許容: {}".format(
            source, ", ".join(sorted(SOURCE_CONFIDENCE_BANDS))))

    # --- confidence -----------------------------------------------------
    confidence = node.get("confidence")
    if confidence is None:
        report.error(path, "confidence が無い（憲法ルール3）")
    elif not isinstance(confidence, (int, float)) or isinstance(confidence, bool):
        report.error(path, "confidence が数値でない: {!r}".format(confidence))
    elif not 0.0 <= confidence <= 1.0:
        report.error(path, "confidence={} が 0.0–1.0 の外".format(confidence))
    elif source in SOURCE_CONFIDENCE_BANDS:
        low, high = SOURCE_CONFIDENCE_BANDS[source]
        # 帯を「上に」外れるのは過大申告であり危険。エラーにする。
        # 帯を「下に」外れるのは通常より慎重なだけで、防ぎたい事故ではない。
        # 例: secondary / 0.6 = 「二次情報で、しかも一次資料と突き合わせられていない」
        #     official / 0.8 = 「公式資料に載っているが、グレード間の対応が曖昧」
        # どちらも正直な申告であって、禁じると嘘をつく方向に圧力がかかる。
        if confidence > high:
            report.error(path, "confidence={} が source={!r} の上限 {} を超えている（過大申告）".format(
                confidence, source, high))
        elif confidence < low:
            report.warn(path, "confidence={} が source={!r} の帯 {}–{} を下回る（慎重側。note に理由を書くこと）".format(
                confidence, source, low, high))

    # --- unit -----------------------------------------------------------
    # 数値には unit を必須とする。文字列（形式名・型式など）は測定値ではないので免除。
    unit = node.get("unit")
    is_numeric = isinstance(value, (int, float)) and not isinstance(value, bool)
    if is_numeric:
        if unit is None:
            report.error(path, "数値なのに unit が無い（憲法ルール13）。無次元なら \"-\" と書く")
        elif unit in ACCEPTED_NON_SI_UNITS:
            report.warn(path, "unit={!r} は SI ではない（許容理由: {}）".format(
                unit, ACCEPTED_NON_SI_UNITS[unit]))
        elif unit not in SI_UNITS:
            report.error(path, "unit={!r} は SI 単位でない（憲法ルール5）".format(unit))
    elif unit is not None and unit not in SI_UNITS and unit not in ACCEPTED_NON_SI_UNITS:
        report.error(path, "unit={!r} は SI 単位でない（憲法ルール5）".format(unit))

    # --- method ---------------------------------------------------------
    if source in METHOD_REQUIRED_SOURCES and not node.get("method"):
        report.error(path, "source={!r} には method が必須（憲法ルール4）".format(source))

    # --- min / max ------------------------------------------------------
    vmin, vmax = node.get("min"), node.get("max")
    if (vmin is None) != (vmax is None):
        report.error(path, "min と max は両方揃えること")
    elif vmin is not None:
        if not is_numeric:
            report.error(path, "数値でない value に min/max がある")
        elif vmin > vmax:
            report.error(path, "min={} > max={}".format(vmin, vmax))
        elif not vmin <= value <= vmax:
            report.error(path, "value={} が min={} / max={} の範囲外".format(value, vmin, vmax))
    elif is_numeric and source and source != "official":
        report.warn(path, "source={!r} だが min/max が無い（不確実性の範囲を持たせること）".format(source))


def validate_schema(data, label, report):
    if not isinstance(data, dict):
        report.error("(root)", "トップレベルが JSON オブジェクトでない")
        return

    if not isinstance(data.get("_meta"), dict) or not data["_meta"].get("schema_version"):
        report.warn("_meta.schema_version", "スキーマバージョンが無い")

    seen = 0
    for path, node in walk_measurement_nodes(data):
        validate_node(path, node, report)
        seen += 1

    if seen == 0:
        report.error("(root)", "測定ノード（value を持つ項目）が1つも無い")


# ---------------------------------------------------------------------------
# Level 0 の変更検出
# ---------------------------------------------------------------------------


def resolve_paths(data, pattern):
    """'a.*.b' 形式のパターンを (具体パス, 値) の一覧に展開する。"""
    results = []

    def rec(node, parts, path):
        if not parts:
            results.append((path, node))
            return
        head, rest = parts[0], parts[1:]
        if not isinstance(node, dict):
            return
        if head == "*":
            for key in sorted(node):
                rec(node[key], rest, "{}.{}".format(path, key) if path else key)
        elif head in node:
            rec(node[head], rest, "{}.{}".format(path, head) if path else head)

    rec(data, pattern.split("."), "")
    return results


def compare_level0(old, new, report):
    """Level 0 パラメータが変更されていたらエラーにする。"""
    for pattern in LEVEL0_PATHS:
        old_map = dict(resolve_paths(old, pattern))
        new_map = dict(resolve_paths(new, pattern))

        for path in sorted(set(old_map) | set(new_map)):
            if path not in new_map:
                report.error(path, "Level 0 パラメータが削除されている")
            elif path not in old_map:
                report.warn(path, "Level 0 パラメータが新規追加された（出典を確認すること）")
            elif old_map[path] != new_map[path]:
                report.error(path, "Level 0 パラメータが変更されている: {} → {}".format(
                    json.dumps(old_map[path], ensure_ascii=False),
                    json.dumps(new_map[path], ensure_ascii=False)))


# ---------------------------------------------------------------------------


def load(path):
    try:
        if path == "-":
            return json.load(sys.stdin)
        with open(path, encoding="utf-8") as handle:
            return json.load(handle)
    except FileNotFoundError:
        print("ERROR: ファイルが無い: {}".format(path), file=sys.stderr)
        raise SystemExit(1)
    except json.JSONDecodeError as exc:
        print("ERROR: JSON として読めない: {} — {}".format(path, exc), file=sys.stderr)
        raise SystemExit(1)


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="vehicle.json のスキーマ検証と Level 0 パラメータの変更検出",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("files", nargs="*", help="検証する JSON（- で標準入力）")
    parser.add_argument("--level0", nargs=2, metavar=("OLD", "NEW"),
                        help="Level 0 パラメータの変更を検出する（旧 → 新）")
    parser.add_argument("--list-level0", action="store_true",
                        help="Level 0 として保護しているパスを表示して終了する")
    args = parser.parse_args(argv)

    if args.list_level0:
        print("Level 0（絶対変更禁止 / Docs/SPEC_ZN6.md §5.3）:")
        for pattern in LEVEL0_PATHS:
            print("  " + pattern)
        return 0

    if not args.files and not args.level0:
        parser.print_usage(sys.stderr)
        print("\nERROR: 検証するファイルを指定すること", file=sys.stderr)
        return 2

    ok = True

    for path in args.files:
        label = "stdin" if path == "-" else path
        print("スキーマ検証: {}".format(label))
        report = Report()
        validate_schema(load(path), label, report)
        ok &= report.print(label)
        print()

    if args.level0:
        old_path, new_path = args.level0
        print("Level 0 変更検出: {} → {}".format(old_path, new_path))
        report = Report()
        compare_level0(load(old_path), load(new_path), report)
        ok &= report.print("Level 0")
        print()

    if not ok:
        print("検証に失敗した。", file=sys.stderr)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
