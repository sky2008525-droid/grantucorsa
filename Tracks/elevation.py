# -*- coding: utf-8 -*-
"""コースの縦断（標高）プロファイル。

**「峠」が平地だったのはここが無かったからである。**

`Blender/build_track.py` の冒頭には長らく「起伏を走行面に入れては
いけない」と書かれていた。それは**接地モデルと地形勾配が入る前の話**で、
今は違う:

  - `Physics/terrain.py` の `Heightfield` が4輪それぞれの下の地面を返す
  - `body_gravity()` が斜面の重力を車体座標へ分解する
  - `Physics/ride.py` が heave / pitch / roll を解き、浮いた輪の
    接地力を 0 にする

**つまり坂は物理として扱える。** 実際、上り坂で車体が機首上げになる
向きの検査が `Tests/` にある。

---

## 何を守るか

**1. 周回で閉じること。**
`z(0) == z(L)` かつ **傾きも一致**していなければ、スタートラインに
段差ができる。1周して同じ場所に戻るのだから、高さも戻らなければ
ならない。これは好みではなく幾何の要請である。

**2. 勾配が現実の範囲に収まること。**
日本の道路構造令が定める縦断勾配の最大値は、設計速度が低いほど
大きい。**ここで使う値は「その種類の道路として不自然でない範囲」で
あって、実在の路線を測った値ではない**（憲法ルール1・2）。

**3. 勾配の変化が急すぎないこと。**
縦断曲線が短いと、車は凸部で飛び、凹部で底を打つ。実際の道路には
縦断曲線半径の下限がある。ここでは**勾配の変化率**を見る。

---

## なぜ制御点と周期補間なのか

正弦波の重ね合わせにすると閉合は自動で満たせるが、「ここを上って
ここで下る」という設計ができない。制御点なら、コースのどの区間が
上りかを線形の設計と対応させて書ける。

補間は**周期 Catmull-Rom**。端の点が先頭の点と繋がるので、
値も傾きも自動的に一周する。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict, List, Tuple


@dataclass
class ElevationProfile:
    """1コースぶんの縦断。

    `control` は `(周回の割合 0..1, 標高 [m])` の並び。
    **末尾に 1.0 の点を置かないこと。** 周期補間なので、先頭が
    そのまま終端に繋がる。置くと同じ点が二重になる。
    """

    control: List[Tuple[float, float]] = field(default_factory=list)

    #: 高架か。**真なら地面は路面に追従しない。**
    #:
    #: 峠や丘のコースでは、路面が上がれば周りの地面も一緒に上がる
    #: （コースアウトしても地続き）。高架は違う。**桁が地面から
    #: 離れて浮いている**ので、周りの地面は下のままである。
    is_viaduct: bool = False

    #: 高架の桁の下に見える地面の標高 [m]。`is_viaduct` のときだけ使う。
    ground_level_m: float = 0.0

    #: この種類の道路として許す最大勾配 [%]。**検査に使う。**
    max_gradient_pct: float = 8.0

    #: 勾配の変化率の上限 [%/100m]。**検査に使う。**
    #: 大きいと凸部で車が飛ぶ。
    max_gradient_change_pct_per_100m: float = 6.0

    #: 縦断曲線の長さ [m]。**制御点をそのまま繋がない理由がこれ。**
    #:
    #: 実際の道路は、勾配が変わる所に必ず縦断曲線（縦方向の円弧）を
    #: 挟む。挟まずに折れ線で繋ぐと、凸部で車が飛び、凹部で底を打つ。
    #: 制御点を Catmull-Rom で繋いだだけでも同じことが起きる
    #: （制御点の位置で勾配が階段状に変わる。実測で 270 %/100m）。
    #:
    #: ここでは**移動平均を2回**かけて実現する。幅がそのまま縦断曲線の
    #: 長さになり、勾配の変化率は自動的に頭打ちになる。
    vertical_curve_m: float = 80.0

    #: 内部の標本表。`length_m` ごとに作って使い回す。
    _tables: dict = field(default_factory=dict, repr=False, compare=False)

    def is_flat(self) -> bool:
        return not self.control or all(
            abs(z) < 1e-9 for _, z in self.control)

    # --- 補間 -------------------------------------------------------------

    #: 標本の数。1 周をこれだけに割る。
    SAMPLES = 2048

    def _table(self, length_m: float) -> List[float]:
        """平滑化済みの標高表。**1周ぶん、等間隔。**

        制御点 -> Catmull-Rom -> 移動平均2回、の順。移動平均は
        **周期的に**かける（端を巻き戻す）ので、閉合は保たれる。
        """
        key = round(length_m, 6)
        cached = self._tables.get(key)
        if cached is not None:
            return cached

        raw = [self._raw_height(i / self.SAMPLES) for i in range(self.SAMPLES)]

        # 移動平均の幅 [標本]。**必ず奇数にする**（偶数だと半標本ずれる）。
        step_m = length_m / self.SAMPLES if length_m > 0.0 else 1.0
        width = int(round(self.vertical_curve_m / max(step_m, 1e-9)))
        width = max(1, width | 1)

        smoothed = raw
        for _ in range(2):
            smoothed = _periodic_box_filter(smoothed, width)

        self._tables[key] = smoothed
        return smoothed

    def height_at(self, s_fraction: float, length_m: float = 0.0) -> float:
        """周回の割合 0..1 での標高 [m]。**範囲外は巻き戻す。**

        `length_m` を渡すと縦断曲線（平滑化）が掛かる。0 のときは
        制御点をそのまま繋いだ値で、**設計の確認用**である。
        走行面に使うときは必ず長さを渡すこと。
        """
        if not self.control:
            return 0.0
        if length_m <= 0.0:
            return self._raw_height(s_fraction)

        table = self._table(length_m)
        u = (s_fraction - math.floor(s_fraction)) * len(table)
        i0 = int(u) % len(table)
        i1 = (i0 + 1) % len(table)
        t = u - int(u)
        return table[i0] * (1.0 - t) + table[i1] * t

    def _raw_height(self, s_fraction: float) -> float:
        """制御点をそのまま繋いだ標高 [m]（平滑化前）。"""
        if not self.control:
            return 0.0
        if len(self.control) == 1:
            return self.control[0][1]

        u = s_fraction - math.floor(s_fraction)

        count = len(self.control)
        # u が入る区間を探す。**制御点は昇順である前提**（`validate` が見る）。
        index = 0
        for i in range(count):
            if self.control[i][0] <= u:
                index = i
            else:
                break

        # 周期 Catmull-Rom。前後の点は巻き戻して取る。
        p0 = self.control[(index - 1) % count]
        p1 = self.control[index % count]
        p2 = self.control[(index + 1) % count]
        p3 = self.control[(index + 2) % count]

        # 区間の長さ。巻き戻した点は割合が小さくなるので 1 を足す。
        span = (p2[0] - p1[0]) % 1.0
        if span <= 1e-12:
            return p1[1]
        t = ((u - p1[0]) % 1.0) / span

        return _catmull_rom(p0[1], p1[1], p2[1], p3[1], t)

    def gradient_at(self, s_fraction: float, length_m: float,
                    step_m: float = 1.0) -> float:
        """その地点の勾配 [%]（上りが正）。差分で出す。"""
        if length_m <= 0.0:
            return 0.0
        ds = step_m / length_m
        ahead = self.height_at(s_fraction + ds, length_m)
        behind = self.height_at(s_fraction - ds, length_m)
        return (ahead - behind) / (2.0 * step_m) * 100.0

    # --- 検査 -------------------------------------------------------------

    def validate(self, length_m: float) -> List[str]:
        """守れていないことを日本語で並べて返す。**空なら問題なし。**

        呼ぶ側は戻り値を捨てないこと（憲法ルール6）。
        """
        problems: List[str] = []

        if not self.control:
            return problems

        fractions = [f for f, _ in self.control]
        if fractions != sorted(fractions):
            problems.append("制御点が昇順でない: {}".format(fractions))
        if any(f < 0.0 or f >= 1.0 for f in fractions):
            problems.append(
                "制御点の割合は 0 以上 1 未満（1.0 は先頭と重なる）: {}"
                .format(fractions))
        if len(set(fractions)) != len(fractions):
            problems.append("制御点の割合が重複している: {}".format(fractions))

        if problems:
            return problems          # 並びが壊れていたら以降は測れない

        # **閉合。** 補間そのものが周期なので値は必ず一致するが、
        # 「一致していること」を検査で言えるようにしておく。
        if abs(self.height_at(0.0, length_m)
               - self.height_at(1.0, length_m)) > 1e-9:
            problems.append("周回で標高が閉じていない")

        samples = 2000
        gradients = []
        for i in range(samples):
            u = i / samples
            gradients.append(self.gradient_at(u, length_m))

        worst = max(abs(g) for g in gradients)
        if worst > self.max_gradient_pct:
            problems.append(
                "勾配が急すぎる: 最大 {:.1f} %（上限 {:.1f} %）"
                .format(worst, self.max_gradient_pct))

        # 勾配の変化率。100 m あたり何 % 変わるか。
        step_m = length_m / samples
        worst_change = 0.0
        for i in range(samples):
            change = abs(gradients[(i + 1) % samples] - gradients[i])
            worst_change = max(worst_change, change / step_m * 100.0)
        if worst_change > self.max_gradient_change_pct_per_100m:
            problems.append(
                "勾配の変化が急すぎる: {:.1f} %/100m（上限 {:.1f}）。"
                "凸部で車が飛ぶ"
                .format(worst_change, self.max_gradient_change_pct_per_100m))

        return problems


def _periodic_box_filter(values: List[float], width: int) -> List[float]:
    """周期的な移動平均。**端を巻き戻す**ので閉合が壊れない。"""
    if width <= 1:
        return list(values)
    count = len(values)
    half = width // 2
    # 累積和で O(n)。素直に二重ループを回すと 2048 x 200 で遅い。
    prefix = [0.0]
    for v in values:
        prefix.append(prefix[-1] + v)
    total = prefix[-1]

    out = []
    for i in range(count):
        lo = i - half
        hi = i + half + 1
        if lo >= 0 and hi <= count:
            acc = prefix[hi] - prefix[lo]
        elif lo < 0:
            acc = (prefix[count] - prefix[count + lo]) + prefix[hi]
        else:
            acc = (prefix[count] - prefix[lo]) + prefix[hi - count]
        out.append(acc / width)
    # 平均値が動かないことの保険（累積和の誤差）
    drift = (sum(out) - total) / count
    return [v - drift for v in out]


def _catmull_rom(y0: float, y1: float, y2: float, y3: float, t: float) -> float:
    """4 点の Catmull-Rom 補間。t は y1 と y2 の間で 0..1。"""
    t2 = t * t
    t3 = t2 * t
    return 0.5 * (
        (2.0 * y1)
        + (-y0 + y2) * t
        + (2.0 * y0 - 5.0 * y1 + 4.0 * y2 - y3) * t2
        + (-y0 + 3.0 * y1 - 3.0 * y2 + y3) * t3
    )


# ---------------------------------------------------------------------------
# コースごとの縦断
# ---------------------------------------------------------------------------
#
# **数値の性格をはっきりさせておく。**
#
# ここにある標高と勾配は、**そのコースを「らしく」見せるために選んだ
# 演出値**である（憲法ルール18）。実在の路線を測った値ではないし、
# 実在の路線に対応させてもいない。
#
# 一方、**上限として使っている数値には根拠の型がある**:
#
#   - 日本の道路構造令は縦断勾配の最大値を設計速度ごとに定めており、
#     設計速度 80 km/h で 5%、40 km/h で 7%、特例でさらに 2〜3% 増と
#     いう桁である。**条文そのものを確認したわけではないので `unknown`
#     扱いとし、ここでは「その桁を超えない」ことだけを守る**
#   - 峠の県道・林道はこれより急な区間を持つ。10% 級の標識は珍しくない
#
# したがって上限は「この種類の道として不自然でない範囲」であって、
# 「実測値」ではない。

PROFILES: Dict[str, ElevationProfile] = {
    # **物理の基準コースなので平坦のままにする。**
    #
    # ここに勾配を入れると、0-100 km/h やラップタイムの回帰値が全部
    # 変わる。基準として使えなくなるので触らない。
    "physics_test_track": ElevationProfile(control=[]),

    # 常設サーキットの緩い起伏。**高低差 12 m 程度。**
    # 実在サーキットの高低差はこの桁（数 m〜数十 m）にある。
    "technical_circuit": ElevationProfile(
        control=[
            (0.00, 0.0),      # メインストレート（低い側）
            (0.14, 3.0),      # 1コーナーへ向けて上る
            (0.30, 6.8),      # 丘の頂上
            (0.46, 5.1),
            (0.60, -1.3),     # 下りの複合
            (0.74, -3.4),     # 最低点
            (0.88, -1.7),
        ],
        max_gradient_pct=6.0,
        max_gradient_change_pct_per_100m=5.0,
        # **全長 619 m と短い。** 縦断曲線を長く取らないと、
        # コーナーごとに勾配が変わって落ち着かない。
        vertical_curve_m=170.0,
    ),

    # **高架の都市高速。** 桁が地面から離れて浮いている。
    #
    # 桁の高さ（路面が地面から何 m か）は `ground_level_m` との差で決まる。
    # 都市高速の高架はおおむね 10〜20 m の桁下高を持つ区間がある。
    # ここでは 14 m を基準に、緩やかに上下させる。
    "high_speed_ring": ElevationProfile(
        control=[
            (0.00, 14.0),
            (0.18, 17.5),     # 跨道部で持ち上がる
            (0.34, 15.0),
            (0.52, 11.5),     # いちばん低い区間
            (0.68, 13.0),
            (0.84, 16.0),
        ],
        is_viaduct=True,
        ground_level_m=0.0,
        # **高速道路なので勾配は緩い。** 5% を超えない。
        max_gradient_pct=5.0,
        max_gradient_change_pct_per_100m=3.0,
    ),

    # **峠。ここがいちばん動く。**
    #
    # 全長 1106 m で高低差 35 m。局所的には 10% 級の勾配になる。
    # **10% の峠は日本では珍しくない**（勾配標識で見かける桁）。
    #
    # 最初は高低差 62 m で書いたが、それだと勾配が 18% まで立った。
    # 検査（`validate`）が止めたので落とした。**「峠だから急でいい」で
    # 通さないこと。** 18% は林道でも急な部類で、この車では上れない。
    "mountain_pass": ElevationProfile(
        control=[
            (0.00, 0.0),      # 麓
            (0.10, 5.2),
            (0.22, 14.3),     # 上りの九十九折り
            (0.34, 24.7),
            (0.46, 33.8),
            (0.55, 40.3),     # 峠の頂上
            (0.66, 35.1),
            (0.78, 22.1),     # 下り
            (0.88, 10.4),
            (0.95, 3.3),
        ],
        max_gradient_pct=11.0,
        max_gradient_change_pct_per_100m=9.0,
        vertical_curve_m=170.0,
    ),
}


def profile_for(key: str) -> ElevationProfile:
    """そのコースの縦断。**知らないコースは平坦を返さない。**

    平坦を返すと、コースを足したときに「なぜか平ら」という形で
    黙って抜ける。
    """
    if key not in PROFILES:
        raise KeyError(
            "縦断が定義されていないコース: {}（ある: {}）"
            .format(key, ", ".join(sorted(PROFILES))))
    return PROFILES[key]
