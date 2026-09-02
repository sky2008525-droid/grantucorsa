// ボディカラー。
//
// **ここにある色は演出であって車両仕様ではない**（憲法ルール18）。
// ZN6 の純正色（コード・名称）の出典は取れていない。だから
// **「純正のオレンジ」などと名乗らない。** 単に選べる色として置く。
// `vehicle.json` に入れないのはそのため。
//
// ## 塗る場所をどう決めるか
//
// 車体メッシュはマテリアルスロットを 37 個持つ。ガラス・灯火・クローム・
// タイヤまで塗ると車が単色の塊になる。**塗る対象を選ばなければならない。**
//
// スロット番号を直接書くのは避ける（モデルを差し替えた瞬間に壊れ、
// しかも「別の場所が塗られる」という気づきにくい壊れ方をする）。
// 代わりに **元のモデルが持っている塗装色と一致するスロットだけ**を塗る。
//
// 調べた結果、元モデルの塗装は次の2スロットだった:
//
//     16  body        BaseColorFactor = (1.00, 0.19, 0.00)
//     19  Color_A02   BaseColorFactor = (1.00, 0.34, 0.00)
//
// どちらも橙系で、それ以外のスロットは黒・灰・白・クローム。
// **「赤成分が高く、青成分がほぼ無い」**という条件で拾える。

#pragma once

#include "CoreMinimal.h"

namespace ZN6
{
	/** 選べる色。**純正色ではない**（出典が無い）。 */
	struct FPaintColour
	{
		const TCHAR* Name;
		FLinearColor Colour;
	};

	/**
	 * 色の一覧。**出典は無い。** 見て選ぶためのもの。
	 *
	 * sRGB ではなくリニアで持つ。`BaseColorFactor` はリニアで解釈される
	 * ので、sRGB の値をそのまま入れると**全部やけに明るくなる。**
	 */
	inline TArrayView<const FPaintColour> PaintPalette()
	{
		static const FPaintColour Palette[] = {
			{ TEXT("Orange"),      FLinearColor(1.000f, 0.190f, 0.000f) },
			{ TEXT("Pure Red"),    FLinearColor(0.640f, 0.020f, 0.020f) },
			{ TEXT("Deep Blue"),   FLinearColor(0.012f, 0.055f, 0.290f) },
			{ TEXT("Racing Green"),FLinearColor(0.010f, 0.110f, 0.055f) },
			{ TEXT("Silver"),      FLinearColor(0.520f, 0.540f, 0.560f) },
			{ TEXT("Gunmetal"),    FLinearColor(0.085f, 0.095f, 0.110f) },
			{ TEXT("Pearl White"), FLinearColor(0.870f, 0.880f, 0.880f) },
			{ TEXT("Satin Black"), FLinearColor(0.015f, 0.015f, 0.017f) },
			{ TEXT("Lightning Yellow"), FLinearColor(0.900f, 0.560f, 0.010f) },
		};
		return MakeArrayView(Palette, UE_ARRAY_COUNT(Palette));
	}

	/**
	 * そのスロットが塗装かどうか。
	 *
	 * 元モデルの塗装は橙（赤が高く青がほぼ無い）。ガラス・クローム・黒樹脂は
	 * 灰色系なので、この条件で分かれる。
	 *
	 * **一致するスロットが1つも無ければ「塗る場所が分からない」。**
	 * その場合は黙って何もせず、呼び出し側が警告を出すこと（ルール6）。
	 */
	inline bool IsPaintSlot(const FLinearColor& Base)
	{
		return Base.R > 0.5f && Base.B < 0.10f && Base.R > Base.G * 1.8f;
	}
}
