# -*- coding: utf-8 -*-
"""コースごとの環境。**「4コースとも同じ見た目」の原因はここが無かったこと。**

`Blender/build_track.py` は長らく

  - 樹木の種類と間隔（`TREE_SPACING_M = 4.0`）
  - 置く物の一覧（`PROP_PLAN`）
  - 起伏の大きさ（`RELIEF_AMPLITUDE_M = 7.0`）

を**全コース共通の定数**として持っていた。だから峠も都市高速も
サーキットも、同じ木が同じ間隔で並び、同じコンクリートバリアが
同じ距離に並んだ。線形（コーナーの並び）だけが違う4本になっていた。

**性格はここで分ける。**

---

## 数値の性格

ここにある数値は、ほぼ全部が**演出値**である（憲法ルール18）。
「峠の木は 1.2 m 間隔」という実測があるわけではない。

例外は、**現実の寸法として意味があるもの**で、その場合は根拠の型を
コメントに書く。書けないものは書けないと明記する。

---

## 遠景をなぜ別に作るか

「山っぽく見せるには遠くの景色が大事」というのはそのとおりで、
これまでの起伏は**振幅 7 m・到達 420 m** だった。7 m は木より低い。
つまり遠景が存在していなかった。

かといって近くの地面（4 m 格子）をそのまま 2.5 km 先まで伸ばすと
セルが 200 万個を超えて現実的でない。**遠景は別メッシュ・粗い格子**
で作る。物理の高さ場は近くのぶんだけで足りる（車はそこまで行けない）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple


@dataclass
class TreeLayer:
    """樹木の1層。**層を重ねて林を作る。**

    1種類を1つの間隔で撒くと、どれだけ本数を増やしても「同じ木の
    並木」にしかならない。高木・低木・枯れ木を別の層として重ねると
    密度と背丈がばらけて林に見える。
    """

    species: List[str]

    #: 中心線に沿って何 m ごとに1本置くか（左右それぞれ）。
    spacing_m: float

    #: 中心線からの距離の範囲 [m]。
    offset_m: Tuple[float, float]

    #: 拡大率の範囲。
    #:
    #: **PolyHaven の樹木は sapling（若木）で実寸 1〜3 m しかない。**
    #: 等倍だと下草にしかならないので拡大している。実寸から離れるが、
    #: これは景観であって計測対象ではない。**大きい木の実物が手に入ったら
    #: 拡大をやめること。**
    scale: Tuple[float, float] = (1.9, 3.4)


@dataclass
class DistantTerrain:
    """遠景の地形。**物理の高さ場には入らない**（車が行けないため）。"""

    #: 起伏の高さ [m]。**山に見せたいなら 100 m 級が要る。**
    amplitude_m: float

    #: 横方向の波長 [m]。長いほど尾根が大きくなだらかになる。
    wavelength_m: float

    #: どこまで作るか [m]（コースの外接矩形から）。
    reach_m: float

    #: 格子の大きさ [m]。**近くの地面（4 m）より粗くする。**
    cell_m: float = 40.0

    #: 立ち上がりきるまでの距離 [m]（近景の端から）。
    blend_m: float = 250.0

    #: 山を何重に見せるか。**空気遠近の代わり。**
    #:
    #: 遠くの尾根ほど高く、波長も長くする。1 枚の雑音だけだと
    #: 「でこぼこした平原」に見えて、山並みにならない。
    ridges: int = 3


@dataclass
class Environment:
    """1コースぶんの環境。"""

    #: 近景の起伏 [m]。走行域の外にだけ乗る。
    relief_amplitude_m: float = 7.0
    relief_wavelength_m: float = 140.0

    #: 遠景。`None` なら作らない。
    distant: Optional[DistantTerrain] = None

    #: 樹木の層。
    tree_layers: List[TreeLayer] = field(default_factory=list)

    #: 置く物。`(種類, 中心線からの距離 m, 間隔 m, 拡大率, 置き方)`。
    #: 置き方は `Blender/build_track.py` の `plan_props()` が解釈する。
    props: List[tuple] = field(default_factory=list)

    #: ガードレールを敷くか。**峠と高架には要る。**
    guardrail: bool = False

    #: 高架の橋脚を立てるか。
    viaduct_piers: bool = False

    #: 遮音壁を立てるか（高架）。
    noise_wall: bool = False


# ---------------------------------------------------------------------------
# 置く物の共通部品
# ---------------------------------------------------------------------------
#
# 表の読み方: (種類, 中心線からの距離 [m], 進行方向の間隔 [m],
#              拡大率の範囲, 置き方)
#
# 置き方:
#   "outside"     コースの外側
#   "both"        左右
#   "left"/"right" 片側
#   "tyre_wall"   曲率のあるところの外側だけ（タイヤバリア）
#   "corner_exit" コーナーの立ち上がり（パイロン）

_CONES = ("traffic_cone", 1.8, 7.0, (1.0, 1.0), "corner_exit")


ENVIRONMENTS: Dict[str, Environment] = {
    # -----------------------------------------------------------------
    # 物理の基準コース。**平坦なテストコース。**
    #
    # ここは「見た目を作り込む場所」ではない。0-100 km/h やラップの
    # 回帰値を取る場所なので、縦断も平坦のまま。周りも簡素にする。
    # -----------------------------------------------------------------
    "physics_test_track": Environment(
        relief_amplitude_m=7.0,
        relief_wavelength_m=140.0,
        distant=None,
        tree_layers=[
            TreeLayer(
                species=["pine_sapling_small", "fir_sapling", "searsia_lucida",
                         "othonna_cerarioides", "tree_stump_01"],
                spacing_m=4.0,
                offset_m=(18.0, 70.0),
            ),
        ],
        props=[
            ("concrete_road_barrier", 11.0, 4.2, (1.0, 1.0), "outside"),
            ("concrete_road_barrier_02", 11.0, 4.2, (1.0, 1.0), "outside"),
            ("old_tyre", 9.5, 1.1, (1.0, 1.0), "tyre_wall"),
            ("modular_chainlink_fence", 26.0, 4.0, (1.0, 1.0), "both"),
            ("street_lamp_01", 20.0, 46.0, (1.0, 1.0), "left"),
            ("Barrel_01", 16.0, 75.0, (1.0, 1.0), "both"),
            ("plastic_crate_02", 15.0, 110.0, (1.0, 1.0), "left"),
            _CONES,
        ],
    ),

    # -----------------------------------------------------------------
    # 常設サーキット。
    #
    # **ピットもスタンドもまだ無い。** 箱物のアセットが要る
    # （`Docs/PHASE15_DATA_LICENCE.md` §6 に何が足りないかを書く）。
    # 今あるもので出来るのは、タイヤバリア・フェンス・広い緑地まで。
    # -----------------------------------------------------------------
    "technical_circuit": Environment(
        relief_amplitude_m=9.0,
        relief_wavelength_m=180.0,
        distant=DistantTerrain(
            amplitude_m=45.0, wavelength_m=900.0, reach_m=1400.0, ridges=2),
        tree_layers=[
            # **コース際には木を置かない。** サーキットは見通しを取る。
            TreeLayer(
                species=["searsia_lucida", "othonna_cerarioides"],
                spacing_m=7.0,
                offset_m=(45.0, 110.0),
                scale=(1.6, 2.6),
            ),
        ],
        props=[
            ("concrete_road_barrier", 12.5, 4.2, (1.0, 1.0), "outside"),
            ("concrete_road_barrier_02", 12.5, 4.2, (1.0, 1.0), "outside"),
            ("old_tyre", 10.5, 1.1, (1.0, 1.0), "tyre_wall"),
            ("modular_chainlink_fence", 17.0, 4.0, (1.0, 1.0), "both"),
            ("Barrel_01", 20.0, 60.0, (1.0, 1.0), "both"),
            ("plastic_crate_02", 24.0, 90.0, (1.0, 1.0), "left"),
            _CONES,
        ],
    ),

    # -----------------------------------------------------------------
    # 都市高速（高架）。
    #
    # **桁の上と下でまったく別の世界になる。**
    #   桁の上: 遮音壁・照明・標識だけ。木は生えない
    #   桁の下: 街。ビル・電柱・フェンス
    # -----------------------------------------------------------------
    "high_speed_ring": Environment(
        # 街なので地形の起伏はほぼ無い。
        relief_amplitude_m=2.5,
        relief_wavelength_m=260.0,
        distant=DistantTerrain(
            amplitude_m=18.0, wavelength_m=1400.0, reach_m=2200.0, ridges=1),
        tree_layers=[
            # 街路樹。**桁の下**なので、地面の高さに乗る。
            TreeLayer(
                species=["searsia_lucida", "othonna_cerarioides"],
                spacing_m=9.0,
                offset_m=(34.0, 95.0),
                scale=(1.5, 2.4),
            ),
        ],
        props=[
            # 桁の上（路肩）。**地覆の外に立てる。**
            ("concrete_road_barrier", 8.2, 4.2, (1.0, 1.0), "outside"),
            ("street_lamp_01", 8.6, 38.0, (1.0, 1.0), "left"),
            # 桁の下の街。距離を取る（橋脚に当たらない位置）。
            ("modular_urban_apartments_facade", 62.0, 74.0, (1.0, 1.0), "both"),
            ("modular_electricity_poles", 30.0, 55.0, (1.0, 1.0), "right"),
            ("modular_chainlink_fence", 24.0, 4.0, (1.0, 1.0), "both"),
            ("rollershutter_door", 40.0, 150.0, (1.0, 1.0), "left"),
            ("Barrel_01", 27.0, 95.0, (1.0, 1.0), "both"),
        ],
        guardrail=False,          # 遮音壁が兼ねる
        viaduct_piers=True,
        noise_wall=True,
    ),

    # -----------------------------------------------------------------
    # 峠。**ここがいちばん変わる。**
    #
    # 指摘: 「アップダウン少ない」「草とか木を密集させて」
    #       「全部同じ木だと面白くない」「遠くの景色が大事」
    #
    # 縦断は `Tracks/elevation.py`（高低差 35 m、勾配 10% 級）。
    # ここでやるのは、**林の密度・種類のばらつき・遠景の山**である。
    # -----------------------------------------------------------------
    "mountain_pass": Environment(
        # 近景の起伏も大きくする。**道の両脇が斜面**であってほしい。
        relief_amplitude_m=26.0,
        relief_wavelength_m=110.0,
        distant=DistantTerrain(
            # **山並み。** 100 m 級を 3 重に重ねる。
            amplitude_m=160.0, wavelength_m=760.0, reach_m=2600.0,
            cell_m=36.0, ridges=3),
        tree_layers=[
            # 高木（針葉樹）。**いちばん密に、いちばん大きく。**
            TreeLayer(
                species=["fir_sapling", "pine_sapling_small"],
                spacing_m=1.6,
                offset_m=(13.0, 95.0),
                scale=(2.6, 4.6),
            ),
            # 広葉樹。混交林にする。
            TreeLayer(
                species=["searsia_lucida"],
                spacing_m=2.6,
                offset_m=(12.0, 85.0),
                scale=(2.2, 3.6),
            ),
            # 下草・低木。**林床が見えるかどうかで密度の印象が変わる。**
            TreeLayer(
                species=["othonna_cerarioides"],
                spacing_m=1.9,
                offset_m=(10.5, 60.0),
                scale=(1.1, 2.0),
            ),
            # 立ち枯れ・切株。**同じ木ばかりにしない。**
            TreeLayer(
                species=["tree_stump_01"],
                spacing_m=11.0,
                offset_m=(11.0, 70.0),
                scale=(0.9, 1.7),
            ),
        ],
        props=[
            ("boulder_01", 13.5, 17.0, (0.6, 1.6), "both"),
            ("rock_07", 12.0, 11.0, (0.5, 1.4), "both"),
            _CONES,
        ],
        guardrail=True,
    ),
}


def environment_for(key: str) -> Environment:
    """そのコースの環境。**知らないコースは既定で誤魔化さない。**"""
    if key not in ENVIRONMENTS:
        raise KeyError(
            "環境が定義されていないコース: {}（ある: {}）"
            .format(key, ", ".join(sorted(ENVIRONMENTS))))
    return ENVIRONMENTS[key]


def all_species(env: Environment) -> List[str]:
    """その環境で使う樹木の種類（重複なし、出てくる順）。"""
    seen: List[str] = []
    for layer in env.tree_layers:
        for name in layer.species:
            if name not in seen:
                seen.append(name)
    return seen


def all_prop_kinds(env: Environment) -> List[str]:
    seen: List[str] = []
    for entry in env.props:
        if entry[0] not in seen:
            seen.append(entry[0])
    return seen
