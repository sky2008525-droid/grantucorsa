"""音響パラメータの計算（Phase 14）。

**ここは演出であって物理ではない。**（憲法ルール18 / バックログ §5）

この層は物理の出力（回転数・スロットル・タイヤ利用率・速度・路面）を
受け取り、**再生パラメータ**（周波数・音量・クロスフェード比）を返す。
物理へは何も返さない。`Physics/` を import しないのはそのため。

## この層が守っていること

- **`vehicle.json` を書き換えない。** 音のために回転数やトルクを変えない
- **音のパラメータを `vehicle.json` に入れない。** `Audio/audio.json` に置く
- 回転数の範囲（アイドル・レッドライン）だけは `vehicle.json` から読む。
  音の範囲がエンジンの範囲とずれていたら、それは音側の間違い
- **オフにできる。** `AudioModel` を呼ばなければ物理は何も変わらない

## この音は FA20 の音ではない

実車の録音はしていない（バックログ §1.2 の前提）。素材も選んでいない。
`synth.py` が「4ストローク4気筒の点火が等間隔なら基本次数は回転の2倍」
という運動学だけを使って合成する。**倍音の重みは耳で決めた値**であり、
`audio.json` にすべて `assumed` と記録してある。

**「実車の音に近い」と主張しない。**
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Sequence, Tuple

REPO_ROOT = Path(__file__).resolve().parent.parent
AUDIO_JSON = Path(__file__).resolve().parent / "audio.json"


class AudioData:
    """`audio.json` の読み取り。**数値をコードに書かないための入口。**

    `Physics/vehicle_data.py` と同じ考え方だが、要求はもっと緩い。
    ここにある値はすべて `assumed`（演出）なので、confidence を積む
    意味が無い。代わりに**「実測だと誤解されないこと」**を守る。
    """

    def __init__(self, path: Path = AUDIO_JSON) -> None:
        with open(path, encoding="utf-8") as handle:
            self._data = json.load(handle)
        self._path = path

    def node(self, dotted: str) -> dict:
        node = self._data
        for part in dotted.split("."):
            if part not in node:
                raise KeyError("{} に {} が無い".format(self._path.name, dotted))
            node = node[part]
        if not isinstance(node, dict) or "value" not in node:
            raise KeyError("{} は測定ノードでない".format(dotted))
        return node

    def value(self, dotted: str, unit: str):
        node = self.node(dotted)
        if node.get("unit") != unit:
            raise ValueError(
                "{} の単位が {} でなく {}".format(dotted, unit, node.get("unit")))
        value = node["value"]
        if value == "unknown":
            # **既定値で代用しない。** 音であっても、無いものは無い。
            raise ValueError("{} が unknown".format(dotted))
        return value

    def number(self, dotted: str, unit: str) -> float:
        return float(self.value(dotted, unit))

    def is_all_assumed(self) -> bool:
        """**すべて出典なしであることを確かめる。**

        音のパラメータに `official` や `measured` が紛れ込んだら、
        それは vehicle.json に置くべき値がこちらに漏れている。
        `firing_order_per_rev` だけは運動学なので `kinematic`。
        """
        allowed = {"assumed", "kinematic"}
        return all(source in allowed for _, source in self.iter_sources())

    def iter_sources(self):
        def walk(node, prefix):
            if isinstance(node, dict) and "value" in node and "source" in node:
                yield prefix, node["source"]
                return
            if isinstance(node, dict):
                for key, child in node.items():
                    if key.startswith("_"):
                        continue
                    yield from walk(child, "{}.{}".format(prefix, key) if prefix else key)

        yield from walk(self._data, "")


@dataclass(frozen=True)
class EngineVoice:
    """エンジン音1フレーム分の再生パラメータ。"""

    fundamental_hz: float
    """点火の基本周波数 [Hz]。回転数 x firing_order / 60。"""

    gain: float
    """音量 [-]。0..1。"""

    brightness: float
    """高次倍音の持ち上げ [-]。0（オフ）..1（全開）。"""

    limiter_gate: float
    """レブリミッタによる断続 [-]。1 = 鳴っている / 0 = 切れている。"""


@dataclass(frozen=True)
class TireVoice:
    """タイヤのスキール音1フレーム分。"""

    hz: float
    gain: float


@dataclass(frozen=True)
class RoadVoice:
    """ロードノイズ1フレーム分。路面ごとの混合比を持つ。"""

    gain: float
    blend: Dict[str, float]
    """路面名 -> 比率。**合計は必ず 1。**"""


@dataclass(frozen=True)
class AudioFrame:
    engine: EngineVoice
    tire: TireVoice
    road: RoadVoice
    master_gain: float


class AudioModel:
    """物理の出力から再生パラメータを作る。

    **物理を変えない。** 引数はすべて読むだけで、返すのは音の値だけ。
    """

    def __init__(self, vehicle_data, audio_data: AudioData = None) -> None:
        self.audio = audio_data if audio_data is not None else AudioData()

        # **回転数の範囲は vehicle.json から。** 音側で決め打ちすると、
        # エンジンの範囲とずれても誰も気づかない。
        self.idle_rpm = float(vehicle_data.value("engine.idle_rpm", "1/min"))
        self.redline_rpm = float(vehicle_data.value("engine.redline", "1/min"))
        if self.redline_rpm <= self.idle_rpm:
            raise ValueError(
                "レッドラインがアイドルより低い: {} <= {}".format(
                    self.redline_rpm, self.idle_rpm))

        self.firing_order_per_rev = self.audio.number("engine.firing_order_per_rev", "-")
        self.harmonics: List[Tuple[float, float]] = [
            (float(order), float(amp))
            for order, amp in self.audio.value("engine.harmonics", "-")
        ]
        self.idle_gain = self.audio.number("engine.idle_gain", "-")
        self.redline_gain = self.audio.number("engine.redline_gain", "-")
        self.gain_curve_exponent = self.audio.number("engine.gain_curve_exponent", "-")
        self.overrun_gain = self.audio.number("engine.overrun_gain", "-")
        self.load_brightness = self.audio.number("engine.load_brightness", "-")
        self.limiter_flutter_hz = self.audio.number("engine.limiter_flutter_hz", "Hz")

        self.skid_threshold = self.audio.number("tire.skid_slip_threshold", "-")
        self.skid_full = self.audio.number("tire.skid_full_slip", "-")
        if self.skid_full <= self.skid_threshold:
            raise ValueError("スキールの飽和点が閾値以下")
        self.skid_gain = self.audio.number("tire.skid_gain", "-")
        self.skid_base_hz = self.audio.number("tire.skid_base_hz", "Hz")
        self.skid_speed_ref_mps = self.audio.number("tire.skid_speed_ref_mps", "m/s")

        self.surfaces: List[str] = list(self.audio.value("road.surfaces", "-"))
        self.crossfade_m = self.audio.number("road.crossfade_m", "m")
        self.rolling_gain = self.audio.number("road.rolling_gain", "-")
        self.rolling_ref_mps = self.audio.number("road.rolling_ref_mps", "m/s")

        self.engine_loop_steps = int(self.audio.value("engine.loop_steps", "-"))
        self.master_gain = self.audio.number("mix.master_gain", "-")
        self.sample_rate_hz = int(self.audio.value("mix.sample_rate_hz", "Hz"))

    # --- エンジン ---------------------------------------------------------

    def engine_voice(self, engine_rpm: float, throttle: float, time_s: float
                     ) -> EngineVoice:
        """回転数とスロットルからエンジン音のパラメータ。

        **回転数をここで丸めない。** 物理が出した値をそのまま使う。
        アイドル以下でも音は止めず、周波数だけ下がる（エンストの音は
        「無音」ではない）。
        """
        if not math.isfinite(engine_rpm):
            # **握りつぶさない**（憲法ルール6）。
            raise ValueError("回転数が有限でない: {}".format(engine_rpm))

        rpm = max(engine_rpm, 0.0)
        fundamental_hz = rpm * self.firing_order_per_rev / 60.0

        # 回転に対する音量。アイドルからレッドラインまでを 0..1 に写す。
        span = (rpm - self.idle_rpm) / (self.redline_rpm - self.idle_rpm)
        span = min(max(span, 0.0), 1.0)
        curve = span ** self.gain_curve_exponent
        gain = self.idle_gain + (self.redline_gain - self.idle_gain) * curve

        # 負荷。**アクセルオフで音量が落ちる**（負荷による音色変化の表現）。
        load = min(max(throttle, 0.0), 1.0)
        gain *= self.overrun_gain + (1.0 - self.overrun_gain) * load

        # レブリミッタ。**回転を制限するのは物理側の仕事。**
        # ここでは既に当たっている回転数で音を断続させるだけ。
        limiter_gate = 1.0
        if rpm >= self.redline_rpm:
            phase = math.sin(2.0 * math.pi * self.limiter_flutter_hz * time_s)
            limiter_gate = 1.0 if phase >= 0.0 else 0.0

        return EngineVoice(
            fundamental_hz=fundamental_hz,
            gain=gain,
            brightness=self.load_brightness * load,
            limiter_gate=limiter_gate,
        )

    def harmonic_amplitudes(self, brightness: float) -> List[Tuple[float, float]]:
        """倍音の [次数, 振幅]。**負荷で高次が持ち上がる。**

        振幅の合計で正規化する。持ち上げた結果、合計が 1 を超えて
        クリップするのを避けるため。
        """
        weighted = []
        for order, amp in self.harmonics:
            # 次数が高いほど brightness の効きを強くする
            lift = 1.0 + brightness * (order - 1.0) / max(len(self.harmonics), 1)
            weighted.append((order, amp * lift))

        total = sum(amp for _, amp in weighted)
        if total <= 0.0:
            raise ValueError("倍音の振幅が全てゼロ")
        return [(order, amp / total) for order, amp in weighted]

    # --- ループの選択 -----------------------------------------------------

    def engine_loop_rpms(self, steps: int = None) -> List[float]:
        """エンジンループを作る回転数。**等比で並べる。**

        等間隔にすると、低回転側の隣り合う段の比が大きくなりすぎる
        （700 と 1657 なら 2.37 倍）。再生時のピッチ倍率は比で効くので、
        **UE の SetPitchMultiplier が受け付ける範囲（既定 0.4〜2.0）を
        超えてしまう。** 等比なら全ての段で同じ比になる。
        """
        count = self.engine_loop_steps if steps is None else steps
        if count < 2:
            raise ValueError("段数が少なすぎる: {}".format(count))
        ratio = (self.redline_rpm / self.idle_rpm) ** (1.0 / (count - 1))
        return [self.idle_rpm * ratio ** index for index in range(count)]

    def engine_loop_blend(self, engine_rpm: float
                          ) -> List[Tuple[int, float, float]]:
        """再生するループの選択。`[(段の番号, 音量比, ピッチ倍率), ...]`。

        隣り合う2段を混ぜる。**音量比の合計は必ず 1。**
        ピッチ倍率は「今の回転数 / その段の回転数」。

        範囲外（アイドル未満・レッドライン超過）では端の1段だけを使い、
        ピッチだけを伸ばす。**段を勝手に増やさない。**
        """
        rpms = self.engine_loop_rpms()
        rpm = max(float(engine_rpm), 1.0)

        if rpm <= rpms[0]:
            return [(0, 1.0, rpm / rpms[0])]
        if rpm >= rpms[-1]:
            last = len(rpms) - 1
            return [(last, 1.0, rpm / rpms[last])]

        upper = next(i for i, value in enumerate(rpms) if value >= rpm)
        lower = upper - 1

        # **対数で混ぜる。** 線形だと段の中央でピッチが偏る。
        span = math.log(rpms[upper] / rpms[lower])
        ratio = math.log(rpm / rpms[lower]) / span

        return [
            (lower, 1.0 - ratio, rpm / rpms[lower]),
            (upper, ratio, rpm / rpms[upper]),
        ]

    # --- タイヤ -----------------------------------------------------------

    def tire_voice(self, utilisation: float, speed_mps: float) -> TireVoice:
        """タイヤの利用率（摩擦円の使用率）と速度からスキール音。

        `utilisation` は `Physics/vehicle.py` が出す 0..1 の値。
        **閾値以下では無音。** 常時鳴らすと、限界が近いことが分からない。
        """
        used = min(max(utilisation, 0.0), 1.5)
        if used <= self.skid_threshold:
            return TireVoice(hz=self.skid_base_hz, gain=0.0)

        span = (used - self.skid_threshold) / (self.skid_full - self.skid_threshold)
        gain = self.skid_gain * min(span, 1.0)

        # 速度が上がるとピッチも上がる。**測ったものではない。**
        speed_factor = math.sqrt(
            max(speed_mps, 0.0) / max(self.skid_speed_ref_mps, 1e-6))
        return TireVoice(hz=self.skid_base_hz * max(speed_factor, 0.25), gain=gain)

    # --- 路面 -------------------------------------------------------------

    def road_voice(self, speed_mps: float, distance_to_edge_m: float,
                   inside_surface: str = "asphalt",
                   outside_surface: str = "grass") -> RoadVoice:
        """速度と「路面の境界までの距離」からロードノイズ。

        `distance_to_edge_m` は**路面の内側を正**とする符号つきの距離。
        コース上なら正、はみ出したら負。境界の前後 `crossfade_m` で混ぜる。

        **境界で音が飛ばないこと**が要件（バックログ §5「路面状態: クロスフェード」）。
        """
        for surface in (inside_surface, outside_surface):
            if surface not in self.surfaces:
                raise ValueError(
                    "audio.json の road.surfaces に無い路面: {}".format(surface))

        half = self.crossfade_m / 2.0
        if half <= 0.0:
            inside_ratio = 1.0 if distance_to_edge_m >= 0.0 else 0.0
        else:
            inside_ratio = (distance_to_edge_m + half) / (2.0 * half)
            inside_ratio = min(max(inside_ratio, 0.0), 1.0)

        blend = {inside_surface: inside_ratio,
                 outside_surface: 1.0 - inside_ratio}

        gain = self.rolling_gain * min(
            max(speed_mps, 0.0) / max(self.rolling_ref_mps, 1e-6), 1.0)
        return RoadVoice(gain=gain, blend=blend)

    # --- 1フレーム --------------------------------------------------------

    def frame(self, engine_rpm: float, throttle: float, utilisation: float,
              speed_mps: float, distance_to_edge_m: float, time_s: float
              ) -> AudioFrame:
        return AudioFrame(
            engine=self.engine_voice(engine_rpm, throttle, time_s),
            tire=self.tire_voice(utilisation, speed_mps),
            road=self.road_voice(speed_mps, distance_to_edge_m),
            master_gain=self.master_gain,
        )
