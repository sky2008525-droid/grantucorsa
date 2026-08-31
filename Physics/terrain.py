"""地形の高さ場。接地位置と斜面方向の重力を求める.

## なぜ描画メッシュから読まないのか

憲法ルール4「物理計算と表示用3Dモデルを完全に分離する」。
地面メッシュ（`Tracks/Export/TrackGround.fbx`）の頂点を物理が読むと、
描画の都合（LOD、Nanite、簡略化）が物理に混入する。

`Blender/build_track.py` が地面メッシュと**同じ値**から
`Tracks/Export/heightfield.json` を書き出す。物理も描画もこれを読む。

## この地形が物理に与えるもの／与えないもの

**与える:**

  - 接地面の高さ（車体がどの高さにあるか）
  - 接地面の傾き（斜面方向の重力成分。上り坂で減速する）
  - 法線方向の荷重 mg*cos(傾き)

**与えない:**

  - サスペンションの伸縮とその動特性

`suspension.damper_front` / `damper_rear` は `"unknown"` であり、
モーションレシオも未知（`spring_rate_front` の WARNING）。**バネ上の
上下振動は組めない。** ここで扱うのは剛体が地面に沿うことだけ。
データが揃ったら Level 1 へ拡張する（issue #19）。
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Tuple

from units import GRAVITY_MPS2


class Heightfield:
    """等間隔格子の高さ場。双線形補間で高さと法線を返す。"""

    def __init__(self, path) -> None:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)

        self.x0_m = float(data["x0_m"])
        self.y0_m = float(data["y0_m"])
        self.cell_m = float(data["cell_m"])
        self.nx = int(data["nx"])
        self.ny = int(data["ny"])
        self.heights = data["heights_m"]        # [iy][ix]

        if self.cell_m <= 0.0 or self.nx < 2 or self.ny < 2:
            raise ValueError("高さ場の格子が不正: cell=%s nx=%s ny=%s"
                             % (self.cell_m, self.nx, self.ny))
        if len(self.heights) != self.ny or len(self.heights[0]) != self.nx:
            raise ValueError("高さ場の大きさが宣言と違う")

        self.x1_m = self.x0_m + (self.nx - 1) * self.cell_m
        self.y1_m = self.y0_m + (self.ny - 1) * self.cell_m

    @classmethod
    def from_export(cls, repo_root) -> "Heightfield":
        return cls(Path(repo_root) / "Tracks" / "Export" / "heightfield.json")

    # --- 標本 -------------------------------------------------------------

    def _clamped_index(self, value, origin, count):
        """格子添字と補間係数。**範囲外は端で頭打ちにする。**

        地形の外へ出た車を落とさない。端の高さがそのまま続くとみなす。
        """
        raw = (value - origin) / self.cell_m
        index = int(math.floor(raw))
        if index < 0:
            return 0, 0.0
        if index >= count - 1:
            return count - 2, 1.0
        return index, raw - index

    def height_at(self, x_m: float, y_m: float) -> float:
        """(x, y) の地面高さ [m]。双線形補間。"""
        ix, fx = self._clamped_index(x_m, self.x0_m, self.nx)
        iy, fy = self._clamped_index(y_m, self.y0_m, self.ny)

        h00 = self.heights[iy][ix]
        h10 = self.heights[iy][ix + 1]
        h01 = self.heights[iy + 1][ix]
        h11 = self.heights[iy + 1][ix + 1]

        lower = h00 + (h10 - h00) * fx
        upper = h01 + (h11 - h01) * fx
        return lower + (upper - lower) * fy

    def slope_at(self, x_m: float, y_m: float) -> Tuple[float, float]:
        """(dz/dx, dz/dy)。中心差分。"""
        step = self.cell_m
        dzdx = (self.height_at(x_m + step, y_m) - self.height_at(x_m - step, y_m)) / (2.0 * step)
        dzdy = (self.height_at(x_m, y_m + step) - self.height_at(x_m, y_m - step)) / (2.0 * step)
        return dzdx, dzdy

    def normal_at(self, x_m: float, y_m: float) -> Tuple[float, float, float]:
        """地面の単位法線（世界座標）。"""
        dzdx, dzdy = self.slope_at(x_m, y_m)
        length = math.sqrt(dzdx * dzdx + dzdy * dzdy + 1.0)
        return -dzdx / length, -dzdy / length, 1.0 / length


def body_gravity(dzdx: float, dzdy: float, heading_rad: float
                 ) -> Tuple[float, float, float]:
    """斜面が車体に与える重力成分と法線荷重の係数を返す。

    戻り値: (前後 [m/s^2], 左右 [m/s^2], 法線係数)

    ## 導出

    車体の前後軸・左右軸は**接平面の中にある**（車が地面に沿っているため）。
    したがって重力 (0,0,-g) をその軸へ直接射影すればよい。

        h = (cos psi, sin psi, 0)          水平面での進行方向
        f = normalise(h - (h.n) n)         接平面へ落とした前後軸
        l = n x f                          左方向（右手系 f x l = n）

        slope_gx = (0,0,-g) . f = -g * f_z
        slope_gy = (0,0,-g) . l = -g * l_z

    **接平面成分の水平投影を取ってはいけない。** 最初それで書いたが、
    傾きが大きいと成分を合成しても g に戻らない（テストで検出した）。
    車体の軸は水平ではないので、水平投影は軸への射影と一致しない。

    法線係数は n_z で、**これを静的荷重に掛ける**（斜面では垂直荷重が
    mg*cos(傾き) になる）。

    **符号を推測で書かないこと。** 下り坂（前方が低い = dz/dx < 0）で
    前向きに加速するのが正しい。Tests/test_terrain.py で検査している。
    """
    length = math.sqrt(dzdx * dzdx + dzdy * dzdy + 1.0)
    nx, ny, nz = -dzdx / length, -dzdy / length, 1.0 / length

    # 水平面での進行方向を接平面へ落とす
    hx, hy = math.cos(heading_rad), math.sin(heading_rad)
    dot = hx * nx + hy * ny
    fx, fy, fz = hx - dot * nx, hy - dot * ny, -dot * nz
    norm = math.sqrt(fx * fx + fy * fy + fz * fz)
    if norm < 1e-12:
        # 前後軸が法線と平行（垂直な壁）。**黙って 0 を返さず平地扱いにする。**
        return 0.0, 0.0, nz
    fx, fy, fz = fx / norm, fy / norm, fz / norm

    # 左方向 l = n x f
    lz = nx * fy - ny * fx

    return -GRAVITY_MPS2 * fz, -GRAVITY_MPS2 * lz, nz
