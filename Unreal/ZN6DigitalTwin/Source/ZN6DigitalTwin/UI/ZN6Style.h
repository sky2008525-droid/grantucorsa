// 画面の見た目を決める値を1箇所に集める。
//
// **色やサイズをウィジェットの中に散らさない。** 散らすと、後から
// 「もう少し暗く」と言われたときに直す場所が分からなくなる。
//
// ここは視覚層なので、評価のオラクルは**見る人の主観**である
// （Docs/AGENT_TOPOLOGY.md §4）。物理層と違って「良く見えるまで直す」が
// 正しい進め方であり、この数値に出典は要らない。
// **ただし物理の数値をここに置かないこと。** 逆も同じ。

#pragma once

#include "CoreMinimal.h"
#include "Fonts/SlateFontInfo.h"
#include "Styling/CoreStyle.h"

namespace ZN6UI
{
	// --- 色 -----------------------------------------------------------------
	//
	// 暗い背景に、シアンを差し色。計器は白、警告は橙、限界は赤。
	// **意味の違う情報に同じ色を使わない。**

	/** 背景。パネルは半透明にして、走っている画が透ける。 */
	inline FLinearColor PanelBackground() { return FLinearColor(0.02f, 0.03f, 0.04f, 0.72f); }
	inline FLinearColor PanelEdge()       { return FLinearColor(1.0f, 1.0f, 1.0f, 0.10f); }
	inline FLinearColor Overlay()         { return FLinearColor(0.01f, 0.015f, 0.02f, 0.92f); }

	/** 差し色。選択中・強調。 */
	inline FLinearColor Accent()          { return FLinearColor(0.20f, 0.85f, 0.95f, 1.0f); }
	inline FLinearColor AccentDim()       { return FLinearColor(0.20f, 0.85f, 0.95f, 0.28f); }

	/** 文字。 */
	inline FLinearColor TextPrimary()     { return FLinearColor(0.96f, 0.97f, 0.98f, 1.0f); }
	inline FLinearColor TextSecondary()   { return FLinearColor(0.62f, 0.67f, 0.72f, 1.0f); }
	inline FLinearColor TextFaint()       { return FLinearColor(0.40f, 0.44f, 0.48f, 1.0f); }

	/** 状態。 */
	inline FLinearColor Good()            { return FLinearColor(0.35f, 0.90f, 0.55f, 1.0f); }
	inline FLinearColor Warn()            { return FLinearColor(1.00f, 0.68f, 0.20f, 1.0f); }
	inline FLinearColor Danger()          { return FLinearColor(1.00f, 0.28f, 0.30f, 1.0f); }

	/** 目盛りの下地。 */
	inline FLinearColor GaugeTrack()      { return FLinearColor(1.0f, 1.0f, 1.0f, 0.08f); }

	// --- 文字 ---------------------------------------------------------------
	//
	// **アセットを増やさない。** エンジン標準のフォントを使う。
	// 独自フォントを入れるとライセンスの管理が要る（Phase 15 と同じ話）。

	inline FSlateFontInfo Font(int32 Size, const ANSICHAR* Face = "Bold")
	{
		return FCoreStyle::GetDefaultFontStyle(Face, Size);
	}

	/** 数字を大きく出すとき。速度・カウントダウン。 */
	inline FSlateFontInfo NumeralFont(int32 Size) { return Font(Size, "Bold"); }
	/** 見出し。 */
	inline FSlateFontInfo LabelFont(int32 Size = 11) { return Font(Size, "Regular"); }

	// --- 寸法 ---------------------------------------------------------------

	/** パネルの内側の余白。 */
	inline float PadS() { return 8.0f; }
	inline float PadM() { return 16.0f; }
	inline float PadL() { return 28.0f; }

	/** 角の丸み。Slate の箱には無いので、枠線の描画で使う。 */
	inline float Corner() { return 4.0f; }

	// --- 数字の書式 ---------------------------------------------------------

	/**
	 * ラップタイムを `M:SS.mmm` にする。
	 *
	 * **0 は「記録なし」として `--:--.---` にする。**
	 * 0:00.000 と出すと、0 秒で走ったように見える。
	 */
	inline FString FormatLapTime(double Seconds)
	{
		if (Seconds <= 0.0)
		{
			return TEXT("--:--.---");
		}
		const int32 Minutes = FMath::FloorToInt(static_cast<float>(Seconds / 60.0));
		const double Rest = Seconds - Minutes * 60.0;
		return FString::Printf(TEXT("%d:%06.3f"), Minutes, Rest);
	}

	/** ベストとの差。**符号を必ず付ける。** 付けないと速いのか遅いのか分からない。 */
	inline FString FormatDelta(double Seconds)
	{
		if (FMath::IsNearlyZero(Seconds, 1e-4))
		{
			return TEXT("+0.000");
		}
		return FString::Printf(TEXT("%+.3f"), Seconds);
	}
}
