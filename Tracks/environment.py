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

    #: 遠景の基準面を上下させる量 [m]。
    #:
    #: **負にすると、起伏の低いところが水面より下になる。**
    #: 湾岸の都市高速は、海と埋立地が入り混じった景色になる。
    base_offset_m: float = 0.0

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

    #: 海面の標高 [m]。`None` なら海を作らない。
    #:
    #: 指摘: 「首都高はオブジェクトめっちゃあると思います。（略）**海**
    #: めちゃめちゃありますよ」。湾岸の都市高速は、走っている間ずっと
    #: 水面が見えている。
    sea_level_m: Optional[float] = None

    #: ピットの建屋に使うアセット名。`None` ならピットを作らない。
    #:
    #: **レーンと壁は手続きで作り、建屋だけ外部アセットを置く。**
    #: レーンは道路（コースの形で決まる）、建屋は建築物（決まらない）
    #: という違いによる。
    pit_building: Optional[str] = None


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


# **実寸の大きい樹木が入った**（2026-09-02）。
#
# それまでは sapling（若木、実寸 1〜3 m）しか無く、3 倍前後に拡大して
# 大木のふりをしていた。拡大した若木は枝ぶりも葉の大きさも若木のままで、
# 「大きい下草」にしか見えない。実寸で 9〜20 m のものが入ったので、
# **拡大をやめて等倍前後で使う。**
#
#   fir_tree_01           14.5 m  モミ（針葉樹）
#   jacaranda_tree        19.5 m  広葉樹。**花が紫**なので日本の山には使わない
#   pine_sapling_medium   11.5 m  マツ
#   fir_sapling_medium     8.9 m  モミ（小）
#   island_tree_01         5.0 m  広葉樹（小）
#   tree_small_02          4.6 m  広葉樹（小）
#   island_tree_02         3.4 m  低木
#
# **日本の樹種そのものは手に入っていない。** 杉も檜も無い。
# 針葉樹として形の近いモミ・マツで代える（憲法ルール18: 演出）。

_CONIFERS = ["fir_tree_01", "pine_sapling_medium", "fir_sapling_medium"]
_BROADLEAF = ["island_tree_01", "tree_small_02", "searsia_burchellii"]
_UNDERGROWTH = ["fern_02", "grass_medium_01", "grass_medium_02",
                "nettle_plant", "periwinkle_plant", "weed_plant_02",
                "celandine_01", "shrub_01", "shrub_03", "shrub_04"]
_DEADWOOD = ["dead_quiver_trunk", "dead_tree_trunk_02", "dead_tree_trunk",
             "dry_branches_medium_01", "tree_stump_01", "tree_stump_02",
             "root_cluster_01", "pine_roots"]


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
                species=["pine_sapling_small", "fir_sapling",
                         "searsia_lucida", "othonna_cerarioides"],
                spacing_m=4.0,
                offset_m=(18.0, 70.0),
                scale=(1.9, 3.4),
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
    # **ピットもスタンドもまだ無い。** 箱物のアセットが足りない。
    # 今あるもので出来るのは、タイヤバリア・フェンス・広い緑地・
    # 資材置き場まで。**「ピットくらいはある」という指摘には
    # まだ答えられていない。**
    # -----------------------------------------------------------------
    "technical_circuit": Environment(
        relief_amplitude_m=9.0,
        relief_wavelength_m=180.0,
        distant=DistantTerrain(
            amplitude_m=45.0, wavelength_m=900.0, reach_m=1400.0, ridges=2),
        tree_layers=[
            # **コース際には木を置かない。** サーキットは見通しを取る。
            TreeLayer(
                species=["fir_sapling_medium", "island_tree_01",
                         "tree_small_02"],
                spacing_m=6.0,
                offset_m=(38.0, 120.0),
                scale=(0.9, 1.5),
            ),
            TreeLayer(
                species=["shrub_02", "grass_medium_01", "shrub_01"],
                spacing_m=5.0,
                offset_m=(26.0, 90.0),
                scale=(1.0, 2.0),
            ),
        ],
        props=[
            ("concrete_road_barrier", 12.5, 4.2, (1.0, 1.0), "outside"),
            ("concrete_road_barrier_02", 12.5, 4.2, (1.0, 1.0), "outside"),
            ("old_tyre", 10.5, 1.1, (1.0, 1.0), "tyre_wall"),
            ("modular_chainlink_fence", 17.0, 4.0, (1.0, 1.0), "both"),
            # パドックの資材置き場らしいもの。
            ("wooden_crate_01", 21.0, 70.0, (1.0, 1.4), "left"),
            ("old_military_crate", 22.0, 95.0, (1.0, 1.3), "right"),
            ("Barrel_01", 20.0, 60.0, (1.0, 1.0), "both"),
            ("Barrel_02", 20.5, 66.0, (1.0, 1.0), "both"),
            ("hand_truck", 23.0, 180.0, (1.0, 1.0), "left"),
            _CONES,
        ],
        # **ピット。** メインストレートの外側にレーンを引き、
        # その外に建屋を並べる。指摘「すくなくともピットくらいはある」。
        pit_building="modular_factory_facade",
    ),

    # -----------------------------------------------------------------
    # 都市高速（高架）。
    #
    # **桁の上と下でまったく別の世界になる。**
    #   桁の上: 遮音壁・照明だけ。木は生えない
    #   桁の下: 街。ビル・電柱・室外機・自販機の類
    #
    # **道路標識がまだ無い。** 案内標識（緑）が無いと都市高速に
    # 見えないので、アセットが入り次第ここへ足すこと。
    # -----------------------------------------------------------------
    "high_speed_ring": Environment(
        # 街なので地形の起伏はほぼ無い。
        relief_amplitude_m=2.5,
        relief_wavelength_m=260.0,
        distant=DistantTerrain(
            # **基準面を海面より下げる。** そうしないと陸しか出来ず、
            # 水面が地面に隠れて 1 ピクセルも見えない。
            amplitude_m=26.0, wavelength_m=1250.0, reach_m=2400.0, ridges=2,
            base_offset_m=-11.0),
        tree_layers=[
            # 街路樹。**桁の下**なので、地面の高さに乗る。
            TreeLayer(
                species=["island_tree_01", "tree_small_02", "island_tree_02"],
                spacing_m=8.0,
                offset_m=(26.0, 80.0),
                scale=(0.9, 1.4),
            ),
        ],
        props=[
            # --- 桁の上（路肩）---
            ("concrete_road_barrier", 8.2, 4.2, (1.0, 1.0), "outside"),
            ("street_lamp_01", 8.6, 38.0, (1.0, 1.0), "left"),

            # --- 桁の下の街 ---
            # **ビルを何種類も混ぜる。** 1 種類だと同じ建物が等間隔に
            # 並ぶだけで、街に見えない。
            ("modular_urban_apartments_facade", 58.0, 78.0, (1.0, 1.0), "both"),
            ("modular_factory_facade", 74.0, 132.0, (1.0, 1.0), "left"),
            ("modular_fire_escape", 46.0, 96.0, (1.0, 1.0), "right"),
            ("rollershutter_door", 34.0, 110.0, (1.0, 1.0), "left"),
            ("rollershutter_window_01", 36.0, 130.0, (1.0, 1.0), "right"),
            ("large_iron_gate", 40.0, 210.0, (1.0, 1.0), "left"),
            ("modular_electricity_poles", 30.0, 55.0, (1.0, 1.0), "right"),
            ("modular_electric_cables", 31.0, 62.0, (1.0, 1.0), "left"),
            ("modular_chainlink_fence", 24.0, 4.0, (1.0, 1.0), "both"),
            # 街の小物。**細かいものが在るかどうかで「街」に見えるかが決まる。**
            ("street_lamp_02", 22.0, 44.0, (1.0, 1.0), "right"),
            ("power_box_01", 25.0, 88.0, (1.0, 1.0), "both"),
            ("utility_box_01", 26.0, 104.0, (1.0, 1.0), "left"),
            ("utility_box_02", 27.0, 118.0, (1.0, 1.0), "right"),
            ("exterior_aircon_unit", 44.0, 86.0, (1.0, 1.0), "both"),
            ("modular_airduct_rectangular_01", 50.0, 140.0, (1.0, 1.0), "left"),
            ("modular_pipes", 48.0, 160.0, (1.0, 1.0), "right"),
            ("fire_hydrant", 23.0, 92.0, (1.0, 1.0), "both"),
            ("metal_trash_can", 24.5, 74.0, (1.0, 1.0), "left"),
            ("modular_street_seating", 25.5, 150.0, (1.0, 1.0), "right"),
            ("covered_car", 33.0, 128.0, (1.0, 1.0), "both"),
            ("water_manhole_cover", 21.0, 58.0, (1.0, 1.0), "both"),
            ("plastic_container", 28.0, 112.0, (1.0, 1.0), "left"),
        ],
        guardrail=False,          # 遮音壁が兼ねる
        viaduct_piers=True,
        noise_wall=True,
        # **海。** 桁の高さ 11〜17 m から見下ろす位置に水面を置く。
        sea_level_m=-3.0,
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
            # 高木（針葉樹）。**いちばん密に。実寸 9〜14 m なので拡大しない。**
            TreeLayer(
                species=_CONIFERS,
                spacing_m=2.2,
                offset_m=(12.0, 100.0),
                scale=(0.85, 1.35),
            ),
            # 広葉樹。混交林にする。
            TreeLayer(
                species=_BROADLEAF,
                spacing_m=3.2,
                offset_m=(11.0, 90.0),
                scale=(0.9, 1.6),
            ),
            # 下草・シダ。**林床が埋まっているかで密度の印象が決まる。**
            TreeLayer(
                species=_UNDERGROWTH,
                spacing_m=1.1,
                offset_m=(9.5, 55.0),
                scale=(1.0, 2.4),
            ),
            # 立ち枯れ・倒木・切株。**同じ木ばかりにしない。**
            TreeLayer(
                species=_DEADWOOD,
                spacing_m=7.0,
                offset_m=(10.5, 70.0),
                scale=(0.9, 1.8),
            ),
        ],
        props=[
            # 法面と岩。**山肌を作る。**
            ("mountainside", 46.0, 58.0, (0.8, 1.8), "both"),
            ("namaqualand_cliff_01", 30.0, 44.0, (0.9, 1.7), "both"),
            ("namaqualand_cliff_02", 34.0, 52.0, (0.8, 1.5), "both"),
            ("rock_face_01", 16.0, 26.0, (0.8, 1.6), "both"),
            ("rock_face_02", 15.0, 31.0, (0.8, 1.6), "both"),
            ("boulder_01", 13.5, 17.0, (0.6, 1.6), "both"),
            ("rock_07", 12.0, 11.0, (0.5, 1.4), "both"),
            ("rock_09", 12.5, 13.0, (0.5, 1.4), "both"),
            ("namaqualand_boulder_03", 11.5, 9.0, (0.7, 1.5), "both"),
            ("namaqualand_boulder_04", 11.8, 12.0, (0.7, 1.4), "both"),
            ("rock_moss_set_01", 11.0, 8.0, (0.8, 1.5), "both"),
            ("rock_moss_set_02", 11.2, 9.5, (0.8, 1.5), "both"),
            ("stone_01", 10.8, 6.5, (0.8, 1.6), "both"),
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
