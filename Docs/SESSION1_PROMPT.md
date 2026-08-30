# Claude Code セッション1 指示文

> このファイルの「貼り付ける内容」以下をそのまま Claude Code に貼る。

---

## 事前準備（人間の作業）

```bash
git clone https://github.com/sky2008525-droid/grantucorsa.git
cd grantucorsa

# 配布された Docs/ と Vehicles/ を配置
# Docs/HANDOFF.md
# Docs/ZN6_BASELINE.md
# Docs/SPEC_CL1_original.md
# Docs/DATA_COLLECTION_ZN6.md
# Docs/SESSION1_PROMPT.md
# Vehicles/ZN6/vehicle.json

python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install numpy scipy matplotlib pytest

code .
```

**あわせてやっておくこと**

- 公式諸元表PDFを `Vehicles/ZN6/References/86_spec_201502.pdf` として保存
  （`https://toyota.jp/pages/contents/86/001_p_004/pdf/spec/86_spec_201502.pdf`。URLは将来失効する）
- GitHub Settings → General → Danger Zone → Change visibility で private へ

---

## 貼り付ける内容

```
まず以下を読んでほしい。

- Docs/HANDOFF.md        ← 現在の方針。これが最優先
- Docs/ZN6_BASELINE.md   ← 基準車両の確定と、検証で見つかった罠2件
- Vehicles/ZN6/vehicle.json ← 公式値を収録済み
- Docs/SPEC_CL1_original.md ← 古い仕様書（CL1前提）

HANDOFF.md と SPEC_CL1_original.md が矛盾したら HANDOFF.md が優先。

このセッションでは物理コードをまだ書かないでほしい。
以下の5つを作る。


0. .gitignore の作り直し

現在入っているのは AL (Dynamics 365 Business Central) 用テンプレートで、
このプロジェクトとは無関係。
Python + venv + UE5 + Blender + OS生成ファイル向けに置き換える。
.vscode/ は無視せず共有する方針。


1. Docs/SPEC_ZN6.md

HANDOFF.md §1 の要書き換え箇所を反映した ZN6 版仕様書。

「既知」として挙げた箇所以外にも FF→FR で変わる記述がないか
SPEC_CL1_original.md を全文精査し、見つけたものは冒頭の変更点一覧に追記すること。
単語置換で済ませないこと。

基準車両は ZN6_BASELINE.md の決定（前期 GT 6MT 日本仕様）に従う。


2. Docs/SPEC_PHASE2_BACKLOG.md

HANDOFF.md §4 に従い、Phase 9〜15（Tripo / Blender / PBR / 音響 / 実在コース）を退避。
SPEC_ZN6.md 側からは該当フェーズを削除し、このファイルへの参照だけ残す。


3. CLAUDE.md と .claude/rules/

HANDOFF.md §3 の三層構造に従う。

- CLAUDE.md は150行以内。判断を要するルールのみ
- .claude/rules/ に paths フロントマター付きでパス限定ルールを置く
  physics.md → Physics/**/*.py
  vehicle-data.md → Vehicles/**/*.json
  blender.md → Blender/**/*.py （Phase 11まで未使用）

SPEC_CL1_original.md §5.1 の18ルールをどう三分類したか、
分類理由を Docs/RULE_CLASSIFICATION.md に記録すること。

仕様書全文を CLAUDE.md に入れないこと。


4. .claude/hooks/pre-commit-gate.sh と Tools/validate_vehicle.py

HANDOFF.md §3 層1 の3項目を実装する。

- Level 0 パラメータ（排気量・ホイールベース・公式ギア比・公式車重）が
  vehicle.json で変更されたらコミットを止める
- vehicle.json のスキーマ検証
  （source / confidence / unit の必須、SI単位、"unknown" の許容）
- pytest 失敗時のコミット停止

検証スクリプトは Python で書き、単体でも実行できるようにすること。


作業を始める前に、上記の理解と作業計画を提示してほしい。
不明点があれば先に質問すること。
```

---

## セッション2以降

セッション1が終わったら、Day 1 の検証ループへ進む（HANDOFF.md §5）。

最小の縦断モデルを Python で書き、0-100km/h を計算して実測値と比較する。
必要な公式値は `vehicle.json` に揃っている。未取得はタイヤ有効半径のみ。

**最初の壁は「実測値そのものが不確実」という問題になる。**
これは confidence システムが機能するかを確かめるテストケースなので、
逃げずにルールを決めること。
