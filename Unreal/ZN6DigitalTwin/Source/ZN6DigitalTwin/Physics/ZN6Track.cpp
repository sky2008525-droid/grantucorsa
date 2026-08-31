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
			PointsX.Add(XM);
			PointsY.Add(YM);
		}

		bLoaded = true;
		return true;
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

		double NearestSq = TNumericLimits<double>::Max();
		for (int32 Index = 0; Index < PointsX.Num(); ++Index)
		{
			const double Dx = PointsX[Index] - XM;
			const double Dy = PointsY[Index] - YM;
			const double Sq = Dx * Dx + Dy * Dy;
			if (Sq < NearestSq)
			{
				NearestSq = Sq;
			}
		}

		return TrackWidthM / 2.0 - FMath::Sqrt(NearestSq);
	}
}
