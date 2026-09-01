"""コース中心線から路面・地面メッシュと樹木の配置を生成する（Blender headless）.

    blender --background --python Blender/build_track.py -- \
        <track.json> <out_dir>

**形状をここで定義しないこと。** 中心線は `Tracks/physics_test_track.py`
が生成し、`Tools/export_track.py` が JSON にしたものだけを読む。
描画側が独自にコースを持つと、物理と絵がずれる。

## 起伏を走行面に入れてはいけない

物理モデル（`Physics/vehicle.py`）は**平面3自由度**（前後・左右・ヨー）で、
上下方向の動特性を持たない。サスペンションのバネ定数・減衰力が `unknown`
のため、`Tracks/physics_test_track.py` は段差も意図的に外している。

したがって**車は常に z=0 を走る。** 走行面に起伏を付けると、車が地面に
埋まる／浮く。起伏は路肩の外側にだけ入れ、走行面は完全な平面に保つ。

「見た目のために少しだけ傾ける」も駄目。物理が知らない量を絵に入れると、
**モデルの限界が見えなくなる。**
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

    # 上面（走行面。**必ず z=0**）と、その真下の縁。
    rows = []
    for p in points:
        heading = p["heading_rad"]
        # 左手側の法線（物理の座標系で Y が左）
        nx = -math.sin(heading)
        ny = math.cos(heading)
        lx, ly = p["x_m"] + nx * half, p["y_m"] + ny * half
        rx, ry = p["x_m"] - nx * half, p["y_m"] - ny * half

        left = bm.verts.new((lx, ly, 0.0))
        right = bm.verts.new((rx, ry, 0.0))
        # **下へ押し出す。** 上面は動かさない（物理は z=0 の平面を走る）。
        left_low = bm.verts.new((lx, ly, -ROAD_THICKNESS_M))
        right_low = bm.verts.new((rx, ry, -ROAD_THICKNESS_M))
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


def centreline_sampler(points, stride):
    """距離計算用に間引いた中心線。**全点使うと地面生成が現実的な時間で終わらない。**"""
    return [(p["x_m"], p["y_m"]) for p in points[::stride]]


def distance_to_centreline(x, y, samples):
    best = 1e30
    for cx, cy in samples:
        d = (cx - x) ** 2 + (cy - y) ** 2
        if d < best:
            best = d
    return math.sqrt(best)


def build_ground(points, width_m, shoulder_m):
    """走行面の外側にだけ起伏を持つ地面を作る。

    **走行面（中心線から width/2 + shoulder 以内）は z=0 で完全に平ら。**
    物理が平面3自由度である以上、ここに凹凸を入れてはいけない。
    """
    xs = [p["x_m"] for p in points]
    ys = [p["y_m"] for p in points]
    x0, x1 = min(xs) - GROUND_MARGIN_M, max(xs) + GROUND_MARGIN_M
    y0, y1 = min(ys) - GROUND_MARGIN_M, max(ys) + GROUND_MARGIN_M

    nx = int((x1 - x0) / GROUND_CELL_M) + 1
    ny = int((y1 - y0) / GROUND_CELL_M) + 1
    log("ground grid %d x %d (%.0f x %.0f m)", nx, ny, x1 - x0, y1 - y0)

    # **起伏の判定は中心線からの距離ではなく、コースの外接矩形からの距離。**
    #
    # 中心線基準だと、コースの内側（インフィールド）や折り返しの内側が
    # 「中心線から遠い」と判定されて起伏が立つ。そこは車がコースアウトで
    # 到達する場所であり、物理は平面を走り続けるので車が浮く。
    track_x0, track_x1 = min(xs), max(xs)
    track_y0, track_y1 = min(ys), max(ys)

    def relief_mask(x, y):
        """コースの外接矩形からどれだけ外れているかで 0..1 を返す。"""
        # **矩形距離（max）で測る。** hypot（円形）にすると、矩形で
        # 検査している「走行しうる範囲」と角で食い違い、角だけ起伏が
        # 立つ。実際に 0.24 m のずれとして検出された。
        dx = max(track_x0 - x, 0.0, x - track_x1)
        dy = max(track_y0 - y, 0.0, y - track_y1)
        outside = max(dx, dy)

        if outside <= DRIVABLE_FLAT_MARGIN_M:
            return 0.0
        if outside >= DRIVABLE_FLAT_MARGIN_M + RELIEF_BLEND_M:
            return 1.0
        t = (outside - DRIVABLE_FLAT_MARGIN_M) / RELIEF_BLEND_M
        return t * t * (3.0 - 2.0 * t)     # smoothstep

    mesh = bpy.data.meshes.new("TrackGround")
    bm = bmesh.new()
    uv_layer = bm.loops.layers.uv.new("UVMap")

    grid = []
    heights = []
    for iy in range(ny):
        row = []
        height_row = []
        y = y0 + iy * GROUND_CELL_M
        for ix in range(nx):
            x = x0 + ix * GROUND_CELL_M
            mask = relief_mask(x, y)

            if mask <= 0.0:
                z = -GROUND_SINK_M
            else:
                n = noise.noise(Vector((x / RELIEF_WAVELENGTH_M,
                                        y / RELIEF_WAVELENGTH_M, 0.0)))
                z = -GROUND_SINK_M + n * RELIEF_AMPLITUDE_M * mask

            row.append(bm.verts.new((x, y, z)))
            height_row.append(z)
        grid.append(row)
        heights.append(height_row)

    bm.verts.ensure_lookup_table()

    # 地面のテクスチャの繰り返し間隔 [m]。
    #
    # **10 m にすると格子模様として見える。** 地面は 1,374 x 950 m あり、
    # 10 m 周期だと 137 回繰り返す。俯瞰したときに市松模様になった。
    # 周期を伸ばすと近くでぼやけるが、繰り返しが目立つほうが不自然。
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
    heightfield = {
        "x0_m": x0, "y0_m": y0,
        "cell_m": GROUND_CELL_M,
        "nx": nx, "ny": ny,
        "heights_m": heights,
    }
    return obj, (x0, x1, y0, y1), heightfield


def plan_trees(points, width_m, offsets, species_list):
    """中心線に沿って樹木の配置を決める。

    **路面から離す。** 物理に衝突判定が無いため、木に突っ込むと
    すり抜ける。近くに置くほどその絵が出やすくなる。
    """
    rng = random.Random(RANDOM_SEED)
    min_offset, max_offset = offsets
    samples = centreline_sampler(points, 5)

    spacing = points[1]["s_m"] - points[0]["s_m"]
    step = max(int(TREE_SPACING_M / spacing), 1)

    placements = []
    for index in range(0, len(points), step):
        p = points[index]
        heading = p["heading_rad"]
        nx = -math.sin(heading)
        ny = math.cos(heading)

        for side in (+1.0, -1.0):
            species = rng.choice(species_list)
            offset = rng.uniform(min_offset, max_offset)
            jitter = rng.uniform(-TREE_SPACING_M * 0.4, TREE_SPACING_M * 0.4)
            x = p["x_m"] + nx * offset * side + math.cos(heading) * jitter
            y = p["y_m"] + ny * offset * side + math.sin(heading) * jitter

            # **他の区間の路面に近すぎないか必ず見る。**
            # ヘアピンやS字では中心線が折り返すので、「自分の断面から
            # 18 m 外側」でも別区間の路面上ということが起こる。
            if distance_to_centreline(x, y, samples) < min_offset:
                continue

            placements.append({
                "species": species,
                "x_m": x,
                "y_m": y,
                "z_m": 0.0,
                "yaw_rad": rng.uniform(0.0, 2.0 * math.pi),
                # **PolyHaven の樹木は sapling（若木）で 1〜3 m しかない。**
                # 等倍だと並木ではなく下草に見える。切株以外は拡大する。
                # 実寸から離れるが、**これは景観であって計測対象ではない。**
                "scale": (rng.uniform(0.8, 1.4) if species == "tree_stump_01"
                          else rng.uniform(1.9, 3.4)),
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

    started = time.time()
    clear_scene()

    road = build_road(points, width_m)
    log("road: %d 面", len(road.data.polygons))

    ground, extent, heightfield = build_ground(points, width_m, shoulder_m)
    log("ground: %d 面 (%.1fs)", len(ground.data.polygons), time.time() - started)

    # 走行面が本当に平らかを確認する。**目視ではなく数値で。**
    #
    # 厚みを付けたので、頂点は上面（z=0）か下面（z=-ROAD_THICKNESS_M）の
    # どちらかになる。**その2つ以外が現れたら、走行面が傾いている。**
    top = 0
    bottom = 0
    worst_top = 0.0
    for vert in road.data.vertices:
        z = vert.co.z
        if abs(z) < 1e-9:
            top += 1
        elif abs(z + ROAD_THICKNESS_M) < 1e-9:
            bottom += 1
        else:
            log("!! 路面に上面でも下面でもない頂点がある (z = %.6f)", z)
            return 1
        if abs(z) < ROAD_THICKNESS_M / 2.0:
            worst_top = max(worst_top, abs(z))

    if top == 0:
        log("!! 路面に上面の頂点が無い")
        return 1
    log("路面の平坦性 OK (上面 %d 頂点 max |z| = %.2e / 下面 %d 頂点、厚み %.3f m)",
        top, worst_top, bottom, ROAD_THICKNESS_M)

    # **車が到達しうる範囲の地面も平らかを確認する。**
    #
    # 物理は z=0 の平面を走り続けるので、走行しうる場所に起伏があると
    # 車が地面から浮く／埋まる。以前これを見落として、コースアウトすると
    # 丘の上を宙に浮いて走る状態になった。**目視ではなく数値で止める。**
    xs_t = [p["x_m"] for p in points]
    ys_t = [p["y_m"] for p in points]
    tx0, tx1 = min(xs_t) - DRIVABLE_FLAT_MARGIN_M, max(xs_t) + DRIVABLE_FLAT_MARGIN_M
    ty0, ty1 = min(ys_t) - DRIVABLE_FLAT_MARGIN_M, max(ys_t) + DRIVABLE_FLAT_MARGIN_M

    worst = 0.0
    checked = 0
    for vert in ground.data.vertices:
        x, y, z = vert.co
        if tx0 <= x <= tx1 and ty0 <= y <= ty1:
            checked += 1
            worst = max(worst, abs(z + GROUND_SINK_M))

    if worst > 1e-9:
        log("!! 走行しうる範囲の地面が平らでない (max ずれ %.6f m)", worst)
        return 1
    log("走行域の地面の平坦性 OK (%d 頂点、max ずれ %.2e m)", checked, worst)

    species = ["pine_sapling_small", "fir_sapling", "searsia_lucida",
               "othonna_cerarioides", "tree_stump_01"]
    trees = plan_trees(points, width_m, track["tree_offset_m"], species)
    log("trees: %d 本", len(trees))

    counts = {}
    for tree in trees:
        counts[tree["species"]] = counts.get(tree["species"], 0) + 1
    for name, count in sorted(counts.items()):
        log("   %-22s %d", name, count)

    export_fbx([road], os.path.join(out_dir, "TrackRoad.fbx"))
    export_fbx([ground], os.path.join(out_dir, "TrackGround.fbx"))

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
        "ground_fbx": "TrackGround.fbx",
        "species": species,
        "trees": trees,
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
