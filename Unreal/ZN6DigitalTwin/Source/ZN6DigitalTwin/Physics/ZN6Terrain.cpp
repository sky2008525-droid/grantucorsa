#include "ZN6Terrain.h"

#include "Dom/JsonObject.h"
#include "Misc/FileHelper.h"
#include "Serialization/JsonReader.h"
#include "Serialization/JsonSerializer.h"

#include "ZN6Units.h"

#include <cmath>

namespace ZN6
{
	bool FHeightfield::LoadFromFile(const FString& Path, FString& OutError)
	{
		bLoaded = false;

		FString Text;
		if (!FFileHelper::LoadFileToString(Text, *Path))
		{
			OutError = FString::Printf(TEXT("高さ場を読めない: %s"), *Path);
			return false;
		}

		TSharedPtr<FJsonObject> Root;
		const TSharedRef<TJsonReader<>> Reader = TJsonReaderFactory<>::Create(Text);
		if (!FJsonSerializer::Deserialize(Reader, Root) || !Root.IsValid())
		{
			OutError = FString::Printf(TEXT("高さ場の JSON を解釈できない: %s"), *Path);
			return false;
		}

		if (!Root->TryGetNumberField(TEXT("x0_m"), X0M)
		    || !Root->TryGetNumberField(TEXT("y0_m"), Y0M)
		    || !Root->TryGetNumberField(TEXT("cell_m"), CellM)
		    || !Root->TryGetNumberField(TEXT("nx"), Nx)
		    || !Root->TryGetNumberField(TEXT("ny"), Ny))
		{
			OutError = TEXT("高さ場に格子の定義が無い");
			return false;
		}

		if (CellM <= 0.0 || Nx < 2 || Ny < 2)
		{
			OutError = FString::Printf(TEXT("高さ場の格子が不正: cell=%f nx=%d ny=%d"),
			                           CellM, Nx, Ny);
			return false;
		}

		const TArray<TSharedPtr<FJsonValue>>* Rows = nullptr;
		if (!Root->TryGetArrayField(TEXT("heights_m"), Rows) || Rows->Num() != Ny)
		{
			OutError = TEXT("高さ場の行数が宣言と違う");
			return false;
		}

		Heights.SetNumUninitialized(Nx * Ny);
		for (int32 IY = 0; IY < Ny; ++IY)
		{
			const TArray<TSharedPtr<FJsonValue>>* Row = nullptr;
			if (!(*Rows)[IY]->TryGetArray(Row) || Row->Num() != Nx)
			{
				OutError = FString::Printf(TEXT("高さ場の %d 行目の列数が違う"), IY);
				return false;
			}
			for (int32 IX = 0; IX < Nx; ++IX)
			{
				Heights[IY * Nx + IX] = (*Row)[IX]->AsNumber();
			}
		}

		bLoaded = true;
		return true;
	}

	int32 FHeightfield::ClampedIndex(double Value, double Origin, int32 Count,
	                                 double& OutFraction) const
	{
		// **範囲外は端で頭打ちにする。** 地形の外へ出た車を落とさない。
		const double Raw = (Value - Origin) / CellM;
		const int32 Index = static_cast<int32>(std::floor(Raw));
		if (Index < 0)
		{
			OutFraction = 0.0;
			return 0;
		}
		if (Index >= Count - 1)
		{
			OutFraction = 1.0;
			return Count - 2;
		}
		OutFraction = Raw - Index;
		return Index;
	}

	double FHeightfield::HeightAt(double XM, double YM) const
	{
		if (!bLoaded)
		{
			return 0.0;
		}

		double FX = 0.0;
		double FY = 0.0;
		const int32 IX = ClampedIndex(XM, X0M, Nx, FX);
		const int32 IY = ClampedIndex(YM, Y0M, Ny, FY);

		const double H00 = Heights[IY * Nx + IX];
		const double H10 = Heights[IY * Nx + IX + 1];
		const double H01 = Heights[(IY + 1) * Nx + IX];
		const double H11 = Heights[(IY + 1) * Nx + IX + 1];

		const double Lower = H00 + (H10 - H00) * FX;
		const double Upper = H01 + (H11 - H01) * FX;
		return Lower + (Upper - Lower) * FY;
	}

	void FHeightfield::SlopeAt(double XM, double YM, double& OutDzDx, double& OutDzDy) const
	{
		if (!bLoaded)
		{
			OutDzDx = 0.0;
			OutDzDy = 0.0;
			return;
		}
		const double Step = CellM;
		OutDzDx = (HeightAt(XM + Step, YM) - HeightAt(XM - Step, YM)) / (2.0 * Step);
		OutDzDy = (HeightAt(XM, YM + Step) - HeightAt(XM, YM - Step)) / (2.0 * Step);
	}

	void BodyGravity(double DzDx, double DzDy, double HeadingRad,
	                 double& OutForwardMps2, double& OutLeftMps2, double& OutNormalScale)
	{
		const double Length = std::sqrt(DzDx * DzDx + DzDy * DzDy + 1.0);
		const double NX = -DzDx / Length;
		const double NY = -DzDy / Length;
		const double NZ = 1.0 / Length;
		OutNormalScale = NZ;

		// 水平面での進行方向を接平面へ落とす
		const double HX = std::cos(HeadingRad);
		const double HY = std::sin(HeadingRad);
		const double Dot = HX * NX + HY * NY;

		double FX = HX - Dot * NX;
		double FY = HY - Dot * NY;
		double FZ = -Dot * NZ;
		const double Norm = std::sqrt(FX * FX + FY * FY + FZ * FZ);
		if (Norm < 1e-12)
		{
			// 前後軸が法線と平行（垂直な壁）。**黙って 0 を返さず平地扱い。**
			OutForwardMps2 = 0.0;
			OutLeftMps2 = 0.0;
			return;
		}
		FX /= Norm;
		FY /= Norm;
		FZ /= Norm;

		// 左方向 l = n x f
		const double LZ = NX * FY - NY * FX;

		OutForwardMps2 = -GravityMps2 * FZ;
		OutLeftMps2 = -GravityMps2 * LZ;
	}
}
