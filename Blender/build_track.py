"""コース中心線から路面・地面メッシュと樹木の配置を生成する（Blender headless）.

    blender --background --python Blender/build_track.py -- \
        <track.json> <out_dir>

**形状をここで定義しないこと。** 中心線は `Tracks/physics_test_track.py`
が生成し、`Tools/export_track.py` が JSON にしたものだけを読む。
描画側が独自にコースを持つと、物理と絵がずれる。

## 起伏について（**この節は 2026-09-02 に書き換えた**）

**以前ここには「起伏を走行面に入れてはいけない」と書いてあった。**
その理由は「物理が平面3自由度で、上下方向の動特性を持たないから」
というもので、当時は正しかった。

**今は違う。** 次の3つが揃っている:

  - `Physics/terrain.py` の `Heightfield` が4輪それぞれの下の地面を返す
  - `body_gravity()` が斜面の重力を車体座標へ分解する
  - `Physics/ride.py` が heave / pitch / roll を解き、浮いた輪の
    接地力を 0 にする

つまり**坂は物理として扱える。** 縦断（どこが上りでどこが下りか）は
`Tracks/elevation.py` が持ち、`Tools/export_track.py` が各点の `z_m` と
して配る。ここはそれを読むだけで、**形状をここで決めない。**

古い注記を残したまま「峠」を平地で作っていたので、**峠が峠に見えない**
という指摘を受けた。制約が消えたら注記も消すこと。

### 高架（`is_viaduct`）

都市高速の高架は、**桁が地面から離れて浮いている。** 峠のように
「路面が上がれば周りの地面も上がる」のではない。したがって:

  - 走行面は縦断のとおりの高さ
  - 地面（見た目）は下のまま。その間に橋脚と桁を立てる
  - **高さ場は桁に追従させる**（車は桁の上を走るので）。桁の外側では
    下の地面まで落とす。**落ちるのは正しい挙動**である
"""

from __future__ import annotations

import json
import math
import os
import random
import sys
import time

import bpy
import bmesh
from mathutils import Vector, noise

# **縁石の敷き方は `Tracks/kerb.py` が決める。** 定数を 3 箇所（ここ・
# road_texture.py・テスト）に書き分けると、片方だけ直したときに黙って
# ずれる。`Tracks/` を import できるようにする。
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "Tracks"))
from kerb import (KERB_HEIGHT_M, KERB_RISE_M, KERB_TILE_LENGTH_M,  # noqa: E402
                  KERB_WIDTH_M, corner_exit_indices, kerb_spans)
from environment import (all_prop_kinds, all_species,  # noqa: E402
                         environment_for)
from pit import (PIT_LANE_WIDTH_M, PIT_WALL_HEIGHT_M,  # noqa: E402
                 PIT_WALL_THICKNESS_M, garage_positions, plan_pit_lane)

# 路面の外側に付ける起伏の大きさ [m] と横方向の波長 [m]。
# **控えめにする。** 平坦な走行面から急に山が立ち上がると不自然。
RELIEF_AMPLITUDE_M = 7.0
RELIEF_WAVELENGTH_M = 140.0

# 地面グリッドの解像度 [m] と、コース外周に取る余白 [m]
GROUND_CELL_M = 4.0
GROUND_MARGIN_M = 420.0

# **車が到達しうる範囲は完全に平ら**にする距離 [m]（コースの外接矩形から）。
#
# 以前は中心線から 12 m で起伏を立ち上げていた。**コースアウトすると
# すぐ丘に乗り上げ、車が地面から浮いた場所を走る**（物理は z=0 の平面を
# 走り続けるため）。実際にその状態になった。
#
# 物理が平面3自由度である以上、**車が行ける場所はすべて平面でなければ
# ならない。** 起伏は遠景としてのみ置く。
DRIVABLE_FLAT_MARGIN_M = 120.0

# 起伏が立ち上がりきるまでの距離 [m]
RELIEF_BLEND_M = 200.0

# 地面の高さを決めるとき、中心線を何 m ごとに標本化するか。
# **細かくしても地面の解像度（4 m）以上には効かない。**
GROUND_HEIGHT_SAMPLE_M = 8.0

# 地面の高さを「近くの中心線」から作るときの効く距離 [m]。
#
# **最近傍の標高をそのまま使わない。** ヘアピンでは往路と復路が
# 20 m と離れずに並び、標高が 10 m 違うことがある。最近傍だと
# その中間に垂直な崖が立つ。距離で重み付けして混ぜれば斜面になる。
GROUND_HEIGHT_BLEND_M = 30.0

# 路面のそばで「いちばん近い点の標高」をそのまま使う幅 [m]（路面端から）。
#
# **ここを平均で作ると路面が埋まる。** 勾配 10% の区間では、30 m 先の
# 標高を混ぜるだけで 1〜3 m ずれる。
GROUND_ROAD_CORRIDOR_M = 14.0

# そこから平均へ寄せきるまでの距離 [m]。
GROUND_ROAD_BLEND_M = 26.0

# 高架の桁が路面の外へ何 m 張り出しているか [m]（片側）。
# **路肩ぶん。** ここまでは路面と同じ高さで、車が乗っていられる。
VIADUCT_DECK_SHOULDER_M = 2.5

# 桁の縁から下の地面まで落ちきる距離 [m]。
# **短くする。** 長いと高架の縁ではなく土手に見える。
VIADUCT_EDGE_DROP_M = 3.0

# 地面を路面より何 m 下げるか。
#
# **0 にしてはいけない。** 走行面付近では地面も z=0 なので、路面メッシュと
# 完全に同一平面になり、Z ファイティング（描画のちらつき）が出る。
# 実際の道路も路肩より数 cm 高いので、下げること自体は不自然ではない。
GROUND_SINK_M = 0.05

#: 路面の厚み [m]。**上面は z=0 のまま、下へ押し出す。**
#:
#: これが無いと路面は厚さゼロのリボンで、低い視点から見たときに
#: 「草地に貼った帯」にしか見えない。舗装の切り口が見えると立体になる。
#:
#: **上面を動かさないこと。** 物理は z=0 の平面を走る（このファイルの
#: 冒頭の注記）。下へ伸ばすぶんには走行面に影響しない。
#: 地面は GROUND_SINK_M だけ沈めてあるので、実際に見える段差は
#: 5cm だが、切り口があるだけで印象が変わる。
ROAD_THICKNESS_M = 0.14

#: 白線・ひび割れのテクスチャ1枚が進行方向に何メートル分か。
#:
#: **`Tracks/road_texture.py` の TILE_LENGTH_M と一致させること。**
#: ずれると破線の間隔が設計と変わる。
ROAD_MARKING_REPEAT_M = 24.0

# 路面テクスチャの繰り返し間隔 [m]（UV の V 方向）
ROAD_UV_REPEAT_M = 8.0

# 樹木の配置密度: 中心線に沿って何 m ごとに1本置くか（左右それぞれ）。
# **木は 18〜70 m の幅に散らばるので、間隔をコース沿いの見た目の密度と
# 同一視しないこと。** 11 m にしたときは 195 本で、並木としては疎かった。
TREE_SPACING_M = 4.0

RANDOM_SEED = 20260831


def log(fmt, *args):
    print(("[track] " + fmt) % args)


def clear_scene():
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete(use_global=False)
    for block in (bpy.data.meshes, bpy.data.objects):
        for item in list(block):
            try:
                block.remove(item)
            except (RuntimeError, ReferenceError):
                pass


def build_road(points, width_m):
    """中心線の左右に幅を振って路面のリボンを作る。

    UV は U が幅方向 0..1、V が距離 s をテクスチャ間隔で割ったもの。
    **V を頂点番号ではなく実距離から作ること。** 番号だと点間隔が
    変わったときにテクスチャの伸びが変わる。
    """
    half = width_m / 2.0
    mesh = bpy.data.meshes.new("TrackRoad")
    bm = bmesh.new()

    # UV0: アスファルト。**実寸でタイリングする。**
    #
    # 以前は U を 0..1 にしていたので、1枚のアスファルトが幅 12 m 全体に
    # 引き伸ばされ、**のっぺりした灰色の帯**になっていた。
    uv_layer = bm.loops.layers.uv.new("UVMap")

    # UV1: 白線・ひび割れ。**U が 0..1 でコース幅**。
    # 幅に対する比で位置を決めたいので、こちらは実寸にしない。
    mark_layer = bm.loops.layers.uv.new("UVMap2")

    # 上面（走行面）と、その真下の縁。
    #
    # **高さは中心線の `z_m` をそのまま使う。** 左右の端も同じ高さに
    # 置く（横断勾配＝バンクは付けていない。付けるならタイヤ荷重にも
    # 効く量なので、絵だけで入れてはいけない）。
    rows = []
    for p in points:
        heading = p["heading_rad"]
        z = p.get("z_m", 0.0)
        # 左手側の法線（物理の座標系で Y が左）
        nx = -math.sin(heading)
        ny = math.cos(heading)
        lx, ly = p["x_m"] + nx * half, p["y_m"] + ny * half
        rx, ry = p["x_m"] - nx * half, p["y_m"] - ny * half

        left = bm.verts.new((lx, ly, z))
        right = bm.verts.new((rx, ry, z))
        # **下へ押し出す。** 上面は動かさない（そこが走行面）。
        left_low = bm.verts.new((lx, ly, z - ROAD_THICKNESS_M))
        right_low = bm.verts.new((rx, ry, z - ROAD_THICKNESS_M))
        rows.append((left, right, left_low, right_low, p["s_m"]))

    bm.verts.ensure_lookup_table()

    count = len(rows)
    for index in range(count):
        left_a, right_a, left_low_a, right_low_a, s_a = rows[index]
        # **最後の点は先頭へ繋ぐ。** 閉じた周回なので、ここを繋がないと
        # スタートラインに幅 1 m の隙間が開く。
        left_b, right_b, left_low_b, right_low_b, s_b = rows[(index + 1) % count]
        if index + 1 == count:
            s_b = s_a + (rows[1][4] - rows[0][4])

        v_a = s_a / ROAD_UV_REPEAT_M
        v_b = s_b / ROAD_UV_REPEAT_M

        try:
            face = bm.faces.new((left_a, right_a, right_b, left_b))
        except ValueError:
            continue                      # 同一面の重複。閉合部で起こりうる

        mv_a = s_a / ROAD_MARKING_REPEAT_M
        mv_b = s_b / ROAD_MARKING_REPEAT_M

        for loop in face.loops:
            on_left = loop.vert in (left_a, left_b)
            on_first = loop.vert in (left_a, right_a)
            # アスファルトは実寸。幅方向も同じ間隔で繰り返す。
            loop[uv_layer].uv = ((-half if on_left else half) / ROAD_UV_REPEAT_M,
                                 v_a if on_first else v_b)
            # 白線は幅に対する比。
            loop[mark_layer].uv = (0.0 if on_left else 1.0,
                                   mv_a if on_first else mv_b)

        # --- 側面（舗装の切り口）---
        #
        # **法線が外を向くように巻く。** 逆に巻くと内側からしか見えず、
        # 「厚みを付けたのに何も変わらない」ように見える。
        for verts, u_base in (
                ((left_b, left_a, left_low_a, left_low_b), 0.0),
                ((right_a, right_b, right_low_b, right_low_a), 1.0)):
            try:
                side = bm.faces.new(verts)
            except ValueError:
                continue
            for loop in side.loops:
                # 側面は縦に細長い。U を厚み方向、V を距離にする。
                on_top = abs(loop.vert.co.z) < 1e-9
                on_first = loop.vert in (left_a, right_a, left_low_a, right_low_a)
                loop[uv_layer].uv = (
                    u_base + (0.0 if on_top else ROAD_THICKNESS_M / ROAD_UV_REPEAT_M),
                    v_a if on_first else v_b)
                # **側面に白線を出さない。** U=0.5 はセンターラインの位置
                # なので、そこへ置くと舗装の切り口に白い帯が走る。
                loop[mark_layer].uv = (0.20, mv_a if on_first else mv_b)

    bm.normal_update()
    bm.to_mesh(mesh)
    bm.free()

    obj = bpy.data.objects.new("TrackRoad", mesh)
    bpy.context.scene.collection.objects.link(obj)
    return obj


#: 縁石の断面。**(路面端からの横位置 [m], 高さ [m])** を外側へ並べたもの。
#:
#:      z
#:      ^      2______3 の上
#:      |     /|
#:   0__|__1/  |          0 : 路面端（z = 0。路面の上面と揃える）
#:   ===路面=  |          1 : 立ち上がりの頂点
#:            |3          2 : 上面の外端
#:                        3 : 外側の垂直面の下端（地面に隠れる）
#:
#: **上面を水平にする。** 実際の縁石は外へ向かって少し下がるものもあるが、
#: ここで傾けると `KERB_HEIGHT_M` が「どこの高さか」曖昧になる。
KERB_PROFILE = (
    (0.0,           0.0),
    (KERB_RISE_M,   KERB_HEIGHT_M),
    (KERB_WIDTH_M,  KERB_HEIGHT_M),
    (KERB_WIDTH_M, -ROAD_THICKNESS_M),
)


def build_kerbs(points, width_m, spacing_m):
    """コーナーの路面端に縁石を作る。**直線には作らない。**

    どの区間に敷くかは `Tracks/kerb.py` の `kerb_spans()` が決める
    （しきい値と、その値を採った理由もそこにある）。

    UV は **U が縁石を横切る向き 0..1、V が区間の先頭からの距離**を
    `KERB_TILE_LENGTH_M` で割ったもの。

    **V を通し距離 `s_m` から作らないこと。** 周回の閉合をまたぐ区間では
    s が全長から 0 へ飛ぶので、そこで縞が切れる。区間の先頭から測り直せば
    切れ目が出ないうえ、**どの縁石も赤で始まる**（実際の縁石もそう見える）。
    """
    half = width_m / 2.0
    spans = kerb_spans([p["curvature_1pm"] for p in points], spacing_m)

    mesh = bpy.data.meshes.new("TrackKerb")
    bm = bmesh.new()
    uv_layer = bm.loops.layers.uv.new("UVMap")

    # 断面の各点に割り当てる U。**外側の垂直面は上面の外端と同じ列を使う。**
    # 別の列にすると、角で色が変わって縞が途切れて見える。
    us = [min(lateral / KERB_WIDTH_M, 1.0) for lateral, _ in KERB_PROFILE]

    faces = 0
    for span in spans:
        for side in (+1.0, -1.0):
            rows = []
            travelled = 0.0
            previous = None
            for index in span:
                p = points[index]
                if previous is not None:
                    travelled += math.hypot(p["x_m"] - previous["x_m"],
                                            p["y_m"] - previous["y_m"])
                previous = p
                heading = p["heading_rad"]
                # 左手側の法線（物理の座標系で Y が左）に side を掛ける
                nx = -math.sin(heading) * side
                ny = math.cos(heading) * side
                road_z = p.get("z_m", 0.0)
                column = [bm.verts.new((p["x_m"] + nx * (half + lateral),
                                        p["y_m"] + ny * (half + lateral),
                                        road_z + dz))
                          for lateral, dz in KERB_PROFILE]
                rows.append((column, travelled))

            for row_index in range(len(rows) - 1):
                column_a, s_a = rows[row_index]
                column_b, s_b = rows[row_index + 1]
                v_a = s_a / KERB_TILE_LENGTH_M
                v_b = s_b / KERB_TILE_LENGTH_M

                for j in range(len(KERB_PROFILE) - 1):
                    # **巻き方で法線が決まる。** 進行方向 t と外向き法線 n は
                    # t × n = +z（左側）なので、この順で回すと上面・外面とも
                    # 外を向く。右側は n が反転するため、順を逆にする。
                    quad = (column_a[j], column_b[j],
                            column_b[j + 1], column_a[j + 1])
                    uvs = {column_a[j]: (us[j], v_a),
                           column_b[j]: (us[j], v_b),
                           column_b[j + 1]: (us[j + 1], v_b),
                           column_a[j + 1]: (us[j + 1], v_a)}
                    if side < 0.0:
                        quad = tuple(reversed(quad))
                    try:
                        face = bm.faces.new(quad)
                    except ValueError:
                        continue          # 同一面の重複（極端に詰まった点）
                    for loop in face.loops:
                        loop[uv_layer].uv = uvs[loop.vert]
                    faces += 1

    bm.verts.ensure_lookup_table()
    bm.normal_update()
    bm.to_mesh(mesh)
    bm.free()

    obj = bpy.data.objects.new("TrackKerb", mesh)
    bpy.context.scene.collection.objects.link(obj)
    return obj, spans, faces


def centreline_sampler(points, stride):
    """距離計算用に間引いた中心線。**全点使うと地面生成が現実的な時間で終わらない。**"""
    return [(p["x_m"], p["y_m"]) for p in points[::stride]]


def build_distant_terrain(points, extent, distant, base_z_m):
    """遠景の山並み。**物理の高さ場には入らない**（車が行けない）。

    近景の地面（4 m 格子）をそのまま 2.6 km 先まで伸ばすとセルが
    200 万個を超える。遠景は**別メッシュ・粗い格子**で作る。

    **1 枚の雑音では山並みにならない。** でこぼこした平原に見える。
    波長と振幅の違う層を重ねると、手前の尾根の向こうに次の尾根が
    見える形になる（`ridges`）。
    """
    x0, x1, y0, y1 = extent
    reach = distant.reach_m
    dx0, dx1 = x0 - reach, x1 + reach
    dy0, dy1 = y0 - reach, y1 + reach

    cell = distant.cell_m
    nx = int((dx1 - dx0) / cell) + 1
    ny = int((dy1 - dy0) / cell) + 1
    log("distant grid %d x %d (%.0f x %.0f m)", nx, ny, dx1 - dx0, dy1 - dy0)

    mesh = bpy.data.meshes.new("TrackDistant")
    bm = bmesh.new()
    uv_layer = bm.loops.layers.uv.new("UVMap")

    def height(x, y):
        # 近景の縁からどれだけ外れたか
        outside = max(x0 - x, 0.0, x - x1, y0 - y, 0.0, y - y1)
        if outside <= 0.0:
            return None                    # 近景の内側。ここには作らない
        t = min(outside / distant.blend_m, 1.0)
        mask = t * t * (3.0 - 2.0 * t)

        total = 0.0
        amplitude = distant.amplitude_m
        wavelength = distant.wavelength_m
        for ridge in range(distant.ridges):
            n = noise.noise(Vector((x / wavelength, y / wavelength,
                                    ridge * 11.7)))
            # **絶対値を取って尾根にする。** 素の雑音は丘の集まりで、
            # 山の稜線に見えない。1-|n| は谷が平らで尾根が立つ。
            total += (1.0 - abs(n)) * amplitude
            amplitude *= 0.55
            wavelength *= 0.42
        return base_z_m + distant.base_offset_m + total * mask

    grid = []
    for iy in range(ny):
        row = []
        y = dy0 + iy * cell
        for ix in range(nx):
            x = dx0 + ix * cell
            z = height(x, y)
            row.append(None if z is None else bm.verts.new((x, y, z)))
        grid.append(row)

    bm.verts.ensure_lookup_table()
    uv_scale = 1.0 / 180.0
    faces = 0
    for iy in range(ny - 1):
        for ix in range(nx - 1):
            verts = (grid[iy][ix], grid[iy][ix + 1],
                     grid[iy + 1][ix + 1], grid[iy + 1][ix])
            if any(v is None for v in verts):
                continue                   # 近景と重なる部分は作らない
            try:
                face = bm.faces.new(verts)
            except ValueError:
                continue
            for loop in face.loops:
                loop[uv_layer].uv = (loop.vert.co.x * uv_scale,
                                     loop.vert.co.y * uv_scale)
            faces += 1

    bm.normal_update()
    bm.to_mesh(mesh)
    bm.free()

    obj = bpy.data.objects.new("TrackDistant", mesh)
    bpy.context.scene.collection.objects.link(obj)
    return obj, faces


#: ガードレールの高さ [m]（路面から板の上端まで）。
#:
#: 日本のガードレールの設置高さはおおむね 0.6〜0.8 m の桁にある
#: （見た目で腰くらい）。**条文を確認していないので実測扱いにしない**
#: （憲法ルール2）。板の上下端で 1 枚に見せる。
GUARDRAIL_HEIGHT_M = 0.75
GUARDRAIL_PANEL_M = 0.32
GUARDRAIL_OFFSET_M = 1.4
GUARDRAIL_POST_SPACING_M = 4.0
GUARDRAIL_POST_WIDTH_M = 0.11


def build_guardrail(points, width_m):
    """路肩にガードレールを立てる。**峠に要る。**

    **手続きで作る。** これは「道路構造の一部」であって飾りではない。
    路面の形が決まればガードレールの形も決まるので、外から持ってくる
    モデルを並べるより、中心線から生成するほうが確実に沿う。

    支柱も立てる。板だけだと宙に浮いて見える。
    """
    half = width_m / 2.0
    mesh = bpy.data.meshes.new("TrackGuardrail")
    bm = bmesh.new()
    uv_layer = bm.loops.layers.uv.new("UVMap")

    spacing = points[1]["s_m"] - points[0]["s_m"]
    post_step = max(int(GUARDRAIL_POST_SPACING_M / spacing), 1)

    top = GUARDRAIL_HEIGHT_M
    bottom = GUARDRAIL_HEIGHT_M - GUARDRAIL_PANEL_M

    count = len(points)
    faces = 0
    for side in (+1.0, -1.0):
        previous = None
        for index in range(count + 1):
            p = points[index % count]
            heading = p["heading_rad"]
            z = p.get("z_m", 0.0)
            nx = -math.sin(heading) * side
            ny = math.cos(heading) * side
            lateral = half + GUARDRAIL_OFFSET_M
            x = p["x_m"] + nx * lateral
            y = p["y_m"] + ny * lateral

            current = (bm.verts.new((x, y, z + bottom)),
                       bm.verts.new((x, y, z + top)),
                       p["s_m"])
            if previous is not None:
                quad = (previous[0], previous[1], current[1], current[0])
                if side < 0.0:
                    quad = tuple(reversed(quad))
                try:
                    face = bm.faces.new(quad)
                except ValueError:
                    previous = current
                    continue
                for loop in face.loops:
                    along = (previous[2] if loop.vert in previous[:2]
                             else current[2]) / 3.0
                    high = loop.vert in (previous[1], current[1])
                    loop[uv_layer].uv = (along, 1.0 if high else 0.0)
                faces += 1
            previous = current

            # 支柱
            if index % post_step == 0 and index < count:
                w = GUARDRAIL_POST_WIDTH_M / 2.0
                tx, ty = math.cos(heading) * w, math.sin(heading) * w
                a = bm.verts.new((x - tx, y - ty, z))
                b = bm.verts.new((x + tx, y + ty, z))
                c = bm.verts.new((x + tx, y + ty, z + bottom))
                d = bm.verts.new((x - tx, y - ty, z + bottom))
                try:
                    face = bm.faces.new((a, b, c, d))
                    for loop in face.loops:
                        loop[uv_layer].uv = (0.0, 0.0)
                    faces += 1
                except ValueError:
                    pass

    bm.normal_update()
    bm.to_mesh(mesh)
    bm.free()

    obj = bpy.data.objects.new("TrackGuardrail", mesh)
    bpy.context.scene.collection.objects.link(obj)
    return obj, faces


#: 橋脚の間隔 [m]。都市高速の高架は 20〜40 m 間隔の径間が多い。
#: **実測ではなく、その桁として不自然でない値**（憲法ルール1）。
PIER_SPACING_M = 30.0
PIER_WIDTH_M = 2.2
PIER_DEPTH_M = 1.6

#: 遮音壁の高さ [m]（路面から）。**桁の上に立つ。**
NOISE_WALL_HEIGHT_M = 2.6
NOISE_WALL_OFFSET_M = 1.2


def build_viaduct(points, width_m, ground_level_m, piers=True, wall=True):
    """高架の橋脚と遮音壁を立てる。

    **「高さがある状態にしてください」への答えがここ。**
    桁（路面）を持ち上げただけでは、宙に浮いた帯にしか見えない。
    下に橋脚が立ち、上に壁があって初めて高架に見える。
    """
    half = width_m / 2.0
    mesh = bpy.data.meshes.new("TrackViaduct")
    bm = bmesh.new()
    uv_layer = bm.loops.layers.uv.new("UVMap")

    spacing = points[1]["s_m"] - points[0]["s_m"]
    pier_step = max(int(PIER_SPACING_M / spacing), 1)
    count = len(points)
    stats = {"faces": 0}

    def box(cx, cy, z_low, z_high, hx, hy, heading):
        cos_h, sin_h = math.cos(heading), math.sin(heading)

        def corner(u, v):
            return (cx + cos_h * u - sin_h * v, cy + sin_h * u + cos_h * v)

        pts = [corner(-hx, -hy), corner(hx, -hy), corner(hx, hy), corner(-hx, hy)]
        low = [bm.verts.new((x, y, z_low)) for x, y in pts]
        high = [bm.verts.new((x, y, z_high)) for x, y in pts]
        for i in range(4):
            j = (i + 1) % 4
            try:
                face = bm.faces.new((low[i], low[j], high[j], high[i]))
            except ValueError:
                continue
            for loop in face.loops:
                loop[uv_layer].uv = (0.0, 0.0)
            stats["faces"] += 1

    if piers:
        for index in range(0, count, pier_step):
            p = points[index]
            z = p.get("z_m", 0.0)
            # **桁の下面まで。** 路面の厚みぶん下げる。
            box(p["x_m"], p["y_m"], ground_level_m - 1.0, z - ROAD_THICKNESS_M,
                PIER_DEPTH_M / 2.0, PIER_WIDTH_M / 2.0, p["heading_rad"])

    if wall:
        for side in (+1.0, -1.0):
            previous = None
            for index in range(count + 1):
                p = points[index % count]
                heading = p["heading_rad"]
                z = p.get("z_m", 0.0)
                nx = -math.sin(heading) * side
                ny = math.cos(heading) * side
                lateral = half + NOISE_WALL_OFFSET_M
                x = p["x_m"] + nx * lateral
                y = p["y_m"] + ny * lateral
                current = (bm.verts.new((x, y, z)),
                           bm.verts.new((x, y, z + NOISE_WALL_HEIGHT_M)),
                           p["s_m"])
                if previous is not None:
                    quad = (previous[0], previous[1], current[1], current[0])
                    if side < 0.0:
                        quad = tuple(reversed(quad))
                    try:
                        face = bm.faces.new(quad)
                        for loop in face.loops:
                            along = (previous[2] if loop.vert in previous[:2]
                                     else current[2]) / 4.0
                            high = loop.vert in (previous[1], current[1])
                            loop[uv_layer].uv = (along, 1.0 if high else 0.0)
                        stats["faces"] += 1
                    except ValueError:
                        pass
                previous = current

    bm.normal_update()
    bm.to_mesh(mesh)
    bm.free()

    obj = bpy.data.objects.new("TrackViaduct", mesh)
    bpy.context.scene.collection.objects.link(obj)
    return obj, stats["faces"]


def build_sea(extent, distant, sea_level_m):
    """水面。**1 枚の板。**

    起伏を付けない。付けると遠景の地形と交差して、水面が斑に切れる。
    波は静止画では見えないうえ、動かすには物理と関係ない更新が要る。
    **見えるところだけを作る**（憲法ルール18: 演出）。
    """
    x0, x1, y0, y1 = extent
    reach = distant.reach_m if distant else 1500.0
    # **遠景より一回り広く取る。** 同じ大きさだと、水平線の手前で
    # 水面が切れて世界の縁が見える。
    margin = reach * 1.4
    sx0, sx1 = x0 - margin, x1 + margin
    sy0, sy1 = y0 - margin, y1 + margin

    mesh = bpy.data.meshes.new("TrackSea")
    bm = bmesh.new()
    uv_layer = bm.loops.layers.uv.new("UVMap")

    corners = [(sx0, sy0), (sx1, sy0), (sx1, sy1), (sx0, sy1)]
    verts = [bm.verts.new((x, y, sea_level_m)) for x, y in corners]
    face = bm.faces.new(verts)
    scale = 1.0 / 90.0
    for loop in face.loops:
        loop[uv_layer].uv = (loop.vert.co.x * scale, loop.vert.co.y * scale)

    bm.normal_update()
    bm.to_mesh(mesh)
    bm.free()

    obj = bpy.data.objects.new("TrackSea", mesh)
    bpy.context.scene.collection.objects.link(obj)
    return obj, 1


def build_pit(points, lane):
    """ピットレーンとピットウォールを作る。

    **ピットレーンは道路である。** コースの形が決まれば形が決まるので、
    外から持ってくるモデルより中心線から作るほうが確実に沿う
    （建屋は外部アセット。`Tracks/environment.py` の pit_building）。

    どこに引くかは `Tracks/pit.py` が決める。
    """
    mesh = bpy.data.meshes.new("TrackPit")
    bm = bmesh.new()
    uv_layer = bm.loops.layers.uv.new("UVMap")

    faces = 0
    rows = []
    for position, index in enumerate(lane.indices):
        p = points[index]
        heading = p["heading_rad"]
        z = p.get("z_m", 0.0)
        nx = -math.sin(heading) * lane.side
        ny = math.cos(heading) * lane.side

        centre = lane.offset_at(position)
        inner = centre - PIT_LANE_WIDTH_M / 2.0
        outer = centre + PIT_LANE_WIDTH_M / 2.0

        def at(lateral, height):
            return bm.verts.new((p["x_m"] + nx * lateral,
                                 p["y_m"] + ny * lateral, z + height))

        rows.append({
            "lane_in": at(inner, 0.0),
            "lane_out": at(outer, 0.0),
            "wall_low": at(inner - PIT_WALL_THICKNESS_M, 0.0),
            "wall_high": at(inner - PIT_WALL_THICKNESS_M, PIT_WALL_HEIGHT_M),
            "wall_top_in": at(inner, PIT_WALL_HEIGHT_M),
            "s": p["s_m"],
        })

    bm.verts.ensure_lookup_table()

    for i in range(len(rows) - 1):
        a, b = rows[i], rows[i + 1]
        va = a["s"] / ROAD_UV_REPEAT_M
        vb = b["s"] / ROAD_UV_REPEAT_M

        # 舗装面
        quad = (a["lane_in"], a["lane_out"], b["lane_out"], b["lane_in"])
        if lane.side < 0.0:
            quad = tuple(reversed(quad))
        try:
            face = bm.faces.new(quad)
            for loop in face.loops:
                on_out = loop.vert in (a["lane_out"], b["lane_out"])
                first = loop.vert in (a["lane_in"], a["lane_out"])
                loop[uv_layer].uv = (
                    (PIT_LANE_WIDTH_M / 2.0 if on_out else
                     -PIT_LANE_WIDTH_M / 2.0) / ROAD_UV_REPEAT_M,
                    va if first else vb)
            faces += 1
        except ValueError:
            pass

        # ピットウォール（本線側の面と天端）
        for pair, other in ((("wall_low", "wall_high"), False),
                            (("wall_high", "wall_top_in"), True)):
            quad = (a[pair[0]], a[pair[1]], b[pair[1]], b[pair[0]])
            if lane.side < 0.0:
                quad = tuple(reversed(quad))
            try:
                face = bm.faces.new(quad)
                for loop in face.loops:
                    loop[uv_layer].uv = (0.0 if other else 1.0, 0.0)
                faces += 1
            except ValueError:
                pass

    bm.normal_update()
    bm.to_mesh(mesh)
    bm.free()

    obj = bpy.data.objects.new("TrackPit", mesh)
    bpy.context.scene.collection.objects.link(obj)
    return obj, faces


def make_height_lookup(field):
    """高さ場から (x, y) -> z を引く関数を作る（双一次補間）。

    **樹木も小物もこれで地面に乗せる。** 乗せないと、峠では
    35 m 埋まるか浮くかする。以前は z=0 固定で、走行面が平らな
    あいだだけ正しかった。
    """
    x0 = field["x0_m"]
    y0 = field["y0_m"]
    cell = field["cell_m"]
    nx = field["nx"]
    ny = field["ny"]
    heights = field["heights_m"]

    def lookup(x, y):
        fx = (x - x0) / cell
        fy = (y - y0) / cell
        ix = int(math.floor(fx))
        iy = int(math.floor(fy))
        # **端は内側へ寄せる。** 外を引くと IndexError で落ちる。
        ix = max(0, min(ix, nx - 2))
        iy = max(0, min(iy, ny - 2))
        tx = min(max(fx - ix, 0.0), 1.0)
        ty = min(max(fy - iy, 0.0), 1.0)
        h00 = heights[iy][ix]
        h10 = heights[iy][ix + 1]
        h01 = heights[iy + 1][ix]
        h11 = heights[iy + 1][ix + 1]
        return ((h00 * (1.0 - tx) + h10 * tx) * (1.0 - ty)
                + (h01 * (1.0 - tx) + h11 * tx) * ty)

    return lookup


def centreline_sampler_3d(points, stride):
    """標高つきの間引き中心線。地面の高さを決めるのに使う。"""
    return [(p["x_m"], p["y_m"], p.get("z_m", 0.0)) for p in points[::stride]]


def distance_to_centreline(x, y, samples):
    best = 1e30
    for cx, cy in samples:
        d = (cx - x) ** 2 + (cy - y) ** 2
        if d < best:
            best = d
    return math.sqrt(best)


def build_ground(points, width_m, shoulder_m, elevation=None, env=None):
    """地面を作り、同時に**物理が読む高さ場**を返す。

    **地面は路面の高さに追従する。**

    以前はコース全体を z=0 の平面にしていた（物理が平面3自由度だった
    ころの名残）。縦断が入った今それをやると、峠の頂上で車が地面から
    35 m 浮く。追従させ方は 2 通りあり、コースの性格で使い分ける:

      **地続き**（峠・サーキット）
        路面が上がれば周りの地面も上がる。コースアウトしても
        地面は続いている。高さは**近くの中心線の標高を距離で重み付け
        して平均**する。最近傍の値をそのまま使うと、ヘアピンの内側で
        高さが不連続になり、崖が立つ。

      **高架**（都市高速）
        桁が地面から離れて浮いている。周りの地面は下のまま。
        高さ場は**桁に追従**させ（車は桁の上を走る）、桁の外側では
        下の地面まで落とす。**落ちるのは正しい挙動**である。

    起伏（丘）は、いずれの場合も「車が到達しうる範囲」の外にだけ乗せる。
    """
    import numpy as np

    is_viaduct = bool(elevation and elevation.get("is_viaduct"))
    ground_level_m = float(elevation.get("ground_level_m", 0.0)) if elevation else 0.0
    # **起伏の大きさはコースごとに違う**（`Tracks/environment.py`）。
    # 峠は道の両脇が斜面、都市高速はほぼ平ら。
    amplitude_m = env.relief_amplitude_m if env else RELIEF_AMPLITUDE_M
    wavelength_m = env.relief_wavelength_m if env else RELIEF_WAVELENGTH_M

    xs = [p["x_m"] for p in points]
    ys = [p["y_m"] for p in points]
    x0, x1 = min(xs) - GROUND_MARGIN_M, max(xs) + GROUND_MARGIN_M
    y0, y1 = min(ys) - GROUND_MARGIN_M, max(ys) + GROUND_MARGIN_M

    nx = int((x1 - x0) / GROUND_CELL_M) + 1
    ny = int((y1 - y0) / GROUND_CELL_M) + 1
    log("ground grid %d x %d (%.0f x %.0f m)", nx, ny, x1 - x0, y1 - y0)

    grid_x = x0 + np.arange(nx) * GROUND_CELL_M
    grid_y = y0 + np.arange(ny) * GROUND_CELL_M
    mesh_x, mesh_y = np.meshgrid(grid_x, grid_y)          # (ny, nx)

    # --- 中心線からの距離と、その付近の路面標高 --------------------------
    #
    # **numpy で一括に解く。** 素の二重ループだと 82,000 セル x 220 標本で
    # 分単位になる。
    stride = max(1, int(round(GROUND_HEIGHT_SAMPLE_M /
                              max(points[1]["s_m"] - points[0]["s_m"], 1e-6))))
    samples = centreline_sampler_3d(points, stride)
    sample_x = np.array([sx for sx, _, _ in samples])
    sample_y = np.array([sy for _, sy, _ in samples])
    sample_z = np.array([sz for _, _, sz in samples])

    nearest_d2 = np.full((ny, nx), 1e30)
    nearest_z = np.zeros((ny, nx))
    weight_sum = np.zeros((ny, nx))
    weighted_z = np.zeros((ny, nx))

    # 重みの効く距離 [m]。**これが「近くの中心線」の意味。**
    falloff2 = GROUND_HEIGHT_BLEND_M * GROUND_HEIGHT_BLEND_M

    for index in range(len(sample_x)):
        d2 = (mesh_x - sample_x[index]) ** 2 + (mesh_y - sample_y[index]) ** 2
        closer = d2 < nearest_d2
        nearest_z = np.where(closer, sample_z[index], nearest_z)
        nearest_d2 = np.where(closer, d2, nearest_d2)
        w = 1.0 / (d2 + falloff2)
        w *= w                                   # 1/(d^2+a)^2。近い点を強く効かせる
        weight_sum += w
        weighted_z += w * sample_z[index]

    smooth_z = weighted_z / np.maximum(weight_sum, 1e-30)
    distance = np.sqrt(nearest_d2)

    # **路面のそばでは「いちばん近い点の標高」をそのまま使う。**
    #
    # 重み付き平均だけで作っていたときは、勾配 10% の区間で平均が
    # 引き戻され、**地面が路面より高くなって路面を埋めた。**
    # 実際に走らせたら車が草の上を走っていた（路面は地面の下にあった）。
    #
    # そして**検査がそれを見逃した。** 「地面が路面に追従しているか」を
    # 平均値そのものと比べていたので、常に一致していた。
    # **自分自身と比べる検査は、何も検査していない。**
    #
    # 離れたところでは平均に寄せる。最近傍のままだと、ヘアピンの内側
    # （往路と復路の中間）で標高が不連続になり崖が立つ。
    corridor = width_m / 2.0 + GROUND_ROAD_CORRIDOR_M
    blend = np.clip((distance - corridor) / GROUND_ROAD_BLEND_M, 0.0, 1.0)
    blend = blend * blend * (3.0 - 2.0 * blend)          # smoothstep
    road_z = nearest_z * (1.0 - blend) + smooth_z * blend

    # --- 起伏の掛かり方 ---------------------------------------------------
    #
    # **判定は中心線からの距離ではなくコースの外接矩形からの距離。**
    # 中心線基準だと、コースの内側（インフィールド）や折り返しの内側が
    # 「中心線から遠い」と判定されて起伏が立つ。そこは車がコースアウトで
    # 到達する場所である。
    track_x0, track_x1 = min(xs), max(xs)
    track_y0, track_y1 = min(ys), max(ys)
    outside = np.maximum(
        np.maximum(track_x0 - mesh_x, np.maximum(0.0, mesh_x - track_x1)),
        np.maximum(track_y0 - mesh_y, np.maximum(0.0, mesh_y - track_y1)))

    t = np.clip((outside - DRIVABLE_FLAT_MARGIN_M) / RELIEF_BLEND_M, 0.0, 1.0)
    mask = t * t * (3.0 - 2.0 * t)                # smoothstep

    relief = np.zeros((ny, nx))
    nonzero = mask > 0.0
    if np.any(nonzero):
        for iy in range(ny):
            for ix in range(nx):
                if not nonzero[iy, ix]:
                    continue
                relief[iy, ix] = noise.noise(
                    Vector((mesh_x[iy, ix] / wavelength_m,
                            mesh_y[iy, ix] / wavelength_m, 0.0)))
    relief *= amplitude_m * mask

    if is_viaduct:
        # 桁の幅。この内側は桁の上（路面と同じ高さ）。
        deck_half = width_m / 2.0 + VIADUCT_DECK_SHOULDER_M
        # 桁の外はすぐ下の地面へ落ちる。**なだらかにしない。**
        # なだらかにすると「高架の縁」に見えず、土手になる。
        edge = np.clip((distance - deck_half) / VIADUCT_EDGE_DROP_M, 0.0, 1.0)
        below = ground_level_m + relief
        heights = road_z * (1.0 - edge) + below * edge - GROUND_SINK_M
        # 見た目の地面は**下だけ**（桁は路面メッシュが担当する）。
        visual = below - GROUND_SINK_M
    else:
        heights = road_z + relief - GROUND_SINK_M
        visual = heights

    # --- メッシュ ---------------------------------------------------------
    mesh = bpy.data.meshes.new("TrackGround")
    bm = bmesh.new()
    uv_layer = bm.loops.layers.uv.new("UVMap")

    grid = []
    for iy in range(ny):
        row = []
        for ix in range(nx):
            row.append(bm.verts.new((float(mesh_x[iy, ix]),
                                     float(mesh_y[iy, ix]),
                                     float(visual[iy, ix]))))
        grid.append(row)

    bm.verts.ensure_lookup_table()

    # 地面のテクスチャの繰り返し間隔 [m]。
    #
    # **10 m にすると格子模様として見える。** 地面は 1,374 x 950 m あり、
    # 10 m 周期だと 137 回繰り返す。俯瞰したときに市松模様になった。
    ground_uv_scale = 1.0 / 26.0
    for iy in range(ny - 1):
        for ix in range(nx - 1):
            verts = (grid[iy][ix], grid[iy][ix + 1],
                     grid[iy + 1][ix + 1], grid[iy + 1][ix])
            try:
                face = bm.faces.new(verts)
            except ValueError:
                continue
            for loop in face.loops:
                loop[uv_layer].uv = (loop.vert.co.x * ground_uv_scale,
                                     loop.vert.co.y * ground_uv_scale)

    bm.normal_update()
    bm.to_mesh(mesh)
    bm.free()

    obj = bpy.data.objects.new("TrackGround", mesh)
    bpy.context.scene.collection.objects.link(obj)

    # **高さ場を一緒に返す。** 物理側はこれを読んで接地を計算する。
    # 描画メッシュを物理へ流用しない（憲法ルール4）ための唯一の情報源。
    #
    # 高架では**見た目の地面と高さ場が違う**（前者は下、後者は桁）。
    # これは食い違いではなく、そもそも別のものである。桁は路面メッシュ
    # として別に立ち、車はその上を走る。
    heightfield = {
        "x0_m": x0, "y0_m": y0,
        "cell_m": GROUND_CELL_M,
        "nx": nx, "ny": ny,
        "heights_m": [[float(v) for v in heights[iy]] for iy in range(ny)],
    }

    # --- 検査値 -----------------------------------------------------------
    #
    # **「走行しうる範囲の地面が平らか」は、もう平らでは測れない。**
    # 代わりに「路面の高さに追従しているか」を測る。追従していなければ、
    # 車が地面から浮くか埋まる（以前は丘の上を宙に浮いて走っていた）。
    # **比べる相手は「いちばん近い中心線の標高」。**
    #
    # ここを road_z（自分が作った値）と比べていたときは、常に一致して
    # 検査が素通りした。比べるべきは**元の設計値**である。
    inside_box = outside <= DRIVABLE_FLAT_MARGIN_M
    if is_viaduct:
        # 高架は桁の上だけを見る。**桁の外は落ちるのが正しい。**
        region = inside_box & (distance <= width_m / 2.0 + VIADUCT_DECK_SHOULDER_M)
    else:
        # 路面とその周り。**ここで路面が埋まっていないことが要点。**
        region = inside_box & (distance <= corridor)

    target = nearest_z - GROUND_SINK_M
    if np.any(region):
        follow_error = float(np.max(np.abs(heights - target)[region]))
        # **地面が路面より高くなっていないか**を別に見る。追従の誤差が
        # 小さくても、符号が一方に偏っていれば路面は埋まる。
        above = float(np.max((heights - nearest_z)[region]))
    else:
        follow_error = 0.0
        above = 0.0
    checked = int(np.count_nonzero(region))

    # 見た目の地面の高さ場。**樹木と小物はこちらに乗せる**（描かれる物
    # なので、描かれる地面に合わせる）。地続きのコースでは物理の高さ場と
    # 同じ値だが、高架では違う（物理は桁、見た目は下の地面）。
    visual_field = {
        "x0_m": x0, "y0_m": y0,
        "cell_m": GROUND_CELL_M,
        "nx": nx, "ny": ny,
        "heights_m": [[float(v) for v in visual[iy]] for iy in range(ny)],
    }

    checks = {
        "visual_field": visual_field,
        "above_road_m": above,
        "follow_error_m": follow_error,
        "checked_cells": checked,
        "is_viaduct": is_viaduct,
        "min_height_m": float(np.min(heights)),
        "max_height_m": float(np.max(heights)),
    }
    return obj, (x0, x1, y0, y1), heightfield, checks


def plan_trees(points, width_m, layers, height_at=None):
    """中心線に沿って樹木の配置を決める。**層ごとに撒く。**

    層を分けるのが要点である。1 種類を 1 つの間隔で撒くと、本数を
    どれだけ増やしても「同じ木の並木」にしかならない。高木・広葉樹・
    下草・立ち枯れを別の層として重ねると、密度も背丈もばらける。
    どの層をどれだけ撒くかは `Tracks/environment.py` が持つ。

    **路面から離す。** 物理に衝突判定が無いため、木に突っ込むと
    すり抜ける。近くに置くほどその絵が出やすくなる。
    """
    samples = centreline_sampler(points, 5)
    spacing = points[1]["s_m"] - points[0]["s_m"]

    placements = []
    for layer_index, layer in enumerate(layers):
        # **層ごとに種を変える。** 同じ種だと、間隔の違う層でも
        # 同じ乱数列を使ってしまい、木が同じ場所に重なって生える。
        rng = random.Random(RANDOM_SEED + layer_index * 977)
        min_offset, max_offset = layer.offset_m
        step = max(int(layer.spacing_m / spacing), 1)
        scale_low, scale_high = layer.scale

        for index in range(0, len(points), step):
            p = points[index]
            heading = p["heading_rad"]
            nx = -math.sin(heading)
            ny = math.cos(heading)

            for side in (+1.0, -1.0):
                species = rng.choice(layer.species)
                offset = rng.uniform(min_offset, max_offset)
                jitter = rng.uniform(-layer.spacing_m * 0.45,
                                     layer.spacing_m * 0.45)
                x = p["x_m"] + nx * offset * side + math.cos(heading) * jitter
                y = p["y_m"] + ny * offset * side + math.sin(heading) * jitter

                # **他の区間の路面に近すぎないか必ず見る。**
                # ヘアピンやS字では中心線が折り返すので、「自分の断面から
                # 13 m 外側」でも別区間の路面上ということが起こる。
                if distance_to_centreline(x, y, samples) < min_offset:
                    continue

                placements.append({
                    "species": species,
                    "x_m": x,
                    "y_m": y,
                    "z_m": height_at(x, y) if height_at else 0.0,
                    "yaw_rad": rng.uniform(0.0, 2.0 * math.pi),
                    # **PolyHaven の樹木は sapling（若木）で実寸 1〜3 m
                    # しかない。** 等倍だと並木ではなく下草に見えるので
                    # 拡大している。実寸から離れるが、これは景観であって
                    # 計測対象ではない（憲法ルール18）。
                    # **大きい木の実物が手に入ったら拡大をやめること。**
                    "scale": rng.uniform(scale_low, scale_high),
                })
    return placements


#: コース周りに置くもの。**自分でモデリングせず、CC0 のアセットを使う。**
#:
#: (種類, 中心線からの距離 [m], 間隔 [m], 尺度の範囲, 置き方)
#:
#: 置き方:
#:   "both"        左右どちらにも
#:   "outside"     **コーナーの外側だけ。** バリアやタイヤバリアはここ
#:   "left" / "right"
#:   "corner_exit" **コーナーの立ち上がり、外側の路肩だけ。** パイロン
#:
#: **距離は路肩より外にする。** 物理の当たり判定は樹木と世界境界しか
#: 持たないので、路面のすぐ脇に置くとすり抜ける絵が出る。
#:
#: **"corner_exit" だけ距離の基準が違う。** 中心線からではなく
#: **路面端から**の距離で書く。コース幅は 9〜14 m とばらつくので、
#: 中心線基準だと狭いコースでは遠すぎ、広いコースでは路面に乗る。
PROP_PLAN = [
    # 種類, 距離, 間隔, 尺度, 置き方
    ("concrete_road_barrier",          11.0,  4.2, (1.0, 1.0), "outside"),
    ("concrete_road_barrier_02",       11.0,  4.2, (1.0, 1.0), "outside"),
    ("old_tyre",                        9.5,  1.1, (1.0, 1.0), "tyre_wall"),
    ("modular_chainlink_fence",        26.0,  4.0, (1.0, 1.0), "both"),
    ("street_lamp_01",                 20.0, 46.0, (1.0, 1.0), "left"),
    ("modular_electricity_poles",      34.0, 62.0, (1.0, 1.0), "right"),
    ("modular_urban_apartments_facade", 78.0, 90.0, (1.0, 1.0), "both"),
    ("rollershutter_door",             30.0,190.0, (1.0, 1.0), "left"),
    ("Barrel_01",                      16.0, 75.0, (1.0, 1.0), "both"),
    ("barrel_03",                      17.5, 88.0, (1.0, 1.0), "both"),
    ("plastic_crate_02",               15.0,110.0, (1.0, 1.0), "left"),
    ("boulder_01",                     44.0, 55.0, (0.6, 1.3), "both"),
    ("rock_07",                        38.0, 33.0, (0.5, 1.1), "both"),
    # **パイロン。** 距離は路面端から（上の注記）。縁石の外端が 1.0 m
    # なので、その 0.8 m 外に置く。**走行ラインにも縁石にも掛からない。**
    ("traffic_cone",                    1.8,  7.0, (1.0, 1.0), "corner_exit"),
]

#: パイロンを路面端から何 m まで近づけてよいか [m]。
#:
#: 一般のプロップは中心線から `half + 2.5` m 以内を禁止しているが、
#: パイロンは**路肩に置くもの**なのでそれでは置けない。代わりに
#: 「別区間の路面に乗っていないか」だけを見る。
CONE_MIN_CLEARANCE_M = 1.0

#: タイヤバリアを置く曲率のしきい値 [1/m]。これより曲がっている所だけ。
#: **直線に積んでも意味がない。** 飛び出すのはコーナーの外側。
TYRE_WALL_CURVATURE = 1.0 / 60.0


def plan_props(points, width_m, species_list, height_at=None,
               plan=None):
    """コース周りの物を置く。

    **樹木と同じ規則を守る。** 路面から離し、他区間の路面に近すぎたら
    置かない（ヘアピンやS字では中心線が折り返すので、自分の断面から
    離れていても別区間の路面上ということが起こる）。
    """
    rng = random.Random(RANDOM_SEED + 77)
    samples = centreline_sampler(points, 5)
    spacing = points[1]["s_m"] - points[0]["s_m"]
    half = width_m / 2.0
    # **置く物の一覧はコースごとに違う**（`Tracks/environment.py`）。
    # 共通の PROP_PLAN しか無かったころ、4コースとも同じバリアが
    # 同じ距離に並んでいた。
    plan = PROP_PLAN if plan is None else plan

    # パイロンを置くコーナー後半の点。**縁石と同じ判定を使う**
    # （`Tracks/kerb.py`）。別の閾値にすると「縁石があるのにパイロンが
    # 無いコーナー」が出て、理由が説明できなくなる。
    exit_indices = corner_exit_indices([p["curvature_1pm"] for p in points])

    # **パイロン用は間引かない標本を使う。**
    #
    # `centreline_sampler(points, 5)` は 5 点ごとなので、点と点の間に
    # 落ちた場所では距離が最大 2.5 m 過大に出る。一般のプロップは
    # 余裕が 2.5 m あるので影響しないが、パイロンは路面端から 1.8 m に
    # 置くため、過大評価がそのまま「別区間の路面に乗る」事故になる。
    fine_samples = centreline_sampler(points, 1)

    placements = []
    for kind, offset_m, gap_m, scale_range, mode in plan:
        if kind not in species_list:
            # **黙って飛ばさない**（憲法ルール6）。取り込み忘れに気づけない。
            log("!! アセットが無いので置けない: %s", kind)
            continue

        step = max(int(gap_m / spacing), 1)
        for index in range(0, len(points), step):
            p = points[index]
            heading = p["heading_rad"]
            nx = -math.sin(heading)
            ny = math.cos(heading)
            curvature = p["curvature_1pm"]

            if mode == "outside":
                # 曲率が正（左旋回）なら外側は右。直線には置かない。
                if abs(curvature) < 1.0 / 200.0:
                    continue
                sides = [-1.0 if curvature > 0.0 else 1.0]
            elif mode == "tyre_wall":
                if abs(curvature) < TYRE_WALL_CURVATURE:
                    continue
                sides = [-1.0 if curvature > 0.0 else 1.0]
            elif mode == "corner_exit":
                # 立ち上がりだけ。曲率が正（左旋回）なら外側は右。
                if index not in exit_indices:
                    continue
                sides = [-1.0 if curvature > 0.0 else 1.0]
            elif mode == "left":
                sides = [1.0]
            elif mode == "right":
                sides = [-1.0]
            else:
                sides = [1.0, -1.0]

            for side in sides:
                distance = offset_m
                if mode == "tyre_wall":
                    # タイヤは2段に積む。**1個だけだとゴミに見える。**
                    stacks = [(distance, 0.0), (distance + 0.62, 0.0),
                              (distance + 0.31, 0.30)]
                elif mode == "corner_exit":
                    # **路面端からの距離**（PROP_PLAN の注記）。
                    # 高さは地面に合わせる。樹木は z=0 に置いてあるが、
                    # 地面は GROUND_SINK_M だけ沈めてあるので、高さ 0.7 m の
                    # パイロンだと 5 cm 浮いているのがはっきり見える。
                    stacks = [(half + distance, -GROUND_SINK_M)]
                else:
                    stacks = [(distance, 0.0)]

                for lateral, height in stacks:
                    x = p["x_m"] + nx * lateral * side
                    y = p["y_m"] + ny * lateral * side

                    if mode == "corner_exit":
                        if (distance_to_centreline(x, y, fine_samples)
                                < half + CONE_MIN_CLEARANCE_M):
                            continue
                    elif distance_to_centreline(x, y, samples) < half + 2.5:
                        continue

                    # 向き。バリアとフェンスはコースに沿わせる。
                    if mode in ("outside", "tyre_wall") or kind.startswith("modular_chainlink"):
                        yaw = heading
                    else:
                        yaw = rng.uniform(0.0, 2.0 * math.pi)

                    low, high = scale_range
                    placements.append({
                        "kind": kind,
                        "x_m": x,
                        "y_m": y,
                        # **地面に乗せる。** height は路面からの持ち上げ量
                        # （タイヤバリアの段など）で、地面の標高とは別。
                        "z_m": (height_at(x, y) if height_at else 0.0) + height,
                        "yaw_rad": yaw,
                        "scale": rng.uniform(low, high),
                    })
    return placements


def export_fbx(objects, path):
    bpy.ops.object.select_all(action="DESELECT")
    for obj in objects:
        obj.select_set(True)
    bpy.context.view_layer.objects.active = objects[0]
    bpy.ops.export_scene.fbx(
        filepath=path,
        use_selection=True,
        apply_unit_scale=True,
        global_scale=1.0,
        axis_forward="X",
        axis_up="Z",
        mesh_smooth_type="FACE",
        use_mesh_modifiers=True,
        bake_space_transform=False,
        path_mode="COPY",
    )


def main():
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    if len(argv) < 2:
        print("usage: ... -- <track.json> <out_dir>")
        return 1
    track_path, out_dir = argv[0], argv[1]
    os.makedirs(out_dir, exist_ok=True)

    with open(track_path, encoding="utf-8") as handle:
        track = json.load(handle)

    points = track["points"]
    width_m = track["width_m"]
    shoulder_m = track["shoulder_m"]

    # **コースごとの環境**（`Tracks/environment.py`）。
    #
    # 樹木の密度も、置く物も、起伏の大きさも、ここから来る。
    # 共通の定数しか無かったころ、4コースとも同じ木が同じ間隔で並び、
    # 同じバリアが同じ距離に並んでいた。**線形だけが違う4本**だった。
    #
    # コース名は出力先の名前から取る（`Tracks/Export/<key>/`）。
    track_key = os.path.basename(os.path.normpath(out_dir))
    env = environment_for(track_key)
    log("環境: %s（樹木 %d 層 / 物 %d 種 / 遠景 %s）", track_key,
        len(env.tree_layers), len(env.props),
        "あり" if env.distant else "なし")

    started = time.time()
    clear_scene()

    road = build_road(points, width_m)
    log("road: %d 面", len(road.data.polygons))

    # **縁石はコーナーだけ。** 路面と別のメッシュにする（マテリアルが
    # 違う。赤白の縞と白線を 1 枚のテクスチャに混ぜられない）。
    spacing_m = points[1]["s_m"] - points[0]["s_m"]
    kerb, kerb_spans_used, kerb_faces = build_kerbs(points, width_m, spacing_m)
    if kerb_faces == 0:
        # **黙って縁石無しで通さない**（憲法ルール6）。
        log("!! 縁石が 1 面も出来なかった（コーナーが検出されていない）")
        return 1
    log("kerb: %d 区間 / %d 面", len(kerb_spans_used), kerb_faces)

    elevation = track.get("elevation")
    ground, extent, heightfield, ground_checks = build_ground(
        points, width_m, shoulder_m, elevation, env)
    log("ground: %d 面 (%.1fs)", len(ground.data.polygons), time.time() - started)

    # 走行面が**縦断のとおりの高さ**にあるかを確認する。目視ではなく数値で。
    #
    # 以前ここは「路面は z=0 の平面か」を見ていた。縦断が入った今、
    # それでは通らない。見るべきことは変わっていない——
    # **路面の高さが、物理が読む値と一致しているか**である。
    # 一致していなければ、車は地面から浮くか埋まる。
    #
    # 頂点は上面（中心線の z）か下面（その ROAD_THICKNESS_M 下）の
    # どちらかにしかならない。**それ以外が出たら座標変換が壊れている。**
    allowed = set()
    for p in points:
        z = round(p.get("z_m", 0.0), 6)
        allowed.add(z)
        allowed.add(round(z - ROAD_THICKNESS_M, 6))
    allowed_sorted = sorted(allowed)

    def nearest_allowed(z):
        best = min(allowed_sorted, key=lambda a: abs(a - z))
        return abs(best - z)

    # **許容は 1 mm。** Blender の頂点は float32 なので、標高 35 m では
    # 刻みそのものが 2e-6 m ある。1e-6 で見ると「正しいのに落ちる」。
    # 1 mm は物理的に意味のある量より 2 桁小さく、壊れ方（数 cm 以上の
    # ずれ）とは 1 桁以上離れている。
    MESH_TOLERANCE_M = 1e-3

    worst_road = 0.0
    for vert in road.data.vertices:
        worst_road = max(worst_road, nearest_allowed(round(vert.co.z, 6)))
    if worst_road > MESH_TOLERANCE_M:
        log("!! 路面に縦断と合わない頂点がある (ずれ %.6f m)", worst_road)
        return 1

    road_z_values = [p.get("z_m", 0.0) for p in points]
    log("路面の縦断 OK (%d 頂点、ずれ %.2e m / 標高 %.2f 〜 %.2f m)",
        len(road.data.vertices), worst_road,
        min(road_z_values), max(road_z_values))

    # 縁石の高さが断面どおりかを確認する。**目視ではなく数値で。**
    #
    # 断面は 4 点しか無いので、頂点の z はその 4 値のいずれかにしかならない。
    # **それ以外が出たら、断面か座標変換が壊れている。**
    # **断面は路面からの相対で見る。** 縦断が入ったので絶対の z では測れない。
    kerb_allowed = set()
    for p in points:
        base = p.get("z_m", 0.0)
        for _, dz in KERB_PROFILE:
            kerb_allowed.add(round(base + dz, 6))
    kerb_sorted = sorted(kerb_allowed)
    worst_kerb = 0.0
    for vert in kerb.data.vertices:
        z = round(vert.co.z, 6)
        worst_kerb = max(worst_kerb,
                         min(abs(a - z) for a in kerb_sorted))
    if worst_kerb > MESH_TOLERANCE_M:
        log("!! 縁石に断面外の頂点がある (ずれ %.6f m)", worst_kerb)
        return 1
    log("縁石の断面 OK (%d 頂点、高さ %.3f m / 幅 %.2f m、ずれ %.2e m)",
        len(kerb.data.vertices), KERB_HEIGHT_M, KERB_WIDTH_M, worst_kerb)

    # **車が到達しうる範囲の地面が、路面の高さに追従しているかを確認する。**
    #
    # 以前ここは「その範囲の地面が z=0 で平らか」を見ていた。縦断が
    # 入った今、平らであってはいけない——峠の頂上では地面も 35 m
    # 上がっていなければならない。**見るべきことは変わっていない**:
    # 車が到達する場所で、地面が路面の高さから離れていないこと。
    # 離れていれば、コースアウトした車が宙に浮くか地面に埋まる。
    #
    # 高架では「桁の上」だけを見る。**桁の外で落ちるのは正しい挙動**で、
    # そこを平らにすると高架が土手になる。
    tolerance = 1e-6
    if ground_checks["follow_error_m"] > tolerance:
        log("!! 走行しうる範囲の地面が路面に追従していない (max ずれ %.6f m)",
            ground_checks["follow_error_m"])
        return 1
    # **地面が路面より高いと、路面が地面に埋まって見えなくなる。**
    # 実際にそうなり、車が草の上を走っていた（路面は下にあった）。
    if ground_checks["above_road_m"] > -GROUND_SINK_M / 2.0:
        log("!! 地面が路面より高い (最大 %.3f m)。路面が埋まる",
            ground_checks["above_road_m"])
        return 1
    log("地面の追従 OK (%s / %d セル、max ずれ %.2e m、標高 %.1f 〜 %.1f m)",
        "高架（桁の上）" if ground_checks["is_viaduct"] else "地続き",
        ground_checks["checked_cells"], ground_checks["follow_error_m"],
        ground_checks["min_height_m"], ground_checks["max_height_m"])

    # --- 道路構造（手続きで作るもの）-------------------------------------
    #
    # **これらは「飾り」ではなく道路構造の一部である。** 路面の形が
    # 決まれば形も決まるので、外から持ってくるモデルを並べるより
    # 中心線から生成するほうが確実に沿う。
    # 建物・標識・樹木のような「置く物」は外部の CC0 アセットを使う
    # （ユーザーの方針。`Docs/PHASE15_DATA_LICENCE.md` §6）。
    extras = []

    if env.distant is not None:
        base_z = (elevation.get("ground_level_m", 0.0)
                  if elevation and elevation.get("is_viaduct")
                  else min(p.get("z_m", 0.0) for p in points))
        distant_obj, distant_faces = build_distant_terrain(
            points, extent, env.distant, base_z)
        if distant_faces == 0:
            log("!! 遠景が 1 面も出来なかった")
            return 1
        extras.append(distant_obj)
        log("distant: %d 面（振幅 %.0f m / 到達 %.0f m / %d 重）",
            distant_faces, env.distant.amplitude_m, env.distant.reach_m,
            env.distant.ridges)

    if env.guardrail:
        rail, rail_faces = build_guardrail(points, width_m)
        if rail_faces == 0:
            log("!! ガードレールが 1 面も出来なかった")
            return 1
        extras.append(rail)
        log("guardrail: %d 面（高さ %.2f m）", rail_faces, GUARDRAIL_HEIGHT_M)

    if env.sea_level_m is not None:
        sea, sea_faces = build_sea(extent, env.distant, env.sea_level_m)
        extras.append(sea)
        log("sea: 水面 %.1f m", env.sea_level_m)

    # **ピット。** 直線が短いコースには作らない（幅 9 m の峠に
    # ピットレーンがあったらおかしい）。
    garages = []
    if env.pit_building:
        lane = plan_pit_lane(points, spacing_m)
        if lane is None:
            log("pit: 直線が短いので作らない")
        else:
            pit, pit_faces = build_pit(points, lane)
            if pit_faces == 0:
                log("!! ピットレーンが 1 面も出来なかった")
                return 1
            extras.append(pit)
            garages = garage_positions(lane, points, spacing_m)
            for garage in garages:
                garage["kind"] = env.pit_building
                garage["scale"] = 1.0
            log("pit: %d 面（レーン %.0f m / 建屋 %d 棟）",
                pit_faces, lane.length_m * spacing_m, len(garages))

    if env.viaduct_piers or env.noise_wall:
        viaduct, viaduct_faces = build_viaduct(
            points, width_m,
            elevation.get("ground_level_m", 0.0) if elevation else 0.0,
            piers=env.viaduct_piers, wall=env.noise_wall)
        if viaduct_faces == 0:
            log("!! 高架の橋脚・遮音壁が 1 面も出来なかった")
            return 1
        extras.append(viaduct)
        log("viaduct: %d 面（橋脚 %s / 遮音壁 %s）", viaduct_faces,
            "あり" if env.viaduct_piers else "なし",
            "あり" if env.noise_wall else "なし")

    ground_height_at = make_height_lookup(ground_checks["visual_field"])
    species = all_species(env)
    trees = plan_trees(points, width_m, env.tree_layers, ground_height_at)
    log("trees: %d 本（%d 層 / %d 種）",
        len(trees), len(env.tree_layers), len(species))

    # **コース周りの物。** 自分でモデリングせず CC0 のアセットを置く。
    prop_kinds = all_prop_kinds(env)
    props = plan_props(points, width_m, prop_kinds, ground_height_at,
                       env.props)
    # **ピットの建屋は plan_props を通さない。** 置く場所が
    # 「中心線から何 m」ではなくピットレーンの外側と決まっているため。
    props += garages
    prop_counts = {}
    for prop in props:
        prop_counts[prop["kind"]] = prop_counts.get(prop["kind"], 0) + 1
    log("props: %d 個", len(props))
    for name, count in sorted(prop_counts.items()):
        log("   %-34s %d", name, count)

    counts = {}
    for tree in trees:
        counts[tree["species"]] = counts.get(tree["species"], 0) + 1
    for name, count in sorted(counts.items()):
        log("   %-22s %d", name, count)

    export_fbx([road], os.path.join(out_dir, "TrackRoad.fbx"))
    export_fbx([kerb], os.path.join(out_dir, "TrackKerb.fbx"))
    export_fbx([ground], os.path.join(out_dir, "TrackGround.fbx"))

    # **道路構造は 1 つずつ別の FBX に出す。**
    #
    # まとめると UE 側でマテリアルを分けられない。遠景の山と
    # ガードレールと橋脚は、どれも見た目がまったく違う。
    extra_files = {}
    for obj in extras:
        name = obj.name                       # TrackDistant / TrackGuardrail / ...
        path = os.path.join(out_dir, name + ".fbx")
        export_fbx([obj], path)
        extra_files[name] = name + ".fbx"

    placement = {
        "_meta": {
            "generator": "Blender/build_track.py",
            "source_track": track_path,
            "frame": "physics (X forward-ish, Y left, Z up, metres)",
            "note": "UE 側はこの配置で樹木をインスタンス化する。手で編集しない。",
        },
        "track_name": track["name"],
        "extent_m": {"x0": extent[0], "x1": extent[1], "y0": extent[2], "y1": extent[3]},
        "road_fbx": "TrackRoad.fbx",
        "kerb_fbx": "TrackKerb.fbx",
        "ground_fbx": "TrackGround.fbx",
        # 遠景・ガードレール・高架。**あるものだけ載る。**
        "structure_fbx": extra_files,
        # **空と光もコースごとに違う**（`Tracks/environment.py`）。
        # UE 側が `Tracks/` を import しなくて済むよう、ここで値を渡す。
        "ground_texture": env.ground_texture,
        "distant_texture": env.distant_texture,
        "lighting": {
            "sun_pitch_deg": env.lighting.sun_pitch_deg,
            "sun_yaw_deg": env.lighting.sun_yaw_deg,
            "sun_intensity": env.lighting.sun_intensity,
            "fog_density": env.lighting.fog_density,
            "fog_height_falloff": env.lighting.fog_height_falloff,
            "fog_colour": list(env.lighting.fog_colour),
            "sky_light_intensity": env.lighting.sky_light_intensity,
        },
        "kerb_spans": len(kerb_spans_used),
        "species": species,
        "trees": trees,
        "prop_kinds": sorted({prop["kind"] for prop in props}),
        "props": props,
    }
    with open(os.path.join(out_dir, "placement.json"), "w", encoding="utf-8") as handle:
        json.dump(placement, handle, ensure_ascii=False, indent=1)

    # --- 高さ場 -----------------------------------------------------------
    #
    # **物理と描画で同じ地形を使うための唯一の情報源。**
    # 描画メッシュ（TrackGround.fbx）から高さを読み取ると、憲法ルール4
    # （物理計算に表示用3Dモデルを流用しない）に反する。生成元のデータを
    # 直接配る。
    #
    # 値は mm 単位まで丸める。**それ以上の精度は意味が無い**（地形は
    # 4 m グリッドの線形補間で、元が滑らかな関数）。
    heightfield["heights_m"] = [
        [round(z, 3) for z in row] for row in heightfield["heights_m"]
    ]
    heightfield["_meta"] = {
        "generator": "Blender/build_track.py",
        "note": "**手で編集しないこと。** 地形を変えたら再生成する。"
                "物理（接地）と描画（地面メッシュ）が同じ値を使う。",
        "frame": "physics (X forward-ish, Y left, Z up, metres)",
        "ground_sink_m": GROUND_SINK_M,
        "drivable_flat_margin_m": DRIVABLE_FLAT_MARGIN_M,
    }
    with open(os.path.join(out_dir, "heightfield.json"), "w", encoding="utf-8") as handle:
        json.dump(heightfield, handle, ensure_ascii=False, separators=(",", ":"))

    log("heightfield: %d x %d（%.0f m 格子）", heightfield["nx"], heightfield["ny"],
        heightfield["cell_m"])

    log("done (%.1fs) -> %s", time.time() - started, out_dir)
    return 0


if __name__ == "__main__":
    sys.exit(main())
