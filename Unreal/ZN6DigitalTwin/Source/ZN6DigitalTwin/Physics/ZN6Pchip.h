// 単調3次補間（PCHIP）。scipy.interpolate.PchipInterpolator の移植。
//
// **なぜ線形補間ではいけないか**
//
// FA20 は 4,000rpm 付近にトルクの谷がある（Docs/ZN6_BASELINE.md 罠③）。
// 通常の3次スプラインだと谷の前後でオーバーシュートして存在しない山や谷を
// 作る。PCHIP はデータ点の間で振動しない。**谷の形状を勝手に増幅しないこと**が
// 重要なので、Python 側と同じ補間方式でなければ 0-100km/h が一致しない。
//
// **アルゴリズムを「だいたい同じ」で済ませないこと。** 端点の扱い
// (_edge_case) と内部点の重み付き調和平均を scipy と厳密に合わせてある。
// ここを自己流にすると、テストが通らない原因が補間なのか物理なのか
// 切り分けられなくなる。

#pragma once

#include "CoreMinimal.h"

namespace ZN6
{
	/**
	 * 単調3次補間（Fritsch-Carlson / PCHIP）。
	 *
	 * scipy.interpolate.PchipInterpolator(x, y, extrapolate=False) と同じ値を返す。
	 * 範囲外は呼び出し側で端点保持すること（このクラスは外挿しない）。
	 */
	class FPchipInterpolator
	{
	public:
		/**
		 * @param InX  単調増加する x（3点以上）
		 * @param InY  対応する y
		 * @return 構築できたら true（点数不足・非単調な x は false）
		 */
		bool Build(const TArray<double>& InX, const TArray<double>& InY, FString& OutError);

		/** x での補間値。範囲外は端点の値を返す。 */
		double Evaluate(double X) const;

		bool IsValid() const { return X.Num() >= 2; }
		double MinX() const { return X.Num() > 0 ? X[0] : 0.0; }
		double MaxX() const { return X.Num() > 0 ? X.Last() : 0.0; }

	private:
		/**
		 * 端点の片側3点推定（scipy の _edge_case そのまま）。
		 * Cleve Moler, Numerical Computing with MATLAB, Chap 3.6 (pchiptx.m) 由来。
		 */
		static double EdgeCase(double H0, double H1, double M0, double M1);

		TArray<double> X;
		TArray<double> Y;
		TArray<double> Derivatives;
	};
}
