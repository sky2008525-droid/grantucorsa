"""縁石（カーブ / kerb）を中心線のどこに敷くかを決める。

**形状の定義は `Tracks/track_catalogue.py` にしかない**（`Tools/export_track.py`
の注記）。ここが決めるのは「中心線のどの区間に縁石を敷くか」だけで、

  - メッシュを作るのは `Blender/build_track.py`
  - 赤白の縞を描くのは `Tracks/road_texture.py`

の 2 つが利用者になる。**同じ定数を 2 箇所に書かないためにここへ集めた。**
`ROAD_MARKING_REPEAT_M` と `TILE_LENGTH_M` を人手で一致させていた既存の
やり方は、片方だけ直すと破線の間隔が黙って変わる（`build_track.py` の注記）。

## 縁石は路面の一部であって「飾り」ではない

コース周りの物（`PROP_PLAN`）は CC0 のアセットを外から持ってくる方針だが、
縁石は**中心線と路面幅から一意に決まる形**なので手続き生成する。
汎用の縁石モデルを買ってきても、幅 9〜14 m のコーナーに合わせて曲げ直す
作業が要るだけで、外部アセットにする利点が無い。
"""

from __future__ import annotations

from typing import Iterable, List, Sequence, Set

#: 縁石を敷く曲率のしきい値 [1/m]。**R = 180 m 以下の区間にだけ敷く。**
#:
#: 実在のサーキットは、アペックスと立ち上がりで縁石を使う区間にだけ縁石を
#: 持つ。**直線に敷くと赤白の帯が延々と続く絵になり、コーナーがどこか
#: 分からなくなる。**
#:
#: 180 m を採った理由は幾何であって物理ではない。`Tracks/track_catalogue.py`
#: の 4 コースが実際に使っている円弧の半径は **20〜150 m**、直線の曲率は
#: **ちょうど 0**。180 m はその隙間にあり、**設計上のコーナーを全て拾い、
#: 直線を 1 点も拾わない。** 隙間の中ならどの値でも結果は同じなので、
#: 最大半径 150 m の 1.2 倍に置いて余裕を取ってある。
#:
#: **「ZN6 の横G の限界から導いた」ではない。** そう書けそうだが、
#: そのためには各コーナーの通過速度が要り、それはコースではなく走り方の
#: 話になる。根拠を実際より強く見せない（憲法ルール2）。
#:
#: `PROP_PLAN` の "outside"（1/200）や `TYRE_WALL_CURVATURE`（1/60）とは
#: 別の値にしてある。バリアは「飛び出しうる所」、タイヤバリアは「速度が
#: 乗ったまま外へ出る所」、縁石は「アペックスを使う所」で、判定したい対象
#: が違う。同じ定数に寄せない。
KERB_CURVATURE_1PM = 1.0 / 180.0

#: 縁石をコーナーの前後へ何 m 伸ばすか。
#:
#: 実際の縁石は、旋回が始まる少し手前から敷かれて、立ち上がりの先で終わる。
#: 円弧の区間ちょうどで切ると、**縁石が宙で始まって宙で終わる**ように見える。
KERB_LEAD_M = 4.0

#: 縁石の幅 [m]。路面端（中心線から width/2）から外側へこの幅を取る。
#: 実際のサーキットの縁石は 0.5〜1.5 m。狭いと車載視点でほとんど見えない。
KERB_WIDTH_M = 1.00

#: 縁石の高さ [m]。**路面の上面（z = 0）より上に出る量。**
#:
#: 実際のサーキット用縁石（いわゆるソーセージでない通常のもの）は
#: 5〜7 cm。**高くしないこと。** 物理は平面3自由度で当たり判定を持たず、
#: 車は z = 0 を走り続けるので、高い縁石を置いても車はすり抜ける。
#: 見た目に厚みがあれば足りる（`ROAD_THICKNESS_M` と同じ考え方）。
KERB_HEIGHT_M = 0.055

#: 路面側の立ち上がり（面取り）の幅 [m]。
#: 垂直に立てると、路面と縁石の間に黒い筋が出る（法線が真横を向くため）。
KERB_RISE_M = 0.14

#: 縁石テクスチャ 1 枚が進行方向に何メートル分か。
#: **`Tracks/road_texture.py` と `Blender/build_track.py` の両方がこれを読む。**
KERB_TILE_LENGTH_M = 4.0

#: 1 枚のタイルに入る縞の数。赤白が交互なので偶数にする。
#: 4 なら 1 ブロック 1.0 m で、実際のサーキットの縞（0.5〜1 m）に近い。
KERB_STRIPES_PER_TILE = 4

#: コーナーのうち「立ち上がり」と見なす後半の割合。
#: パイロンはここへ置く（`Blender/build_track.py` の PROP_PLAN）。
#: 進入側に置くと、ブレーキングの目標物のように見えてしまう。
CONE_EXIT_FRACTION = 0.45


def _dilate(flags: Sequence[bool], radius: int) -> List[bool]:
    """True の区間を前後 `radius` 点ずつ広げる。**周回なので端をまたぐ。**"""
    count = len(flags)
    if radius <= 0:
        return list(flags)
    out = [False] * count
    for index, on in enumerate(flags):
        if not on:
            continue
        for offset in range(-radius, radius + 1):
            out[(index + offset) % count] = True
    return out


def _contiguous_runs(flags: Sequence[bool]) -> List[List[int]]:
    """True が続く区間を添字の並びで返す。

    **周回として扱う。** 添字 0 の直前（末尾）から続いているコーナーは
    1 本の区間にする。ここを分けると、スタートラインの上で縁石が
    途切れて見える。
    """
    count = len(flags)
    if count == 0 or not any(flags):
        return []
    if all(flags):
        return [list(range(count))]

    # **直線から始めて一周する。** True から始めると、末尾をまたぐ区間が
    # 2 本に割れる。
    start = list(flags).index(False)
    runs: List[List[int]] = []
    current: List[int] = []
    for offset in range(count):
        index = (start + offset) % count
        if flags[index]:
            current.append(index)
        elif current:
            runs.append(current)
            current = []
    if current:
        runs.append(current)
    return runs


def corner_flags(curvatures: Iterable[float],
                 threshold: float = KERB_CURVATURE_1PM) -> List[bool]:
    """各点が「コーナー」かどうか。"""
    return [abs(value) >= threshold for value in curvatures]


def kerb_spans(curvatures: Sequence[float], spacing_m: float) -> List[List[int]]:
    """縁石を敷く区間を、中心線の添字の並びとして返す。

    コーナーの前後へ `KERB_LEAD_M` だけ伸ばしてから区間をまとめる。
    **伸ばした結果くっついた 2 つのコーナーは 1 本の縁石になる**
    （切り返しの間が短い区間で、実際にも縁石は繋がっている）。
    """
    if spacing_m <= 0.0:
        raise ValueError("点間隔が 0 以下: {}".format(spacing_m))
    lead = int(round(KERB_LEAD_M / spacing_m))
    return _contiguous_runs(_dilate(corner_flags(curvatures), lead))


def corner_exit_indices(curvatures: Sequence[float],
                        fraction: float = CONE_EXIT_FRACTION) -> Set[int]:
    """各コーナーの「立ち上がり」に当たる点の添字。

    **伸ばす前のコーナーを使う。** 縁石は前後へ伸ばすが、立ち上がりは
    旋回が続いている区間の後半でなければ意味がない。
    """
    if not 0.0 < fraction <= 1.0:
        raise ValueError("割合が範囲外: {}".format(fraction))
    out: Set[int] = set()
    for run in _contiguous_runs(corner_flags(curvatures)):
        take = max(int(len(run) * fraction), 1)
        out.update(run[-take:])
    return out
