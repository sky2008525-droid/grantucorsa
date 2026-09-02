#include "ZN6Obstacles.h"

#include "Dom/JsonObject.h"
#include "Misc/FileHelper.h"
#include "Serialization/JsonReader.h"
#include "Serialization/JsonSerializer.h"

#include <cmath>

namespace ZN6
{
	bool FCollisionBody::Init(FVehicleData& Data, FString& OutError)
	{
		double LengthM = 0.0;
		double WidthM = 0.0;
		double WheelbaseM = 0.0;
		double LfM = 0.0;

		if (!Data.GetValue(TEXT("dimensions.length"), TEXT("m"), LengthM, OutError)) { return false; }
		if (!Data.GetValue(TEXT("dimensions.width"), TEXT("m"), WidthM, OutError)) { return false; }
		if (!Data.GetValue(TEXT("dimensions.wheelbase"), TEXT("m"), WheelbaseM, OutError)) { return false; }
		if (!Data.GetValue(TEXT("inertia.cg_longitudinal_from_front_axle"), TEXT("m"),
		                   LfM, OutError)) { return false; }

		const double LrM = WheelbaseM - LfM;

		// 車体の中心をホイールベースの中点に置く（= 前後オーバーハング等分）。
		// **前後オーバーハングの配分は vehicle.json に無い。** 実測値ではない。
		// 資料が取れたら dimensions に front_overhang / rear_overhang を足して、
		// ここをその値に差し替えること。
		const double CentreFromCgM = (LfM - LrM) / 2.0;
		FrontM = CentreFromCgM + LengthM / 2.0;
		RearM = LengthM / 2.0 - CentreFromCgM;
		HalfWidthM = WidthM / 2.0;

		if (FrontM <= 0.0 || RearM <= 0.0 || HalfWidthM <= 0.0)
		{
			OutError = FString::Printf(
				TEXT("車体の外形が正でない: front=%f rear=%f half_width=%f"),
				FrontM, RearM, HalfWidthM);
			return false;
		}
		return true;
	}

	double FCollisionBody::BoundingRadiusM() const
	{
		return std::hypot(FMath::Max(FrontM, RearM), HalfWidthM);
	}

	void FCollisionBody::Corners(double OutX[4], double OutY[4]) const
	{
		OutX[0] = FrontM;  OutY[0] = HalfWidthM;
		OutX[1] = FrontM;  OutY[1] = -HalfWidthM;
		OutX[2] = -RearM;  OutY[2] = HalfWidthM;
		OutX[3] = -RearM;  OutY[3] = -HalfWidthM;
	}

	bool CircleContact(const FCollisionBody& Body, double BxM, double ByM, double RadiusM,
	                   double& OutPxM, double& OutPyM, double& OutNx, double& OutNy,
	                   double& OutDepthM, bool& bOutEngulfed)
	{
		// 長方形上で幹の中心に最も近い点
		const double Px = FMath::Min(FMath::Max(BxM, -Body.RearM), Body.FrontM);
		const double Py = FMath::Min(FMath::Max(ByM, -Body.HalfWidthM), Body.HalfWidthM);

		const double Dx = Px - BxM;
		const double Dy = Py - ByM;
		const double DistanceSq = Dx * Dx + Dy * Dy;

		if (DistanceSq > RadiusM * RadiusM)
		{
			return false;
		}

		if (DistanceSq > 0.0)
		{
			const double Distance = std::sqrt(DistanceSq);
			OutPxM = Px;
			OutPyM = Py;
			OutNx = Dx / Distance;
			OutNy = Dy / Distance;
			OutDepthM = RadiusM - Distance;
			bOutEngulfed = false;
			return true;
		}

		// --- 幹の中心が車体の内側 ---
		//
		// **黙って 0 を返さない**（憲法ルール6）。最も近い辺から押し出す。
		const double Depths[4] = {
			Body.FrontM - BxM + RadiusM,
			BxM + Body.RearM + RadiusM,
			Body.HalfWidthM - ByM + RadiusM,
			ByM + Body.HalfWidthM + RadiusM,
		};
		const double Nxs[4] = { -1.0, 1.0, 0.0, 0.0 };
		const double Nys[4] = { 0.0, 0.0, -1.0, 1.0 };
		const double Pxs[4] = { Body.FrontM, -Body.RearM, BxM, BxM };
		const double Pys[4] = { ByM, ByM, Body.HalfWidthM, -Body.HalfWidthM };

		int32 Best = 0;
		for (int32 Index = 1; Index < 4; ++Index)
		{
			if (Depths[Index] < Depths[Best])
			{
				Best = Index;
			}
		}

		OutDepthM = Depths[Best];
		OutNx = Nxs[Best];
		OutNy = Nys[Best];
		OutPxM = Pxs[Best];
		OutPyM = Pys[Best];
		bOutEngulfed = true;
		return true;
	}

	void ContactImpulse(double VxMps, double VyMps, double YawRateRads,
	                    double PxM, double PyM, double Nx, double Ny,
	                    double MassKg, double IzzKgm2, double Restitution,
	                    double& OutImpulseNs, double& OutClosingMps)
	{
		// 接触点の速度（剛体）
		const double PointVx = VxMps - YawRateRads * PyM;
		const double PointVy = VyMps + YawRateRads * PxM;
		OutClosingMps = PointVx * Nx + PointVy * Ny;

		if (OutClosingMps >= 0.0)
		{
			// **離れつつある。押し戻しだけ行い、撃力は入れない。**
			// これが無いと、触れた物体に何ステップも撃力が入って弾き飛ばされる。
			OutImpulseNs = 0.0;
			return;
		}

		const double Lever = PxM * Ny - PyM * Nx;
		const double InverseMass = 1.0 / MassKg + Lever * Lever / IzzKgm2;
		OutImpulseNs = -(1.0 + Restitution) * OutClosingMps / InverseMass;
	}

	bool FObstacleField::LoadFromPlacement(const FString& Path, FString& OutError)
	{
		bLoaded = false;
		Trees.Reset();

		FString Text;
		if (!FFileHelper::LoadFileToString(Text, *Path))
		{
			OutError = FString::Printf(TEXT("配置データを読めない: %s"), *Path);
			return false;
		}

		TSharedPtr<FJsonObject> Root;
		const TSharedRef<TJsonReader<>> Reader = TJsonReaderFactory<>::Create(Text);
		if (!FJsonSerializer::Deserialize(Reader, Root) || !Root.IsValid())
		{
			OutError = FString::Printf(TEXT("配置データの JSON を解釈できない: %s"), *Path);
			return false;
		}

		const TSharedPtr<FJsonObject>* Extent = nullptr;
		if (!Root->TryGetObjectField(TEXT("extent_m"), Extent)
		    || !(*Extent)->TryGetNumberField(TEXT("x0"), X0M)
		    || !(*Extent)->TryGetNumberField(TEXT("x1"), X1M)
		    || !(*Extent)->TryGetNumberField(TEXT("y0"), Y0M)
		    || !(*Extent)->TryGetNumberField(TEXT("y1"), Y1M))
		{
			OutError = TEXT("配置データに extent_m が無い");
			return false;
		}

		if (X1M <= X0M || Y1M <= Y0M)
		{
			OutError = FString::Printf(TEXT("世界境界が不正: x %f..%f / y %f..%f"),
			                           X0M, X1M, Y0M, Y1M);
			return false;
		}

		const TArray<TSharedPtr<FJsonValue>>* TreeArray = nullptr;
		if (!Root->TryGetArrayField(TEXT("trees"), TreeArray))
		{
			OutError = TEXT("配置データに trees が無い");
			return false;
		}

		Trees.Reserve(TreeArray->Num());
		for (int32 Index = 0; Index < TreeArray->Num(); ++Index)
		{
			const TSharedPtr<FJsonObject>* Tree = nullptr;
			if (!(*TreeArray)[Index]->TryGetObject(Tree))
			{
				OutError = FString::Printf(TEXT("樹木 %d を解釈できない"), Index);
				return false;
			}

			double Scale = 0.0;
			double XM = 0.0;
			double YM = 0.0;
			if (!(*Tree)->TryGetNumberField(TEXT("scale"), Scale)
			    || !(*Tree)->TryGetNumberField(TEXT("x_m"), XM)
			    || !(*Tree)->TryGetNumberField(TEXT("y_m"), YM))
			{
				OutError = FString::Printf(TEXT("樹木 %d に位置か scale が無い"), Index);
				return false;
			}
			if (Scale <= 0.0)
			{
				OutError = FString::Printf(TEXT("樹木 %d の scale が正でない: %f"), Index, Scale);
				return false;
			}

			FTree Entry;
			Entry.XM = XM;
			Entry.YM = YM;
			Entry.RadiusM = Feel.TrunkRadiusPerScaleM * Scale;
			Trees.Add(Entry);
		}

		bLoaded = true;
		return true;
	}

	bool FObstacleField::GetTree(int32 Index, double& OutXM, double& OutYM,
	                             double& OutRadiusM) const
	{
		if (!Trees.IsValidIndex(Index))
		{
			return false;
		}
		OutXM = Trees[Index].XM;
		OutYM = Trees[Index].YM;
		OutRadiusM = Trees[Index].RadiusM;
		return true;
	}

	int32 FObstacleField::Resolve(FVehicleState& State, const FCollisionBody& Body,
	                              double MassKg, double IzzKgm2,
	                              TArray<FContact>* OutContacts) const
	{
		if (!bLoaded)
		{
			return 0;
		}

		const double CosH = std::cos(State.HeadingRad);
		const double SinH = std::sin(State.HeadingRad);
		const double ReachM = Body.BoundingRadiusM();

		int32 Count = 0;

		// 撃力と押し戻しを反映する
		auto Apply = [&](double PxM, double PyM, double Nx, double Ny, double DepthM,
		                 bool bTree, int32 Index, bool bEngulfed)
		{
			double ImpulseNs = 0.0;
			double ClosingMps = 0.0;
			ContactImpulse(State.VxMps, State.VyMps, State.YawRateRads,
			               PxM, PyM, Nx, Ny, MassKg, IzzKgm2, Feel.Restitution,
			               ImpulseNs, ClosingMps);

			if (ImpulseNs > 0.0)
			{
				State.VxMps += ImpulseNs * Nx / MassKg;
				State.VyMps += ImpulseNs * Ny / MassKg;
				State.YawRateRads += ImpulseNs * (PxM * Ny - PyM * Nx) / IzzKgm2;
			}

			// **車体固定系の法線を世界へ戻してから動かす。**
			State.XM += (Nx * CosH - Ny * SinH) * DepthM;
			State.YM += (Nx * SinH + Ny * CosH) * DepthM;

			++Count;
			if (OutContacts != nullptr)
			{
				FContact Contact;
				Contact.bTree = bTree;
				Contact.Index = Index;
				Contact.DepthM = DepthM;
				Contact.ClosingSpeedMps = ClosingMps;
				Contact.ImpulseNs = ImpulseNs;
				Contact.bEngulfed = bEngulfed;
				OutContacts->Add(Contact);
			}
		};

		// --- 樹木 ---
		//
		// **順番を固定する。** placement.json の並び順で解く。順番が変わると
		// 同時接触の結果が変わり、Python と一致しなくなる。
		for (int32 Index = 0; Index < Trees.Num(); ++Index)
		{
			const FTree& Tree = Trees[Index];
			const double Dx = Tree.XM - State.XM;
			const double Dy = Tree.YM - State.YM;
			const double Limit = ReachM + Tree.RadiusM;
			if (Dx * Dx + Dy * Dy > Limit * Limit)
			{
				continue;
			}

			double PxM = 0.0, PyM = 0.0, Nx = 0.0, Ny = 0.0, DepthM = 0.0;
			bool bEngulfed = false;
			if (!CircleContact(Body, Dx * CosH + Dy * SinH, -Dx * SinH + Dy * CosH,
			                   Tree.RadiusM, PxM, PyM, Nx, Ny, DepthM, bEngulfed))
			{
				continue;
			}
			Apply(PxM, PyM, Nx, Ny, DepthM, true, Index, bEngulfed);
		}

		// --- 世界境界 ---
		//
		// 4隅のうち**最も外へ出ている角**で判定する。重心1点で見ると、
		// 斜めを向いた車が角から先に境界を越えても気づけない。
		const double NxWorld[4] = { 1.0, -1.0, 0.0, 0.0 };
		const double NyWorld[4] = { 0.0, 0.0, 1.0, -1.0 };
		const double Limits[4] = { X0M, X1M, Y0M, Y1M };

		double CornerX[4];
		double CornerY[4];
		Body.Corners(CornerX, CornerY);

		for (int32 Side = 0; Side < 4; ++Side)
		{
			double WorstDepthM = 0.0;
			double WorstX = 0.0;
			double WorstY = 0.0;

			for (int32 Corner = 0; Corner < 4; ++Corner)
			{
				const double WorldX = State.XM + CornerX[Corner] * CosH - CornerY[Corner] * SinH;
				const double WorldY = State.YM + CornerX[Corner] * SinH + CornerY[Corner] * CosH;
				const double Position = (Side < 2) ? WorldX : WorldY;
				const double Normal = (Side < 2) ? NxWorld[Side] : NyWorld[Side];
				const double DepthM = (Limits[Side] - Position) * Normal;
				if (DepthM > WorstDepthM)
				{
					WorstDepthM = DepthM;
					WorstX = CornerX[Corner];
					WorstY = CornerY[Corner];
				}
			}

			if (WorstDepthM <= 0.0)
			{
				continue;
			}

			// 世界の法線を車体固定系へ
			const double Nx = NxWorld[Side] * CosH + NyWorld[Side] * SinH;
			const double Ny = -NxWorld[Side] * SinH + NyWorld[Side] * CosH;
			Apply(WorstX, WorstY, Nx, Ny, WorstDepthM, false, Side, false);
		}

		return Count;
	}
}
