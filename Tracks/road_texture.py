"""路面の白線・ひび割れ・補修跡を手続き的に描く。

    python Tracks/road_texture.py

`Tracks/Export/Textures/` に PNG を書き出す。

## なぜ画像を作るのか

路面は PolyHaven のアスファルト1枚だけを貼っていて、**白線もひび割れも
無い灰色の帯**だった。テクスチャを探して足すこともできるが、

- 白線は**コース幅に合っていなければ意味がない。** 汎用のテクスチャでは
  幅 12 m のどこに引くかを決められない
- ひび割れ・補修跡の入った素材はライセンスの管理が増える
  （`Docs/PHASE15_DATA_LICENCE.md` と同じ話）

**幅方向の UV（0..1 がコース幅）に合わせて自分で描けば、両方とも解ける。**
`Audio/synth.py` が音を合成するのと同じ考え方。

## 出力

| ファイル | 内容 |
|---|---|
| `road_overlay_diff.png` | 上に載せる色。白線は白、ひび割れと補修跡は暗い |
| `road_overlay_mask.png` | どれだけ載せるか。0 = アスファルトのまま |
| `road_overlay_rough.png` | ラフネスの差。白線は滑らか、ひび割れは粗い |
| `kerb_diff.png` | 縁石の赤白の縞 |
| `kerb_rough.png` | 縁石のラフネス。塗料は滑らか、縁は粗い |

マテリアル側は `lerp(アスファルト, overlay, mask)` で合成する。
**上書きではなく混ぜる**ので、アスファルトの質感が下に残る。
縁石は下地が無いので、そのまま貼る。

## 座標

画像の **x がコース幅方向**（0 = 左端、1 = 右端）、**y が進行方向**。
`Blender/build_track.py` が第2 UV（UVMap2）をこの向きで作る。
**ここを取り違えると、白線が横向きに走る。**
"""

from __future__ import annotations

import argparse
import math
from pathlib import Path

import numpy as np
from PIL import Image

from kerb import KERB_STRIPES_PER_TILE, KERB_TILE_LENGTH_M

OUT_DIR = Path(__file__).resolve().parent / "Export" / "Textures"

#: 画像の大きさ。幅方向は細かくなくてよい（白線の縁が見える程度）。
WIDTH_PX = 256
HEIGHT_PX = 1024

#: このテクスチャ1枚が進行方向に何メートル分か。
#: **破線の間隔がこれで決まる。** 実際の道路の白線は 8 m 前後の周期。
TILE_LENGTH_M = 24.0

#: 乱数の種。**固定する。** 同じコードから毎回同じ路面が出ないと、
#: 「見た目が変わった」のが変更のせいか偶然かを切り分けられない。
SEED = 20260901


def _u_axis() -> np.ndarray:
    """幅方向 0..1。画素の中心を取る。"""
    return (np.arange(WIDTH_PX) + 0.5) / WIDTH_PX


def _v_axis() -> np.ndarray:
    return (np.arange(HEIGHT_PX) + 0.5) / HEIGHT_PX


def _band(u: np.ndarray, centre: float, half_width: float,
          softness: float = 0.004) -> np.ndarray:
    """`centre` を中心とした帯。縁を少しぼかす。

    **完全な矩形にしない。** 縁が立っていると、斜めから見たときに
    階段状に見える（ミップマップでも消えない）。
    """
    distance = np.abs(u - centre)
    return np.clip((half_width + softness - distance) / max(softness, 1e-6), 0.0, 1.0)


def build_markings() -> tuple:
    """白線を描く。**幅方向の位置はコース幅に対する比で決める。**

    @return (色, 量) それぞれ (H, W) の float 配列
    """
    u = _u_axis()[None, :]
    v = _v_axis()[:, None]

    # --- 外側の白線（実線）---
    #
    # 端そのものではなく少し内側に引く。端に引くと、舗装の切り口と
    # 重なって線が痩せて見える。
    edge = _band(u, 0.030, 0.011) + _band(u, 0.970, 0.011)

    # --- センターライン（破線）---
    #
    # 実際の道路と同じく、線と間隔の比はおよそ 1:2。
    dashes_per_tile = 3.0
    phase = np.mod(v * dashes_per_tile, 1.0)
    dash_on = np.clip((0.34 - phase) / 0.03, 0.0, 1.0) * np.clip(phase / 0.03, 0.0, 1.0)
    centre = _band(u, 0.5, 0.010) * dash_on

    amount = np.clip(edge + centre, 0.0, 1.0)
    return amount


def build_cracks(rng: np.random.Generator) -> np.ndarray:
    """ひび割れ。**細く、曲がり、枝分かれする。**

    直線を引くと「線を引いた」ようにしか見えない。進行方向へ流れる
    ゆるい曲線を何本か置き、そこから短い枝を出す。
    """
    field = np.zeros((HEIGHT_PX, WIDTH_PX), dtype=np.float64)

    def draw(u0: float, v0: float, length: float, drift: float, weight: float):
        steps = int(length * HEIGHT_PX)
        if steps <= 1:
            return
        u_pos = u0
        wander = 0.0
        for step in range(steps):
            v_index = int((v0 * HEIGHT_PX + step)) % HEIGHT_PX
            wander += rng.normal(0.0, drift)
            wander *= 0.92                      # 戻す力。無いと画面外へ出ていく
            u_pos = np.clip(u_pos + wander, 0.02, 0.98)
            x = int(u_pos * WIDTH_PX)
            # 太さ 1〜2 画素。**太いとひびに見えない。**
            field[v_index, x] = max(field[v_index, x], weight)
            if x + 1 < WIDTH_PX:
                field[v_index, x + 1] = max(field[v_index, x + 1], weight * 0.45)

    # 縦に流れる主なひび
    for _ in range(7):
        draw(rng.uniform(0.08, 0.92), rng.uniform(0.0, 1.0),
             rng.uniform(0.15, 0.55), 0.0016, rng.uniform(0.55, 1.0))

    # 短い枝
    for _ in range(22):
        draw(rng.uniform(0.05, 0.95), rng.uniform(0.0, 1.0),
             rng.uniform(0.02, 0.08), 0.004, rng.uniform(0.3, 0.7))

    return np.clip(field, 0.0, 1.0)


def build_patches(rng: np.random.Generator) -> np.ndarray:
    """補修跡。**周りより少し暗く、境界がはっきりした四角い領域。**

    実際の道路は切り取って詰め直すので、角のある形になる。
    """
    field = np.zeros((HEIGHT_PX, WIDTH_PX), dtype=np.float64)
    for _ in range(5):
        w = rng.integers(24, 70)
        h = rng.integers(40, 180)
        x = rng.integers(0, WIDTH_PX - w)
        y = rng.integers(0, HEIGHT_PX - h)
        field[y:y + h, x:x + w] = rng.uniform(0.35, 0.7)
    return field


def build_grain(rng: np.random.Generator,
                width_px: int = WIDTH_PX, height_px: int = HEIGHT_PX) -> np.ndarray:
    """細かいざらつき。**一様なノイズではなく、いくつかの粗さを重ねる。**"""
    total = np.zeros((height_px, width_px), dtype=np.float64)
    weight = 0.0
    for scale, amount in ((4, 1.0), (16, 0.5), (64, 0.25)):
        small = rng.random((max(height_px // scale, 2), max(width_px // scale, 2)))
        image = Image.fromarray((small * 255).astype(np.uint8)).resize(
            (width_px, height_px), Image.BILINEAR)
        total += np.asarray(image, dtype=np.float64) / 255.0 * amount
        weight += amount
    return total / weight


# --- 縁石 -------------------------------------------------------------------
#
# 白線と同じ枠組みに乗せる。**画像の x が縁石を横切る向き**（0 = 路面側、
# 1 = 外側）、**y が進行方向**。`Blender/build_track.py` の `build_kerbs()` が
# UV をこの向きで作る。**取り違えると縞が縦に走る**（進行方向に赤白が
# 分かれた、道路にあり得ない絵になる）。

#: 縁石テクスチャの画素数。**細かくしても情報が増えない。**
#: 描いているのは縞と汚れだけで、写真ではない。
KERB_WIDTH_PX = 64
KERB_HEIGHT_PX = 512

#: 赤と白。**彩度を上げすぎない。** 屋外で日に焼けた塗料は
#: 純色ではなく、真っ赤にすると縁石だけが画面から浮く。
KERB_RED = (0.55, 0.11, 0.10)
KERB_WHITE = (0.84, 0.83, 0.79)


def build_kerb(rng: np.random.Generator) -> dict:
    """縁石の赤白の縞を描く。

    縞は**進行方向に並ぶ**（1 ブロックが `KERB_TILE_LENGTH_M /
    KERB_STRIPES_PER_TILE` メートル）。横切る向きには色を変えない。

    @return {"kerb_diff": (H, W, 3), "kerb_rough": (H, W)}
    """
    u = ((np.arange(KERB_WIDTH_PX) + 0.5) / KERB_WIDTH_PX)[None, :]
    v = ((np.arange(KERB_HEIGHT_PX) + 0.5) / KERB_HEIGHT_PX)[:, None]

    # --- 縞 ---
    #
    # **縁を少しぼかす。** 完全な矩形にすると、斜めから見たときに
    # 境界が階段状に見える（白線と同じ話）。
    phase = np.mod(v * KERB_STRIPES_PER_TILE, 1.0)
    # 1 ブロックは KERB_HEIGHT_PX / KERB_STRIPES_PER_TILE 画素。境界を 2 画素で渡す。
    softness = 2.0 * KERB_STRIPES_PER_TILE / KERB_HEIGHT_PX
    red = np.clip((0.5 - phase) / softness, 0.0, 1.0) \
        * np.clip(phase / softness, 0.0, 1.0)
    red = np.broadcast_to(red, (KERB_HEIGHT_PX, KERB_WIDTH_PX))

    colour = (np.asarray(KERB_WHITE)[None, None, :] * (1.0 - red[..., None])
              + np.asarray(KERB_RED)[None, None, :] * red[..., None])

    # --- 汚れ ---
    #
    # **路面側が汚い。** タイヤが乗る側にゴムと砂が溜まる。
    # 一様に汚すと「灰色がかった縞」にしかならない。
    road_side = np.clip(1.0 - u / 0.35, 0.0, 1.0)
    grime = build_grain(rng, KERB_WIDTH_PX, KERB_HEIGHT_PX)
    dirt = np.clip(road_side * 0.45 + (grime - 0.5) * 0.30, 0.0, 1.0)
    colour = colour * (1.0 - dirt[..., None] * 0.55)

    # --- 外側の縁 ---
    #
    # 一番外の 1 割は、外側の垂直面が使う列（`build_kerbs()` は u=1 を
    # 割り当てる）。**少し暗くする。** 上面と同じ明るさだと角が消える。
    edge = np.clip((u - 0.90) / 0.10, 0.0, 1.0)
    colour = colour * (1.0 - np.broadcast_to(edge, red.shape)[..., None] * 0.30)

    # --- ラフネス ---
    #
    # 塗料は滑らか、汚れた所と外側の縁は粗い。
    rough = np.full((KERB_HEIGHT_PX, KERB_WIDTH_PX), 0.45, dtype=np.float64)
    rough = rough + dirt * 0.35 + np.broadcast_to(edge, red.shape) * 0.15
    rough = np.clip(rough + (grime - 0.5) * 0.08, 0.0, 1.0)

    return {"kerb_diff": np.clip(colour, 0.0, 1.0), "kerb_rough": rough}


def build() -> dict:
    rng = np.random.default_rng(SEED)

    markings = build_markings()
    cracks = build_cracks(rng)
    patches = build_patches(rng)
    grain = build_grain(rng)

    # --- 色 ---
    #
    # 下地はアスファルトが透ける中間色。**ここを真っ黒にしない。**
    # mask が 0 でない場所でアスファルトが沈む。
    colour = np.full((HEIGHT_PX, WIDTH_PX, 3), 0.42, dtype=np.float64)

    # 補修跡は少し暗く、青みを落とす（新しい舗装は黒っぽい）
    colour[..., 0] = np.where(patches > 0.0, 0.26, colour[..., 0])
    colour[..., 1] = np.where(patches > 0.0, 0.25, colour[..., 1])
    colour[..., 2] = np.where(patches > 0.0, 0.24, colour[..., 2])

    # ひび割れは暗い
    colour *= (1.0 - cracks[..., None] * 0.82)

    # 白線。**最後に載せる。** 先に載せるとひび割れで削れてしまう
    # （実際の白線もひびで割れるが、そこまでやると読みにくくなる）。
    colour = colour * (1.0 - markings[..., None]) + markings[..., None] * 0.93

    # --- どれだけ載せるか ---
    #
    # 白線とひび割れははっきり、補修跡とざらつきは薄く。
    mask = np.clip(markings * 1.0
                   + cracks * 0.85
                   + patches * 0.45
                   + (grain - 0.5) * 0.10, 0.0, 1.0)

    # --- ラフネス ---
    #
    # 白線は塗料なので滑らか、ひび割れと補修跡は粗い。
    rough = np.full((HEIGHT_PX, WIDTH_PX), 0.5, dtype=np.float64)
    rough = rough + cracks * 0.35 + patches * 0.15 - markings * 0.30
    rough = np.clip(rough + (grain - 0.5) * 0.08, 0.0, 1.0)

    return {"road_overlay_diff": colour,
            "road_overlay_mask": mask,
            "road_overlay_rough": rough}


def save(images: dict, out_dir: Path = OUT_DIR) -> list:
    out_dir.mkdir(parents=True, exist_ok=True)
    written = []
    for name, data in images.items():
        if data.ndim == 2:
            array = (np.clip(data, 0.0, 1.0) * 255.0).astype(np.uint8)
            image = Image.fromarray(array, mode="L")
        else:
            array = (np.clip(data, 0.0, 1.0) * 255.0).astype(np.uint8)
            image = Image.fromarray(array, mode="RGB")
        path = out_dir / (name + ".png")
        image.save(path)
        written.append(path)
    return written


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true",
                        help="書き出さず、性質だけ調べる")
    args = parser.parse_args()

    images = build()
    images.update(build_kerb(np.random.default_rng(SEED + 1)))

    # **描けているかを数値で確かめる。** 真っ白や真っ黒を書き出して
    # 「生成した」と言わないため。
    markings_area = float((images["road_overlay_mask"] > 0.9).mean())
    print("白線とひびが占める面積: {:.3%}".format(markings_area))
    if markings_area < 0.002 or markings_area > 0.25:
        print("!! 面積が想定外。白線が消えているか、塗り潰している", flush=True)
        return 1

    # 縁石も同じく数値で見る。**赤と白の両方が要る。**
    # 片方が消えても「縞のテクスチャを書き出した」とは言える状態になるので、
    # 面積で止める（憲法ルール6）。
    kerb = images["kerb_diff"]
    reddish = float(((kerb[..., 0] - kerb[..., 2]) > 0.15).mean())
    whitish = float((kerb.min(axis=2) > 0.45).mean())
    print("縁石 赤: {:.1%} / 白: {:.1%}".format(reddish, whitish))
    if reddish < 0.25 or whitish < 0.25:
        print("!! 縁石の縞が片方しか無い（赤 {:.1%} / 白 {:.1%}）"
              .format(reddish, whitish), flush=True)
        return 1

    if args.check:
        return 0

    for path in save(images):
        print("書き出した: {}".format(path.name))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
