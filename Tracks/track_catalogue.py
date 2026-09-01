"""走れるコースの一覧。

`Tracks/physics_test_track.py` の `_Builder`（直線と円弧）で組む。
形状の定義はここにしかない。**描画側が独自に持たないこと**
（`Tools/export_track.py` の注記）。

## 閉合をどう解くか

周回にするには、最後に始点へ戻り、向きも一周していなければならない。
`physics_test_track()` はこれを**手で解いていた**（総旋回角 360 度、
y 方向の釣り合いから R=55 を逆算、など）。レイアウトを増やすたびに
その計算をやり直すのは現実的でない。

**直線の長さについて、閉合の式は線形である。**
各直線は「その時点の向き × 長さ」だけ位置を動かす。向きは円弧だけで
決まるので、直線の長さを変えても向きは変わらない。したがって

    Σ(固定ぶんの変位) + L1*d1 + L2*d2 = 0

という 2 元 1 次方程式になり、**厳密に解ける。**

ここでは式を導かず、**2 回試しに組んで差分から d1・d2 を求める**。
円弧の変位を手で書き下ろすと符号を間違えやすく、間違えても
「なんとなく閉じていない」形になるだけで原因が見えない。

## 実在コースを模さない

首都高やつくばサーキットのレイアウトを再現しない。理由は
`Docs/PHASE15_DATA_LICENCE.md`。ここにあるのは**架空のコース**で、
実在コースの名前も名乗らない。
"""

from __future__ import annotations

import math
from typing import Callable, Dict, List, Tuple

from physics_test_track import Track, _Builder, closure_error, physics_test_track

#: 直線 / 円弧の指定。
#:
#:   ("straight", 長さ [m], ラベル)
#:   ("arc", 半径 [m], 角度 [deg], ラベル)   角度が正で左旋回
#:   ("free", 仮の長さ, ラベル)              **閉合のために長さを解く直線**
Segment = tuple


#: 自由直線の下限 [m]。これより短いとコーナーが繋がって見える。
MIN_FREE_STRAIGHT_M = 45.0

#: 固定直線の伸縮の上限。これを超えるなら設計を直すべき。
MAX_FIXED_SCALE = 12.0


class ClosureError(Exception):
    """閉合を解けなかった。**黙って開いたコースを返さない**（憲法ルール6）。"""


def _build(segments: List[Segment], free_lengths: List[float],
           spacing_m: float, name: str, width_m: float,
           fixed_scale: float = 1.0) -> Track:
    builder = _Builder(spacing_m)
    free_index = 0
    for segment in segments:
        kind = segment[0]
        if kind == "straight":
            builder.straight(segment[1] * fixed_scale, segment[2])
        elif kind == "arc":
            builder.arc(segment[1], segment[2], segment[3])
        elif kind == "free":
            length = free_lengths[free_index]
            free_index += 1
            if length <= 0.0:
                raise ClosureError(
                    "{}: 閉合の解が負の直線長を要求した（{:.1f} m）。"
                    "円弧の配置を見直すこと".format(name, length))
            builder.straight(length, segment[2])
        else:
            raise ValueError("知らない区間: {}".format(kind))
    track = builder.build(name)
    track.width_m = width_m
    return track


def _total_turn_deg(segments: List[Segment]) -> float:
    return sum(s[2] for s in segments if s[0] == "arc")


def solve_closed_track(segments: List[Segment], name: str,
                       spacing_m: float = 1.0, width_m: float = 12.0) -> Track:
    """`free` の直線長を解いて、閉じた周回にする。

    **`free` はちょうど 2 本必要。** 位置の閉合は x と y の 2 条件なので、
    1 本では足りず、3 本では解が一意に決まらない。
    """
    free_count = sum(1 for s in segments if s[0] == "free")
    if free_count != 2:
        raise ClosureError(
            "{}: free の直線は 2 本必要（今 {} 本）".format(name, free_count))

    turn = _total_turn_deg(segments)
    if abs(abs(turn) - 360.0) > 1e-9:
        # **向きが一周していなければ、直線をどう伸ばしても閉じない。**
        raise ClosureError(
            "{}: 総旋回角が {:.3f} deg。360 でないと周回にならない".format(name, turn))

    # **試し打ちは大きく取る。**
    #
    # `_Builder.straight` は長さを刻み幅の整数倍に丸める
    # （steps = max(round(length/spacing), 1)）。1 m と 0 m はどちらも
    # 1 ステップになるので、**差が出ず det が厳密に 0 になる**
    # （実際にそうなって解けなかった）。
    probe_m = 100.0

    def residual(lengths: List[float], scale: float) -> Tuple[float, float]:
        safe = [max(value, spacing_m) for value in lengths]
        track = _build(segments, safe, spacing_m, name, width_m, scale)
        last = track.points[-1]
        # 最後の点の「次」が始点に来てほしい
        return (last.x_m + spacing_m * math.cos(last.heading_rad),
                last.y_m + spacing_m * math.sin(last.heading_rad))

    def solve_free(scale: float) -> Tuple[float, float]:
        base = residual([spacing_m, spacing_m], scale)
        with_first = residual([spacing_m + probe_m, spacing_m], scale)
        with_second = residual([spacing_m, spacing_m + probe_m], scale)

        # d1, d2 は「その直線を 1 m 伸ばしたときの終点の動き」
        d1 = ((with_first[0] - base[0]) / probe_m,
              (with_first[1] - base[1]) / probe_m)
        d2 = ((with_second[0] - base[0]) / probe_m,
              (with_second[1] - base[1]) / probe_m)

        det = d1[0] * d2[1] - d1[1] * d2[0]
        if abs(det) < 1e-9:
            # **2 本の直線が平行だと解けない。** 向きの違う場所を選ぶこと。
            raise ClosureError(
                "{}: free の 2 直線が平行で閉合を解けない（det={:.3e}）"
                .format(name, det))

        rx, ry = -base[0], -base[1]
        # base は「両方 spacing_m」で測ったので、そのぶんを足し戻す
        return (spacing_m + (rx * d2[1] - ry * d2[0]) / det,
                spacing_m + (d1[0] * ry - d1[1] * rx) / det)

    # **固定直線を一様に伸縮させて、自由直線が正になるようにする。**
    #
    # 円弧の並びだけで閉じる形が決まってしまうと、自由直線が負を要求する
    # ことがある（実際に -1812 m を要求して解けなかった）。固定直線を
    # まとめて伸ばせば、その負のぶんを吸収できる。
    #
    # 自由直線の長さは scale について1次なので、2 点測れば決まる。
    first_at_1, second_at_1 = solve_free(1.0)
    first_at_2, second_at_2 = solve_free(2.0)

    fixed_scale = 1.0
    smallest = min(first_at_1, second_at_1)
    if smallest < MIN_FREE_STRAIGHT_M:
        # 小さいほうを MIN_FREE_STRAIGHT_M に持ち上げる scale を求める
        if first_at_1 <= second_at_1:
            slope = first_at_2 - first_at_1
            intercept = first_at_1
        else:
            slope = second_at_2 - second_at_1
            intercept = second_at_1
        if abs(slope) < 1e-9:
            raise ClosureError(
                "{}: 固定直線を伸ばしても自由直線が {:.1f} m のまま。"
                "円弧の配置を見直すこと".format(name, intercept))
        fixed_scale = 1.0 + (MIN_FREE_STRAIGHT_M - intercept) / slope

    if fixed_scale <= 0.0 or fixed_scale > MAX_FIXED_SCALE:
        raise ClosureError(
            "{}: 閉じるのに固定直線を {:.2f} 倍する必要がある（上限 {:.1f}）。"
            "円弧の配置を見直すこと".format(name, fixed_scale, MAX_FIXED_SCALE))

    length_first, length_second = solve_free(fixed_scale)

    # **刻み幅の整数倍へ丸める。** 丸めるのは builder なので、こちらで
    # 先に丸めておかないと「解いた長さ」と「実際に組まれる長さ」がずれる。
    length_first = round(length_first / spacing_m) * spacing_m
    length_second = round(length_second / spacing_m) * spacing_m

    track = _build(segments, [length_first, length_second], spacing_m, name,
                   width_m, fixed_scale)

    # **解いたつもりで開いていないことがある。** 必ず数値で確かめる。
    #
    # 丸めのぶん、1 点間隔ぶんまではずれる。路面メッシュは最後の点を
    # 先頭へ繋ぐので、この程度なら 1 枚のポリゴンで埋まる
    # （既存の physics_test_track も 0.371 m ずれている）。
    # **円弧で終えないこと。**
    #
    # `_Builder.arc` は点を出してから向きを進めるので、最後の点の向きは
    # 1 ステップぶん足りない。円弧で終わると、その差がそのまま閉合の
    # 方位ずれになる（R=30 の 130 度なら 1.9 度）。各コースは短い直線で
    # 終えてある。
    position_error, heading_error = closure_error(track)
    if position_error > 1.6 * spacing_m or abs(heading_error) > math.radians(0.5):
        raise ClosureError(
            "{}: 閉合が甘い（位置 {:.3f} m / 方位 {:.4f} rad）"
            .format(name, position_error, heading_error))

    return track


# ---------------------------------------------------------------------------
# コース
#
# **性格を変える。** 同じような形を並べても走り分けにならない。


def technical_circuit(spacing_m: float = 1.0) -> Track:
    """低速テクニカル。**2速と3速で忙しく、ブレーキが効く。**

    小さい半径の連続。FR の弱アンダー〜パワーオーバーの境目を探る場所。
    直線が短いので最高速は伸びない。
    """
    segments = [
        ("free", 0.0, "main straight"),
        ("arc", 28.0, 100.0, "T1 left"),
        ("straight", 50.0, "short chute"),
        ("arc", 22.0, -80.0, "T2 right"),
        # **切り返しの間に直線を挟む。**
        # 逆向きの円弧を直接繋ぐと、曲率が 1 点で -1/22 から +1/22 へ
        # 一段で反転する。実車はハンドルを瞬間的に切り返せない。
        ("straight", 14.0, "T2-T3 flick"),
        ("arc", 22.0, 80.0, "T3 left"),
        ("straight", 45.0, "esses exit"),
        ("arc", 25.0, 130.0, "T4 hairpin left"),
        ("free", 0.0, "middle straight"),
        ("arc", 30.0, 130.0, "T5 left onto main"),
        ("straight", 30.0, "start line"),
    ]
    return solve_closed_track(segments, "Technical Circuit", spacing_m, width_m=11.0)


def high_speed_ring(spacing_m: float = 1.0) -> Track:
    """高速。**4〜5速中心。横Gの上限が効く。**

    大きい半径の連続コーナーと長い直線。ブレーキはほとんど要らない。
    """
    segments = [
        ("free", 0.0, "main straight"),
        ("arc", 150.0, 90.0, "T1 fast left"),
        ("straight", 300.0, "back straight"),
        ("arc", 130.0, 90.0, "T2 sweep left"),
        ("straight", 220.0, "link"),
        ("arc", 120.0, -40.0, "T3 right kink"),
        ("arc", 120.0, 40.0, "T4 left kink"),
        ("straight", 200.0, "run to T5"),
        ("arc", 140.0, 90.0, "T5 left"),
        # **free をここに置く。** 先頭の free と 90 度違う向きなので、
        # 2 本が平行にならず閉合を解ける（平行だと det=0 になる）。
        ("free", 0.0, "run to T6"),
        ("arc", 140.0, 90.0, "T6 left onto main"),
        ("straight", 40.0, "start line"),
    ]
    return solve_closed_track(segments, "High Speed Ring", spacing_m, width_m=14.0)


def mountain_pass(spacing_m: float = 1.0) -> Track:
    """峠。**幅が狭く、切り返しとヘアピンが続く。**

    上下方向の起伏は入れていない（走行面は平面。`Blender/build_track.py`
    の注記）。**「峠」は勾配ではなく、線形の性格を指している。**
    勾配を入れるには高さ場と走行面の両方を変える必要がある。
    """
    segments = [
        ("free", 0.0, "main straight"),
        ("arc", 30.0, 90.0, "1st corner left"),
        ("straight", 60.0, "chute"),
        ("arc", 20.0, -70.0, "tight right"),
        # 切り返しの間の直線（technical_circuit と同じ理由）
        ("straight", 12.0, "flick"),
        ("arc", 20.0, 70.0, "tight left"),
        ("straight", 50.0, "esses exit"),
        ("arc", 26.0, 150.0, "hairpin left"),
        ("free", 0.0, "long straight"),
        ("arc", 45.0, 120.0, "final left"),
        ("straight", 25.0, "start line"),
    ]
    return solve_closed_track(segments, "Mountain Pass", spacing_m, width_m=9.0)


#: 名前 -> 生成関数。**UI と書き出しはここを読む。**
CATALOGUE: Dict[str, Callable[[float], Track]] = {
    "physics_test_track": physics_test_track,
    "technical_circuit": technical_circuit,
    "high_speed_ring": high_speed_ring,
    "mountain_pass": mountain_pass,
}


def build(key: str, spacing_m: float = 1.0) -> Track:
    if key not in CATALOGUE:
        raise KeyError("知らないコース: {}（ある: {}）".format(
            key, ", ".join(sorted(CATALOGUE))))
    return CATALOGUE[key](spacing_m)


def summary(track: Track) -> str:
    """一行の要約。**設計どおりか目で確かめるため。**"""
    curvatures = [abs(p.curvature_1pm) for p in track.points if p.curvature_1pm != 0.0]
    tightest = (1.0 / max(curvatures)) if curvatures else float("inf")
    corner_fraction = len(curvatures) / len(track.points)
    position_error, heading_error = closure_error(track)
    return ("{:<22s} 全長 {:7.1f} m  幅 {:4.1f} m  最小R {:6.1f} m  "
            "コーナー率 {:4.0%}  閉合ずれ {:.3f} m / {:.4f} rad"
            .format(track.name, track.length_m, track.width_m, tightest,
                    corner_fraction, position_error, heading_error))
