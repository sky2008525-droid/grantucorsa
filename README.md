# grantucorsa

公開情報と物理モデルだけで、**トヨタ86（ZN6）のデジタルツイン**を構築するレーシングシミュレーター開発プロジェクト。

実車スキャン・実車録音・実測を一切行わない。「見た目がリアル」ではなく、**エンジン・駆動系・タイヤ・サスペンション・荷重移動を個別に検証したうえでの物理的整合性**をゴールとする。

---

## 対象車両

**トヨタ86 前期型 GT グレード 6MT 日本仕様（DBA-ZN6-D2E8）**

| 項目 | 値 | 出典 |
|---|---|---|
| 駆動方式 | FR | official |
| エンジン | FA20（水平対向4気筒 1,998cc） | official |
| 最高出力 / 最大トルク | 147 kW / 205 N·m | official |
| 車両重量 | 1,230 kg | official |
| ホイールベース | 2,570 mm | official |
| デフ | トルセンLSD（GT 標準） | official |

一次資料は `Vehicles/ZN6/References/86_spec_201502.pdf`（トヨタ公式 主要諸元表 2015年2月版）。
選定理由と、検証で見つかった罠2件は [`Docs/ZN6_BASELINE.md`](Docs/ZN6_BASELINE.md) を読むこと。

> **注意**: ファイナルギア比は単一値ではない（G 6MT のみ 3.727、他は 4.100）。
> 全高の公称値 1,320mm はアンテナ込み。3Dスケール補正には `roof_height` = 1.285m を使う。

---

## 今回のスコープ

**Phase 1〜8 相当**（環境構築 → 憲法 → データ → 物理モデル → 統合 → AIドライバー → テストコース）。

- **第1完成目標**: 灰色の ZN6（3Dなし）が、物理的に妥当な動きでテストコースを1周する
- UE5 は **Phase 8 まで導入しない**。それまでの可視化は matplotlib で足りる
- Blender は **Phase 11 まで導入しない**
- 3D・音響・実在コース（Phase 9〜15）は [`Docs/SPEC_PHASE2_BACKLOG.md`](Docs/SPEC_PHASE2_BACKLOG.md) へ退避済み

---

## ドキュメントの読む順番

| # | ファイル | 位置づけ |
|---|---|---|
| 1 | [`Docs/HANDOFF.md`](Docs/HANDOFF.md) | **現在の方針。最優先。** 元仕様書の外側で決まったこと |
| 2 | [`Docs/ZN6_BASELINE.md`](Docs/ZN6_BASELINE.md) | 基準車両の確定理由と、検証で見つかった罠2件 |
| 3 | [`Docs/SPEC_ZN6.md`](Docs/SPEC_ZN6.md) | **要件定義書。** 作業仕様の本体 |
| 4 | [`Docs/AGENT_TOPOLOGY.md`](Docs/AGENT_TOPOLOGY.md) | 「作るAI」と「疑うAI」の分離。**評価のオラクルは層によって違う** |
| 5 | [`Docs/SOURCE_A_VERIFICATION.md`](Docs/SOURCE_A_VERIFICATION.md) | 公式諸元表と `vehicle.json` の照合記録。**PDFはテキスト抽出できない** |
| 6 | [`Docs/DATA_COLLECTION_ZN6.md`](Docs/DATA_COLLECTION_ZN6.md) | 収集項目リスト（14カテゴリ） |
| 7 | [`Docs/SPEC_PHASE2_BACKLOG.md`](Docs/SPEC_PHASE2_BACKLOG.md) | 退避したフェーズ |
| — | [`Docs/SPEC_CL1_original.md`](Docs/SPEC_CL1_original.md) | **編集禁止。** ZN6 へ切り替える前の元仕様書。差分の基準点 |
| — | [`Docs/RULE_CLASSIFICATION.md`](Docs/RULE_CLASSIFICATION.md) | 憲法18ルールをどう三層に分類したかの記録 |

**`HANDOFF.md` と `SPEC_CL1_original.md` が矛盾したら `HANDOFF.md` が優先。**

---

## セットアップ

```bash
git clone https://github.com/sky2008525-droid/grantucorsa.git
cd grantucorsa

python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install numpy scipy matplotlib pytest

# コミット前ゲートを有効化（これを実行しないとフックは動かない）
git config core.hooksPath .claude/hooks
```

`git config core.hooksPath` は clone ごとに1回必要。`.git/hooks/` はリポジトリに含まれないため、
フック本体を `.claude/hooks/` に置いて git にそこを見させる構成にしている。

---

## リポジトリ構成

```
grantucorsa/
├── CLAUDE.md              # 憲法・層2（判断を要するルール / 全セッション読み込み）
├── .claude/
│   ├── rules/             # 憲法・層3（paths でスコープ / 該当ファイルを触るときだけ読む）
│   └── hooks/             # 憲法・層1（機械的に強制。コミットを止める）
├── Docs/                  # 仕様書・方針・記録
├── Vehicles/ZN6/
│   ├── vehicle.json       # 車両データベース（最重要）
│   └── References/        # 一次資料（公式諸元表PDF 等）
├── Physics/               # Python 物理モデル
├── Tests/                 # 自動回帰テスト
├── Tools/                 # ユーティリティ（validate_vehicle.py 等）
├── Tracks/                # 物理検証用コース
├── Data/                  # テレメトリーログ (CSV)
└── AI/                    # AIエージェント用プロンプト
```

`Unreal/` `Blender/` `Audio/` は Phase 8 / 11 / 14 で追加する。**先に空ディレクトリを作らない。**

---

## 憲法（開発ルール）の構造

18のルールを性質別に3層へ分割している。1ファイルに全部並べると遵守率が下がるため。
分類理由は [`Docs/RULE_CLASSIFICATION.md`](Docs/RULE_CLASSIFICATION.md)。

| 層 | 場所 | 性質 |
|---|---|---|
| 1 | `.claude/hooks/` | **機械的に強制。** AIの判断に関係なくコミットを止める |
| 2 | `CLAUDE.md`（150行以内） | 判断を要するもの。全セッションで読み込まれる |
| 3 | `.claude/rules/*.md` | `paths` フロントマターでスコープ。該当ファイルを触るときだけ読まれる |

層1が止めるもの:

- **Level 0 パラメータの変更**（排気量・ホイールベース・公式ギア比・公式車重）
- `vehicle.json` のスキーマ違反（`source` / `confidence` / `unit` の欠落、非SI単位）
- pytest の失敗

---

## データの扱い

`vehicle.json` の全数値は **`source` / `confidence` / `unit`** を持つ。単一値ではなく **min/max の範囲**を持たせる。

| Source | confidence | 変更権限 |
|---|---|---|
| `official`（メーカー公式資料） | 0.90 – 1.0 | **Level 0: 変更禁止** |
| `official_marketing`（公式だが販促資料・測定条件不明） | 0.70 – 0.89 | Level 1: 限定的 |
| `measured` / `secondary`（第三者実測・二次情報） | 0.70 – 0.89 | Level 1: 限定的 |
| `estimated`（物理モデルから推定） | 0.40 – 0.69 | Level 2: 探索可能 |
| `assumed`（AIによる仮定） | 0.0 – 0.39 | Level 3: 完全探索 |

**不明な値は `"unknown"` と書く。埋めない。** 出典が取れるまで空欄のままにするのが正しい状態。

検証:

```bash
python3 Tools/validate_vehicle.py Vehicles/ZN6/vehicle.json   # スキーマ検証
python3 Tools/validate_vehicle.py --list-level0               # 保護対象の一覧
python3 -m pytest Tests                                       # 回帰テスト
```

いずれもコミット前フックが自動で実行する。

---

## 現在の状態

`vehicle.json`: official 38項目 / secondary 4項目 / **unknown 47項目**。

未取得のうち影響が大きいもの:

- **FA20 のトルクカーブ** — 最重要。4,000rpm 付近の谷があるため、最大出力点と最大トルク点の2点だけで補間してはいけない
- **タイヤ有効半径** — 縦断モデルに必須。純正サイズの一次出典を取ってから幾何計算する
- **0-100km/h の基準値** — 出典によってばらつく。「どの出典を採るか」のルールを先に決める（Day 1 の課題）
- 前後重量配分 / Cd値・前面投影面積 / スプリング・ダンパー・スタビ / 慣性モーメント

---

## 作業場所の分担

Claude Code とチャット（claude.ai）は**コンテキストを共有しない**。橋渡しはファイルシステムと Git のみ。

| 役割 | 場所 |
|---|---|
| 実装・テスト・コミット（PHYSICS / QA） | Claude Code |
| 仕様の議論、公開情報の調査と出典整理（RESEARCH） | チャット |
| 「この数値は物理的に妥当か」の監査（VALIDATOR） | チャット |

「作るAI」と「疑うAI」を分離するための構成。同一コンテキストで自己検算させても意味がない。
