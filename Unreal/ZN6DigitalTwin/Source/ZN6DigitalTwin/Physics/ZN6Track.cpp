#include "ZN6Track.h"

#include "Dom/JsonObject.h"
#include "Misc/FileHelper.h"
#include "Serialization/JsonReader.h"
#include "Serialization/JsonSerializer.h"

namespace ZN6
{
	bool FTrackEdge::LoadFromFile(const FString& Path, FString& OutError)
	{
		bLoaded = false;
		PointsX.Reset();
		PointsY.Reset();

		FString Text;
		if (!FFileHelper::LoadFileToString(Text, *Path))
		{
			OutError = FString::Printf(TEXT("コース定義を読めない: %s"), *Path);
			return false;
		}

		TSharedPtr<FJsonObject> Root;
		const TSharedRef<TJsonReader<>> Reader = TJsonReaderFactory<>::Create(Text);
		if (!FJsonSerializer::Deserialize(Reader, Root) || !Root.IsValid())
		{
			OutError = FString::Printf(TEXT("コース定義の JSON を解釈できない: %s"), *Path);
			return false;
		}

		if (!Root->TryGetNumberField(TEXT("width_m"), TrackWidthM) || TrackWidthM <= 0.0)
		{
			OutError = TEXT("コース定義に正の width_m が無い");
			return false;
		}

		const TArray<TSharedPtr<FJsonValue>>* Points = nullptr;
		if (!Root->TryGetArrayField(TEXT("points"), Points) || Points->Num() < 2)
		{
			OutError = TEXT("コース定義に中心線の点が足りない");
			return false;
		}

		PointsX.Reserve(Points->Num());
		PointsY.Reserve(Points->Num());
		PointsS.Reserve(Points->Num());
		for (int32 Index = 0; Index < Points->Num(); ++Index)
		{
			const TSharedPtr<FJsonObject>* Point = nullptr;
			double XM = 0.0;
			double YM = 0.0;
			if (!(*Points)[Index]->TryGetObject(Point)
			    || !(*Point)->TryGetNumberField(TEXT("x_m"), XM)
			    || !(*Point)->TryGetNumberField(TEXT("y_m"), YM))
			{
				OutError = FString::Printf(TEXT("中心線の点 %d を読めない"), Index);
				return false;
			}

			// **道のりはコース定義の s_m を使う。** 点間距離を足し上げると、
			// 端数の積み重ねで1周の長さが定義とずれる。
			double SM = 0.0;
			if (!(*Point)->TryGetNumberField(TEXT("s_m"), SM))
			{
				OutError = FString::Printf(TEXT("中心線の点 %d に s_m が無い"), Index);
				return false;
			}

			PointsX.Add(XM);
			PointsY.Add(YM);
			PointsS.Add(SM);
		}

		if (!Root->TryGetNumberField(TEXT("length_m"), TrackLengthM)
		    || TrackLengthM <= 0.0)
		{
			OutError = TEXT("コース定義に正の length_m が無い");
			return false;
		}

		MinXM = MaxXM = PointsX[0];
		MinYM = MaxYM = PointsY[0];
		for (int32 Index = 1; Index < PointsX.Num(); ++Index)
		{
			MinXM = FMath::Min(MinXM, PointsX[Index]);
			MaxXM = FMath::Max(MaxXM, PointsX[Index]);
			MinYM = FMath::Min(MinYM, PointsY[Index]);
			MaxYM = FMath::Max(MaxYM, PointsY[Index]);
		}

		bLoaded = true;
		return true;
	}

	double FTrackEdge::NearestPoint(double XM, double YM, double& OutSM,
	                                double& OutLateralM) const
	{
		OutSM = 0.0;
		OutLateralM = 0.0;
		if (!bLoaded)
		{
			return 0.0;
		}

		int32 Best = 0;
		double NearestSq = TNumericLimits<double>::Max();
		for (int32 Index = 0; Index < PointsX.Num(); ++Index)
		{
			const double Dx = PointsX[Index] - XM;
			const double Dy = PointsY[Index] - YM;
			const double Sq = Dx * Dx + Dy * Dy;
			if (Sq < NearestSq)
			{
				NearestSq = Sq;
				Best = Index;
			}
		}

		OutSM = PointsS[Best];

		// **横ずれには符号を付ける。** どちら側へ外れたかが分からないと、
		// ミニマップでコースのどちら側にいるか描けない。
		//
		// 中心線の進行方向を隣の点から求め、その左向きへの射影をとる。
		const int32 Next = (Best + 1) % PointsX.Num();
		const int32 Prev = (Best + PointsX.Num() - 1) % PointsX.Num();
		const double TangentX = PointsX[Next] - PointsX[Prev];
		const double TangentY = PointsY[Next] - PointsY[Prev];
		const double TangentLength = FMath::Sqrt(TangentX * TangentX
		                                       + TangentY * TangentY);
		if (TangentLength > 1e-9)
		{
			// 左向きは進行方向を +90 度回したもの: (-ty, tx)
			const double Dx = XM - PointsX[Best];
			const double Dy = YM - PointsY[Best];
			OutLateralM = (-TangentY * Dx + TangentX * Dy) / TangentLength;
		}

		return FMath::Sqrt(NearestSq);
	}

	double FTrackEdge::DistanceToEdgeM(double XM, double YM) const
	{
		if (!bLoaded)
		{
			// **でっち上げない。** 読めていないなら「路面の外」でも
			// 「路面の上」でもない。呼び出し側が 0 を境界として扱えるよう、
			// 中立の 0 を返す。
			return 0.0;
		}

		double SM = 0.0;
		double LateralM = 0.0;
		return TrackWidthM / 2.0 - NearestPoint(XM, YM, SM, LateralM);
	}
}
