# 入力機器 — DualSense / T300RS / H パターンシフター

調査日 **2026-09-02**。対象は **UE 5.8**（`C:\Program Files\Epic Games\UE_5.8`）。

対応する変更は `Unreal/ZN6DigitalTwin/Config/DefaultInput.ini` の末尾にある。
**C++ は一切触っていない。** 必要な変更は §8 に場所と理由だけ書いた。

---

## 0. 先に — **実機が無い。動作確認はしていない**

DualSense も T300RS も TH8A も**手元に無い。**
したがってこの文書に「動いた」と書けることは1つも無い。

代わりに、根拠を2種類に分けて全部明示する。

| 記号 | 意味 |
|---|---|
| **[確認]** | UE 5.8 のソース、または Microsoft の公式ドキュメントを**読んで**確かめた |
| **[未検証]** | 読んだ範囲からの推論。**実機で確かめていない** |

**[確認] は「ソースにそう書いてある」ことの確認であって、
「挿したら動いた」の確認ではない。** ここを混ぜない。

憲法ルール6・16（失敗した状態を完成と呼ばない）に照らして、
**この作業の成果は「設定と、実機が来たときの手順書」であって、
「PS5 コントローラとハンドルに対応した」ではない。**

### 0.1 結論だけ3行で

1. **XInput として見えるパッドは UE 標準で動く見込み。** DualSense は
   そのままでは XInput ではないので、Steam Input 等の変換が要る（§2）
2. **T300RS を読むにはプラグインが要る**（RawInput か GameInput）。どちらも既定は無効（§3）
3. **T300RS のフォースフィードバックは UE 標準では出せない。**
   出す口が engine 側に無い。これは推測ではなくソースで確認した（§7）

---

## 1. 今の入力の作り（変更前の事実）

| 項目 | 実装 |
|---|---|
| 割り当て | `Config/DefaultInput.ini` の **旧式** `ActionMappings` / `AxisMappings` |
| 受け口 | `AZN6VehicleActor::SetupPlayerInputComponent`（`ZN6VehicleActor.cpp:1396`）の `BindAxis` / `BindAction` |
| 生の値 | `RawThrottle` / `RawBrake` / `RawSteer` / `RawClutch` / `RawHandbrake` |
| 変換 | `AZN6VehicleActor::ApplyDriverInput`（同 `:1437`）。**キーボード用の平滑化が入っている** |
| 物理への口 | `ZN6::FControlInput`（`Physics/ZN6Vehicle.h:51`）= Python の `ControlInput`（`Physics/vehicle.py:52`） |
| 変速 | `ZN6_ShiftUp` / `ZN6_ShiftDown` の**相対**。`Control.GearIndex` を ±1 |

**[確認] 旧式の割り当ては UE 5.8 でもまだ動く。**
`UInputComponent::BindAxis` は `LogDeprecatedBindingWarning` を出すだけで、
機能は残っている（`Engine/Source/Runtime/Engine/Classes/Components/InputComponent.h:930`）。
`DefaultInput.ini` の `bEnabledLegacyMappingDeprecationWarnings=True` はその警告の
オンオフである。だから今回の追記も**同じ旧式の書き方に揃えた。**
Enhanced Input への移行は入力機器の対応とは別の作業なので、混ぜない。

---

## 2. デバイスごとに — 何が UE 標準で動き、何にプラグインが要るか

| デバイス | UE 標準（プラグイン追加なし） | 要るもの |
|---|---|---|
| Xbox / XInput 互換パッド | **動く見込み [未検証]** | なし |
| **DualSense（PS5）** | **動かない** | 外部で XInput へ変換（§2.1） |
| **T300RS（ハンドル・ペダル）** | **動かない** | RawInput か GameInput プラグイン（§3） |
| **TH8A（H パターン）** | **動かない** | 同上 **＋ C++ の変更**（§4） |
| T300RS の**フォースフィードバック** | **出せない** | UE 標準に口が無い（§7） |

### 2.1 DualSense

**[確認] `XInputDevice` プラグインは既定で有効。**
`Engine/Plugins/Runtime/Windows/XInputDevice/XInputDevice.uplugin` に
`"EnabledByDefault": true`。だから **XInput として見えるパッドは、
プロジェクト側で何もしなくても `Gamepad_*` キーが鳴るはず**である [未検証]。

**問題は DualSense が XInput デバイスではないこと。**
Windows は DualSense を汎用 HID ゲームパッドとして扱う。XInput の
API からは見えない。

**[確認] エンジン同梱の `WinDualShock` プラグインは使えない。**
`Engine/Plugins/Runtime/Windows/WinDualShock/Source/WinDualShock/WinDualShock.Build.cs`
は Sony の `LibScePad` が見つかったときだけ機能を有効にする:

```
PublicDefinitions.Add("DUALSHOCK4_SUPPORT=" + (bHasSupport ? "1" : "0"));
PublicDefinitions.Add("DUALSENSE_SUPPORT=" + (PlatformName.Equals("PS5") ? "1" : "0"));
```

`LibScePad` は PlayStation の開発者向け SDK に入っているもので、
**ライセンスを受けた開発者でなければ手に入らない。** 無い環境では
`DUALSHOCK4_SUPPORT=0` になり、プラグインの中身は丸ごとコンパイルされない。
**「プラグインを有効にすれば DualSense が使える」ではない。**

したがって、現実的な選択肢は次の2つ。

| 方法 | 中身 | 判定 |
|---|---|---|
| **A. Steam Input（または DS4Windows）で Xbox 互換にエミュレート** | OS 側で仮想 XInput パッドになる。ゲームからは普通の Xbox パッドに見える | **これを既定とする。** 追記した `Gamepad_*` の割り当てはこの前提 [未検証] |
| B. `GameInput` プラグインの `bProcessGamepad` | GameInput は HID 系のデバイスも扱う | **[未検証]。** DualSense が `GameInputKindGamepad` として出るか未確認 |

**[確認] B を試すときの注意がエンジンのコメントに書いてある。**
`GameInputDeveloperSettings.h:366-373`:

> Note: If you are using Game Input on Windows where there are other Input Device
> module plugins (XInput, WinDualShock, etc) you should disable those to use this.
> Otherwise, there will be "duplicate" gamepad input events.

つまり **GameInput と XInputDevice を同時に有効にすると入力が二重に来る。**
片方を切ること。

**アダプティブトリガ・触覚・ライトバー・ジャイロ・タッチパッドは、
どの方法でも取れない。** これらは PS5 固有の機能で、UE 標準の
Windows 経路には対応するキーも API も無い [確認]。

### 2.2 T300RS

Windows では DirectInput / HID のハンドルとして見える。UE の Windows 標準経路
（XInput）からは**軸が1本も見えない。** §3 のどちらかのプラグインが要る。

**[確認] Microsoft の `Windows.Gaming.Input.RacingWheel` は T300RS を
明示的に対応機種として挙げている。** 「Force feedback is supported on the
following device models」の表に Thrustmaster **T300RS** がある
（`RacingWheel` クラスのドキュメント。§9 出典1）。

**ただしこれは Windows の API の話であって、UE がそこまで通してくれるかは別。**
UE 5.8 の `GameInput` プラグインが使うのは GameInput API（`GameInputKindRacingWheel`）で、
上の WinRT API とは別物である。**T300RS が UE から「レーシングホイール」として
見えるかどうかは未確認 [未検証]。**

### 2.3 TH8A（H パターンシフター）

**2つの問題が別々にある。混ぜないこと。**

1. **読めるか** — §3 のプラグインが要る。TH8A は独立した USB デバイスとして
   繋ぐこともハンドルに刺すこともできる（接続方法で見え方が変わる）[未検証]
2. **読めても入らない** — H パターンは**絶対位置**を伝える。今の C++ は
   相対変速しか受け取れない。**設定では直せない。** §4

---

## 3. プラグインの選び方

**[確認] どちらも Win64 専用で、どちらも既定では無効。**

| | RawInput | GameInput |
|---|---|---|
| 場所 | `Engine/Plugins/Experimental/RawInput` | `Engine/Plugins/Runtime/GameInput` |
| 既定 | `EnabledByDefault: false` | `EnabledByDefault: false` |
| 状態 | **`"DeprecatedEngineVersion": "5.8"`。UE 5.8 で非推奨** | 現行 |
| 見え方 | `GenericUSBController_Axis1..24` / `Button1..96`（無名の軸番号） | `GameInput_RacingWheel_Wheel` 等（意味のある名前） |
| H パターン | ボタンとして来る（絶対位置を自前で組み立てる） | `..._PatternShifterGear` **1本の軸で来る** |
| FFB | **出せない**（§7） | **出せない**（§7） |

**推奨: GameInput を先に試す。** 名前が付いていて、H パターンが1本の軸で
来るぶん扱いが素直で、非推奨でもない。**駄目だったときの逃げ道が RawInput。**

### 3.1 RawInput — 軸番号を割り出す手順

**軸番号を推測で ini に書かない**（憲法ルール1・2）。T300RS のどの軸が
ハンドルでどれがペダルかは、資料によって食い違っていて確定できなかった。
VendorID `0x044F`（ThrustMaster）は複数の資料で一致するが、
ProductID は動作モードで変わる（`0xB65D` / `0xB66D` / `0xB66E` / `0xB66F` の
いずれか、という記述が見つかる。§9 出典5）。**確定させない。**

実機が来たらこうする。

1. `ZN6DigitalTwin.uproject` の `Plugins` に `RawInput` を足して有効化
2. 起動して、コンソール（`@` か `~`）で **`showdebug RawInput`**
   **[確認]** `FRawInputWindows::ShowDebugInfo` が
   `RawInputWindows.cpp:591` にあり、`AHUD::OnShowDebugInfo` に繋がっている。
   接続中のデバイスごとに `Analog ID: ... Val: ...` と
   `Button: ... Val: TRUE/FALSE` を毎フレーム描く
3. ハンドルを左右に回す → 動いた軸の番号を控える
4. アクセル・ブレーキ・クラッチを1本ずつ踏む → 同じく控える
5. シフターを 1速〜6速・R に入れる → **どのボタンが TRUE になるか**を控える
6. `DefaultInput.ini` の雛形（`[/Script/RawInput.RawInputSettings]` の
   コメント）に埋めて、コメントを外す

**[確認] 値の正規化はこうなっている**
（`RawInputWindows.h` の `FAnalogData::GetValue`）:

```
bGamepadStick = True :  ((v/(max-min)) - 0.5) * 2 * (bInverted ? -1 : 1) + Offset   → -1..1
bGamepadStick = False:  (v/(max-min)) * (bInverted ? -1 : 1) + Offset               →  0..1
```

**ここに罠がある。** ペダルは機器によって「離した状態が最大値」で来る。
そのとき `bInverted=True` だけを付けると値域が **-1..0** になり、
アクセルは常に0のままになる。**`Offset=1.0` を必ず一緒に入れること。**

もう1つの罠。**旧式の `AxisMappings` は、同じ軸名に割り当てた全部の入力を
足し算する。** キーボードの `W` とペダルを両方 `ZN6_Throttle` に割り当てた
状態で、ペダルが未較正で 1.0 を返し続けると、**アクセルが踏みっぱなしになる。**
必ず §3.1 の手順で値を目で見てから繋ぐこと。

### 3.2 GameInput — レーシングホイールを有効にする

`DefaultInput.ini` に追記した `GameInput_RacingWheel_*` の割り当ては、
**次の3つが揃わないと1つも鳴らない。**

1. `ZN6DigitalTwin.uproject` で `GameInput` プラグインを有効化
2. **`bProcessRacingWheel = true`**
   **[確認]** 既定 `false`。`GameInputDeveloperSettings.h:422`。
   コメントに `Note: This is experimental!` とある
3. **`RacingWheelDeadzone` を 0 付近へ**
   **[確認]** 既定は `7849.0f / 32768.0f` = **0.2395**（同 `:425`）。
   `FGameInputRacingWheelProcessor::ProcessWheelAnalogState` が
   ハンドル・ペダル・**そして絶対ギアの値にまで**この不感帯を掛ける。
   **ハンドルの中央 24% が死ぬ。** 触らずに走らせて「効きが悪い」と
   判断しないこと

**2 と 3 は `DefaultInput.ini` には書けない可能性が高い。**
**[確認]** `UGameInputPlatformSettings` は `UPlatformSettings` 派生で
`UCLASS(config = Input, DefaultConfig)` かつ `perObjectConfig`、さらに
`GetConfigOverridePlatform()` がプラットフォーム名（`Windows`）を返す
（`PlatformSettings.h:69`）。オブジェクト名は
`GameInputPlatformSettings_Windows`（`PlatformSettingsManager.cpp:65`）なので、
節は `[GameInputPlatformSettings_Windows GameInputPlatformSettings]` になり、
読み先は Windows 用の Input 設定階層になる。

**この節を `DefaultInput.ini` に書いて効くかどうかは未検証 [未検証]。**
効かない場合は `Config/Windows/WindowsInput.ini` を作るか、エディタの
Project Settings → Game Input から設定する。**確かめずに「設定した」と
書かないこと。** そのため ini には**書いていない**（コメントで場所だけ示した）。

**[確認] 値域**（Microsoft のドキュメント。§9 出典2）:

| メンバ | 値域 |
|---|---|
| `wheel` | **-1.0 〜 1.0** |
| `throttle` / `brake` / `clutch` / `handbrake` | **0.0 〜 1.0** |
| `patternShifterGear` | `int32_t`。**-1 = R、0 = N、1 以上が前進段**（§9 出典3） |

---

## 4. H パターンは絶対位置。今の実装は相対変速

### 4.1 何が噛み合わないか

今の実装:

```cpp
// ZN6VehicleActor.cpp:1426
void AZN6VehicleActor::ShiftUp()
{
    Control.GearIndex = FMath::Min(Control.GearIndex + 1, ZN6::ForwardGearCount - 1);
}
```

これは「**1段上げろ**」という命令である。パドルやシーケンシャルのレバーは
これでよい。

H パターンのシフターが伝えるのは「**いまレバーは3速の位置にある**」という
状態である。**命令ではない。** 4速から2速へ一気に入れれば、押されるのは
「2速の位置」1つだけで、「2段下げろ」は誰も送らない。

**相対の口に絶対の情報は入らない。** ini の割り当てだけでは解決しない。

### 4.2 さらに悪いこと — **物理にニュートラルとリバースが無い**

**[確認]** C++ 側の `ZN6::FControlInput::GearIndex` は
`int32 GearIndex = 0;`（`Physics/ZN6Vehicle.h:56`）で、使う側は

```cpp
// Physics/ZN6Components.cpp:162
check(GearIndex >= 0 && GearIndex < ForwardGearCount);   // ForwardGearCount = 6
```

**0〜5 の前進6段しか受け付けない。範囲外を渡すと `check` で落ちる。**

一方、Python 側の `Drivetrain` は `FORWARD_GEARS + ["R"]` でギア比を
持っている（`Physics/drivetrain.py:26`）。**リバースの比は Python にはあり、
C++ には無い。**

H パターンのシフターは

- **どこにも入っていない状態（ニュートラル）が普通にある**
- **R の位置がある**

ので、**そのまま繋ぐと `check` で落ちるか、N と R を黙って握り潰すことになる。**
黙って握り潰すのは憲法ルール6（エラーを隠蔽しない）に反する。

### 4.3 どうするか（方針。実装は §8 に場所だけ）

**段階を分ける。**

| 段 | 内容 | 何が要るか |
|---|---|---|
| **1** | 絶対変速の口を作る（前進6段だけ） | `SetGearAbsolute(int32)`。C++ |
| **2** | ニュートラルを表現する | `FControlInput` に N を足すか、`Clutch=0` で代用するか**決める**。C++・物理 |
| **3** | リバースを表現する | C++ の `Drivetrain` に R の比を足す。Python と数値を合わせる。**Phase 8 の判定基準に関わる** |

段1だけでも「1〜6速の H パターンで走れる」ようになる。
**段2・3を飛ばして「シフターに対応した」と書かないこと。**

**ニュートラルの代用について（決めるのは実装者）**

`Control.Clutch = 0.0`（完全に切る）はニュートラルと**似ているが同じではない。**
クラッチを切った状態ではエンジン側の慣性が車輪から切り離されるが、
ギアは入ったままなので `GearIndex` に応じた反射慣性の扱いが変わる
（`Physics/vehicle.py:342` の `reflected_inertia_at_wheel_kgm2`）。
**「だいたい同じだから」で代用すると、それは物理モデルの改変である。**
代用するなら、そう明記したうえで数値の差を測ること。

### 4.4 ini に何を置いたか

`ZN6_Gear1` 〜 `ZN6_Gear6` / `ZN6_GearReverse` / `ZN6_GearNeutral` の
ActionMapping と、`ZN6_GearAbsolute` の AxisMapping を追加した。

**現時点ではこれらは1つも C++ に繋がっていない。押しても何も起きない。**
実装の目印として置いてある。

**キーボードの数字キーにも同じ名前を割り当ててある。**
実機が無くても、C++ を足した人がその場で絶対変速を確かめられるようにするため。
**これは「実機の代わりの検証」ではなく、「C++ の受け口が動くかの検証」である。**

---

## 5. `FZN6DriverFeel` の平滑化 — アナログでは邪魔になる

### 5.1 今なにをしているか

`ApplyDriverInput`（`ZN6VehicleActor.cpp:1437`）は生の入力をそのまま
物理へ渡さず、時間をかけて目標値へ寄せる。**理由はコードのコメントに
書いてあるとおりで、正しい。**

> **キーボードの 0/1 をそのまま物理へ入れない。**
> 踏み込み量が無い入力を生で渡すと、アクセルもブレーキも常に全開全閉に
> なり、FR では即スピンする。

**キーボードには踏み込み量が無いので、無い情報を時間で作っている。**

数値（`FZN6DriverFeel`、`ZN6VehicleActor.h:124`）と、そこから出る時間:

| 項目 | 値 | 意味 |
|---|---|---|
| `PedalRatePerS` | 4.0 /s | 0 → 全開まで **0.25 秒** |
| `SteerRateRadPerS` | 1.1 rad/s | 中立 → フルロックまで **0.40 秒** |
| `SteerReturnRateRadPerS` | 2.2 rad/s | 戻りは **0.20 秒** |
| `SteerSpeedFalloffPerMps` | 0.045 /(m/s) | 速度で最大舵角を絞る |

最大舵角は `MaxSteerRad = atan2(2.570, 5.4) = 0.4443 rad = **25.5°**`
（`ZN6VehicleActor.cpp:689`。ホイールベース 2.570 m と最小回転半径 5.4 m は
どちらも `official`）。

速度による絞りは `1 / (1 + 0.045 * v)` なので

| 速度 | 係数 | 使える最大舵角 |
|---|---|---|
| 0 km/h | 1.000 | 25.5° |
| 60 km/h | 0.571 | **14.5°** |
| 100 km/h | 0.444 | **11.3°** |

### 5.2 アナログ入力に掛けると何が起きるか

**ハンドルもトリガも、すでに踏み込み量を持っている。**
そこへ同じ処理を掛けると、無い情報を作るのではなく、**ある情報を捨てる。**

1. **立ち上がりの緩和** — 実際の操作より最大 0.25 秒（ペダル）／
   0.40 秒（操舵）遅れて物理に届く。**カウンターステアが間に合わない。**
   FR でパワーオーバーステアを起こす車（`CLAUDE.md`「FR であることの帰結」）で、
   0.4 秒の操舵遅れは致命的である
2. **速度による舵角の絞り** — ハンドルの**同じ角度が、速度によって違う
   舵角を意味する**ことになる。実車のステアリングギア比は一定なので、
   これは運転者の手が知っている感覚を壊す。100 km/h でフルロックまで
   回しても 11.3° しか切れない
3. **不感帯の二重掛け** — GameInput の `RacingWheelDeadzone`（既定 0.2395）と
   `AxisConfig` の `DeadZone` は別物で、**両方掛かる**

### 5.3 どうすべきか

**アナログのときは、平滑化と舵角の絞りを両方切る。**
そのうえで、切ったことを明示する。

| 入力 | キーボード | アナログ |
|---|---|---|
| アクセル・ブレーキ | `Approach` で緩和 | **そのまま渡す** |
| 操舵の速さ制限 | `SteerRateRadPerS` | **無し**（人の手が速さを決める） |
| 速度による舵角の絞り | `SteerSpeedFalloffPerMps` | **無し** |
| 操舵の写像 | — | ハンドルの可動域全体を **±`MaxSteerRad`** に線形で割り当てる |
| クラッチ・サイド | 現状のまま | 現状のまま（**もともと平滑化されていない**） |
| カウントダウン中の遮断（`Race.InputScale()`） | そのまま | **そのまま。切らない** |

**クラッチとサイドは今も生値である**（`ApplyDriverInput` の
`Control.Clutch = 1.0 - clamp(RawClutch)` と `Control.Handbrake = clamp(...)`）。
**アナログのペダルを繋いだ瞬間から、半クラッチが物理にそのまま入る。**
`FControlInput::Clutch` は最初から 0..1 の連続値なので（`vehicle.py:57` の
「**bool ではない**」）、ここは物理側の改造が要らない。**唯一そのまま噛み合う場所。**

### 5.4 切り替えをどう決めるか — **自動で切り替えない**

「最後に触った機器で自動的に切り替える」は**やらないほうがよい。**

平滑化の有無は**ラップタイムを変える。** 自動で切り替わると、
「どちらの状態で出た結果か」が記録に残らない。§5.1 のとおり、
これらは補助であって車両仕様ではない（憲法ルール18）ので、
**検証結果には「補助を切って出した」と書けなければならない。**

したがって:

- **明示的な設定にする**（既定はキーボード＝現状のまま。**既存の結果が
  1ビットも変わらないこと**）
- **起動オプションで上書きできるようにする**（`-ZN6Analog` のような）
- **HUD に今どちらかを出す。** 信頼度表示と同じ扱い

### 5.5 ハンドルの回転角と舵角の対応 — **物理的には決められない**

**[確認] `vehicle.json` の `steering.gear_ratio` と `steering.lock_to_lock` は
どちらも `"unknown"`。**

つまり「ハンドルを 90° 回すと前輪が何度切れるか」を実車のデータから
出すことが**できない。** 出典が無い（憲法ルール1・2）。

だから §5.3 の「可動域全体を ±`MaxSteerRad` に線形で割り当てる」は
**演出であって車両仕様ではない**（憲法ルール18）。そう明記すること。
T300RS 側の回転角（1080° / 900° など）は Thrustmaster のドライバで
変えられるが、**その設定値をこちらは知らない。**

ここは埋めるべき穴として残す。`steering.gear_ratio` の出典が取れたら、
ハンドルの回転角 → 舵角を実車どおりに写せる。

---

## 6. メニューがパッド・ハンドルで操作できない

**[確認] `SZN6Menu::OnKeyDown`（`UI/SZN6Menu.cpp:214-219`）は
キーボードのキーしか見ていない。**

```cpp
if (Pressed == EKeys::Up || Pressed == EKeys::W)          { MoveSelection(-1); }
else if (Pressed == EKeys::Down || Pressed == EKeys::S)   { MoveSelection(1); }
...
else if (Pressed == EKeys::Escape || Pressed == EKeys::BackSpace) { GoBack(); }
```

**[確認] メニューを開くと `AZN6VehicleActor::SyncInputModeToMenu`
（`ZN6VehicleActor.cpp:530`）が `FInputModeUIOnly` に切り替える。**
以後、Pawn に束ねた `ZN6_Menu` の ActionMapping は発火しない。
閉じる操作は上の `OnKeyDown` だけが受ける。

**したがって、パッドのボタンでメニューを開くと、パッドでは閉じられない。**
キーボードに手を伸ばすまで詰む。

**だから `ZN6_Menu` をパッドにもハンドルにも割り当てなかった。**
これは書き忘れではない。`DefaultInput.ini` にもその旨を書いた。

`Docs/HANDOFF_NEXT_SESSION.md` §0.1 が記録している事故
（「操作できないメニューを『入れた』と報告した」）と**同じ形**である。
先に `SZN6Menu` を直すこと（§8）。

---

## 7. フォースフィードバック — **UE 標準では出せない**

**推測ではない。両方のプラグインのソースで確認した。**

**[確認] RawInput は空実装。**
`Engine/Plugins/Experimental/RawInput/Source/RawInput/Public/Windows/RawInputWindows.h:259-260`:

```cpp
virtual void SetChannelValue(int32 ControllerId, FForceFeedbackChannelType ChannelType, float Value) override {}
virtual void SetChannelValues(int32 ControllerId, const FForceFeedbackValues& Values) override {}
```

**中身が無い。呼んでも何も起きない。**
`UForceFeedbackEffect` を再生してもハンドルは1ミリも動かない。

**[確認] GameInput は「振動」しか送らない。**
`GameInput/Source/GameInputBase/Private/IGameInputDeviceInterface.cpp:281` の
`SetChannelValues` は `GameInputRumbleParams`（`lowFrequency` /
`highFrequency` / `leftTrigger` / `rightTrigger`）を組み立てて
`SetRumbleState` を呼ぶだけである。

**振動モーターとハンドルのフォースフィードバックは別物である。**
FFB は「ハンドルを特定の向きに、特定の強さで押す」定常力（constant force）で、
振動の強さ1つでは表現できない。

**[確認] Windows 側には口がある。**
`Windows.Gaming.Input.RacingWheel` の `WheelMotor`（`ForceFeedbackMotor`）が
それで、T300RS は対応機種の表に載っている（§9 出典1）。
**UE 5.8 の GameInput プラグインはそこへ繋いでいない。**

### 7.1 出したいなら何が要るか

| 方法 | 中身 | 評価 |
|---|---|---|
| **A. 自前の `IInputDevice` プラグインを書く** | DirectInput の FFB か `Windows.Gaming.Input.ForceFeedback` を叩き、`IForceFeedbackSystem` に繋ぐ | 一番筋が良いが**それ自体が1つのフェーズ**。物理から「ハンドルに返すべき力」を出す設計も要る |
| B. 既存の第三者プラグインを使う | `UEDirectInput` 等がある | **ライセンスと保守の確認が先**（`Docs/PHASE15_DATA_LICENCE.md` と同じ作法）。未調査 |
| C. 出さない | — | **今はこれ。** 「FFB は出ない」と画面にも書く |

### 7.2 出すと決めたときに先に決めること

**「何の力を返すか」を物理から出せなければ、FFB は演出になる。**

実車のステアリングに返る力の主な源はセルフアライニングトルク（タイヤの
横力とニューマチックトレール／キャスタトレールの積）である。今のモデルは
横力（`VehicleOutputs.tire_fy_n`）とスリップ角（`slip_angle_rad`）は出しているが、

- **ニューマチックトレールを出していない**
- **キャスタ角・キャスタトレールが `vehicle.json` に無い**
- **ステアリングギア比が `unknown`**（§5.5）

**この3つが埋まるまで、物理的に正しい FFB は出せない。**
それでも出すなら**「横力に比例させた演出」と明記する**こと（憲法ルール18）。
**実車のステアリング反力だと名乗らない。**

---

## 8. C++ 側に要る変更（**この作業では触っていない。場所と理由だけ**）

| # | 場所 | 何を | なぜ |
|---|---|---|---|
| 1 | `ZN6VehicleActor.cpp:1437` `ApplyDriverInput` | アナログ用の経路を分ける（平滑化と舵角の絞りを飛ばす） | §5.2。踏み込み量のある入力に緩和を掛けると情報を捨てる |
| 2 | `ZN6VehicleActor.h:124` `FZN6DriverFeel` | 「アナログか」の切り替えを持たせる。**既定はキーボード** | §5.4。既存の結果が変わらないこと |
| 3 | `ZN6VehicleActor.h:488` 付近 | `SetGearAbsolute(int32)` を足す | §4.3 段1。H パターンは絶対位置 |
| 4 | `ZN6VehicleActor.cpp:1396` `SetupPlayerInputComponent` | `ZN6_Gear1..6` / `ZN6_GearReverse` / `ZN6_GearNeutral` / `ZN6_GearAbsolute` を束ねる | §4.4。ini 側は用意済み。**今は受け手が無い** |
| 5 | `Physics/ZN6Vehicle.h:51` `FControlInput` と `Physics/ZN6Components.cpp:162` の `check` | N と R をどう表すか決める | §4.2。**前進6段しか無い。範囲外は `check` で落ちる** |
| 6 | C++ の `Drivetrain`（`ZN6Components.h:110` `GearRatios[6]`） | R の比を足すか、足さないと決める | §4.2。**Python には R がある**（`Physics/drivetrain.py:26`）。片方だけ足すと Phase 8 の一致検査が壊れる |
| 7 | `UI/SZN6Menu.cpp:214-219` `OnKeyDown` | `Gamepad_*` を受ける。スティックは `OnAnalogValueChanged` | §6。**先にこれを直すまで、パッドにメニューを割り当ててはいけない** |
| 8 | `ZN6VehicleActor.cpp:1534`（`DrawTelemetry` の操作説明） | 繋がっている機器の操作を出す | 今はキーボードの説明が固定で出ている |

**5 と 6 は `Physics/` に入るので、他のエージェントと衝突する。
着手前に担当を確認すること。**

---

## 9. 未検証項目の一覧（**実機が来たら最初にこれを潰す**）

| # | 項目 | どう確かめるか |
|---|---|---|
| 1 | DualSense を Steam Input で XInput 化すると `Gamepad_*` が鳴るか | 挿して `showdebug input`。トリガとスティックが動くか |
| 2 | パッドの操舵の不感帯 0.12 が妥当か | 直進で手を離して流れないか。流れるなら上げる |
| 3 | GameInput プラグインで T300RS が `GameInputKindRacingWheel` として出るか | `LogGameInput` に出るデバイス種別を見る |
| 4 | `[GameInputPlatformSettings_Windows GameInputPlatformSettings]` を `DefaultInput.ini` に書いて効くか | `bProcessRacingWheel` が反映されるか。駄目なら `Config/Windows/WindowsInput.ini` |
| 5 | `RacingWheelDeadzone` を 0 にしないとハンドル中央が死ぬか | 中央付近でゆっくり回して舵角が動くか |
| 6 | T300RS の VendorID / ProductID（RawInput 経路のとき） | `showdebug RawInput`、またはデバイスマネージャ |
| 7 | T300RS の軸番号の割り当て | `showdebug RawInput`（§3.1） |
| 8 | ペダルが反転しているか（`bInverted` と `Offset` が要るか） | 同上。**離した状態で 0 か 1 か** |
| 9 | TH8A が独立デバイスとして見えるか、ハンドル経由か | 接続方法を変えて `showdebug RawInput` |
| 10 | `patternShifterGear` が実際に -1 / 0 / 1..6 で来るか | シフターを1段ずつ入れて値を読む |
| 11 | GameInput と XInputDevice を同時に有効にすると二重に来るか | 片方ずつ切って比べる |
| 12 | 平滑化を切った状態のラップタイム差 | **切る前と切った後の両方を記録する。片方だけ残さない** |

---

## 10. 出典

すべて **2026-09-02 に確認**。

### エンジンのソース（このマシンの `C:\Program Files\Epic Games\UE_5.8`）

| # | ファイル | 何を確かめたか |
|---|---|---|
| S1 | `Engine/Plugins/Experimental/RawInput/RawInput.uplugin` | `EnabledByDefault: false`、`"DeprecatedEngineVersion": "5.8"`、Win64 のみ |
| S2 | `.../RawInput/Public/Windows/RawInputWindows.h:259-260` | **FFB が空実装 `{}`** |
| S3 | `.../RawInput/Public/Windows/RawInputWindows.h`（`FAnalogData::GetValue`） | 軸の正規化式。`bGamepadStick` / `bInverted` / `Offset` の効き方 |
| S4 | `.../RawInput/Public/RawInputSettings.h` | ini の構造。`VendorID` / `ProductID` / `AxisProperties` / `ButtonProperties` / `bRegisterDefaultDevice` |
| S5 | `.../RawInput/Private/RawInput.cpp:17-410` | キー名 `GenericUSBController_Axis1..24` / `Button1..96` |
| S6 | `.../RawInput/Private/Windows/RawInputWindows.cpp:591` | `showdebug RawInput` の表示 |
| S7 | `Engine/Plugins/Runtime/GameInput/GameInput.uplugin` | `EnabledByDefault: false`、Win64 のみ |
| S8 | `.../GameInputBase/Private/GameInputKeyTypes.cpp:9-22` | `GameInput_RacingWheel_*` のキー名 |
| S9 | `.../GameInputBase/Public/GameInputDeveloperSettings.h:411-433` | `bProcessRacingWheel` 既定 false（Experimental）、`RacingWheelDeadzone` 既定 7849/32768 |
| S10 | `.../GameInputBase/Public/GameInputDeveloperSettings.h:366-373` | GameInput と XInput を同時に有効にすると入力が二重に来る |
| S11 | `.../GameInputBase/Private/Processors/GameInputDeviceProcessor_RacingWheel.cpp` | `patternShifterGear` を軸として流す。不感帯がギア値にも掛かる |
| S12 | `.../GameInputBase/Private/IGameInputDeviceInterface.cpp:281` | **FFB は `SetRumbleState`（振動）だけ** |
| S13 | `Engine/Plugins/Runtime/Windows/WinDualShock/Source/WinDualShock/WinDualShock.Build.cs` | `DUALSENSE_SUPPORT` は PS5 SDK があるときだけ 1 |
| S14 | `Engine/Plugins/Runtime/Windows/XInputDevice/XInputDevice.uplugin` | `EnabledByDefault: true` |
| S15 | `Engine/Source/Runtime/Engine/Private/UserInterface/InputSettings.cpp:121-148` | `AxisConfig` は**同名の後勝ち**。未登録の Axis1D キーは `DeadZone = 0` |
| S16 | `Engine/Source/Runtime/Engine/Classes/Components/InputComponent.h:930` | 旧式 `BindAxis` は警告を出すだけで機能する |
| S17 | `Engine/Source/Runtime/DeveloperSettings/.../PlatformSettings.h:69` と `PlatformSettingsManager.cpp:65` | プラットフォーム別設定の節名の作られ方 |

### Microsoft のドキュメント

| # | URL | 何を確かめたか |
|---|---|---|
| 1 | <https://learn.microsoft.com/en-us/uwp/api/windows.gaming.input.racingwheel> | 「RacingWheel supports any GIP or XUSB compatible racing wheel」。**FFB 対応機種の表に Thrustmaster T300RS がある** |
| 2 | <https://learn.microsoft.com/en-us/gaming/gdk/docs/reference/input/gameinput/structs/gameinputracingwheelstate> | `wheel` は -1..1、`throttle`/`brake`/`clutch`/`handbrake` は 0..1、`patternShifterGear` は `int32_t` |
| 3 | <https://learn.microsoft.com/en-us/windows/uwp/gaming/racing-wheel-and-force-feedback> | 「A value of -1 or 0 correspond to the reverse and neutral gears, respectively」。FFB は `WheelMotor` が null でなければ対応 |
| 4 | <https://dev.epicgames.com/documentation/en-us/unreal-engine/rawinput-plugin-in-unreal-engine> | RawInput の位置づけ（XInput で扱えない機器のための仕組み）。**ini の書式は載っていない** — 書式は S4 から取った |
| 5 | <https://github.com/cazzoo/hid-tmff2> / <https://www.the-sz.com/products/usbid/index.php?v=044f> | ThrustMaster の VendorID は `0x044F`。**ProductID は動作モードで変わり、資料間で食い違う。確定させていない** |

**出典5は一次資料ではない。** T300RS の ProductID を ini に書かなかったのは
このためである（憲法ルール2）。

---

## 11. 追加した割り当ての一覧

`Config/DefaultInput.ini` の末尾。**[A] は UE 標準、[B] はプラグインが要る、
[C] は C++ に受け手が無く現時点では何も起きない。**

### 軸

| 軸名 | キー | 分類 | 備考 |
|---|---|---|---|
| `ZN6_Throttle` | `Gamepad_RightTriggerAxis` | [A] | 0..1 |
| `ZN6_Brake` | `Gamepad_LeftTriggerAxis` | [A] | 0..1 |
| `ZN6_Steer` | `Gamepad_LeftX`（Scale **-1**） | [A] | 既存の割り当てが**左を正**にしているため反転 |
| `ZN6_Clutch` | `Gamepad_FaceButton_Left`（□） | [A] | ボタンなので 0/1。**半クラッチは出せない** |
| `ZN6_Handbrake` | `Gamepad_FaceButton_Bottom`（×） | [A] | 同上 |
| `ZN6_Throttle` | `GameInput_RacingWheel_Throttle` | [B] | 0..1 |
| `ZN6_Brake` | `GameInput_RacingWheel_Brake` | [B] | 0..1 |
| `ZN6_Steer` | `GameInput_RacingWheel_Wheel`（Scale **-1**） | [B] | -1..1 |
| `ZN6_Clutch` | `GameInput_RacingWheel_Clutch` | [B] | 0..1。**連続値がそのまま物理へ入る** |
| `ZN6_Handbrake` | `GameInput_RacingWheel_Handbrake` | [B] | T300RS 本体にサイドは無い |
| `ZN6_GearAbsolute` | `GameInput_RacingWheel_PatternShifterGear` | **[C]** | -1=R / 0=N / 1..6 |

### ボタン

| アクション名 | キー | 分類 |
|---|---|---|
| `ZN6_ShiftUp` | `Gamepad_RightShoulder`（R1） | [A] |
| `ZN6_ShiftDown` | `Gamepad_LeftShoulder`（L1） | [A] |
| `ZN6_Reset` | `Gamepad_FaceButton_Top`（△） | [A] |
| `ZN6_ShiftUp` | `GameInput_RacingWheel_NextGear` | [B] |
| `ZN6_ShiftDown` | `GameInput_RacingWheel_PreviousGear` | [B] |
| `ZN6_Gear1` 〜 `ZN6_Gear6` | `1` 〜 `6`（キーボード） | **[C]** |
| `ZN6_GearReverse` | `0`（キーボード） | **[C]** |
| `ZN6_GearNeutral` | `N`（キーボード） | **[C]** |

### 軸の設定

| キー | 変更 | 理由 |
|---|---|---|
| `Gamepad_LeftX` | `DeadZone` 0.25 → **0.12** | 操舵の 1/4 が死ぬのは広すぎる。**後勝ちで上書き**（S15）。**出典の無い操作系の値**（憲法ルール18） |

### 割り当てて**いない**もの

| | 理由 |
|---|---|
| `ZN6_Menu` をパッド・ハンドルへ | **開けても閉じられない**（§6） |
| RawInput の `GenericUSBController_*` | **軸番号を推測で書かない**（§3.1）。ini には雛形をコメントで置いた |
| `[/Script/RawInput.RawInputSettings]` の実体 | 同上 |
| `[GameInputPlatformSettings_Windows ...]` | **この ini に書いて効くか未検証**（§3.2） |
