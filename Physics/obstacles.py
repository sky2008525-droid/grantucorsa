"""樹木と世界境界の当たり判定。すり抜けと「世界の外へ無限に走れる」の解消.

## なぜ描画メッシュから形を読まないのか

憲法ルール4「物理計算と表示用3Dモデルを完全に分離する」。
樹木の StaticMesh やそのコリジョンを物理が読むと、描画の都合（LOD、
差し替え、スケール）が物理に混入する。

読むのは `Tracks/Export/placement.json` の**配置データ**だけ。これは
`Blender/build_track.py` が生成した「どこに何を置くか」の定義であって、
メッシュそのものではない。`terrain.py` が `heightfield.json` を読むのと
同じ考え方（描画メッシュ TrackGround.fbx ではなく、その生成元を読む）。

## この当たり判定が与えるもの／与えないもの

**与える:**

  - 樹木（鉛直な円柱）と車体（長方形）の接触判定
  - めり込みの押し戻し（**すり抜けない**）
  - 接触点まわりの法線撃力（速度とヨーレートが変わる）
  - 世界境界（地面メッシュの端）から外へ出さないこと

**与えない:**

  - 車体の変形、部品の脱落、樹木が倒れること
  - 接線（摩擦）方向の撃力。**車体と幹の摩擦係数に出典が無い。**
    無いものを入れずに、法線方向だけを扱う（憲法ルール1）
  - 鉛直方向。物理が平面3自由度である以上、乗り上げは表現できない。
    樹木の `z_m` も使わない（車は必ず地面にいる）

## 実車データではない値について

反発係数と幹の当たり半径には**実車データも実測値も存在しない。**
`vehicle.json` に入れてはいけない（憲法ルール1・18）。ゲーム側の設定
として `ObstacleFeel` に置き、そこに「車両仕様ではない」と明記してある。
`Unreal/.../ZN6VehicleActor.h` の `FZN6DriverFeel` / `FZN6BodyAttitudeFeel`
と同じ扱い。

## 時間刻みとすり抜け

連続衝突判定（CCD）は入れていない。1 ステップの移動量は
100 km/h・dt=2 ms でも 5.6 cm で、**車体長 4.24 m や幹の直径に対して
桁で小さい**ため、幹が車体を跨いで通り抜けることは起きない。
（起きるとすればそれは dt が大き過ぎるということなので、隠さずに
`Contact.engulfed` として報告する。）
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, replace
from pathlib import Path
from typing import List, Optional, Sequence, Tuple


@dataclass(frozen=True)
class ObstacleFeel:
    """当たり判定のゲーム側設定。

    **ここに書く値は車両仕様ではない**（憲法ルール18「現実の車両仕様と
    ゲーム上の演出を明確に分離する」）。実車の ZN6 が木にぶつかったときの
    反発係数を測った資料は無く、樹木も PolyHaven の若木を拡大して並べた
    景観であって計測対象ではない（`Blender/build_track.py` の
    `plan_trees` を参照）。

    **`vehicle.json` に混ぜないこと。** 混ぜると「実車データ」と
    「それらしく決めた値」の区別が付かなくなる。
    """

    restitution: float = 0.15
    """反発係数 [-]。0 = 跳ね返らない / 1 = 完全弾性。

    **出典は無い。** 実車が樹木に衝突したときの反発を測った資料は無い。
    金属の車体と木は大きく塑性変形するので小さめにしてある、という以上の
    根拠は無い。**これを実測値として扱わないこと。**
    """

    trunk_radius_per_scale_m: float = 0.15
    """幹の当たり半径 [m]（`placement.json` の `scale` 1 あたり）。

    実際の半径は `trunk_radius_per_scale_m * scale`。

    **出典は無いし、樹木メッシュからも読んでいない**（読めば憲法ルール4に
    反する）。`placement.json` には位置・向き・スケールしか無く、幹の太さは
    定義されていない。樹種ごとに別の値を置くと**5個の根拠の無い数字**が
    増えるだけなので、スケールに比例する1つの係数で済ませてある。

    見た目と厳密に合わせたいなら、`Blender/build_track.py` が幹半径を
    **定義として** `placement.json` に書き出すのが筋。ここで増やすのではなく、
    配置データ側に持たせること。
    """

    def __post_init__(self) -> None:
        # **物理的にあり得ない設定で黙って走らない**（憲法ルール6）。
        if not 0.0 <= self.restitution <= 1.0:
            raise ValueError(
                "反発係数が [0, 1] の外: {}。1 を超えると衝突で"
                "エネルギーが増える".format(self.restitution)
            )
        if self.trunk_radius_per_scale_m <= 0.0:
            raise ValueError(
                "幹の当たり半径が正でない: {}".format(self.trunk_radius_per_scale_m)
            )


@dataclass(frozen=True)
class CollisionBody:
    """当たり判定に使う車体の外形（重心を原点とする車体固定系、x 前方 / y 左方）。

    **描画メッシュからは読まない**（憲法ルール4）。全長・全幅は
    `vehicle.json` の `official` な値で、重心位置も `vehicle.json` から来る。

    ただし**前後オーバーハングの配分は `vehicle.json` に無い。**
    全長 4.240 m とホイールベース 2.570 m の差 1.670 m を前後にどう
    振り分けるかの一次資料が取れていないので、`from_vehicle_data` は
    **等分と仮定している**（前後 0.835 m ずつ）。これは実測値ではない。
    資料が取れたら `vehicle.json` に `front_overhang` / `rear_overhang` を
    足して、ここをその値に差し替えること。
    """

    front_m: float
    """重心から前端まで [m]。"""

    rear_m: float
    """重心から後端まで [m]（正の距離）。"""

    half_width_m: float
    """車体中心線から側面まで [m]。"""

    def __post_init__(self) -> None:
        if self.front_m <= 0.0 or self.rear_m <= 0.0 or self.half_width_m <= 0.0:
            raise ValueError(
                "車体の外形が正でない: front={} rear={} half_width={}".format(
                    self.front_m, self.rear_m, self.half_width_m
                )
            )

    @property
    def bounding_radius_m(self) -> float:
        """重心から最も遠い車体の角までの距離 [m]。粗い判定に使う。"""
        return math.hypot(max(self.front_m, self.rear_m), self.half_width_m)

    def corners(self) -> Tuple[Tuple[float, float], ...]:
        """車体の4隅（車体固定系）。前左・前右・後左・後右の順。"""
        return (
            (self.front_m, self.half_width_m),
            (self.front_m, -self.half_width_m),
            (-self.rear_m, self.half_width_m),
            (-self.rear_m, -self.half_width_m),
        )

    @classmethod
    def from_vehicle_data(cls, data) -> "CollisionBody":
        """`vehicle.json` から外形を作る。**数値をここに書かない。**"""
        length_m = data.value("dimensions.length", "m")
        width_m = data.value("dimensions.width", "m")
        wheelbase_m = data.value("dimensions.wheelbase", "m")
        lf_m = data.value("inertia.cg_longitudinal_from_front_axle", "m")
        lr_m = wheelbase_m - lf_m

        # 車体の中心をホイールベースの中点に置く（= 前後オーバーハング等分）。
        # **この仮定については class の docstring を読むこと。**
        centre_from_cg_m = (lf_m - lr_m) / 2.0
        return cls(
            front_m=centre_from_cg_m + length_m / 2.0,
            rear_m=length_m / 2.0 - centre_from_cg_m,
            half_width_m=width_m / 2.0,
        )


@dataclass(frozen=True)
class Contact:
    """1回の接触の記録。テレメトリと検査用。"""

    kind: str
    """`"tree"` または `"boundary"`。"""

    index: int
    """樹木なら `placement.json` の `trees` の添字。
    境界なら 0=x0（西） / 1=x1（東） / 2=y0（南） / 3=y1（北）。"""

    depth_m: float
    """めり込み量 [m]。この分だけ車を押し戻した。"""

    closing_speed_mps: float
    """接触点の法線方向の接近速度 [m/s]（負が接近）。"""

    impulse_ns: float
    """加えた法線撃力 [N*s]。離れつつあるときは 0。"""

    engulfed: bool = False
    """幹の中心が車体の内側にあった。**dt が大き過ぎるとこうなる。**
    黙って無視せず、押し出しつつ記録する。"""


# --- 接触の幾何 -------------------------------------------------------------


def circle_contact(body: CollisionBody, bx_m: float, by_m: float, radius_m: float
                   ) -> Optional[Tuple[float, float, float, float, float, bool]]:
    """車体長方形と円（幹の断面）の接触。**すべて車体固定系。**

    引数の `(bx_m, by_m)` は幹の中心。

    戻り値: `(px, py, nx, ny, depth_m, engulfed)` または接触していなければ None。

      - `(px, py)` 接触点（車体側の最近点）
      - `(nx, ny)` 単位法線。**「障害物 -> 車」向き。車を押し戻す方向。**
      - `depth_m`  めり込み量

    **法線の向きを推測で書かないこと。** 逆にすると車が木へ吸い込まれる。
    `Tests/test_obstacles.py` で機械的に縛っている。
    """
    # 長方形上で幹の中心に最も近い点
    px = min(max(bx_m, -body.rear_m), body.front_m)
    py = min(max(by_m, -body.half_width_m), body.half_width_m)

    dx = px - bx_m
    dy = py - by_m
    distance_sq = dx * dx + dy * dy

    if distance_sq > radius_m * radius_m:
        return None

    if distance_sq > 0.0:
        distance = math.sqrt(distance_sq)
        return (px, py, dx / distance, dy / distance, radius_m - distance, False)

    # --- 幹の中心が車体の内側にある ---
    #
    # **黙って 0 を返さない**（憲法ルール6）。どちらへ押し出すかを
    # 決められる: 最も近い辺から出す。出すのに要る距離は
    # 「辺までの距離 + 半径」。
    escapes = (
        # (深さ, nx, ny, px, py)
        (body.front_m - bx_m + radius_m, -1.0, 0.0, body.front_m, by_m),
        (bx_m + body.rear_m + radius_m, +1.0, 0.0, -body.rear_m, by_m),
        (body.half_width_m - by_m + radius_m, 0.0, -1.0, bx_m, body.half_width_m),
        (by_m + body.half_width_m + radius_m, 0.0, +1.0, bx_m, -body.half_width_m),
    )
    depth, nx, ny, px, py = min(escapes)
    return (px, py, nx, ny, depth, True)


def contact_impulse(vx_mps: float, vy_mps: float, yaw_rate_rads: float,
                    px_m: float, py_m: float, nx: float, ny: float,
                    mass_kg: float, izz_kgm2: float, restitution: float
                    ) -> Tuple[float, float]:
    """接触点に加える法線撃力 [N*s] と接近速度 [m/s]。**車体固定系。**

    ## 導出

    接触点（重心から見て `(px, py)`）の速度は、剛体なので

        v_p = (vx - r*py,  vy + r*px)

    法線 `n` は「障害物 -> 車」向きなので、`v_p . n` が**負なら近づいている**。
    離れつつあるならぶつけない（0 を返す）。**ここを省くと、一度触れた
    物体に何ステップも撃力が入り、車が弾き飛ばされる。**

    撃力 j による変化は

        dvx = j*nx/m,  dvy = j*ny/m,  dr = j*(px*ny - py*nx)/Izz

    これを法線方向の跳ね返り条件 `v_p'.n = -e * v_p.n` に入れると

        j = -(1 + e) * (v_p.n) / (1/m + (px*ny - py*nx)^2 / Izz)

    右の括弧が**接触点での有効質量の逆数**。慣性項があるので、
    角でぶつかると同じ速度でも撃力が小さくなり、代わりに車が回る。

    **符号を推測で書かないこと。** `Tests/test_obstacles.py` で
    「正面衝突なら減速する」「角でぶつかるとその向きに回る」を縛っている。
    """
    point_vx = vx_mps - yaw_rate_rads * py_m
    point_vy = vy_mps + yaw_rate_rads * px_m
    closing_mps = point_vx * nx + point_vy * ny

    if closing_mps >= 0.0:
        # 離れつつある。押し戻しだけ行い、撃力は入れない
        return 0.0, closing_mps

    lever = px_m * ny - py_m * nx
    inverse_mass = 1.0 / mass_kg + lever * lever / izz_kgm2
    impulse_ns = -(1.0 + restitution) * closing_mps / inverse_mass
    return impulse_ns, closing_mps


# --- 障害物の集合 -----------------------------------------------------------


class ObstacleField:
    """樹木（鉛直な円柱）の集合と世界境界。

    位置は `Tracks/Export/placement.json` から読む。**樹木の StaticMesh は
    読まない**（憲法ルール4）。
    """

    def __init__(self, trees: Sequence[Tuple[float, float, float]],
                 bounds_m: Tuple[float, float, float, float],
                 feel: ObstacleFeel = ObstacleFeel()) -> None:
        """`trees` は (x [m], y [m], 当たり半径 [m])、`bounds_m` は (x0, x1, y0, y1)。"""
        self.trees: List[Tuple[float, float, float]] = [
            (float(x), float(y), float(r)) for x, y, r in trees
        ]
        self.x0_m, self.x1_m, self.y0_m, self.y1_m = (float(v) for v in bounds_m)
        self.feel = feel

        if self.x1_m <= self.x0_m or self.y1_m <= self.y0_m:
            raise ValueError(
                "世界境界が不正: x {}..{} / y {}..{}".format(
                    self.x0_m, self.x1_m, self.y0_m, self.y1_m
                )
            )
        for index, (_, _, radius) in enumerate(self.trees):
            if radius <= 0.0:
                raise ValueError("樹木 {} の当たり半径が正でない: {}".format(index, radius))

    # --- 読み込み ---------------------------------------------------------

    @classmethod
    def from_placement(cls, path, feel: ObstacleFeel = ObstacleFeel()) -> "ObstacleField":
        """`placement.json` を読む。**メッシュではなく配置データ。**"""
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)

        extent = data["extent_m"]
        trees = []
        for index, tree in enumerate(data["trees"]):
            scale = float(tree["scale"])
            if scale <= 0.0:
                raise ValueError("樹木 {} の scale が正でない: {}".format(index, scale))
            trees.append((
                float(tree["x_m"]),
                float(tree["y_m"]),
                feel.trunk_radius_per_scale_m * scale,
            ))

        return cls(
            trees=trees,
            # **世界境界は地面メッシュの範囲**（placement.json の extent_m）。
            # 高さ場の格子は端数を切り捨てるので最大 1 セル（4 m）だけ狭く、
            # その外側は端の高さが続く扱いになる（terrain.Heightfield）。
            bounds_m=(extent["x0"], extent["x1"], extent["y0"], extent["y1"]),
            feel=feel,
        )

    @classmethod
    def from_export(cls, repo_root, feel: ObstacleFeel = ObstacleFeel(),
                    track_key: str = "physics_test_track") -> "ObstacleField":
        return cls.from_placement(
            Path(repo_root) / "Tracks" / "Export" / track_key / "placement.json",
            feel
        )

    # --- 解決 -------------------------------------------------------------

    def resolve(self, state, body: CollisionBody, mass_kg: float, izz_kgm2: float
                ) -> Tuple[object, List[Contact]]:
        """1ステップ分の接触を解き、(新しい状態, 接触の記録) を返す。

        **どこにも触れていなければ `state` をそのまま返す。**
        障害物が無い既定の走行では、結果が当たり判定を入れる前と
        ビット単位で一致する（`Tests/test_obstacles.py` で検査）。

        `Vehicle.step()` の**後**に呼ぶこと。step は接触を知らないままで
        よい（既に検証済みの結果を汚さないため、step には手を入れていない）。

        接触は逐次に解く（Gauss-Seidel）。1ステップ1巡で、収束まで
        繰り返さない。**2本の木に同時に挟まれると、後に解いた側の押し戻しで
        前の側へわずかに入り直すことがある**が、次のステップで解かれる。
        """
        vx_mps = state.vx_mps
        vy_mps = state.vy_mps
        yaw_rate_rads = state.yaw_rate_rads
        x_m = state.x_m
        y_m = state.y_m

        cos_h = math.cos(state.heading_rad)
        sin_h = math.sin(state.heading_rad)

        contacts: List[Contact] = []
        reach_m = body.bounding_radius_m

        def apply(px_m, py_m, nx, ny, depth_m, kind, index, engulfed):
            """撃力と押し戻しを反映する。**戻り値ではなく閉包で状態を書く。**"""
            nonlocal vx_mps, vy_mps, yaw_rate_rads, x_m, y_m

            impulse_ns, closing_mps = contact_impulse(
                vx_mps, vy_mps, yaw_rate_rads, px_m, py_m, nx, ny,
                mass_kg, izz_kgm2, self.feel.restitution,
            )
            if impulse_ns > 0.0:
                vx_mps += impulse_ns * nx / mass_kg
                vy_mps += impulse_ns * ny / mass_kg
                yaw_rate_rads += impulse_ns * (px_m * ny - py_m * nx) / izz_kgm2

            # めり込みを消す。**車体固定系の法線を世界へ戻してから動かす。**
            x_m += (nx * cos_h - ny * sin_h) * depth_m
            y_m += (nx * sin_h + ny * cos_h) * depth_m

            contacts.append(Contact(
                kind=kind, index=index, depth_m=depth_m,
                closing_speed_mps=closing_mps, impulse_ns=impulse_ns,
                engulfed=engulfed,
            ))

        # --- 樹木 ---
        #
        # **順番を固定する。** placement.json の並び順で解く。順番が変わると
        # 同時接触の結果が変わり、Python と C++ が一致しなくなる。
        for index, (tree_x_m, tree_y_m, radius_m) in enumerate(self.trees):
            dx = tree_x_m - x_m
            dy = tree_y_m - y_m
            limit = reach_m + radius_m
            if dx * dx + dy * dy > limit * limit:
                continue

            hit = circle_contact(body, dx * cos_h + dy * sin_h,
                                 -dx * sin_h + dy * cos_h, radius_m)
            if hit is None:
                continue

            px_m, py_m, nx, ny, depth_m, engulfed = hit
            apply(px_m, py_m, nx, ny, depth_m, "tree", index, engulfed)

        # --- 世界境界 ---
        #
        # 4隅のうち**最も外へ出ている角**で判定する。重心1点で見ると、
        # 斜めを向いた車が角から先に境界を越えても気づけない。
        for side, (nx_world, ny_world) in enumerate(
                ((1.0, 0.0), (-1.0, 0.0), (0.0, 1.0), (0.0, -1.0))):
            limit_m = (self.x0_m, self.x1_m, self.y0_m, self.y1_m)[side]

            worst_depth_m = 0.0
            worst_corner = (0.0, 0.0)
            for corner_x_m, corner_y_m in body.corners():
                world_x_m = x_m + corner_x_m * cos_h - corner_y_m * sin_h
                world_y_m = y_m + corner_x_m * sin_h + corner_y_m * cos_h
                # 法線は「外 -> 内」向きなので、限界からの超過量は
                # 内向き法線と逆向きに測る
                position_m = world_x_m if side < 2 else world_y_m
                normal = nx_world if side < 2 else ny_world
                depth_m = (limit_m - position_m) * normal
                if depth_m > worst_depth_m:
                    worst_depth_m = depth_m
                    worst_corner = (corner_x_m, corner_y_m)

            if worst_depth_m <= 0.0:
                continue

            # 世界の法線を車体固定系へ
            nx = nx_world * cos_h + ny_world * sin_h
            ny = -nx_world * sin_h + ny_world * cos_h
            apply(worst_corner[0], worst_corner[1], nx, ny, worst_depth_m,
                  "boundary", side, False)

        if not contacts:
            # **何にも触れていない。状態を作り直さずそのまま返す。**
            # 作り直すと float の再代入で値は同じでも「変えていない」
            # ことの保証が弱くなる。
            return state, contacts

        return replace(
            state,
            vx_mps=vx_mps,
            vy_mps=vy_mps,
            yaw_rate_rads=yaw_rate_rads,
            x_m=x_m,
            y_m=y_m,
        ), contacts
