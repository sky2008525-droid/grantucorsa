#include "ZN6Components.h"

#include "ZN6Units.h"

// **数学関数は FMath ではなく標準ライブラリを使う。**
//
// Python 側は math モジュール（= C の libm）を呼んでいる。特に
// math.hypot は sqrt(x*x + y*y) と数値的に別物で、中間の二乗による丸めを
// 避けるぶん精度が高い。FMath::Sqrt(X*X + Y*Y) に置き換えると、
// **物理は同じなのに結果が相対 1e-6 ずれる。** 実装間の比較が
// 「同じ計算をしているか」の判定にならなくなるため、関数まで揃える。
#include <cmath>

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

	bool FTire::Init(FVehicleData& Data, double InNominalLoadN, FString& OutError,
	                 bool bReadCamber)
	{
		if (!Data.GetValue(TEXT("tires.friction_coefficient"), TEXT("-"), Mu0, OutError)) { return false; }
		if (!Data.GetValue(TEXT("tires.load_sensitivity"), TEXT("1/N"), LoadSensitivityPerN, OutError)) { return false; }
		if (!Data.GetValue(TEXT("tires.cornering_stiffness_per_load"), TEXT("1/rad"),
		                   CorneringStiffnessPerLoad, OutError)) { return false; }
		if (!Data.GetValue(TEXT("tires.longitudinal_stiffness_per_load"), TEXT("-"),
		                   LongitudinalStiffnessPerLoad, OutError)) { return false; }
		if (!Data.GetValue(TEXT("tires.effective_radius"), TEXT("m"), EffectiveRadiusM, OutError)) { return false; }

		// **キャンバーを使うときだけ読む**（信頼度を不要に下げないため）。
		CamberStiffnessPerLoad = -1.0;
		if (bReadCamber
		    && !Data.GetValue(TEXT("tires.camber_stiffness_per_load"), TEXT("1/rad"),
		                      CamberStiffnessPerLoad, OutError))
		{
			return false;
		}

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

	void FTire::ForcesN(double FzN, double InSlipRatio, double InSlipAngleRad,
	                    double& OutFxN, double& OutFyN, double CamberLeanRad) const
	{
		OutFxN = 0.0;
		OutFyN = 0.0;

		if (FzN <= 0.0)
		{
			return;
		}

		const double MuValue = Mu(FzN);
		const double FMax = MuValue * FzN;
		if (FMax <= 0.0)
		{
			return;
		}

		const double CKappa = LongitudinalStiffnessPerLoad * FzN;
		const double CAlpha = CorneringStiffnessPerLoad * FzN;

		const double FxLinear = CKappa * InSlipRatio;
		double FyLinear = -CAlpha * std::tan(InSlipAngleRad);

		// キャンバー推力。**飽和の前に足す**ので摩擦円を共有する。
		// 後から足すと摩擦円を超える横力が出る。
		if (CamberLeanRad != 0.0)
		{
			if (CamberStiffnessPerLoad < 0.0)
			{
				// **黙って 0 として扱わない**（憲法ルール6）。
				UE_LOG(LogTemp, Error,
				       TEXT("ZN6 tire: キャンバーを渡されたが係数を読んでいない"));
			}
			else
			{
				FyLinear += CamberStiffnessPerLoad * FzN * CamberLeanRad;
			}
		}

		// **hypot を使うこと。** sqrt(x*x + y*y) では丸めが変わる（上の注記）。
		const double FLinear = std::hypot(FxLinear, FyLinear);
		if (FLinear < 1e-9)
		{
			return;
		}

		const double Z = FLinear / (3.0 * FMax);
		const double FTotal = (Z < 1.0)
			? FMax * (3.0 * Z - 3.0 * Z * Z + Z * Z * Z)
			: FMax;

		// **向きは線形力のベクトル方向を保つ。** これにより摩擦円の拘束が
		// 縦横で自動的に共有される（複合スリップ）。
		const double Scale = FTotal / FLinear;
		OutFxN = FxLinear * Scale;
		OutFyN = FyLinear * Scale;
	}

	double FTire::LongitudinalSlopeNPerSlip(double FzN, double InSlipRatio,
	                                        double InSlipAngleRad,
	                                        double CamberLeanRad) const
	{
		if (FzN <= 0.0)
		{
			return 0.0;
		}

		const double FMax = Mu(FzN) * FzN;
		if (FMax <= 0.0)
		{
			return 0.0;
		}

		const double CKappa = LongitudinalStiffnessPerLoad * FzN;
		const double CAlpha = CorneringStiffnessPerLoad * FzN;

		const double FxLinear = CKappa * InSlipRatio;
		double FyLinear = -CAlpha * std::tan(InSlipAngleRad);

		// **ForcesN と同じ動作点で微分すること。** ここでキャンバーを落とすと、
		// 力と接線剛性が別の点の値になり、半陰的な積分の減衰が合わなくなる。
		if (CamberLeanRad != 0.0 && CamberStiffnessPerLoad >= 0.0)
		{
			FyLinear += CamberStiffnessPerLoad * FzN * CamberLeanRad;
		}

		// **hypot を使うこと。** Python 側と丸めを揃える。
		const double FLinear = std::hypot(FxLinear, FyLinear);

		const double Z = FLinear / (3.0 * FMax);
		if (Z >= 1.0)
		{
			return 0.0;
		}
		return CKappa * (1.0 - Z) * (1.0 - Z);
	}

	double FTire::SlipRatio(double WheelOmegaRads, double RadiusM, double ContactSpeedMps)
	{
		const double WheelSpeed = WheelOmegaRads * RadiusM;
		const double Denominator = FMath::Max(FMath::Abs(ContactSpeedMps), 0.5);
		return (WheelSpeed - ContactSpeedMps) / Denominator;
	}

	double FTire::SlipAngleRad(double LateralSpeedMps, double LongitudinalSpeedMps)
	{
		return std::atan2(LateralSpeedMps, FMath::Max(FMath::Abs(LongitudinalSpeedMps), 0.5));
	}

	// =======================================================================
	// FClutch
	// =======================================================================

	bool FClutch::Init(FVehicleData& Data, FString& OutError)
	{
		return Data.GetValue(TEXT("transmission.clutch_capacity"), TEXT("N*m"), CapacityNm, OutError);
	}

	// =======================================================================
	// FBrakes
	// =======================================================================

	bool FBrakes::Init(FVehicleData& Data, FString& OutError)
	{
		if (!Data.GetValue(TEXT("brakes.brake_bias"), TEXT("-"), BiasFront, OutError)) { return false; }
		if (!Data.GetValue(TEXT("brakes.max_brake_torque_total"), TEXT("N*m"), MaxTotalTorqueNm, OutError)) { return false; }
		if (!Data.GetValue(TEXT("brakes.handbrake_torque_rear"), TEXT("N*m"), HandbrakeTorqueNm, OutError)) { return false; }
		return true;
	}

	void FBrakes::AxleTorquesNm(double Pedal, double& OutFrontNm, double& OutRearNm) const
	{
		checkf(Pedal >= 0.0 && Pedal <= 1.0, TEXT("pedal は 0.0-1.0。受け取った値: %f"), Pedal);
		const double Total = MaxTotalTorqueNm * Pedal;
		OutFrontNm = Total * BiasFront;
		OutRearNm = Total * (1.0 - BiasFront);
	}

	double FBrakes::HandbrakeAxleTorqueNm(double Lever) const
	{
		checkf(Lever >= 0.0 && Lever <= 1.0, TEXT("lever は 0.0-1.0。受け取った値: %f"), Lever);
		return HandbrakeTorqueNm * Lever;
	}

	// =======================================================================
	// FDifferential
	// =======================================================================

	namespace
	{
		// ロックの立ち上がりを滑らかにする回転差のスケール [rad/s]。
		// 小さくすると数値的に硬くなり、積分が不安定になる。
		constexpr double LockSmoothingRads = 1.5;
	}

	bool FDifferential::Init(FVehicleData& Data, bool bInUseLsd, FString& OutError)
	{
		bUseLsd = bInUseLsd;
		if (!bUseLsd)
		{
			return true;  // Open Diff はパラメータを持たない（比較基準）
		}

		if (!Data.GetValue(TEXT("differential.preload"), TEXT("N*m"), PreloadNm, OutError)) { return false; }
		if (!Data.GetValue(TEXT("differential.accel_lock_ratio"), TEXT("-"), AccelLockRatio, OutError)) { return false; }
		if (!Data.GetValue(TEXT("differential.decel_lock_ratio"), TEXT("-"), DecelLockRatio, OutError)) { return false; }
		return true;
	}

	void FDifferential::SplitTorqueNm(double TotalTorqueNm, double OmegaLeftRads, double OmegaRightRads,
	                                  double& OutLeftNm, double& OutRightNm) const
	{
		const double Half = TotalTorqueNm / 2.0;

		if (!bUseLsd)
		{
			OutLeftNm = Half;
			OutRightNm = Half;
			return;
		}

		const double LockRatio = (TotalTorqueNm >= 0.0) ? AccelLockRatio : DecelLockRatio;
		const double CapacityNm = PreloadNm + LockRatio * FMath::Abs(TotalTorqueNm);

		// 速い側が正になるようにとり、**速い側から遅い側へ**トルクを移す
		const double OmegaDifference = OmegaLeftRads - OmegaRightRads;
		const double TransferNm = CapacityNm * std::tanh(OmegaDifference / LockSmoothingRads);

		OutLeftNm = Half - TransferNm;
		OutRightNm = Half + TransferNm;
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
