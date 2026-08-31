#include "ZN6Components.h"

#include "ZN6Units.h"

namespace ZN6
{
	const TCHAR* const ForwardGears[6] = { TEXT("1"), TEXT("2"), TEXT("3"), TEXT("4"), TEXT("5"), TEXT("6") };

	// =======================================================================
	// FEngine
	// =======================================================================

	bool FEngine::Init(FVehicleData& Data, FString& OutError)
	{
		if (!Data.GetCurve(TEXT("engine.torque_curve"), TEXT("1/min"), TEXT("N*m"),
		                   CurveRpm, CurveTorqueNm, OutError))
		{
			return false;
		}

		if (CurveRpm.Num() < 3)
		{
			OutError = FString::Printf(
				TEXT("トルクカーブの点数が %d 個しかない。2点（最大出力/最大トルク）だけで")
				TEXT(" 補間してはいけない。FA20 は 4,000rpm 付近に谷がある")
				TEXT("（Docs/ZN6_BASELINE.md）。"), CurveRpm.Num());
			return false;
		}

		if (!Curve.Build(CurveRpm, CurveTorqueNm, OutError))
		{
			return false;
		}

		if (!Data.GetValue(TEXT("engine.redline"), TEXT("1/min"), RedlineRpm, OutError)) { return false; }
		if (!Data.GetValue(TEXT("engine.idle_rpm"), TEXT("1/min"), IdleRpm, OutError)) { return false; }
		if (!Data.GetValue(TEXT("engine.friction_model"), TEXT("N*m*s"), FrictionCoeffNms, OutError)) { return false; }
		if (!Data.GetValue(TEXT("engine.rotational_inertia"), TEXT("kg*m^2"), InertiaKgm2, OutError)) { return false; }

		return true;
	}

	double FEngine::WotTorqueNm(double Rpm) const
	{
		// 範囲外は端点保持（Python 側 wot_torque_nm と同じ）
		if (Rpm <= CurveRpm[0]) { return CurveTorqueNm[0]; }
		if (Rpm >= CurveRpm.Last()) { return CurveTorqueNm.Last(); }
		return Curve.Evaluate(Rpm);
	}

	double FEngine::FrictionTorqueNm(double OmegaRads) const
	{
		return FrictionCoeffNms * FMath::Abs(OmegaRads);
	}

	double FEngine::TorqueNm(double OmegaRads, double Throttle) const
	{
		// Python 側は throttle が範囲外なら例外。ここでは呼び出し側の誤りを
		// 握りつぶさないよう、クランプせずそのまま計算に流す前に検査する。
		checkf(Throttle >= 0.0 && Throttle <= 1.0,
		       TEXT("throttle は 0.0-1.0。受け取った値: %f"), Throttle);

		const double Rpm = RadsToRpm(OmegaRads);
		const double Friction = FrictionTorqueNm(OmegaRads);

		if (Rpm >= RedlineRpm)
		{
			// レブリミッター。燃料カットで駆動トルクは消え、摩擦だけが残る
			return -Friction;
		}

		const double Indicated = WotTorqueNm(Rpm) + Friction;
		return Throttle * Indicated - Friction;
	}

	void FEngine::PeakPowerW(double& OutWatt, double& OutRpm) const
	{
		OutWatt = -TNumericLimits<double>::Max();
		OutRpm = 0.0;

		// Python 側と同じ 10rpm 刻みで走査する（両者の値を一致させるため）
		for (double Rpm = CurveRpm[0]; Rpm <= CurveRpm.Last(); Rpm += 10.0)
		{
			const double Watt = WotTorqueNm(Rpm) * RpmToRads(Rpm);
			if (Watt > OutWatt)
			{
				OutWatt = Watt;
				OutRpm = Rpm;
			}
		}
	}

	// =======================================================================
	// FDrivetrain
	// =======================================================================

	bool FDrivetrain::Init(FVehicleData& Data, FString& OutError)
	{
		for (int32 Index = 0; Index < ForwardGearCount; ++Index)
		{
			const FString Path = FString::Printf(TEXT("transmission.gear_ratios.%s"), ForwardGears[Index]);
			if (!Data.GetValue(Path, TEXT("-"), GearRatios[Index], OutError))
			{
				return false;
			}
		}

		if (!Data.GetValue(TEXT("transmission.final_drive"), TEXT("-"), FinalDrive, OutError)) { return false; }
		if (!Data.GetValue(TEXT("transmission.drivetrain_efficiency"), TEXT("-"), Efficiency, OutError)) { return false; }
		if (!Data.GetValue(TEXT("engine.rotational_inertia"), TEXT("kg*m^2"), EngineInertiaKgm2, OutError)) { return false; }

		return CheckFinalDriveVariant(Data, OutError);
	}

	bool FDrivetrain::CheckFinalDriveVariant(FVehicleData& Data, FString& OutError) const
	{
		FString Grade;
		FString Transmission;
		FString Ignored;

		// グレードが読めない場合は検査自体を落とす（黙って通さない）
		if (!Data.GetPlainString(TEXT("identity.grade"), Grade, Ignored))
		{
			OutError = TEXT("identity.grade が読めないため、ファイナルの整合を検査できない");
			return false;
		}
		if (!Data.GetPlainString(TEXT("identity.transmission_type"), Transmission, Ignored))
		{
			OutError = TEXT("identity.transmission_type が読めないため、ファイナルの整合を検査できない");
			return false;
		}

		const bool bIsGtFamily = (Grade == TEXT("GT")) || (Grade == TEXT("GT\"Limited\""));
		if (bIsGtFamily && FMath::Abs(FinalDrive - 4.100) > 1e-6)
		{
			OutError = FString::Printf(
				TEXT("グレード %s のファイナルは 4.100 のはずだが %f が入っている。")
				TEXT(" G 6MT の値（3.727）と取り違えていないか確認すること")
				TEXT("（Docs/ZN6_BASELINE.md 罠①）。"), *Grade, FinalDrive);
			return false;
		}
		if (Grade == TEXT("G") && Transmission == TEXT("6MT") && FMath::Abs(FinalDrive - 4.100) < 1e-6)
		{
			OutError = TEXT("G 6MT のファイナルは 3.727（オープンデフ）。")
			           TEXT(" トルセンLSD を選択した場合のみ 4.100。どちらか明示すること。");
			return false;
		}
		return true;
	}

	double FDrivetrain::TotalRatio(int32 GearIndex) const
	{
		check(GearIndex >= 0 && GearIndex < ForwardGearCount);
		return GearRatios[GearIndex] * FinalDrive;
	}

	double FDrivetrain::EngineOmegaRads(double WheelOmegaRads, int32 GearIndex) const
	{
		return WheelOmegaRads * TotalRatio(GearIndex);
	}

	double FDrivetrain::WheelTorqueNm(double EngineTorqueNm, int32 GearIndex) const
	{
		const double Ratio = TotalRatio(GearIndex);
		if (EngineTorqueNm >= 0.0)
		{
			return EngineTorqueNm * Ratio * Efficiency;
		}
		return EngineTorqueNm * Ratio / Efficiency;
	}

	double FDrivetrain::ReflectedInertiaAtWheelKgm2(int32 GearIndex) const
	{
		const double Ratio = TotalRatio(GearIndex);
		return EngineInertiaKgm2 * Ratio * Ratio;
	}

	double FDrivetrain::EquivalentMassKg(int32 GearIndex, double WheelRadiusM) const
	{
		return ReflectedInertiaAtWheelKgm2(GearIndex) / (WheelRadiusM * WheelRadiusM);
	}

	// =======================================================================
	// FTire
	// =======================================================================

	bool FTire::Init(FVehicleData& Data, double InNominalLoadN, FString& OutError)
	{
		if (!Data.GetValue(TEXT("tires.friction_coefficient"), TEXT("-"), Mu0, OutError)) { return false; }
		if (!Data.GetValue(TEXT("tires.load_sensitivity"), TEXT("1/N"), LoadSensitivityPerN, OutError)) { return false; }
		if (!Data.GetValue(TEXT("tires.effective_radius"), TEXT("m"), EffectiveRadiusM, OutError)) { return false; }

		NominalLoadN = InNominalLoadN;
		return true;
	}

	double FTire::Mu(double FzN) const
	{
		if (FzN <= 0.0)
		{
			return 0.0;
		}
		const double Value = Mu0 * (1.0 - LoadSensitivityPerN * (FzN - NominalLoadN));
		return FMath::Max(Value, 0.05);
	}

	double FTire::MaxLongitudinalForceN(double FzN) const
	{
		return Mu(FzN) * FMath::Max(FzN, 0.0);
	}

	// =======================================================================
	// FAerodynamics
	// =======================================================================

	bool FAerodynamics::Init(FVehicleData& Data, FString& OutError)
	{
		if (!Data.GetValue(TEXT("aerodynamics.cd"), TEXT("-"), Cd, OutError)) { return false; }
		if (!Data.GetValue(TEXT("aerodynamics.frontal_area"), TEXT("m^2"), FrontalAreaM2, OutError)) { return false; }
		return true;
	}

	double FAerodynamics::DragForceN(double SpeedMps) const
	{
		return 0.5 * AirDensityKgPm3 * Cd * FrontalAreaM2 * SpeedMps * SpeedMps;
	}
}
