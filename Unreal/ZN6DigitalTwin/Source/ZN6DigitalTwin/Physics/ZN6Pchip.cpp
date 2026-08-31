#include "ZN6Pchip.h"

namespace ZN6
{
	namespace
	{
		/** scipy の np.sign と同じ（0 の符号は 0）。 */
		double SignOf(double Value)
		{
			if (Value > 0.0) { return 1.0; }
			if (Value < 0.0) { return -1.0; }
			return 0.0;
		}
	}

	double FPchipInterpolator::EdgeCase(double H0, double H1, double M0, double M1)
	{
		// 片側3点推定
		double D = ((2.0 * H0 + H1) * M0 - H0 * M1) / (H0 + H1);

		// 形状を保つための補正（scipy の mask / mask2 と同じ順序で判定する）
		if (SignOf(D) != SignOf(M0))
		{
			return 0.0;
		}
		if (SignOf(M0) != SignOf(M1) && FMath::Abs(D) > 3.0 * FMath::Abs(M0))
		{
			return 3.0 * M0;
		}
		return D;
	}

	bool FPchipInterpolator::Build(const TArray<double>& InX, const TArray<double>& InY, FString& OutError)
	{
		if (InX.Num() != InY.Num())
		{
			OutError = TEXT("PCHIP: x と y の点数が違う");
			return false;
		}
		if (InX.Num() < 3)
		{
			// **2点補間を許さない。** FA20 の谷が消えるため（憲法ルール / 罠③）。
			OutError = FString::Printf(
				TEXT("PCHIP: 点数が %d 個しかない。2点（最大出力/最大トルク）だけで")
				TEXT(" 補間してはいけない。FA20 は 4,000rpm 付近に谷がある。"), InX.Num());
			return false;
		}
		for (int32 Index = 1; Index < InX.Num(); ++Index)
		{
			if (InX[Index] <= InX[Index - 1])
			{
				OutError = FString::Printf(TEXT("PCHIP: x が単調増加していない（index %d）"), Index);
				return false;
			}
		}

		X = InX;
		Y = InY;

		const int32 Count = X.Num();
		const int32 SegmentCount = Count - 1;

		// 区間幅と区間傾き
		TArray<double> H;
		TArray<double> M;
		H.SetNumUninitialized(SegmentCount);
		M.SetNumUninitialized(SegmentCount);
		for (int32 Index = 0; Index < SegmentCount; ++Index)
		{
			H[Index] = X[Index + 1] - X[Index];
			M[Index] = (Y[Index + 1] - Y[Index]) / H[Index];
		}

		Derivatives.SetNumZeroed(Count);

		// 内部点: 重み付き調和平均。
		// 傾きの符号が変わる点、または傾きがゼロの点では微分をゼロにする
		// （そうしないと補間が単調性を破って存在しない山や谷を作る）。
		for (int32 K = 1; K < Count - 1; ++K)
		{
			const double MPrev = M[K - 1];
			const double MNext = M[K];

			if (SignOf(MPrev) != SignOf(MNext) || MPrev == 0.0 || MNext == 0.0)
			{
				Derivatives[K] = 0.0;
				continue;
			}

			const double W1 = 2.0 * H[K] + H[K - 1];
			const double W2 = H[K] + 2.0 * H[K - 1];
			const double WeightedHarmonicMean = (W1 / MPrev + W2 / MNext) / (W1 + W2);
			Derivatives[K] = 1.0 / WeightedHarmonicMean;
		}

		// 端点
		Derivatives[0] = EdgeCase(H[0], H[1], M[0], M[1]);
		Derivatives[Count - 1] = EdgeCase(
			H[SegmentCount - 1], H[SegmentCount - 2], M[SegmentCount - 1], M[SegmentCount - 2]);

		return true;
	}

	double FPchipInterpolator::Evaluate(double InValue) const
	{
		if (X.Num() == 0)
		{
			return 0.0;
		}
		if (InValue <= X[0])
		{
			return Y[0];
		}
		if (InValue >= X.Last())
		{
			return Y.Last();
		}

		// InValue を含む区間を二分探索で探す
		int32 Low = 0;
		int32 High = X.Num() - 1;
		while (High - Low > 1)
		{
			const int32 Mid = (Low + High) / 2;
			if (X[Mid] <= InValue) { Low = Mid; } else { High = Mid; }
		}

		// scipy の CubicHermiteSpline と同じ多項式係数の組み立て方にする。
		// （数学的に等価な別式でも値はほぼ同じだが、丸め差を減らすため式を揃える）
		const double Dx = X[Low + 1] - X[Low];
		const double Slope = (Y[Low + 1] - Y[Low]) / Dx;
		const double T = (Derivatives[Low] + Derivatives[Low + 1] - 2.0 * Slope) / Dx;

		const double C0 = T / Dx;
		const double C1 = (Slope - Derivatives[Low]) / Dx - T;
		const double C2 = Derivatives[Low];
		const double C3 = Y[Low];

		const double S = InValue - X[Low];
		return ((C0 * S + C1) * S + C2) * S + C3;
	}
}
