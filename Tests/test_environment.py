# -*- coding: utf-8 -*-
"""コースごとの環境の検査.

**「全部のコースの見た目が同じすぎて終わってます」という指摘への答え。**

原因は `Blender/build_track.py` が、樹木の間隔も置く物の一覧も起伏の
大きさも**全コース共通の定数**として持っていたことだった。線形
（コーナーの並び）だけが違う4本になっていた。

見るのは好みではなく、次のように数値で決まること:

1. 全コースに環境が定義されている（既定で誤魔化さない）
2. **コースごとに中身が違う。** 同じなら分けた意味がない
3. 使うアセットが実在する（存在しないものを指していない）
4. 峠は林、都市高速は街、という**性格が数値に出ている**
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from environment import (ENVIRONMENTS, all_prop_kinds, all_species,
                         environment_for)
from track_catalogue import CATALOGUE

ALL_KEYS = sorted(CATALOGUE)

REPO_ROOT = Path(__file__).resolve().parent.parent


def _available_assets():
    """`Tracks/Assets/*/manifest.json` に載っているものの名前。"""
    names = set()
    for manifest in (REPO_ROOT / "Tracks" / "Assets").glob("*/manifest.json"):
        with manifest.open(encoding="utf-8") as handle:
            data = json.load(handle)
        entries = data.get("assets", data)
        if isinstance(entries, dict):
            names.update(entries.keys())
        else:
            for entry in entries:
                if isinstance(entry, dict) and entry.get("slug"):
                    names.add(entry["slug"])
    return names


def test_全コースに環境が定義されている():
    for key in ALL_KEYS:
        environment_for(key)          # 例外にならないこと


def test_知らないコースは既定で誤魔化さない():
    with pytest.raises(KeyError):
        environment_for("nurburgring")


@pytest.mark.parametrize("key", ALL_KEYS)
def test_使うアセットが実在する(key):
    """**存在しないアセットを指していないこと。**

    指していると、UE 側で「置けなかった」が静かに増える。
    """
    available = _available_assets()
    if not available:
        pytest.skip("アセットの manifest が無い")

    env = environment_for(key)
    for name in all_species(env) + all_prop_kinds(env):
        assert name in available, (
            "{}: {} が manifest に無い（Tracks/Assets/*/manifest.json）"
            .format(key, name))


def test_コースごとに置く物が違う():
    """**同じ一覧を使い回していないこと。**

    ここが同じだったせいで、峠にも都市高速にも同じコンクリート
    バリアが同じ距離に並んでいた。
    """
    plans = {key: tuple(sorted(all_prop_kinds(environment_for(key))))
             for key in ALL_KEYS}
    unique = set(plans.values())
    assert len(unique) == len(plans), (
        "置く物の一覧が重複しているコースがある: {}".format(plans))


def test_峠は林になっている():
    """指摘: 「草とか木を密集させてください」「全部同じ木だと面白くない」"""
    env = environment_for("mountain_pass")

    assert len(env.tree_layers) >= 3, (
        "峠の樹木が {} 層しかない（高木・広葉樹・下草・枯れ木を分ける）"
        .format(len(env.tree_layers)))
    assert len(all_species(env)) >= 4, (
        "峠の樹種が {} 種しかない".format(len(all_species(env))))

    densest = min(layer.spacing_m for layer in env.tree_layers)
    assert densest <= 2.0, (
        "いちばん密な層でも {:.1f} m 間隔（密集して見えない）".format(densest))

    # コース際まで木がある（サーキットのように見通しを取らない）
    nearest = min(layer.offset_m[0] for layer in env.tree_layers)
    assert nearest <= 12.0, (
        "いちばん近い木でも中心線から {:.1f} m（道の両脇が開けすぎ）"
        .format(nearest))


def test_峠には遠景の山がある():
    """指摘: 「山っぽく見せるには遠くの景色を表現することが大事」

    以前の起伏は**振幅 7 m・到達 420 m**だった。7 m は木より低い。
    つまり遠景が存在していなかった。
    """
    distant = environment_for("mountain_pass").distant
    assert distant is not None, "峠に遠景が無い"
    assert distant.amplitude_m >= 100.0, (
        "遠景の起伏が {:.0f} m しかない（丘であって山ではない）"
        .format(distant.amplitude_m))
    assert distant.reach_m >= 2000.0, (
        "遠景が {:.0f} m 先までしか無い".format(distant.reach_m))
    assert distant.ridges >= 2, "尾根が1重だと山並みに見えない"


def test_都市高速は高架として作られる():
    """指摘: 「高速コースは日本の高速道路みたいに、高さがある状態に」

    桁（路面）を持ち上げただけでは、宙に浮いた帯にしか見えない。
    下に橋脚が立ち、上に壁があって初めて高架に見える。
    """
    env = environment_for("high_speed_ring")
    assert env.viaduct_piers, "橋脚が無い"
    assert env.noise_wall, "遮音壁が無い"

    # 街なので地形の起伏はほぼ無い
    assert env.relief_amplitude_m < 5.0, (
        "都市高速の地形が {:.1f} m も起伏している".format(env.relief_amplitude_m))


def test_峠にガードレールがある():
    assert environment_for("mountain_pass").guardrail
    # サーキットには無い（あるのはグラベルとバリア）
    assert not environment_for("technical_circuit").guardrail


def test_樹木の密度がコースで違う():
    """峠とサーキットで、木の密度がはっきり違うこと。"""

    def densest(key):
        layers = environment_for(key).tree_layers
        return min(layer.spacing_m for layer in layers) if layers else 1e9

    assert densest("mountain_pass") * 3.0 < densest("technical_circuit"), (
        "峠 {:.1f} m とサーキット {:.1f} m の木の間隔が似すぎている"
        .format(densest("mountain_pass"), densest("technical_circuit")))


def test_物理の基準コースは変えない():
    """**回帰値を取る場所なので、性格を作り込まない。**

    ここに遠景や起伏を足すと、見た目は良くなるがラップタイムの
    比較対象が変わる。
    """
    env = environment_for("physics_test_track")
    assert env.distant is None
    assert not env.guardrail
    assert not env.viaduct_piers
