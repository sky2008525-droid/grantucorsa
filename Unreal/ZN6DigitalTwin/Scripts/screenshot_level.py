"""組んだレベルを実際に描画して画像に落とす（UE Python）.

    UnrealEditor-Cmd.exe <uproject>
        -ExecutePythonScript="Unreal/ZN6DigitalTwin/Scripts/screenshot_level.py"

**-NullRHI を付けないこと。** 描画しないので真っ黒な画像が出る。

## なぜ必要か

`build_level.py` のログは「525 本置いた」「メッシュを 5/5 割り当てた」まで
しか言えない。**それらが正しい位置・向き・大きさで置かれているかは、
描画して見るまで分からない。** 座標変換の符号を1つ間違えただけで、
車が地面に埋まったり木が路面に生えたりするが、ログは成功と言う。

SceneCapture2D を使う。PIE を起動しないので、Actor の BeginPlay は
走らない（＝車は物理を始めず原点に立ったまま）。**ここで見たいのは
配置であって挙動ではない。**

## 現状の限界（未解決）

**ヘッドレス（-unattended、実ビューポート無し）では、このスクリプトは
空しか撮れない。** SkyAtmosphere は写るが、StaticMeshActor が1つも
写らない。

レベルが空なのではないことは確認済み。撮影直前に Actor を数えており、

    Actor 532 個: StaticMeshActor x527, ZN6VehicleActor x1,
                  SkyAtmosphere x1, SkyLight x1,
                  DirectionalLight x1, ExponentialHeightFog x1

と出る。**中身はある。描画されていない。**

切り分けのために試して駄目だったこと:

  - 露出・霧の濃度（既定 0.02 -> 0.0008）    灰色一色は解消したが変化なし
  - HDRIBackdrop -> SkyAtmosphere            空は出るようになった
  - カメラの向きを注視点から計算             向きの問題ではない
  - 撮影中だけ r.Nanite 0                    変化なし

実ビューポートが無いと静的メッシュの描画状態が作られないためと思われるが、
確証は無い。**「たぶんこうだろう」で片付けず、未解決として残す。**

見た目の確認は、当面 UE エディタでレベルを開いて行うこと:

    Content/ZN6/Maps/PhysicsTestTrack
"""

import os

import unreal

LEVEL_PATH = "/Game/ZN6/Maps/PhysicsTestTrack"
OUT_DIR_NAME = "Screenshots"
WIDTH, HEIGHT = 1600, 900


def log(message):
    unreal.log("[ZN6 shot] " + message)


def editor_world():
    return unreal.get_editor_subsystem(unreal.UnrealEditorSubsystem).get_editor_world()


def make_render_target():
    tools = unreal.AssetToolsHelpers.get_asset_tools()
    package = "/Game/ZN6/Debug"
    unreal.EditorAssetLibrary.make_directory(package)
    path = package + "/RT_Screenshot"
    if unreal.EditorAssetLibrary.does_asset_exist(path):
        unreal.EditorAssetLibrary.delete_asset(path)
    target = tools.create_asset("RT_Screenshot", package,
                                unreal.TextureRenderTarget2D,
                                unreal.TextureRenderTargetFactoryNew())
    target.set_editor_property("size_x", WIDTH)
    target.set_editor_property("size_y", HEIGHT)
    # **sRGB で書き出す。** リニアのまま PNG にすると極端に暗い絵になり、
    # 「ライティングが壊れている」と誤診する。
    target.set_editor_property("render_target_format",
                               unreal.TextureRenderTargetFormat.RTF_RGBA8_SRGB)
    return target


def look_at(from_location, to_location):
    """注視点から向きを求める。

    **ピッチ/ヨーの符号を手で決めない。** Rotator の引数順や正の向きを
    取り違えると、空だけを撮った画像が出る（実際に一度そうなった）。
    見たい点を指定すれば規約に依存しない。
    """
    return unreal.MathLibrary.find_look_at_rotation(from_location, to_location)


def capture(target, location, rotation, fov, out_dir, name):
    subsystem = unreal.get_editor_subsystem(unreal.EditorActorSubsystem)
    actor = subsystem.spawn_actor_from_class(unreal.SceneCapture2D, location, rotation)
    if actor is None:
        unreal.log_error("[ZN6 shot] SceneCapture2D を作れない")
        return False

    component = actor.capture_component2d
    # 回転は Actor 側だけでなくコンポーネントにも明示する
    component.set_world_rotation(rotation, False, False)
    component.set_editor_property("texture_target", target)
    component.set_editor_property("fov_angle", fov)
    component.set_editor_property("capture_source",
                                  unreal.SceneCaptureSource.SCS_FINAL_COLOR_LDR)
    # **プロパティ名を決め打ちしない。** UE のバージョンで
    # `b_capture_every_frame` / `capture_every_frame` が入れ替わる。
    # 無いものに代入すると例外で止まるので、あるものだけ設定する。
    for prop in ("capture_every_frame", "capture_on_movement"):
        try:
            component.set_editor_property(prop, False)
        except Exception as exc:                          # noqa: BLE001
            unreal.log_warning("[ZN6 shot] %s を設定できない: %s" % (prop, exc))
    component.capture_scene()

    unreal.RenderingLibrary.export_render_target(editor_world(), target, out_dir, name)
    subsystem.destroy_actor(actor)
    log("撮影: %s" % os.path.join(out_dir, name))
    return True


def main():
    unreal.get_editor_subsystem(unreal.LevelEditorSubsystem).load_level(LEVEL_PATH)

    out_dir = os.path.join(
        unreal.Paths.convert_relative_path_to_full(unreal.Paths.project_saved_dir()),
        OUT_DIR_NAME)
    os.makedirs(out_dir, exist_ok=True)

    # **撮影のあいだだけ Nanite を切る。**
    #
    # Nanite を有効にしたメッシュは SceneCapture2D に映らず、空だけが
    # 写った画像が出る（地面も車も 525 本の木も全て消える）。
    # レベル側の Nanite 設定は正しいので、**確認用の撮影でだけ**無効化する。
    world = editor_world()
    unreal.SystemLibrary.execute_console_command(world, "r.Nanite 0")

    # **撮影対象が本当に読み込まれているかを数える。**
    # 空の絵が出たとき、「描画の問題」なのか「レベルが空」なのかを
    # 切り分けられないと、直しようがない。
    actors = unreal.get_editor_subsystem(
        unreal.EditorActorSubsystem).get_all_level_actors()
    kinds = {}
    for actor in actors:
        name = actor.get_class().get_name()
        kinds[name] = kinds.get(name, 0) + 1
    log("レベル内 Actor %d 個: %s" % (
        len(actors),
        ", ".join("%s x%d" % (k, v) for k, v in sorted(kinds.items()))))

    target = make_render_target()

    # 視点は物理座標を UE へ直したもの（X 前方 / Y 右 / Z 上、cm）。
    #
    #   スタートライン付近の車を斜め後ろから
    #   ヘアピン（中心線 s=400m、x=400 y=0 付近）を上空から
    #   コース全体を俯瞰
    # (名前, カメラ位置, 注視点, 画角)。単位は cm。
    #
    # コース中心線の座標（物理 [m]、X 前方 / Y 左）を UE へ直したもの:
    #   スタートライン  x=0    y=0
    #   ヘアピン        x=400  y=0 付近（中心線 s=400m）
    #   コース中心      x=160  y=55 付近（範囲 x -109..426 / y 0..110）
    views = [
        ("car_start",
         unreal.Vector(-1200.0, 600.0, 300.0), unreal.Vector(200.0, 0.0, 70.0), 60.0),
        ("hairpin",
         unreal.Vector(38000.0, 6000.0, 3000.0), unreal.Vector(42500.0, -2500.0, 0.0), 70.0),
        ("overview",
         unreal.Vector(16000.0, 30000.0, 18000.0), unreal.Vector(16000.0, -5500.0, 0.0), 75.0),
    ]

    for name, location, target_point, fov in views:
        capture(target, location, look_at(location, target_point),
                fov, out_dir, name + ".png")

    unreal.SystemLibrary.execute_console_command(world, "r.Nanite 1")
    log("出力先: %s" % out_dir)


main()
