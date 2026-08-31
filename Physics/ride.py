"""上下・ピッチ・ロールの3自由度と、車輪の接地力。

## なぜ要るのか

これが無い状態では、車体の高さは**高さ場から代入されていた**。
地面の高さを読んで、そこに車を置いていただけである。

つまり:

- 重力が車を落としていない。**落ちていないものは、何にも支えられていない**
- 車輪が地面を押していない。押していないので、押し返されてもいない
- 段差を越えても車輪は浮かない。浮かないので、接地が切れることもない
- 荷重移動は代数式で、`wheel_loads_n()` が加速度から直接答えを出していた。
  過渡（ロールが立ち上がるまでの時間）が存在しない

このモジュールは、そこを**力の釣り合いとして解く。**

    車体は重力で落ちる
      -> 車輪が地面にぶつかり、ばねが縮む
        -> 縮んだぶんだけ押し返す（作用反作用）
          -> 押し返す力の合計が重力と釣り合ったところで止まる

浮くかどうかも、この式から自然に出る。**地面は押せるが引けない**ので、
接地力は 0 で下げ止まる。ばねが伸びきれば車輪は地面を離れる。

## 既定では使わない

`Vehicle.step()` の既定の挙動は変えていない。**検証済みの結果を
1ビットも動かさないため**（憲法ルール6・16）。

準静的モデルとの違い:

| | 準静的（既定） | ライドモデル |
|---|---|---|
| 前後の荷重移動 | `m*ax*h/L` の式 | 力の釣り合いから。**定常では同じ値になる** |
| 左右の荷重移動 | `roll_stiffness_distribution_front`（assumed 0.6）で分配 | ばねレートとトレッドから**導出** |
| 過渡 | 無い（瞬時に移る） | ある（ばねと減衰で決まる） |
| 車輪の浮き | 負になったら 0 で止める | **接地が切れる。切れたら力が 0** |

前後は定常状態で厳密に一致する（`Tests/test_ride.py` で確認）。
左右は一致しない。**合わせにいかない**（憲法ルール3）。
0.6 は出典の無い仮定であり、ばねレートから導いた値のほうが素性がよい。

## 使っているデータの素性

**この層の結果は検証に使えない。** 読んでいる値に `assumed` が混ざる。

| 値 | source |
|---|---|
| ばねレート | `estimated`（confidence 0.25 / 0.20） |
| **モーションレシオ** | **`assumed`**（実測なし） |
| **減衰比** | **`assumed`**（`damper_front` は `unknown` のまま） |
| タイヤ縦剛性 | `assumed` |
| 慣性 Ixx / Iyy | `estimated` |

**スタビライザーを含めていない。** `arb_front` / `arb_rear` は径（18mm /
14mm）が実測で分かっているが、径だけではロール剛性が出ない。アーム長と
レバー比が要り、`suspension.geometry` は `unknown` である。
そのため `arb_roll_rate_*` も `unknown` にしてあり、ここでは読まない。
**実車よりロール剛性が低い**（＝ロールが大きい）ことになる。
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Dict, Tuple

from units import GRAVITY_MPS2

#: 車輪の並び。**この順を変えない**（C++ 側と対応させている）。
WHEELS = ("FL", "FR", "RL", "RR")


@dataclass(frozen=True)
class RideState:
    """バネ上（車体）の上下・ピッチ・ロール。

    **静止した平地での釣り合い姿勢を原点にとる。** そこからのずれを持つ。
    こうすると、平地に置いた車の状態が全てゼロになり、
    「動いていない」ことが読んで分かる。
    """

    heave_m: float = 0.0
    """重心の上下位置 [m]。上が正。"""

    heave_rate_mps: float = 0.0

    pitch_rad: float = 0.0
    """ピッチ [rad]。**正が機首上げ**（UE の正のピッチと同じ向き）。"""

    pitch_rate_rads: float = 0.0

    roll_rad: float = 0.0
    """ロール [rad]。**正が右下がり**（UE の正のロールと同じ向き）。"""

    roll_rate_rads: float = 0.0

    @property
    def airborne(self) -> bool:
        """4輪とも接地していないか。`RideModel.step` が判定して返す。"""
        return self._airborne

    _airborne: bool = False


@dataclass(frozen=True)
class RideOutputs:
    """1ステップ分の接地の結果。"""

    loads_n: Dict[str, float]
    """4輪の接地力 [N]。**接地していない車輪は 0。**"""

    contact: Dict[str, bool]
    """接地しているか。"""

    ride_height_m: Dict[str, float]
    """各隅の、その下の地面からの高さ [m]。"""

    body_height_m: float
    """重心の絶対高さ [m]（地面ではなく世界の基準面から）。"""

    wheel_ground_m: Dict[str, float]
    """各車輪の下の地面の高さ [m]。"""


class RideModel:
    """3自由度（上下・ピッチ・ロール）の接地モデル。

    **平面3自由度の `Vehicle` とは別に持つ。** `Vehicle` は前後・左右・
    ヨーを解く。こちらは上下・ピッチ・ロールを解く。両者は接地力
    （`loads_n`）でのみ繋がる。

    分けてある理由は、**片方だけで走らせられるようにする**ため。
    既定では `Vehicle` だけが動き、結果は今までと完全に一致する。
    """

    def __init__(self, data, setup=None) -> None:
        # **既定は「何も変えない」。** そのときの結果は、セッティング機能を
        # 入れる前とビット単位で一致する。
        from setup import CarSetup
        self.setup = setup if setup is not None else CarSetup()

        self.mass_kg = data.value("mass.curb_mass", "kg")
        # 車高を下げれば重心も下がる。**基準値そのものは書き換えない。**
        self.cg_height_m = self.setup.cg_height_m(
            data.value("inertia.cg_height", "m"))
        self.ixx_kgm2 = data.value("inertia.Ixx", "kg*m^2")
        self.iyy_kgm2 = data.value("inertia.Iyy", "kg*m^2")

        self.wheelbase_m = data.value("dimensions.wheelbase", "m")
        self.track_front_m = data.value("dimensions.track_front", "m")
        self.track_rear_m = data.value("dimensions.track_rear", "m")

        lf = data.value("inertia.cg_longitudinal_from_front_axle", "m")
        lr = self.wheelbase_m - lf

        # 車体固定系の車輪位置（x 前方 / y 左方）。**重心が原点。**
        self._position = {
            "FL": (lf, self.track_front_m / 2.0),
            "FR": (lf, -self.track_front_m / 2.0),
            "RL": (-lr, self.track_rear_m / 2.0),
            "RR": (-lr, -self.track_rear_m / 2.0),
        }

        # --- コーナーごとの剛性 ---
        #
        # ばねとタイヤは**直列**。車体から地面までの間に、順に
        # サスペンションのばねとタイヤのゴムが挟まっている。
        #
        #   1/k = 1/k_wheel + 1/k_tyre
        #
        # 直列を忘れて k_wheel だけを使うと、剛性が 1 割ほど高く出る。
        tyre_k = data.value("tires.vertical_stiffness", "N/m")

        self.wheel_rate_n_per_m: Dict[str, float] = {}
        self.ride_rate_n_per_m: Dict[str, float] = {}
        for wheel in WHEELS:
            axle = "front" if wheel in ("FL", "FR") else "rear"
            spring = data.value("suspension.spring_rate_" + axle, "N/m")
            ratio = data.value("suspension.motion_ratio_" + axle, "-")

            # セッティングの倍率。**vehicle.json の min/max を倍率に
            # 直したものなので、範囲を超えない**（SetupLimits が保証する）。
            spring *= getattr(self.setup, "spring_scale_" + axle)

            # **ホイールレートはモーションレシオの2乗で効く。**
            # 1乗にすると、たわみは合っても力が合わない。
            wheel_rate = spring * ratio * ratio
            self.wheel_rate_n_per_m[wheel] = wheel_rate
            self.ride_rate_n_per_m[wheel] = (
                wheel_rate * tyre_k / (wheel_rate + tyre_k))

        # --- 静的な接地力 ---
        #
        # ここは**準静的モデルと同じ出し方**にする。静止した車の軸重が
        # 2つのモデルで違ったら、片方は間違っている。
        # `Vehicle` は重心位置から出しているので（`vehicle.py` の
        # static_front_n）、こちらも重心位置から出す。
        #
        # **`mass.weight_distribution_front_pct` は使わない。**
        # あの値（0.542）と、重心位置から出る前軸荷重比（lr/L = 0.530）は
        # 一致しない。1.2 点 = 重心位置にして 31 mm の食い違いで、
        # どちらも `estimated`。**どちらが正しいかはここでは決められない**
        # ので、既存モデルと同じ側を使い、食い違い自体は issue に出した。
        #
        # 重心位置を使わずに pct を使うと、静止しているだけの車に
        # 373 N*m のピッチモーメントが残り、**止まっている車が
        # 勝手に前上がりになる**（実際に最初そうなった）。
        front_ratio = lr / self.wheelbase_m
        weight_n = self.mass_kg * GRAVITY_MPS2
        self.static_load_n = {
            "FL": weight_n * front_ratio / 2.0,
            "FR": weight_n * front_ratio / 2.0,
            "RL": weight_n * (1.0 - front_ratio) / 2.0,
            "RR": weight_n * (1.0 - front_ratio) / 2.0,
        }

        # --- 減衰 ---
        #
        # 減衰比からコーナーの減衰係数を出す。**臨界減衰の基準は
        # そのコーナーが支える質量。**
        #
        #   c = 2 * zeta * sqrt(k * m_corner)
        #
        # ここで m_corner を車重の 1/4 で済ませない。前後で軸重が違う。
        self.damping_n_s_per_m: Dict[str, float] = {}
        for wheel in WHEELS:
            axle = "front" if wheel in ("FL", "FR") else "rear"
            zeta = (data.value("suspension.damping_ratio_" + axle, "-")
                    * getattr(self.setup, "damping_scale_" + axle))
            corner_mass = self.static_load_n[wheel] / GRAVITY_MPS2
            self.damping_n_s_per_m[wheel] = (
                2.0 * zeta * math.sqrt(self.ride_rate_n_per_m[wheel] * corner_mass))

        # --- ばねの自由長 ---
        #
        # 静止時に静荷重ぶん縮んでいるので、そのぶん伸ばした位置が自由長。
        # **接地力の式はここからのずれで決まる。**
        self.free_height_m = {
            wheel: self.static_load_n[wheel] / self.ride_rate_n_per_m[wheel]
            for wheel in WHEELS
        }

    # --- 導出量（報告用） ---------------------------------------------------

    @property
    def roll_stiffness_distribution_front(self) -> float:
        """ばねから導いた前ロール剛性配分 [-]。

        **`suspension.roll_stiffness_distribution_front`（assumed 0.6）とは
        別物。** 合わせにいかない（憲法ルール3）。値が違うことは、
        `Tests/test_ride.py` が数値として報告する。

        スタビライザーを含んでいない（`arb_roll_rate_*` が `unknown`）。
        """
        front = (self.ride_rate_n_per_m["FL"] + self.ride_rate_n_per_m["FR"]) \
            / 2.0 * self.track_front_m ** 2 / 2.0
        rear = (self.ride_rate_n_per_m["RL"] + self.ride_rate_n_per_m["RR"]) \
            / 2.0 * self.track_rear_m ** 2 / 2.0
        return front / (front + rear)

    def natural_frequency_hz(self, wheel: str) -> float:
        """そのコーナーの上下固有振動数 [Hz]。**妥当性の目安。**

        乗用車は 1.0〜1.6 Hz あたりに入る。ここを大きく外れていたら、
        ばねレートかモーションレシオの読み方が間違っている。
        """
        corner_mass = self.static_load_n[wheel] / GRAVITY_MPS2
        return math.sqrt(self.ride_rate_n_per_m[wheel] / corner_mass) / (2.0 * math.pi)

    # --- 接地力 -------------------------------------------------------------

    def corner_heights_m(self, state: RideState,
                         ground_m: Dict[str, float]) -> Dict[str, float]:
        """各隅の、その下の地面からの高さ [m]。

        微小角で近似する。**ロール・ピッチが大きいときは誤差が出る**が、
        そこまで傾く前に車は横転している。
        """
        heights = {}
        for wheel in WHEELS:
            x_m, y_m = self._position[wheel]
            body_z = (state.heave_m + x_m * state.pitch_rad
                      + y_m * state.roll_rad)
            heights[wheel] = body_z - ground_m[wheel]
        return heights

    def corner_rates_mps(self, state: RideState,
                         ground_rate_mps: Dict[str, float]) -> Dict[str, float]:
        rates = {}
        for wheel in WHEELS:
            x_m, y_m = self._position[wheel]
            body_rate = (state.heave_rate_mps + x_m * state.pitch_rate_rads
                         + y_m * state.roll_rate_rads)
            rates[wheel] = body_rate - ground_rate_mps[wheel]
        return rates

    def contact_loads_n(self, state: RideState, ground_m: Dict[str, float],
                        ground_rate_mps: Dict[str, float] = None
                        ) -> Tuple[Dict[str, float], Dict[str, bool]]:
        """接地力 [N] と接地しているか。

        **地面は押せるが引けない。** ばねが伸びきったら力は 0 で止まり、
        そこから先は車輪が地面を離れる。負の値を返さないのはそのため。

            N = max(0, k * (自由長 - 高さ) - c * 縮み速度)

        `max` を外すと、浮いた車輪が車体を下へ引っ張る。見た目には
        「なんとなく沈む」だけなので気づきにくい。
        """
        if ground_rate_mps is None:
            ground_rate_mps = {wheel: 0.0 for wheel in WHEELS}

        heights = self.corner_heights_m(state, ground_m)
        rates = self.corner_rates_mps(state, ground_rate_mps)

        loads: Dict[str, float] = {}
        contact: Dict[str, bool] = {}
        for wheel in WHEELS:
            compression_m = self.free_height_m[wheel] - heights[wheel]
            force_n = (self.ride_rate_n_per_m[wheel] * compression_m
                       - self.damping_n_s_per_m[wheel] * rates[wheel])
            if force_n > 0.0:
                loads[wheel] = force_n
                contact[wheel] = True
            else:
                # **接地が切れている。** 引っ張らない。
                loads[wheel] = 0.0
                contact[wheel] = False
        return loads, contact

    # --- 積分 ---------------------------------------------------------------

    def step(self, state: RideState, dt_s: float,
             ground_m: Dict[str, float],
             ax_mps2: float = 0.0, ay_mps2: float = 0.0,
             ground_rate_mps: Dict[str, float] = None
             ) -> Tuple[RideState, RideOutputs]:
        """1ステップ進める。

        `ax` / `ay` は**加速度計が読む値**（タイヤ力 / 質量）。
        `Vehicle.step()` の出力をそのまま渡す。重力を足さないこと
        （`wheel_loads_n()` と同じ約束）。

        `ground_m` は各車輪の下の地面の高さ [m]。**車体の姿勢ではなく
        地形から来る。** 4点別々に渡すので、片輪だけ段差に乗った状態も
        表せる。

        半陰的オイラーで積分する（速度を先に更新してから位置に使う）。
        ばねと減衰の系はこれで安定する。陽解法だと、剛いばねで
        1ステップごとに振幅が増える（issue #24 と同じ形）。
        """
        if dt_s <= 0.0:
            raise ValueError("dt が正でない: {}".format(dt_s))

        loads, contact = self.contact_loads_n(state, ground_m, ground_rate_mps)

        # --- 一般化力 ---
        #
        # z_i = heave + x_i*pitch + y_i*roll なので、
        # ピッチに対する一般化力は sum(x_i * N_i)、ロールは sum(y_i * N_i)。
        total_n = 0.0
        pitch_moment_nm = 0.0
        roll_moment_nm = 0.0
        for wheel in WHEELS:
            x_m, y_m = self._position[wheel]
            total_n += loads[wheel]
            pitch_moment_nm += x_m * loads[wheel]
            roll_moment_nm += y_m * loads[wheel]

        # タイヤの前後力・横力は接地面（重心より h だけ下）に働く。
        # そのぶんのモーメントを足す。**これが荷重移動の正体。**
        #
        #   加速（ax > 0）  -> 機首上げ  -> 後軸へ荷重
        #   左旋回（ay > 0）-> 右下がり  -> 右輪へ荷重
        pitch_moment_nm += self.mass_kg * ax_mps2 * self.cg_height_m
        roll_moment_nm += self.mass_kg * ay_mps2 * self.cg_height_m

        heave_accel = total_n / self.mass_kg - GRAVITY_MPS2
        pitch_accel = pitch_moment_nm / self.iyy_kgm2
        roll_accel = roll_moment_nm / self.ixx_kgm2

        heave_rate = state.heave_rate_mps + heave_accel * dt_s
        pitch_rate = state.pitch_rate_rads + pitch_accel * dt_s
        roll_rate = state.roll_rate_rads + roll_accel * dt_s

        new_state = RideState(
            heave_m=state.heave_m + heave_rate * dt_s,
            heave_rate_mps=heave_rate,
            pitch_rad=state.pitch_rad + pitch_rate * dt_s,
            pitch_rate_rads=pitch_rate,
            roll_rad=state.roll_rad + roll_rate * dt_s,
            roll_rate_rads=roll_rate,
            _airborne=not any(contact.values()),
        )

        heights = self.corner_heights_m(new_state, ground_m)
        outputs = RideOutputs(
            loads_n=loads,
            contact=contact,
            ride_height_m=heights,
            body_height_m=new_state.heave_m,
            wheel_ground_m=dict(ground_m),
        )
        return new_state, outputs

    # --- 釣り合い -----------------------------------------------------------

    def settle(self, ground_m: Dict[str, float], dt_s: float = 0.001,
               max_steps: int = 20000, tolerance_n: float = 1e-3
               ) -> Tuple[RideState, RideOutputs]:
        """静かに置いたときの釣り合い姿勢を求める。

        初期姿勢を作るのに使う。**「たぶんここ」で置かない。**
        地面が傾いていれば、車体もその傾きに落ち着く。

        接地力の合計が重量と `tolerance_n` 以内で一致し、かつ
        速度がほぼ止まったところで収束とみなす。
        """
        state = RideState()
        weight_n = self.mass_kg * GRAVITY_MPS2
        outputs = None

        for _ in range(max_steps):
            state, outputs = self.step(state, dt_s, ground_m)
            total_n = sum(outputs.loads_n.values())
            # **速度も見る。** 力だけだと、釣り合いを通過する瞬間に
            # 「収束した」と誤判定する（振動の中心を通るとき力は合う）。
            settled = (abs(total_n - weight_n) < tolerance_n
                       and abs(state.heave_rate_mps) < 1e-6
                       and abs(state.pitch_rate_rads) < 1e-6
                       and abs(state.roll_rate_rads) < 1e-6)
            if settled:
                return state, outputs

        # **収束しなかったことを黙って返さない**（憲法ルール6）。
        raise RuntimeError(
            "釣り合いに収束しない（{} ステップ）。合計 {:.1f} N / 重量 {:.1f} N"
            .format(max_steps, sum(outputs.loads_n.values()), weight_n))
