# AI完全主導・在宅型リアルレーシングシミュレーター 開発・制作・検証 完全作業仕様書

> **このファイルについて**
> これは ZN6 へ切り替える前の、Honda Accord Euro R (CL1) を対象車両とした元仕様書である。
> **編集禁止。** 改訂は `Docs/SPEC_ZN6.md` 側で行い、このファイルは差分の基準点として保存する。
> 出典: ユーザー作成のオリジナル文書（チャット添付分を復元）

本ドキュメントは、実車データ・3Dスキャン・音声録音を一切行わず、公開情報とAIを駆使して「物理的に整合した車両デジタルツイン」を構築するための完全な開発仕様書・作業手順書である。

---

## 1. プロジェクト基本定義

### 1.1 最終目的

実車の外観・寸法・エンジン特性・駆動系・タイヤ・サスペンション・重量配分・空力・路面・車両運動を、公開情報と物理モデルを利用して可能な限り再現するレーシングシミュレーターの構築。

「見た目がリアル」であることを最終目的とせず、視覚・音響・車両挙動・タイヤ・加減速・旋回・荷重移動・機関系を個別に検証・統合した物理的整合性をゴールとする。

### 1.2 前提条件（固定）

- 家から一歩も出ない（実車スキャン・実車録音・実測なし）。
- 3Dモデル生成の中心は「Tripo AI」。実車画像は公開画像を利用し、ユーザーが手動でTripo AIへ入力する。
- 可能な限り無料のソフト・AIサービスを使用する。
- 基盤エンジンは「Unreal Engine 5 (UE5)」。
- AIに調査・コード生成・3D後処理・テスト・分析のすべてを担当させる。
- 最初のターゲット車両は「Honda Accord Euro R (CL1)」1台に限定する。
- AIが推測した数値と、資料から確認できた数値を絶対に混同しない。
- 完全自動ではなく「無料枠を最大限利用し、ローカルのBlender/Python/UE5で自動後処理・検証する」設計とする。

---

## 2. 人間とAIの役割分担・エージェント構造

人間が判断する工程を極限まで減らし、「作るAI」と「疑うAI」を分離・競合させることで品質を担保する。

### 2.1 人間が行う作業（最小限）

- PCおよび必要ソフトのインストール・環境構築。
- GitHubリポジトリの作成。
- 公開画像の収集と、Tripo AIへの入力。
- Tripo AIの生成結果（GLBファイル）のローカル保存。
- AIが提案する最終的な仕様・変更の承認または却下。

### 2.2 AIエージェント構成

AIの役割を以下のように階層化し、プロンプトでペルソナを切り替えて運用する。

- **PROJECT MANAGER AI**: 全体の進行管理、タスクの割り振り、最終承認の要求。
  - **RESEARCH AI**: インターネット上の公開資料から実車データを収集・分類。
  - **PHYSICS AI**: C++/Pythonを用いた物理計算モデルの実装、テストコード作成。
  - **3D AI**: Tripo AI生成モデルの評価、Blender Pythonスクリプトの生成、PBRマテリアル設定。
  - **VALIDATOR AI**: 「疑うAI」。Research AIのデータとPhysics/3D AIのアウトプットを比較・監査。
  - **OPTIMIZER AI**: Validator AIが発見した誤差を基に、不確実性のあるパラメータを調整。
  - **QA TESTER AI**: 自動回帰テストの実行、Gitコミットメッセージの作成。

---

## 3. 使用ソフトウェア・技術スタック

| 分野 | 使用ツール・言語 |
|---|---|
| ゲームエンジン | Unreal Engine 5 (C++ / Blueprints) |
| 3D生成 | Tripo AI (Webサービス) |
| 3D編集・自動処理 | Blender / Blender Python (bpy) |
| プログラミング/AI | Python, C++, Cursor等のAIコーディング環境, 各種LLM |
| データ解析/グラフ | Python (Pandas, NumPy), Matplotlib |
| 画像/音声処理 | GIMP, Audacity (公開無料素材の加工) |
| バージョン/ソース管理 | Git, GitHub |
| データ形式 | JSON, CSV |

---

## 4. プロジェクト構造・環境構築

最初にGitHubリポジトリ `AI_Racing_Simulator` を作成し、以下のローカルディレクトリ構造を構築する。

```
AI_Racing_Simulator/
├── Unreal/           # UE5プロジェクトファイル
├── Blender/          # 自動処理用.blendファイル、bpyスクリプト
├── Vehicles/
│   └── CL1/
│       ├── References/ # 収集した資料、仕様書
│       ├── Images/     # 収集した公開画像
│       ├── Tripo/      # Tripo入力用画像、出力GLB
│       ├── Raw/        # Blender処理前メッシュ
│       ├── Clean/      # Blender処理後メッシュ
│       ├── PBR/        # 生成テクスチャ
│       ├── Export/     # UE5向けFBX/UASSET
│       └── vehicle.json # 車両データベース（最重要）
├── Tracks/           # コース生成データ、物理テスト用マップ
├── Physics/          # Python物理モデル、テストスクリプト
├── Audio/            # 音声素材、加工スクリプト
├── AI/               # AIエージェント用プロンプト、強化学習用コード
├── Data/             # テレメトリーログ (CSV)
├── Tests/            # 自動回帰テストコード
├── Tools/            # ユーティリティスクリプト
├── Docs/             # ドキュメント
└── AGENTS.md         # AI開発ルール（憲法）
```

---

## 5. 開発の憲法とデータ管理基準（最重要）

### 5.1 AGENTS.md の制定

プロジェクト開始時、AIに以下のルールを絶対遵守させる `AGENTS.md` を作成する。

1. 実車データを捏造しない。
2. 出典のない数値を実測値として扱わない。
3. 推定値には必ず confidence（信頼度）を設定する。
4. 推定値には推定方法（method）を記録する。
5. SI単位系を内部計算の標準とする。
6. 物理計算と表示用3Dモデルを完全に分離する。
7. Tripo AIの生成モデルを物理的に正確なCADモデルとして扱わない。
8. 車両物理コードを変更する場合、必ずテストを追加または更新する。
9. ラップタイムだけを合わせる最適化（チート）を禁止する。
10. 実車データとシミュレーションデータを複数指標で比較する。
11. AIによるパラメータ変更はGitで記録し、変更理由を明記する。
12. 既存コードを読まずに変更しない。
13. 単位を明示する。
14. 不明な値は「unknown」とする。AIが勝手に実測値として補完しない。
15. 物理的にあり得ないパラメータを採用しない。
16. 変更前後で自動回帰テストを実行し、失敗した状態で完成扱いにしない。
17. エラーを隠蔽しない。
18. 現実の車両仕様とゲーム上の演出を明確に分離する。

### 5.2 データの4段階信頼度と Confidence 設定

`vehicle.json` に記録するすべての数値は、以下の4段階の信頼度（Source）と Confidence 値を持つ。

- **A: Official**（メーカー・公式資料） — confidence: 0.90 ～ 1.0
- **B: Measured**（第三者による実測） — confidence: 0.70 ～ 0.89
- **C: Estimated**（物理モデルから推定） — confidence: 0.40 ～ 0.69
- **D: Assumed**（AIによる仮定） — confidence: 0.0 ～ 0.39

単一の数値ではなく、**範囲（不確実性）** を持たせる。

```json
"cg_height": {
    "value": 0.53,
    "min": 0.50,
    "max": 0.56,
    "unit": "m",
    "source": "estimated",
    "method": "vehicle_dynamics_estimation",
    "confidence": 0.35
}
```

### 5.3 AIの変更権限（AIが変更できるもの・できないもの）

AIがラップタイムを合わせるために勝手に仕様を変更することを防ぐ。

- **Level 0（絶対変更禁止）**: エンジン排気量、ホイールベース、公式ギア比、公式車重など（Source: Official）
- **Level 1（限定的変更）**: 空力係数、重量配分など（Source: Measured）
- **Level 2（探索可能）**: CG高さ、タイヤμ、ダンパー、スプリング、ARBなど（Source: Estimated）
- **Level 3（完全探索）**: 詳細が全く不明なパラメータ（Source: Assumed）

---

## 6. 物理モデル構築パイプライン

物理エンジンはUE5の描画（Visual Layer）から独立させ、Pythonで事前構築・検証したのちC++で実装する。

### 6.1 情報収集と優先順位（Research AI）

メーカー公式資料 > サービスマニュアル > 純正部品資料 > 専門誌 > 実測レビュー > オーナー測定 > 掲示板 > AI推定 の順でデータを収集し、`vehicle.json` を構築する。

### 6.2 機関系（エンジン・駆動・ブレーキ）

- **エンジン**: トルクカーブ（RPM vs Torque）をデータポイントとして収集し、スプライン補間。Throttle、Friction、Inertiaを考慮し出力トルクを計算。
- **駆動系**: Throttle → Torque → Clutch → Gearbox → Final Drive → Differential → Half Shaft → Wheel の力の伝達フローを構築。
- **ギア比**: 各ギアの比率、ファイナル、効率を定義。
- **デファレンシャル**: 初期は Open Diff、次に LSD（Preload, Accel Lock, Decel Lock）を実装（**FF車であるCL1において重要**）。
- **ブレーキ**: Brake Torque、Wheel/Vehicle Speed、Tire Slipから計算し、後にABSロジックを統合。

### 6.3 タイヤモデル（最重要：ブラックボックス化禁止）

Pacejkaの係数をAIに適当に生成させない。「公開タイヤ情報 → サイズ・構造 → 一般的特性範囲 → 物理モデル → 不確実性範囲」のロジックで構築する。

- **段階的実装**: Fiala/Brushモデル（初期） → Magic Formula（中期） → Combined Slip考慮（後期）。
- **荷重感度**: 摩擦係数(μ)を固定せず、垂直荷重による特性変化をモデル化。
- **キャンバー変化**: サスペンションストロークによるキャンバー変化が横力に与える影響を組み込む。
- **候補探索**: AIは正解を当てるのではなく、妥当なパラメータセット（TireModel_A, B, C）から矛盾の少ない領域を探索する。

### 6.4 サスペンションモデル（段階的等価モデル）

公開資料の限界を考慮し、3段階でモデル化。

- **Level 1**: 単純な等価サスペンション（バネ、ダンパー、ストローク）。
- **Level 2**: 推定ジオメトリ（キャンバーゲイン、トー変化、ロールセンター）。
- **Level 3**: 詳細なKinematicsモデル（Caster, KPI, Scrub Radius等）。

ダンパーは速度依存の非線形（Compression/Rebound）とし、ARB（アンチロールバー）による左右差の力も計算する。

### 6.5 車体6DoFと慣性

- X, Y, Z, Roll, Pitch, Yaw の独立計算。
- 重量だけでなく慣性モーメント（Ixx, Iyy, Izz）を持ち、不明な場合は寸法と重量配分から AI に推定させる（Confidenceを下げる）。
- 加減速・旋回時の荷重移動を計算。

---

## 7. 3Dモデル構築パイプライン（Visual Layer）

Tripo AI生成モデルを「完全な実車モデル」とは扱わず、「公開画像から生成された視覚的近似モデル」と定義する。

### 7.1 画像収集とTripo入力

- **収集**: Front, Rear, Side, 3/4, Interior, Detail 等、同一仕様（純正・同グレード）の画像を集める。
- **分類**: Image Research AI が画像の一貫性（改造の有無など）を判定。
- **生成**: 選別された画像をTripo AIへ入力し、GLBモデルを取得。

### 7.2 Reference Renderer による自動補正ループ

BlenderのGUI操作を排除し、AI生成のPython (bpy) スクリプトで自動処理・検査する。

- **全体スケール補正**: Tripoのホイールベースと実車寸法（公式値）を比較し、スケールを正規化。
- **レンダリング比較**: Blender内で3Dモデルを実車画像と同じカメラ角度でレンダリング（Reference Renderer）。
- **AI画像比較**: AIが「実車画像 vs レンダリング画像」を比較し、シルエット、ホイール位置、ルーフライン等の誤差 (Error) を計算。
- **修正ループ**: 誤差に基づきAIがBlenderスクリプトを更新し、局所補正（Overhang, Track, 頂点修正）を実行。これを誤差が閾値以下になるまで繰り返す。
- **個別生成**: ホイール、タイヤ、ブレーキ等、Tripoの精度が低い部品は、公開寸法に基づきBlenderで幾何学的に自動生成して置換する。

### 7.3 メッシュクリーンアップとPBR

- **トポロジ修正**: Non-manifold, 重複頂点, 不正な法線を自動修正し、Body, Glass, Wheel等にパーツ分離。
- **UV展開**: 自動UV展開を実行し、AIが結果を検査。
- **マテリアル (PBR)**: 実車写真の影（Shadow/Reflection）を焼き付けず、AIに Intrinsic Albedo を推定させる。自動車塗装（Clear Coat）、ガラス（Transmission）、タイヤ（Roughness）等を適切に設定。

---

## 8. 統合と自動検証システム

### 8.1 物理モデルの自動テスト（Python）

UE5統合前にPython上で単体テストをパスさせる。

- **コンポーネントテスト**: Engine Test, Tire Test, Suspension Test, Brake Test.
- **車両挙動テスト**: 直線加速（0-100-200km/h）, 制動（100-0km/h）, スキッドパッド（定常円旋回）, 過渡応答（ステアリングステップ入力）。

### 8.2 UE5統合と環境構築

- **分離構造**: Tripo生成メッシュは描画専用（Visual only）。物理計算は独立したPhysics Modelが担当。
- **更新周期分離**: Rendering (60–120Hz), Physics (高頻度サブステップ), Telemetry (50–100Hz) を分ける。
- **テストコース**: 直線、強ブレーキ、ヘアピン、S字、段差を含む「Physics Test Track」を生成。
- **音響**: 公開無料素材を使用。RPM連動（Pitch/Volume）、スロットル連動（Load）、タイヤ状態（Slip）、路面状態によるクロスフェードを実装。

### 8.3 AIドライバーとテレメトリー検証

- **AIドライバー**: 初期は Racing Line + PID制御（事故らず1周が目標）。安定後に強化学習(RL)を導入し、Lap Timeだけでなく安定性・過度なスリップ回避を報酬とする。
- **テレメトリー**: Speed, RPM, Throttle, Steering, Gフォース, Slip Angle等を毎フレームCSVに記録。
- **分析**: AIが100周分のログを解析し、異常挙動（例: リアの急激なブレイク）を検出する。

### 8.4 Reality Validator と Optimizer

- **Reality Validator**: シミュレーション結果と実車資料（加速、最高速、横G等）を比較。
- **3つの妥当性評価**:
  - **Physics Validity**: 物理法則上妥当か（例：200ps/1400kgで0-100km/h 3秒はおかしい等、Physics Reviewer AIが監査）。
  - **Vehicle Validity**: その車の仕様（FFの特性など）として妥当か。
  - **Empirical Validity**: 実車計測データと一致しているか。
- **Optimizer**: 誤差が発生した場合、権限レベル（Level 2～3）に基づき、Tire μ, CG Height などの不確実パラメータを変更し、再シミュレーションを行う。

---

## 9. 開発フェーズ（ロードマップ）

| フェーズ | タスク内容 |
|---|---|
| Phase 1 | 開発環境構築 (UE5, Blender, Python, Git, Cursor等) |
| Phase 2 | AI開発ルールの制定 (AGENTS.md 作成) |
| Phase 3 | 車両データベース構築 (CL1公開情報収集, vehicle.json 生成) |
| Phase 4 | Python物理モデル単体実装 (Engine, Tire, Suspension等) |
| Phase 5 | 4輪車両モデルのPython統合と初期挙動テスト |
| Phase 6 | AIドライバー（PIDベース）の開発 |
| Phase 7 | Physics Test Track（物理検証用コース）の作成 |
| Phase 8 | UE5への物理モデル・基本制御の統合 |
| Phase 9 | 実車画像（CL1）の収集と分類 |
| Phase 10 | Tripo AIによる初期3Dモデル生成 |
| Phase 11 | Blender AI + Reference Renderer による寸法・シルエット自動補正ループ |
| Phase 12 | マテリアル分離、PBR設定、LOD・コリジョン作成 |
| Phase 13 | UE5グラフィック統合 (Nanite, Lumen対応) |
| Phase 14 | 音響モデル実装（RPM/負荷/路面連動） |
| Phase 15 | 実在コース情報の取得（標高・GPS）と簡易生成 |
| Phase 16 | Reality Validator による実車データとシム挙動の比較検証 |
| Phase 17 | AI Optimizer による不確実パラメータの自動キャリブレーション |
| Phase 18 | 自動回帰テストスイートの完成とGit運用 |
| Phase 19 | 最終最適化と不確実性レポートの出力 |

- **第1完成目標**: 灰色のCL1（3Dなし）が、物理的に妥当な動きでテストコースを1周する。
- **第2完成目標**: 正確な物理 + 初期Tripoモデル + 簡易コース。
- **第3完成目標**: 高品質補正済み3D + PBR + 物理 + 音 + AIドライバー。
- **最終完成目標**: 完全な自動キャリブレーションループの確立とレポート出力。

---

## 10. 最終評価基準と完成判定

AIが「完成した」と自己判断するための条件（チェックリスト制）。すべてPASSし、かつ自動回帰テストが成功していること。

- [ ] Vehicle dimensions validated
- [ ] Mass & Inertia validated
- [ ] Engine & Gearbox validated
- [ ] Tire model tested (Load sensitivity, Slip limits)
- [ ] Suspension & Brakes tested
- [ ] Acceleration & Braking metrics validated against reality
- [ ] Cornering & Transient response validated
- [ ] Telemetry analysis passed
- [ ] 3D model geometry & proportions validated
- [ ] Materials & PBR validated
- [ ] Audio dynamic response validated
- [ ] Performance (FPS, Physics tick) validated

### 10.1 不確実性レポート（Digital Twin Report）

シミュレーターの信頼性を示すため、最終成果物として「完全一致」を主張するのではなく、以下のレポートをAIに出力させる。各スコアにはConfidence（根拠の強さ）が併記される。

```
=================================================
 CL1 DIGITAL TWIN UNCERTAINTY REPORT
=================================================
 Category           | Score  | Confidence | Status
--------------------|--------|------------|------------------
 Geometry           | 91/100 | High (0.9) | Known / Official
 Engine             | 96/100 | High (0.9) | Known / Official
 Gearbox            | 98/100 | High (1.0) | Known / Official
 Tires              | 78/100 | Med  (0.6) | Estimated
 Suspension (K&C)   | 72/100 | Low  (0.4) | Assumed / Optimized
 Brakes             | 85/100 | Med  (0.7) | Estimated
 Aerodynamics       | 50/100 | Low  (0.3) | Assumed
 Audio              | 75/100 | Med  (0.5) | Estimated
-------------------------------------------------
 Note: Tire and Suspension scores are optimized based on
 telemetry validation, but lack direct empirical measurements.
=================================================
```

---

この仕様に基づき、CL1の開発を通してシステム（画像処理パイプライン、物理計算パイプライン、自動検証ループ）をテンプレート化することで、以降は入力画像とデータを差し替えるだけで別車種への展開が可能なフレームワークが完成する。
