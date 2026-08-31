// 最小の縦断（1自由度）モデル。Physics/longitudinal.py の移植。
//
//   - タイヤは縦方向のみ（横力なし）
//   - サスペンションなし。荷重移動は準静的
//   - 空力は抗力のみ
//
// **FR であることが効く点**: 加速時の荷重移動は駆動輪（後輪）に **乗る**。
// FF とは逆で、トラクション限界が速度とともに緩む方向に働く。荷重移動を
// 無視した定荷重モデルは、FR では発進加速を **過小評価** する。
//
// 荷重移動と加速度は互いに依存するため、各ステップで不動点反復して解く。
//
// **Python 版と数値が一致することが Phase 8 の判定基準**
// （Docs/SPEC_ZN6.md §10.3）。積分の順序・反復回数・打ち切り条件を
// 勝手に「改善」しないこと。改善したければ Python 側を先に変え、
// Tools/export_reference.py で参照値を作り直すこと。

#pragma once

#include "CoreMinimal.h"
#include "ZN6Components.h"
#include "ZN6VehicleData.h"

namespace ZN6
{
	/** 変速の記録。 */
	struct FShiftPoint
	{
		double TimeS = 0.0;
		int32 FromGearIndex = 0;
		int32 ToGearIndex = 0;
	};

	/**
	 * 加速シミュレーションの結果。
	 *
	 * **Confidence と bValidatable を必ず持たせる。** 数値だけを取り出して
	 * 実測と比較させないため（Docs/AGENT_TOPOLOGY.md §3）。
	 */
	struct FAccelerationResult
	{
		bool bReachedTarget = false;
		double TimeToTargetS = 0.0;
		double DistanceAtTargetM = 0.0;
		TArray<FShiftPoint> ShiftPoints;

		int32 SampleCount = 0;
		int32 TractionLimitedSampleCount = 0;

		double Confidence = 0.0;
		bool bValidatable = false;
		FString LimitingParameter;

		/** トラクション限界に張り付いていたサンプルの割合。 */
		double TractionLimitedFraction() const
		{
			return SampleCount == 0
				? 0.0
				: static_cast<double>(TractionLimitedSampleCount) / static_cast<double>(SampleCount);
		}
	};

	/** 加速シミュレーションの測定条件。**車両仕様ではない。** */
	struct FAccelerationSettings
	{
		/**
		 * 変速時間 [s]。**車両の仕様ではなくドライバーと測定手順のパラメータ。**
		 * 公開されている実測値がばらつく主因の1つ（Docs/DATA_SOURCE_POLICY.md §2）。
		 */
		double ShiftTimeS = 0.25;

		/** 発進回転数 [1/min]。同じく測定手順のパラメータ。 */
		double LaunchRpm = 3500.0;

		double TargetKmh = 100.0;
		double DtS = 0.001;
		double MaxTimeS = 60.0;
		double Throttle = 1.0;
	};

	class FLongitudinalModel
	{
	public:
		bool Init(FVehicleData& Data, FString& OutError);

		/** 静止から目標速度までの全開加速。 */
		FAccelerationResult Accelerate(const FAccelerationSettings& Settings) const;

		/**
		 * 保存則と拘束条件を破っていないか検査する。
		 * 「数値が常識的に見えるか」では判定しない（Docs/SPEC_ZN6.md §8.4）。
		 *
		 * @return 問題の一覧（空なら違反なし）
		 */
		TArray<FString> CheckPhysicsValidity(const FAccelerationSettings& Settings) const;

		/** 加速度に応じた後軸荷重 [N]。FR なので加速すると駆動輪の荷重が増える。 */
		double RearAxleLoadN(double AccelMps2) const;

		/** エンジンが出せる駆動力 [N]（トラクション限界は考慮しない）。 */
		double TractiveForceN(double SpeedMps, int32 GearIndex, double Throttle) const;

		double GetStaticRearN() const { return StaticRearN; }
		const FEngine& GetEngine() const { return Engine; }

	private:
		double ResistanceN(double SpeedMps) const;

		/** (加速度, 駆動力, トラクション限界, 限界に当たったか) を解く。 */
		void SolveAcceleration(double SpeedMps, int32 GearIndex, double Throttle, bool bLaunching,
		                       double& OutAccel, double& OutDriveForce,
		                       double& OutTractionLimit, bool& bOutLimited) const;

		FEngine Engine;
		FDrivetrain Drivetrain;
		FTire Tire;
		FAerodynamics Aero;

		double MassKg = 0.0;
		double WheelbaseM = 0.0;
		double CgHeightM = 0.0;
		double FrontFraction = 0.0;
		double Crr = 0.0;
		double WheelRadiusM = 0.0;
		double StaticFrontN = 0.0;
		double StaticRearN = 0.0;
		double RedlineRpm = 0.0;

		// 結果に付ける信頼度。Init 時点の vehicle.json アクセス記録から取る。
		double Confidence = 0.0;
		bool bValidatable = false;
		FString LimitingParameter;
	};
}
