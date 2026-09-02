# -*- coding: utf-8 -*-
"""縦断（標高）の検査.

**「峠が峠に見えない」という指摘への答えがここにある。**

見るのは好みではなく、次のどれも数値で決まること:

1. **周回で閉じているか。** 1周して同じ場所に戻るのだから、高さも
   戻らなければならない。戻らなければスタートラインに段差ができる
2. **勾配がその種類の道として現実的か。** 峠の 18% は林道でも急な
   部類で、この車では上れない
3. **勾配の変化が急すぎないか。** 縦断曲線が短いと凸部で車が飛ぶ
4. **コースごとに性格が違うか。** 全部同じ起伏なら、入れた意味がない
"""

from __future__ import annotations

import math

import pytest

from elevation import PROFILES, ElevationProfile, profile_for
from track_catalogue import CATALOGUE, ElevationError, apply_elevation, build

ALL_KEYS = sorted(CATALOGUE)


def test_全コースに縦断が定義されている():
    """**知らないコースは平坦で誤魔化さない。**

    平坦を返すと、コースを足したときに「なぜか平ら」という形で
    黙って抜ける。
    """
    for key in ALL_KEYS:
        profile_for(key)          # 例外にならないこと


@pytest.mark.parametrize("key", ALL_KEYS)
def test_縦断が条件を満たす(key):
    """`validate` が挙げる問題が 1 つも無いこと。"""
    track = build(key)
    problems = profile_for(key).validate(track.length_m)
    assert not problems, "{}: {}".format(key, " / ".join(problems))


@pytest.mark.parametrize("key", ALL_KEYS)
def test_周回で標高が閉じる(key):
    """**始点と終点の高さが一致すること。**

    ここがずれると、スタートラインで車が段差に乗り上げる。
    """
    track = build(key)
    first = track.points[0]
    last = track.points[-1]
    assert abs(first.z_m - last.z_m) < 0.05, (
        "{}: 始点 {:.3f} m と終点 {:.3f} m が違う"
        .format(key, first.z_m, last.z_m))


@pytest.mark.parametrize("key", ALL_KEYS)
def test_点ごとの高さの飛びが小さい(key):
    """隣り合う点で高さが飛ばないこと。**飛べば段差になる。**

    点の間隔は 1 m。勾配の上限が 11% なら、1 点あたり 0.11 m を
    超えることはない。
    """
    track = build(key)
    limit = profile_for(key).max_gradient_pct / 100.0
    spacing = track.points[1].s_m - track.points[0].s_m

    worst = 0.0
    for previous, current in zip(track.points, track.points[1:]):
        worst = max(worst, abs(current.z_m - previous.z_m))
    assert worst <= limit * spacing * 1.05, (
        "{}: 1 点で {:.4f} m 上がっている（上限 {:.4f} m）"
        .format(key, worst, limit * spacing))


def test_平面形は縦断で変わらない():
    """**縦断を掛けても x, y は動かないこと。**

    閉合（始点に戻るか）は平面形だけの話である。縦断が平面形を
    動かすと、解いた閉合が壊れる。
    """
    for key in ALL_KEYS:
        with_z = build(key)
        raw = CATALOGUE[key](1.0)      # apply_elevation を通していない
        assert len(with_z.points) == len(raw.points)
        for a, b in zip(with_z.points, raw.points):
            assert a.x_m == b.x_m
            assert a.y_m == b.y_m
            assert a.heading_rad == b.heading_rad


def test_コースごとに性格が違う():
    """**全部同じ起伏なら入れた意味がない。**

    高低差で見て、いちばん起伏のあるコースといちばん平坦なコースが
    はっきり違うこと。
    """
    ranges = {}
    for key in ALL_KEYS:
        track = build(key)
        zs = [p.z_m for p in track.points]
        ranges[key] = max(zs) - min(zs)

    assert ranges["mountain_pass"] > 25.0, (
        "峠の高低差が {:.1f} m しかない（峠に見えない）"
        .format(ranges["mountain_pass"]))
    assert ranges["physics_test_track"] == 0.0, (
        "物理の基準コースは平坦のままにすること"
        "（勾配を入れると回帰値が全部変わる）")
    assert ranges["mountain_pass"] > 3.0 * ranges["technical_circuit"], (
        "峠とサーキットの起伏が似すぎている: {}"
        .format({k: round(v, 1) for k, v in ranges.items()}))


def test_高架は高架として印が付く():
    """**地面の作り方が変わるので、印が要る。**

    高架は路面に地面を追従させない（桁の下は地面のまま）。
    """
    assert profile_for("high_speed_ring").is_viaduct
    assert not profile_for("mountain_pass").is_viaduct

    track = build("high_speed_ring")
    zs = [p.z_m for p in track.points]
    ground = profile_for("high_speed_ring").ground_level_m
    assert min(zs) - ground > 8.0, (
        "桁が地面から {:.1f} m しか離れていない（高架に見えない）"
        .format(min(zs) - ground))


# --- 壊れた縦断を受け取らないこと -------------------------------------------


def test_急すぎる縦断を拒否する():
    """**「峠だから急でいい」で通さない。**

    実際、最初に書いた峠は勾配 18% で、検査が止めた。
    """
    steep = ElevationProfile(
        control=[(0.0, 0.0), (0.25, 60.0), (0.5, 0.0), (0.75, -60.0)],
        max_gradient_pct=8.0,
    )
    problems = steep.validate(400.0)
    assert any("勾配が急すぎる" in p for p in problems), problems


def test_制御点の並びが壊れていたら止まる():
    for bad in (
        [(0.5, 0.0), (0.2, 3.0)],          # 昇順でない
        [(0.0, 0.0), (1.0, 5.0)],          # 1.0 は先頭と重なる
        [(0.0, 0.0), (0.0, 5.0)],          # 重複
    ):
        problems = ElevationProfile(control=bad).validate(500.0)
        assert problems, "{} を通してしまった".format(bad)


def test_条件を満たさない縦断でコースを作れない():
    """`apply_elevation` は黙って通さない（憲法ルール6）。"""
    track = build("technical_circuit")
    original = PROFILES["technical_circuit"]
    PROFILES["technical_circuit"] = ElevationProfile(
        control=[(0.0, 0.0), (0.5, 80.0)], max_gradient_pct=5.0)
    try:
        with pytest.raises(ElevationError):
            apply_elevation(track, "technical_circuit")
    finally:
        PROFILES["technical_circuit"] = original


def test_縦断曲線が勾配の変化を抑えている():
    """**制御点をそのまま繋ぐと凸部で車が飛ぶ。**

    平滑化前（`length_m=0`）と後で、勾配の変化率がはっきり下がること。
    """
    profile = profile_for("mountain_pass")
    length = build("mountain_pass").length_m

    def worst_change(smooth):
        samples = 1000
        step = length / samples
        gradients = []
        for i in range(samples):
            u = i / samples
            ds = 1.0 / length
            if smooth:
                ahead = profile.height_at(u + ds, length)
                behind = profile.height_at(u - ds, length)
            else:
                ahead = profile.height_at(u + ds)
                behind = profile.height_at(u - ds)
            gradients.append((ahead - behind) / 2.0 * 100.0)
        return max(abs(gradients[(i + 1) % samples] - gradients[i]) / step * 100.0
                   for i in range(samples))

    assert worst_change(True) < worst_change(False) / 2.0, (
        "平滑化しても勾配の変化が半分にならない"
        "（縦断曲線が効いていない）")
