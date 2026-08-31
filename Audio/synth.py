"""音のループ素材を手続き的に合成する（Phase 14）。

    python Audio/synth.py            # Audio/Generated/*.wav を書き出す
    python Audio/synth.py --check    # 書き出さず、生成物の性質だけ検査する

## なぜ素材を選ばず合成するのか

バックログ §5 は「公開無料素材を使用する。実車録音は行わない」としている。
公開素材を使うこと自体は問題ないが、**この方法だと2つ困ることがある**:

1. リポジトリにライセンスの異なるバイナリが増える（§8 の懸念）
2. **「その素材が FA20 の音かどうか」を誰も検証できない。**
   素材名に "boxer" と書いてあっても、それは出典ではない（憲法ルール2）

手続き合成なら、**音がどこから来たかがコードとして残る。**
そのうえで「これは FA20 の音ではない」と正直に書ける。

## 何を根拠に合成しているか

運動学だけ:

- 4ストローク4気筒で点火が等間隔なら、**1回転あたりの点火は2回**
  （`audio.json` の `firing_order_per_rev`、source は `kinematic`）
- したがって基本周波数は `rpm x 2 / 60` [Hz]

倍音の重み・音量カーブ・スキール音の周波数は**耳で決めた**。
`audio.json` にすべて `assumed` と記録してある。**実車を測っていない。**

## 出力

`Audio/Generated/` に WAV を書く。**リポジトリには生成物を入れない**
（`.gitignore` 済み）。必要なときに再生成する。生成は決定的なので、
同じコードからは毎回同じ音が出る。
"""

from __future__ import annotations

import argparse
import math
import sys
import wave
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "Physics"))

from audio_model import AudioData, AudioModel        # noqa: E402
from vehicle_data import VehicleData                 # noqa: E402

OUT_DIR = Path(__file__).resolve().parent / "Generated"

#: エンジンのループを何段階の回転数で作るか。
#: **段数を増やすほど繋ぎ目が目立たなくなる**が、その分ファイルが増える。
#: 再生側（UE / audio_model）が隣り合う2つを混ぜる前提。
ENGINE_STEPS = 8

#: 1ループの長さ [s]。**点火周期の整数倍に丸めてループ点を合わせる。**
#: 丸めないと、繋ぎ目で位相が飛んで「プツッ」と鳴る。
LOOP_SECONDS = 1.0

#: 乱数の種。**固定する。** 同じコードから毎回同じ音が出ないと、
#: 「音が変わった」のが変更のせいか偶然かを切り分けられない。
NOISE_SEED = 20120406

#: ループ点を繋ぐクロスフェードの長さ [サンプル]。
#:
#: **余分に `FADE` サンプル作ってから、この長さぶんを畳んで消す。**
#: 正弦はループ長で周期が整数なら x[length+k] == x[k] なので畳んでも
#: 変わらないが、ノイズは必ず不連続になるのでここで均す。
FADE = 256


def loop_length_samples(sample_rate_hz: int, fundamental_hz: float) -> int:
    """基本周期の整数倍になるサンプル数。**ループ点を合わせるため。**"""
    if fundamental_hz <= 0.0:
        raise ValueError("基本周波数が正でない: {}".format(fundamental_hz))
    period_samples = sample_rate_hz / fundamental_hz
    cycles = max(int(round(LOOP_SECONDS * fundamental_hz)), 1)
    return int(round(cycles * period_samples))


def engine_loop(model: AudioModel, rpm: float, brightness: float) -> np.ndarray:
    """1つの回転数のエンジンループ。**位相が繋がるように作る。**"""
    voice = model.engine_voice(rpm, throttle=1.0, time_s=0.0)
    fundamental_hz = voice.fundamental_hz
    sample_rate = model.sample_rate_hz

    length = loop_length_samples(sample_rate, fundamental_hz)
    # **余分に FADE サンプル作る。** 最後に畳んでちょうど length にする。
    t = np.arange(length + FADE, dtype=np.float64) / sample_rate
    duration_s = length / sample_rate

    out = np.zeros(length + FADE, dtype=np.float64)
    for order, amp in model.harmonic_amplitudes(brightness):
        hz = fundamental_hz * order
        if hz >= sample_rate / 2.0:
            # **折り返しを黙って混ぜない。** ナイキストを超える倍音は捨てる。
            continue
        # ループ点で位相が合うよう、周期数を整数に丸める
        cycles = max(round(hz * duration_s), 1)
        out += amp * np.sin(2.0 * np.pi * cycles * t / duration_s)

    # 吸気・排気の乱れ。**帯域を絞ったノイズ**を薄く混ぜる。
    # 純粋な正弦の合成だけだと電子音になり、エンジンに聞こえない。
    rng = np.random.default_rng(NOISE_SEED + int(rpm))
    noise = _lowpass(rng.standard_normal(length + FADE),
                     cutoff_hz=fundamental_hz * 6.0, sample_rate_hz=sample_rate)
    out += 0.18 * noise / max(np.max(np.abs(noise)), 1e-9)

    return _normalise(_make_loopable(out))


def skid_loop(model: AudioModel) -> np.ndarray:
    """タイヤのスキール音。

    **ピッチは再生側で変える。** ここでは基準周波数のループだけを作る。
    """
    sample_rate = model.sample_rate_hz
    hz = model.skid_base_hz
    length = loop_length_samples(sample_rate, hz)
    t = np.arange(length + FADE, dtype=np.float64) / sample_rate
    duration_s = length / sample_rate

    out = np.zeros(length + FADE, dtype=np.float64)
    # スキール音は狭帯域だが単一周波数ではない。近接した成分を重ねる。
    for ratio, amp in ((1.0, 1.0), (1.5, 0.35), (2.0, 0.25), (2.51, 0.12)):
        cycles = max(round(hz * ratio * duration_s), 1)
        out += amp * np.sin(2.0 * np.pi * cycles * t / duration_s)

    rng = np.random.default_rng(NOISE_SEED + 7)
    noise = _bandpass(rng.standard_normal(length + FADE),
                      hz * 0.7, hz * 3.0, sample_rate)
    out += 0.5 * noise / max(np.max(np.abs(noise)), 1e-9)
    return _normalise(_make_loopable(out))


def road_loop(model: AudioModel, surface: str) -> np.ndarray:
    """ロードノイズ。路面ごとに帯域を変える。

    **どちらも「それらしい音」であって、路面を測ったものではない。**
    アスファルトは高域寄り、草地は低域寄り＋不規則、という程度の差。
    """
    sample_rate = model.sample_rate_hz
    length = int(sample_rate * 2) + FADE

    seed_offset = {"asphalt": 11, "grass": 23}
    if surface not in seed_offset:
        raise ValueError("知らない路面: {}".format(surface))

    rng = np.random.default_rng(NOISE_SEED + seed_offset[surface])
    noise = rng.standard_normal(length)

    if surface == "asphalt":
        out = _bandpass(noise, 180.0, 4500.0, sample_rate)
    else:
        # 草地。低域寄りで、粒立ちを出すために振幅を揺らす
        out = _bandpass(noise, 80.0, 1400.0, sample_rate)
        envelope = 0.6 + 0.4 * np.abs(
            _lowpass(rng.standard_normal(length), 12.0, sample_rate))
        out *= envelope / max(np.max(envelope), 1e-9)

    return _normalise(_make_loopable(out))


# --- 信号処理の小物 ---------------------------------------------------------
#
# **外部ライブラリを増やさない。** scipy を入れるほどの処理ではない。


def _lowpass(x: np.ndarray, cutoff_hz: float, sample_rate_hz: int) -> np.ndarray:
    """1次 IIR ローパス。位相は気にしない（音色を作るだけ）。"""
    if cutoff_hz <= 0.0:
        raise ValueError("カットオフが正でない")
    alpha = 1.0 - math.exp(-2.0 * math.pi * cutoff_hz / sample_rate_hz)
    alpha = min(max(alpha, 1e-6), 1.0)
    out = np.empty_like(x)
    acc = 0.0
    for index, value in enumerate(x):
        acc += alpha * (value - acc)
        out[index] = acc
    return out


def _bandpass(x: np.ndarray, low_hz: float, high_hz: float,
              sample_rate_hz: int) -> np.ndarray:
    if high_hz <= low_hz:
        raise ValueError("帯域が逆: {} .. {}".format(low_hz, high_hz))
    return _lowpass(x, high_hz, sample_rate_hz) - _lowpass(x, low_hz, sample_rate_hz)


def _make_loopable(x: np.ndarray, fade: int = FADE) -> np.ndarray:
    """末尾 `fade` サンプルを先頭へ畳み、繋ぎ目のクリックを消す。

    **ノイズはループ点で必ず不連続になる。** 正弦は周期を整数に丸めれば
    繋がるが、乱数はそうはいかない。

    戻り値の長さは `len(x) - fade`。呼び出し側は**その分だけ長く作る**こと。
    正弦成分は「畳む長さぶんの位置で1周期ぶん進んでいる」ので、
    畳んでも値が変わらない（x[length + k] == x[k]）。
    """
    if len(x) < 4 * fade:
        raise ValueError("クロスフェードするには短すぎる: {}".format(len(x)))
    out = x.copy()
    ramp = np.linspace(0.0, 1.0, fade)
    out[:fade] = x[:fade] * ramp + x[-fade:] * (1.0 - ramp)
    return out[:-fade]


def _normalise(x: np.ndarray, peak: float = 0.89) -> np.ndarray:
    """**クリップさせない。** 書き出しは 16bit PCM。"""
    largest = float(np.max(np.abs(x)))
    if largest <= 0.0:
        raise ValueError("信号が全てゼロ")
    return x * (peak / largest)


def write_wav(path: Path, samples: np.ndarray, sample_rate_hz: int) -> None:
    if np.max(np.abs(samples)) > 1.0:
        # **黙って丸めない**（憲法ルール6）。
        raise ValueError("{} が 1.0 を超えている".format(path.name))
    pcm = np.clip(samples, -1.0, 1.0)
    pcm = (pcm * 32767.0).astype(np.int16)

    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(sample_rate_hz)
        handle.writeframes(pcm.tobytes())


def engine_rpm_steps(model: AudioModel, steps: int = ENGINE_STEPS):
    """ループを作る回転数の並び。アイドルからレッドラインまで等間隔。"""
    if steps < 2:
        raise ValueError("段数が少なすぎる")
    return [model.idle_rpm
            + (model.redline_rpm - model.idle_rpm) * index / (steps - 1)
            for index in range(steps)]


def build_all(model: AudioModel, write: bool = True):
    """すべてのループを作る。`(名前, 波形)` の並びを返す。"""
    results = []

    for index, rpm in enumerate(engine_rpm_steps(model)):
        # 負荷ありと負荷なしを別に作る。**再生側で混ぜて音色を変える。**
        for tag, brightness in (("load", model.load_brightness), ("overrun", 0.0)):
            name = "engine_{:02d}_{}_{:.0f}rpm".format(index, tag, rpm)
            results.append((name, engine_loop(model, rpm, brightness)))

    results.append(("tire_skid", skid_loop(model)))
    for surface in model.surfaces:
        results.append(("road_" + surface, road_loop(model, surface)))

    if write:
        for name, samples in results:
            path = OUT_DIR / (name + ".wav")
            write_wav(path, samples, model.sample_rate_hz)
            print("書き出した: {} ({:.2f} s)".format(
                path.name, len(samples) / model.sample_rate_hz))

    return results


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true",
                        help="書き出さず、生成物の性質だけ検査する")
    args = parser.parse_args()

    model = AudioModel(VehicleData(), AudioData())

    if not model.audio.is_all_assumed():
        # **音のパラメータに実測が紛れ込んでいない**ことを毎回確かめる。
        print("audio.json に assumed 以外の source がある:", file=sys.stderr)
        for path, source in model.audio.iter_sources():
            if source not in ("assumed", "kinematic"):
                print("  {} = {}".format(path, source), file=sys.stderr)
        return 1

    results = build_all(model, write=not args.check)

    print()
    print("エンジン {} 段（{:.0f} .. {:.0f} rpm）".format(
        ENGINE_STEPS, model.idle_rpm, model.redline_rpm))
    print("**この音は FA20 の音ではない。** 実車を録音していない（Audio/audio.json）。")
    print("合成 {} ファイル / {} Hz".format(len(results), model.sample_rate_hz))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
