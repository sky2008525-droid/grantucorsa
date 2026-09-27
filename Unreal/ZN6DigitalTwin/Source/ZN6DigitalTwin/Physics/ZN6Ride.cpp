#include "ZN6Ride.h"

#include "ZN6Units.h"

#include <cmath>

namespace ZN6
{
	namespace
	{
		bool IsFrontWheel(int32 Wheel)
		{
			return Wheel == static_cast<int32>(EWheel::FL)
			    || Wheel == static_cast<int32>(EWheel::FR);
		}

		bool IsLeftWheel(int32 Wheel)
		{
			return Wheel == static_cast<int32>(EWheel::FL)
			    || Wheel == static_cast<int32>(EWheel::RL);
		}
	}

	bool FRideModel::Init(FVehicleData& Data, FString& OutError,
	                      const FCarSetup& InSetup)
	{
		bReady = false;
		Setup = InSetup;

		double LfM = 0.0;
		double TyreK = 0.0;

		struct FEntry { const TCHAR* Path; const TCHAR* Unit; double* Target; };
		const FEntry Entries[] = {
			{ TEXT("mass.curb_mass"),                            TEXT("kg"),      &Mass },
			{ TEXT("inertia.cg_height"),                         TEXT("m"),       &CgHeightM },
			// **車高を下げれば重心も下がる。** 読んだ後で足す（下を参照）。
			{ TEXT("inertia.Ixx"),                               TEXT("kg*m^2"),  &IxxKgm2 },
			{ TEXT("inertia.Iyy"),                               TEXT("kg*m^2"),  &IyyKgm2 },
			{ TEXT("dimensions.wheelbase"),                      TEXT("m"),       &WheelbaseM },
			{ TEXT("dimensions.track_front"),                    TEXT("m"),       &TrackFrontM },
			{ TEXT("dimensions.track_rear"),                     TEXT("m"),       &TrackRearM },
			{ TEXT("inertia.cg_longitudinal_from_front_axle"),   TEXT("m"),       &LfM },
			{ TEXT("tires.vertical_stiffness"),                  TEXT("N/m"),     &TyreK },
		};
		for (const FEntry& Entry : Entries)
		{
			if (!Data.GetValue(Entry.Path, Entry.Unit, *Entry.Target, OutError))
			{
				return false;
			}
		}

		// 車高を下げれば重心も下がる。**基準値そのものは書き換えない。**
		CgHeightM = Setup.CgHeightM(CgHeightM);

		const double LrM = WheelbaseM - LfM;

		for (int32 Wheel = 0; Wheel < WheelCount; ++Wheel)
		{
			const bool bFront = IsFrontWheel(Wheel);
			const double TrackM = bFront ? TrackFrontM : TrackRearM;
			PositionX[Wheel] = bFront ? LfM : -LrM;
			PositionY[Wheel] = (IsLeftWheel(Wheel) ? 1.0 : -1.0) * TrackM / 2.0;
		}

		// --- 静荷重 ---
		//
		// **重心位置から出す。** mass.weight_distribution_front_pct（0.542）
		// とは 1.2 点食い違っており（issue #29）、既存の準静的モデル
		// （FVehicle）は重心位置のほうを使っている。ここで別の値を使うと、
		// **止まっているだけの車にピッチモーメントが残る。**
		const double FrontRatio = LrM / WheelbaseM;
		const double WeightN = Mass * GravityMps2;

		for (int32 Wheel = 0; Wheel < WheelCount; ++Wheel)
		{
			const bool bFront = IsFrontWheel(Wheel);
			StaticLoad[Wheel] = WeightN * (bFront ? FrontRatio : 1.0 - FrontRatio) / 2.0;

			double SpringN = 0.0;
			double Ratio = 0.0;
			double Zeta = 0.0;
			const TCHAR* Axle = bFront ? TEXT("front") : TEXT("rear");

			if (!Data.GetValue(FString(TEXT("suspension.spring_rate_")) + Axle,
			                   TEXT("N/m"), SpringN, OutError)
			    || !Data.GetValue(FString(TEXT("suspension.motion_ratio_")) + Axle,
			                      TEXT("-"), Ratio, OutError)
			    || !Data.GetValue(FString(TEXT("suspension.damping_ratio_")) + Axle,
			                      TEXT("-"), Zeta, OutError))
			{
				return false;
			}

			// セッティングの倍率。**vehicle.json の min/max を倍率に直した
			// ものなので、範囲を超えない**（FSetupLimits が保証する）。
			SpringN *= bFront ? Setup.SpringScaleFront : Setup.SpringScaleRear;
			Zeta *= bFront ? Setup.DampingScaleFront : Setup.DampingScaleRear;

			// **モーションレシオは2乗で効く。** 1乗にすると力が合わない。
			WheelRate[Wheel] = SpringN * Ratio * Ratio;

			// ばねとタイヤは**直列**。忘れると剛性が1割ほど高く出る。
			RideRate[Wheel] = WheelRate[Wheel] * TyreK / (WheelRate[Wheel] + TyreK);

			// 臨界減衰の基準は**そのコーナーが支える質量**。車重の 1/4 で
			// 済ませない（前後で軸重が違う）。
			const double CornerMass = StaticLoad[Wheel] / GravityMps2;
			Damping[Wheel] = 2.0 * Zeta * std::sqrt(RideRate[Wheel] * CornerMass);

			// 静止時に静荷重ぶん縮んでいるので、そのぶん伸ばした位置が自由長
			FreeHeightM[Wheel] = StaticLoad[Wheel] / RideRate[Wheel];
		}

		bReady = true;
		return true;
	}

	double FRideModel::NaturalFrequencyHz(int32 Wheel) const
	{
		const double CornerMass = StaticLoad[Wheel] / GravityMps2;
		return std::sqrt(RideRate[Wheel] / CornerMass) / (2.0 * Pi);
	}

	double FRideModel::RollStiffnessDistributionFront() const
	{
		const int32 FL = static_cast<int32>(EWheel::FL);
		const int32 FR = static_cast<int32>(EWheel::FR);
		const int32 RL = static_cast<int32>(EWheel::RL);
		const int32 RR = static_cast<int32>(EWheel::RR);

		const double Front = (RideRate[FL] + RideRate[FR]) / 2.0
		                   * TrackFrontM * TrackFrontM / 2.0;
		const double Rear = (RideRate[RL] + RideRate[RR]) / 2.0
		                  * TrackRearM * TrackRearM / 2.0;
		return Front / (Front + Rear);
	}

	void FRideModel::ContactLoads(const FRideState& State, const double GroundM[WheelCount],
	                              double OutLoadsN[WheelCount], bool bOutContact[WheelCount],
	                              double OutHeightM[WheelCount]) const
	{
		for (int32 Wheel = 0; Wheel < WheelCount; ++Wheel)
		{
			const double BodyZ = State.HeaveM
			                   + PositionX[Wheel] * State.PitchRad
			                   + PositionY[Wheel] * State.RollRad;
			const double BodyRate = State.HeaveRateMps
			                      + PositionX[Wheel] * State.PitchRateRads
			                      + PositionY[Wheel] * State.RollRateRads;

			const double HeightM = BodyZ - GroundM[Wheel];
			OutHeightM[Wheel] = HeightM;

			const double CompressionM = FreeHeightM[Wheel] - HeightM;
			const double ForceN = RideRate[Wheel] * CompressionM - Damping[Wheel] * BodyRate;

			// **地面は押せるが引けない。**
			// max を外すと、浮いた車輪が車体を下へ引っ張る。見た目には
			// 「なんとなく沈む」だけなので気づけない。
			if (ForceN > 0.0)
			{
				OutLoadsN[Wheel] = ForceN;
				bOutContact[Wheel] = true;
			}
			else
			{
				OutLoadsN[Wheel] = 0.0;
				bOutContact[Wheel] = false;
			}
		}
	}

	void FRideModel::Step(const FRideState& State, double DtS,
	                      const double GroundM[WheelCount],
	                      double AxMps2, double AyMps2,
	                      FRideState& OutState, FRideOutputs& OutOutputs) const
	{
		if (DtS <= 0.0)
		{
			// **握りつぶさない**（憲法ルール6）。例外が使えないので記録して返す。
			UE_LOG(LogTemp, Error, TEXT("ZN6 ride: dt が正でない: %f"), DtS);
			OutState = State;
			return;
		}

		ContactLoads(State, GroundM, OutOutputs.LoadsN, OutOutputs.bContact,
		             OutOutputs.RideHeightM);

		double TotalN = 0.0;
		double PitchMomentNm = 0.0;
		double RollMomentNm = 0.0;
		bool bAnyContact = false;

		for (int32 Wheel = 0; Wheel < WheelCount; ++Wheel)
		{
			TotalN += OutOutputs.LoadsN[Wheel];
			// z_i = heave + x_i*pitch + y_i*roll なので、一般化力はこの形
			PitchMomentNm += PositionX[Wheel] * OutOutputs.LoadsN[Wheel];
			RollMomentNm += PositionY[Wheel] * OutOutputs.LoadsN[Wheel];
			bAnyContact = bAnyContact || OutOutputs.bContact[Wheel];

			OutOutputs.CompressionM[Wheel] =
				FreeHeightM[Wheel] - OutOutputs.RideHeightM[Wheel];
		}

		// タイヤの前後力・横力は接地面（重心より CgHeight だけ下）に働く。
		// **これが荷重移動の正体。**
		//   加速（Ax > 0）  -> 機首上げ  -> 後軸へ荷重
		//   左旋回（Ay > 0）-> 右下がり  -> 右輪へ荷重
		PitchMomentNm += Mass * AxMps2 * CgHeightM;
		RollMomentNm += Mass * AyMps2 * CgHeightM;

		// 半陰的オイラー。速度を先に更新してから位置に使う。
		// 陽解法だと剛いばねで1ステップごとに振幅が増える（issue #24 と同じ形）。
		const double HeaveRate = State.HeaveRateMps
		                       + (TotalN / Mass - GravityMps2) * DtS;
		const double PitchRate = State.PitchRateRads + (PitchMomentNm / IyyKgm2) * DtS;
		const double RollRate = State.RollRateRads + (RollMomentNm / IxxKgm2) * DtS;

		OutState.HeaveRateMps = HeaveRate;
		OutState.PitchRateRads = PitchRate;
		OutState.RollRateRads = RollRate;
		OutState.HeaveM = State.HeaveM + HeaveRate * DtS;
		OutState.PitchRad = State.PitchRad + PitchRate * DtS;
		OutState.RollRad = State.RollRad + RollRate * DtS;
		OutState.bAirborne = !bAnyContact;
	}

	bool FRideModel::Settle(const double GroundM[WheelCount], FRideState& OutState,
	                        FRideOutputs& OutOutputs, int32 MaxSteps) const
	{
		constexpr double DtS = 0.001;
		constexpr double ToleranceN = 1e-3;

		FRideState State;
		const double WeightN = Mass * GravityMps2;

		for (int32 Step = 0; Step < MaxSteps; ++Step)
		{
			FRideState Next;
			this->Step(State, DtS, GroundM, 0.0, 0.0, Next, OutOutputs);
			State = Next;

			double TotalN = 0.0;
			for (int32 Wheel = 0; Wheel < WheelCount; ++Wheel)
			{
				TotalN += OutOutputs.LoadsN[Wheel];
			}

			// **速度も見る。** 力だけだと、振動の中心を通る瞬間に
			// 「収束した」と誤判定する。
			if (FMath::Abs(TotalN - WeightN) < ToleranceN
			    && FMath::Abs(State.HeaveRateMps) < 1e-6
			    && FMath::Abs(State.PitchRateRads) < 1e-6
			    && FMath::Abs(State.RollRateRads) < 1e-6)
			{
				OutState = State;
				return true;
			}
		}

		OutState = State;
		return false;
	}
}
