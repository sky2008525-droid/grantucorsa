"""音響モデルの検査（Phase 14）.

**音は演出であって物理ではない**（憲法ルール18）。だからここで検査するのは
「実車の音に近いか」ではない。それは測っていないので判定できない。

検査するのはこの4つ:

1. **音のパラメータが `vehicle.json` を汚していないか**（分離が保たれているか）
2. **音を鳴らしても物理が変わらないか**（一方通行であること）
3. **不連続が無いか**（境界で音が飛ぶのは実装のバグであって好みではない）
4. **保存則にあたるもの** — クロスフェード比の合計が 1、振幅が 1 を超えない
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from audio_model import AudioData, AudioModel
from vehicle import ControlInput, Vehicle
from vehicle_data import VehicleData

REPO_ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(scope="module")
def data():
    return VehicleData()


@pytest.fixture(scope="module")
def audio():
    return AudioData()


@pytest.fixture(scope="module")
def model(data, audio):
    return AudioModel(data, audio)


# --- 分離 -------------------------------------------------------------------


def test_音のパラメータがvehicle_jsonに入っていない():
    """**演出を諸元に混ぜない**（憲法ルール18）。

    混ざると、出典のある公式値と耳で決めた値が同じ信頼度で扱われる。
    """
    with open(REPO_ROOT / "Vehicles" / "ZN6" / "vehicle.json", encoding="utf-8") as f:
        vehicle = json.load(f)

    forbidden = ("audio", "sound", "harmonic", "skid_", "gain", "pitch_hz",
                 "rpm_pitch", "volume", "crossfade", "firing_order")
    # **skidpad は音ではない。** validation_targets.skidpad_lateral_acceleration は
    # 定常円旋回の横G（実測比較の対象）。語の一部が同じなだけ。
    allowed = {"validation_targets.skidpad_lateral_acceleration"}
    found = []

    def walk(node, path):
        if isinstance(node, dict):
            for key, child in node.items():
                full = "{}.{}".format(path, key) if path else key
                if any(word in key.lower() for word in forbidden) and full not in allowed:
                    found.append(full)
                walk(child, full)

    walk(vehicle, "")
    assert found == [], "vehicle.json に音のパラメータが混ざっている: {}".format(found)


def test_音のパラメータに実測が混ざっていない(audio):
    """**すべて出典なし。** `official` や `measured` が現れたら、
    それは vehicle.json に置くべき値が漏れている。
    """
    bad = [(path, source) for path, source in audio.iter_sources()
           if source not in ("assumed", "kinematic")]
    assert bad == [], "audio.json に assumed 以外の source: {}".format(bad)
    assert audio.is_all_assumed()


def test_回転数の範囲をvehicle_jsonから読んでいる(model, data):
    """**音側で決め打ちしない。** ずれても誰も気づかないため。"""
    assert model.idle_rpm == data.value("engine.idle_rpm", "1/min")
    assert model.redline_rpm == data.value("engine.redline", "1/min")


def test_音を鳴らしても物理が変わらない(data, model):
    """**一方通行であること。**

    音響モデルが物理の状態を触っていたら、走りが音の設定で変わってしまう。
    """
    car = Vehicle(data)
    control = ControlInput(gear="3", throttle=1.0, brake=0.0,
                           steer_rad=0.05, clutch=1.0, handbrake=0.0)

    def run(with_audio):
        vehicle = Vehicle(data)
        state = vehicle.initial_state(speed_mps=80.0 / 3.6, gear="3")
        for step in range(400):
            state, outputs = vehicle.step(state, control, 0.002)
            if with_audio:
                model.frame(
                    engine_rpm=outputs.engine_rpm,
                    throttle=control.throttle,
                    utilisation=max(outputs.utilisation.values()),
                    speed_mps=state.speed_mps,
                    distance_to_edge_m=3.0,
                    time_s=step * 0.002,
                )
        return state

    quiet = run(False)
    loud = run(True)

    assert loud.vx_mps == quiet.vx_mps
    assert loud.vy_mps == quiet.vy_mps
    assert loud.yaw_rate_rads == quiet.yaw_rate_rads
    assert loud.x_m == quiet.x_m
    assert loud.y_m == quiet.y_m
    del car


# --- エンジン ---------------------------------------------------------------


def test_基本周波数が回転数に比例する(model):
    """4ストローク4気筒で点火が等間隔なら、1回転に2回。**運動学。**"""
    for rpm in (700.0, 3000.0, 7400.0):
        voice = model.engine_voice(rpm, throttle=1.0, time_s=0.0)
        assert voice.fundamental_hz == pytest.approx(rpm * 2.0 / 60.0, rel=1e-12)


def test_回転が上がると音量も上がる(model):
    gains = [model.engine_voice(rpm, 1.0, 0.0).gain
             for rpm in range(700, 7401, 200)]
    for previous, current in zip(gains, gains[1:]):
        assert current >= previous - 1e-12, "回転を上げたのに音量が下がった"
    assert gains[-1] > gains[0]


def test_アクセルオフで音量が下がる(model):
    on = model.engine_voice(4000.0, throttle=1.0, time_s=0.0)
    off = model.engine_voice(4000.0, throttle=0.0, time_s=0.0)
    assert off.gain < on.gain
    assert off.brightness < on.brightness
    # 周波数は負荷では変わらない（回転数だけで決まる）
    assert off.fundamental_hz == on.fundamental_hz


def test_音量が回転に対して跳ばない(model):
    """**不連続が無いこと。** 段差があると「ブツッ」と鳴る。"""
    rpms = np.arange(0.0, 7600.0, 5.0)
    gains = np.array([model.engine_voice(float(r), 1.0, 0.0).gain for r in rpms])
    jumps = np.abs(np.diff(gains))
    assert jumps.max() < 0.01, "音量が {:.4f} 跳んでいる".format(jumps.max())


def test_レブリミッタで音が断続する(model):
    """回転を制限するのは物理側。**ここは音を切るだけ。**"""
    below = [model.engine_voice(7000.0, 1.0, t / 1000.0).limiter_gate
             for t in range(200)]
    assert set(below) == {1.0}, "レッドライン未満で音が切れている"

    above = [model.engine_voice(7500.0, 1.0, t / 1000.0).limiter_gate
             for t in range(200)]
    assert 0.0 in above and 1.0 in above, "レッドライン超過で断続していない"


def test_倍音の振幅は合計1(model):
    for brightness in (0.0, 0.3, 0.6, 1.0):
        amps = model.harmonic_amplitudes(brightness)
        assert sum(amp for _, amp in amps) == pytest.approx(1.0, rel=1e-12)
        assert all(amp > 0.0 for _, amp in amps)


def test_負荷で高次倍音が持ち上がる(model):
    """**音色が負荷で変わる**（バックログ §5「スロットル: Load」）。"""
    quiet = dict(model.harmonic_amplitudes(0.0))
    loud = dict(model.harmonic_amplitudes(1.0))
    lowest = min(quiet)
    highest = max(quiet)
    assert loud[highest] / loud[lowest] > quiet[highest] / quiet[lowest]


def test_回転数が有限でなければ止まる(model):
    """**握りつぶさない**（憲法ルール6）。"""
    with pytest.raises(ValueError):
        model.engine_voice(float("nan"), 1.0, 0.0)
    with pytest.raises(ValueError):
        model.engine_voice(float("inf"), 1.0, 0.0)


# --- タイヤ -----------------------------------------------------------------


def test_限界に近づくまでスキール音は出ない(model):
    """**常時鳴らさない。** 鳴りっぱなしだと限界が近いことが分からない。"""
    assert model.tire_voice(0.0, 20.0).gain == 0.0
    assert model.tire_voice(model.skid_threshold, 20.0).gain == 0.0
    assert model.tire_voice(model.skid_threshold + 0.01, 20.0).gain > 0.0


def test_スキール音は利用率に対して単調(model):
    gains = [model.tire_voice(u / 100.0, 20.0).gain for u in range(0, 121)]
    for previous, current in zip(gains, gains[1:]):
        assert current >= previous - 1e-12


def test_スキール音が飽和する(model):
    """**限界を超えても音量が伸び続けない。** 伸びるとクリップする。"""
    at_full = model.tire_voice(model.skid_full, 20.0).gain
    beyond = model.tire_voice(1.4, 20.0).gain
    assert beyond == pytest.approx(at_full, rel=1e-12)
    assert at_full == pytest.approx(model.skid_gain, rel=1e-12)


def test_停止していてもピッチが0にならない(model):
    """0 Hz の音は再生できない。**下限で止めていること。**"""
    assert model.tire_voice(1.0, 0.0).hz > 0.0


# --- 路面 -------------------------------------------------------------------


def test_路面の混合比は合計1(model):
    for distance_m in np.arange(-5.0, 5.01, 0.05):
        voice = model.road_voice(20.0, float(distance_m))
        assert sum(voice.blend.values()) == pytest.approx(1.0, rel=1e-12)
        assert all(0.0 <= ratio <= 1.0 for ratio in voice.blend.values())


def test_路面の境界で音が飛ばない(model):
    """**クロスフェードの要件**（バックログ §5「路面状態: クロスフェード」）。"""
    distances = np.arange(-5.0, 5.0, 0.01)
    ratios = np.array([model.road_voice(20.0, float(d)).blend["asphalt"]
                       for d in distances])
    jumps = np.abs(np.diff(ratios))
    assert jumps.max() < 0.02, "路面比が {:.4f} 跳んでいる".format(jumps.max())

    # コースの内側は完全にアスファルト、外側は完全に草
    assert model.road_voice(20.0, 5.0).blend["asphalt"] == pytest.approx(1.0)
    assert model.road_voice(20.0, -5.0).blend["grass"] == pytest.approx(1.0)


def test_知らない路面を拒否する(model):
    with pytest.raises(ValueError):
        model.road_voice(20.0, 0.0, inside_surface="ice")


def test_速度が上がるとロードノイズが増える(model):
    assert model.road_voice(0.0, 3.0).gain == 0.0
    assert model.road_voice(10.0, 3.0).gain < model.road_voice(20.0, 3.0).gain
    # **飽和する。** 伸び続けるとクリップする
    assert model.road_voice(100.0, 3.0).gain == pytest.approx(model.rolling_gain)


# --- 合成された波形 ---------------------------------------------------------


def test_合成した波形がクリップしない(model):
    """**16bit PCM に収まること。** 超えたら音が割れる。"""
    import synth

    for name, samples in synth.build_all(model, write=False):
        peak = float(np.max(np.abs(samples)))
        assert peak <= 1.0, "{} のピークが {:.4f}".format(name, peak)
        assert peak > 0.5, "{} がほぼ無音（{:.4f}）".format(name, peak)
        assert np.all(np.isfinite(samples)), "{} に NaN/inf".format(name)


def test_ループの継ぎ目が滑らか(model):
    """**末尾から先頭へ戻るとき段差が無いこと。**

    段差があると1秒ごとに「プツッ」と鳴る。目視では分からないので測る。
    """
    import synth

    for name, samples in synth.build_all(model, write=False):
        seam = abs(float(samples[0] - samples[-1]))
        typical = float(np.mean(np.abs(np.diff(samples))))
        # 継ぎ目の段差が、隣り合うサンプル間の平均的な差の 20 倍を超えない
        assert seam < max(typical * 20.0, 0.02), (
            "{} の継ぎ目が {:.5f}（隣接平均 {:.5f}）".format(name, seam, typical)
        )


def test_エンジンループの基本周波数が回転数と一致する(model):
    """**合成した音の周波数を実際に測る。** 式が合っていても実装がずれる。"""
    import synth

    for rpm in (2614.0, 4529.0, 7400.0):
        samples = synth.engine_loop(model, rpm, brightness=0.0)
        spectrum = np.abs(np.fft.rfft(samples))
        freqs = np.fft.rfftfreq(len(samples), 1.0 / model.sample_rate_hz)

        expected_hz = rpm * 2.0 / 60.0
        peak_hz = float(freqs[int(np.argmax(spectrum))])
        assert peak_hz == pytest.approx(expected_hz, rel=0.02), (
            "{:.0f} rpm で {:.1f} Hz を期待したが {:.1f} Hz".format(
                rpm, expected_hz, peak_hz)
        )


def test_合成が決定的(model):
    """**同じコードから毎回同じ音。** そうでないと変更の影響が測れない。"""
    import synth

    first = synth.engine_loop(model, 4000.0, brightness=0.4)
    second = synth.engine_loop(model, 4000.0, brightness=0.4)
    assert np.array_equal(first, second)


def test_サンプル数がループ周期の整数倍(model):
    """繋ぎ目で位相が飛ばないための条件。"""
    import synth

    for rpm in (1000.0, 3000.0, 6000.0):
        hz = rpm * 2.0 / 60.0
        length = synth.loop_length_samples(model.sample_rate_hz, hz)
        cycles = length * hz / model.sample_rate_hz
        assert abs(cycles - round(cycles)) < 1e-6, (
            "{:.0f} rpm で周期が {:.4f} 個ぶん（整数でない）".format(rpm, cycles)
        )


# --- ループの選択 -----------------------------------------------------------


def test_ループの回転数が等比(model):
    """**等間隔にしない。** 再生時のピッチ倍率は比で効く。

    等間隔だと低回転側の隣り合う段の比が 2.37 倍になり、
    UE の SetPitchMultiplier が受け付ける範囲（既定 0.4〜2.0）を超える。
    """
    rpms = model.engine_loop_rpms()
    assert rpms[0] == pytest.approx(model.idle_rpm, rel=1e-12)
    assert rpms[-1] == pytest.approx(model.redline_rpm, rel=1e-12)

    ratios = [b / a for a, b in zip(rpms, rpms[1:])]
    for ratio in ratios:
        assert ratio == pytest.approx(ratios[0], rel=1e-9), "等比になっていない"
        assert ratio < 2.0, "隣り合う段の比 {:.3f} が大きすぎる".format(ratio)


def test_混合比の合計は1でピッチ倍率が範囲内(model):
    for rpm in np.arange(300.0, 8200.0, 25.0):
        blend = model.engine_loop_blend(float(rpm))
        assert sum(gain for _, gain, _ in blend) == pytest.approx(1.0, rel=1e-12)
        for index, gain, pitch in blend:
            assert 0 <= index < model.engine_loop_steps
            assert gain >= 0.0
            # アイドル未満・レッドライン超過では端の段を伸ばすので、
            # そこだけは範囲を外れうる。**黙って丸めない**ので、
            # 使用回転域（アイドル〜レッドライン）で確かめる。
            if model.idle_rpm <= rpm <= model.redline_rpm:
                assert 0.4 < pitch < 2.0, (
                    "{:.0f} rpm でピッチ倍率 {:.3f}".format(rpm, pitch))


def test_段の回転数ちょうどではその段だけが鳴る(model):
    for index, rpm in enumerate(model.engine_loop_rpms()):
        blend = model.engine_loop_blend(rpm)
        loud = [(i, g, p) for i, g, p in blend if g > 1e-9]
        assert len(loud) == 1, "{:.0f} rpm で {} 段が鳴っている".format(rpm, len(loud))
        assert loud[0][0] == index
        assert loud[0][2] == pytest.approx(1.0, rel=1e-9), "ピッチを変える必要が無い"


def test_混合比が回転に対して跳ばない(model):
    """**段の切り替わりで音量が飛ばないこと。**"""
    rpms = np.arange(model.idle_rpm, model.redline_rpm, 1.0)
    total = np.zeros((len(rpms), model.engine_loop_steps))
    for row, rpm in enumerate(rpms):
        for index, gain, _ in model.engine_loop_blend(float(rpm)):
            total[row, index] = gain
    jumps = np.abs(np.diff(total, axis=0))
    assert jumps.max() < 0.01, "混合比が {:.4f} 跳んでいる".format(jumps.max())


def test_合成したループが選択と同じ段数(model):
    import synth

    rpms = synth.engine_rpm_steps(model)
    assert rpms == model.engine_loop_rpms(), (
        "合成した段と再生側の段がずれている（音が回転数と合わなくなる）"
    )
