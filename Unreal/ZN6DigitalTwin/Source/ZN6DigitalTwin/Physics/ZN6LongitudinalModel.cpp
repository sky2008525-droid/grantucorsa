#include "ZN6LongitudinalModel.h"

#include "ZN6Units.h"

namespace ZN6
{
	bool FLongitudinalModel::Init(FVehicleData& Data, FString& OutError)
	{
		if (!Engine.Init(Data, OutError)) { return false; }
		if (!Drivetrain.Init(Data, OutError)) { return false; }
		if (!Aero.Init(Data, OutError)) { return false; }

		if (!Data.GetValue(TEXT("mass.curb_mass"), TEXT("kg"), MassKg, OutError)) { return false; }
		if (!Data.GetValue(TEXT("dimensions.wheelbase"), TEXT("m"), WheelbaseM, OutError)) { return false; }
		if (!Data.GetValue(TEXT("inertia.cg_height"), TEXT("m"), CgHeightM, OutError)) { return false; }
		if (!Data.GetValue(TEXT("mass.weight_distribution_front_pct"), TEXT("-"), FrontFraction, OutError)) { return false; }
		if (!Data.GetValue(TEXT("tires.rolling_resistance_coefficient"), TEXT("-"), Crr, OutError)) { return false; }

		const double WeightN = MassKg * GravityMps2;
		StaticFrontN = WeightN * FrontFraction;
		StaticRearN = WeightN * (1.0 - FrontFraction);

		// タイヤ1本あたりの公称荷重（荷重感度の基準点）
		if (!Tire.Init(Data, WeightN / 4.0, OutError)) { return false; }
		WheelRadiusM = Tire.GetEffectiveRadiusM();
		RedlineRpm = Engine.GetRedlineRpm();

		// **信頼度は「読んだ入力の最小値」を超えない**（Docs/AGENT_TOPOLOGY.md §3）。
		Confidence = Data.ResultConfidence();
		bValidatable = Data.IsValidatable();
		if (const FParam* Weakest = Data.Weakest())
		{
			LimitingParameter = Weakest->Path;
		}
		return true;
	}

	double FLongitudinalModel::RearAxleLoadN(double AccelMps2) const
	{
		const double TransferN = MassKg * AccelMps2 * CgHeightM / WheelbaseM;
		return FMath::Max(StaticRearN + TransferN, 0.0);
	}

	double FLongitudinalModel::ResistanceN(double SpeedMps) const
	{
		const double Drag = Aero.DragForceN(SpeedMps);
		const double Rolling = Crr * MassKg * GravityMps2;
		return Drag + Rolling;
	}

	double FLongitudinalModel::TractiveForceN(double SpeedMps, int32 GearIndex, double Throttle) const
	{
		const double WheelOmega = SpeedMps / WheelRadiusM;
		const double EngineOmega = Drivetrain.EngineOmegaRads(WheelOmega, GearIndex);
		const double EngineTorque = Engine.TorqueNm(EngineOmega, Throttle);
		const double WheelTorque = Drivetrain.WheelTorqueNm(EngineTorque, GearIndex);
		return WheelTorque / WheelRadiusM;
	}

	void FLongitudinalModel::SolveAcceleration(double SpeedMps, int32 GearIndex, double Throttle, bool bLaunching,
	                                           double& OutAccel, double& OutDriveForce,
	                                           double& OutTractionLimit, bool& bOutLimited) const
	{
		const double Resistance = ResistanceN(SpeedMps);
		const double EngineForce = TractiveForceN(SpeedMps, GearIndex, Throttle);
		const double EquivalentMass = MassKg + Drivetrain.EquivalentMassKg(GearIndex, WheelRadiusM);

		double Accel = 0.0;
		double TractionLimit = 0.0;
		double DriveForce = 0.0;

		// 荷重移動 -> 後軸荷重 -> mu -> トラクション限界 -> 加速度 -> 荷重移動
		// の循環を不動点反復で解く。**反復回数 12 と打ち切り 1e-6 は Python 側と
		// 同じにすること。** 変えると結果が微妙にずれて比較が壊れる。
		for (int32 Iteration = 0; Iteration < 12; ++Iteration)
		{
			const double RearLoad = RearAxleLoadN(Accel);
			// 後輪2本ぶん。mu は荷重依存なので1本あたりの荷重で評価する
			TractionLimit = 2.0 * Tire.MaxLongitudinalForceN(RearLoad / 2.0);

			if (bLaunching)
			{
				// クラッチが滑っている間は、エンジンを LaunchRpm 付近に保てるため
				// トラクション限界まで使えるとみなす（理想的な発進）。
				// 実際の発進はこれより遅い。0-100km/h の実測がばらつく一因。
				DriveForce = TractionLimit;
			}
			else
			{
				DriveForce = FMath::Min(EngineForce, TractionLimit);
			}

			const double NewAccel = (DriveForce - Resistance) / EquivalentMass;
			if (FMath::Abs(NewAccel - Accel) < 1e-6)
			{
				Accel = NewAccel;
				break;
			}
			Accel = NewAccel;
		}

		OutAccel = Accel;
		OutDriveForce = DriveForce;
		OutTractionLimit = TractionLimit;
		bOutLimited = bLaunching || (EngineForce > TractionLimit);
	}

	FAccelerationResult FLongitudinalModel::Accelerate(const FAccelerationSettings& Settings) const
	{
		FAccelerationResult Result;
		Result.Confidence = Confidence;
		Result.bValidatable = bValidatable;
		Result.LimitingParameter = LimitingParameter;

		const double TargetMps = KmhToMps(Settings.TargetKmh);

		double SpeedMps = 0.0;
		double DistanceM = 0.0;
		double TimeS = 0.0;
		int32 GearIndex = 0;
		double ShiftRemainingS = 0.0;

		// クラッチ完全接続まで（1速で LaunchRpm に達する速度）
		const double LockupSpeedMps =
			Settings.LaunchRpm * RadsPerRpm() / Drivetrain.TotalRatio(0) * WheelRadiusM;

		while (TimeS < Settings.MaxTimeS)
		{
			const bool bLaunching = SpeedMps < LockupSpeedMps;

			double Accel = 0.0;
			double DriveForce = 0.0;
			double TractionLimit = 0.0;
			bool bLimited = false;

			if (ShiftRemainingS > 0.0)
			{
				// 変速中は駆動力ゼロ。抵抗だけが効く
				Accel = -ResistanceN(SpeedMps) / MassKg;
				ShiftRemainingS -= Settings.DtS;
			}
			else
			{
				// レブリミットに達したらシフトアップ
				const double WheelOmega = SpeedMps / WheelRadiusM;
				const double Rpm = RadsToRpm(Drivetrain.EngineOmegaRads(WheelOmega, GearIndex));
				if (Rpm >= RedlineRpm && GearIndex < ForwardGearCount - 1)
				{
					FShiftPoint Shift;
					Shift.TimeS = TimeS;
					Shift.FromGearIndex = GearIndex;
					Shift.ToGearIndex = GearIndex + 1;
					Result.ShiftPoints.Add(Shift);

					GearIndex += 1;
					ShiftRemainingS = Settings.ShiftTimeS;

					// **時間を進めずにやり直す。** Python 側の `continue` と同じ。
					// ここで時間を進めると変速のたびに 1 ステップぶんずれる。
					continue;
				}

				SolveAcceleration(SpeedMps, GearIndex, Settings.Throttle, bLaunching,
				                  Accel, DriveForce, TractionLimit, bLimited);
			}

			Result.SampleCount += 1;
			if (bLimited)
			{
				Result.TractionLimitedSampleCount += 1;
			}

			if (!Result.bReachedTarget && SpeedMps >= TargetMps)
			{
				Result.bReachedTarget = true;
				Result.TimeToTargetS = TimeS;
				Result.DistanceAtTargetM = DistanceM;
				break;
			}

			// **半陰的オイラー。** 距離は更新後の速度で積む（Python 側と同じ順序）。
			SpeedMps += Accel * Settings.DtS;
			SpeedMps = FMath::Max(SpeedMps, 0.0);
			DistanceM += SpeedMps * Settings.DtS;
			TimeS += Settings.DtS;
		}

		return Result;
	}

	TArray<FString> FLongitudinalModel::CheckPhysicsValidity(const FAccelerationSettings& Settings) const
	{
		TArray<FString> Problems;
		const double WeightN = MassKg * GravityMps2;

		double PeakPowerWatt = 0.0;
		double PeakPowerRpm = 0.0;
		Engine.PeakPowerW(PeakPowerWatt, PeakPowerRpm);

		// Accelerate() と同じ経路をもう一度回して、各ステップを検査する。
		// **サンプルを丸ごと保持するより、条件だけをその場で見る方が安全。**
		// 「結果が常識的に見えるか」ではなく、破ってはいけない条件で判定する。
		const double TargetMps = KmhToMps(Settings.TargetKmh);

		double SpeedMps = 0.0;
		double TimeS = 0.0;
		int32 GearIndex = 0;
		double ShiftRemainingS = 0.0;

		bool bReportedTraction = false;
		bool bReportedLoad = false;
		bool bReportedPower = false;

		while (TimeS < Settings.MaxTimeS)
		{
			const bool bLaunching = SpeedMps < (Settings.LaunchRpm * RadsPerRpm() / Drivetrain.TotalRatio(0) * WheelRadiusM);

			double Accel = 0.0;
			double DriveForce = 0.0;
			double TractionLimit = 0.0;
			bool bLimited = false;

			if (ShiftRemainingS > 0.0)
			{
				Accel = -ResistanceN(SpeedMps) / MassKg;
				ShiftRemainingS -= Settings.DtS;
			}
			else
			{
				const double WheelOmega = SpeedMps / WheelRadiusM;
				const double Rpm = RadsToRpm(Drivetrain.EngineOmegaRads(WheelOmega, GearIndex));
				if (Rpm >= RedlineRpm && GearIndex < ForwardGearCount - 1)
				{
					GearIndex += 1;
					ShiftRemainingS = Settings.ShiftTimeS;
					continue;
				}
				SolveAcceleration(SpeedMps, GearIndex, Settings.Throttle, bLaunching,
				                  Accel, DriveForce, TractionLimit, bLimited);
			}

			if (!bReportedTraction && DriveForce > TractionLimit + 1.0)
			{
				Problems.Add(FString::Printf(
					TEXT("t=%.3fs: 駆動力 %.0fN がタイヤ摩擦限界 %.0fN を超えている"),
					TimeS, DriveForce, TractionLimit));
				bReportedTraction = true;
			}

			const double RearLoad = RearAxleLoadN(Accel);
			if (!bReportedLoad && RearLoad > WeightN + 1.0)
			{
				Problems.Add(FString::Printf(
					TEXT("t=%.3fs: 後軸荷重 %.0fN が車重 %.0fN を超えている")
					TEXT("（前輪が浮いた状態を超えている）"), TimeS, RearLoad, WeightN));
				bReportedLoad = true;
			}

			if (!bReportedPower && SpeedMps > 0.5 && DriveForce > 0.0)
			{
				const double MechanicalPower = DriveForce * SpeedMps;
				if (MechanicalPower > PeakPowerWatt * 1.02)
				{
					Problems.Add(FString::Printf(
						TEXT("t=%.3fs: 駆動仕事率 %.1fkW がエンジン最高出力 %.1fkW を超えている"),
						TimeS, MechanicalPower / 1000.0, PeakPowerWatt / 1000.0));
					bReportedPower = true;
				}
			}

			if (SpeedMps >= TargetMps)
			{
				break;
			}

			SpeedMps = FMath::Max(SpeedMps + Accel * Settings.DtS, 0.0);
			TimeS += Settings.DtS;
		}

		return Problems;
	}
}
