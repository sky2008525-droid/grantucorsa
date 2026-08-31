// 上下・ピッチ・ロールの3自由度と接地力（Physics/ride.py の移植）。
//
// **Python が唯一の基準。** 数値が食い違ったら C++ 側が間違っている。
//
// これが入るまで、車体の高さは高さ場から**代入されていた**。
// 地面の高さを読んでそこに車を置いていただけで、重力も接地力も無かった。
// ここは力の釣り合いとして解く:
//
//     重力で落ちる -> 車輪が地面に当たってばねが縮む
//       -> 縮んだぶん押し返す -> 重力と釣り合ったところで止まる
//
// **地面は押せるが引けない。** 接地力は 0 で下げ止まり、ばねが伸びきれば
// 車輪は地面を離れる。
//
// 平面3自由度の FVehicle とは別に持つ。FVehicle は前後・左右・ヨーを解き、
// こちらは上下・ピッチ・ロールを解く。**繋がっているのは接地力だけ。**

#pragma once

#include "CoreMinimal.h"
#include "ZN6Vehicle.h"
#include "ZN6VehicleData.h"

namespace ZN6
{
	/** バネ上（車体）の姿勢。**静止した平地での釣り合いを原点にとる。** */
	struct FRideState
	{
		double HeaveM = 0.0;
		double HeaveRateMps = 0.0;
		/** **正が機首上げ**（UE の正のピッチと同じ向き）。 */
		double PitchRad = 0.0;
		double PitchRateRads = 0.0;
		/** **正が右下がり**（UE の正のロールと同じ向き）。 */
		double RollRad = 0.0;
		double RollRateRads = 0.0;
		/** 4輪とも接地していない。 */
		bool bAirborne = false;
	};

	/** 1ステップ分の接地の結果。 */
	struct FRideOutputs
	{
		/** 接地力 [N]。**接地していない車輪は 0。** */
		double LoadsN[WheelCount] = {};
		bool bContact[WheelCount] = {};
		/** 各隅の、その下の地面からの高さ [m]。 */
		double RideHeightM[WheelCount] = {};
		/** サスペンションの縮み [m]。正が縮み。**車輪の描画位置に使う。** */
		double CompressionM[WheelCount] = {};
	};

	class FRideModel
	{
	public:
		bool Init(FVehicleData& Data, FString& OutError);
		bool IsValid() const { return bReady; }

		/**
		 * 1ステップ進める。**FVehicle::Step の後**に呼ぶ。
		 *
		 * @param GroundM  各車輪の下の地面の高さ [m]。4点別々に渡すので、
		 *                 片輪だけ段差に乗った状態も表せる
		 * @param AxMps2   加速度計が読む値（タイヤ力/質量）。**重力を足さない**
		 */
		void Step(const FRideState& State, double DtS, const double GroundM[WheelCount],
		          double AxMps2, double AyMps2,
		          FRideState& OutState, FRideOutputs& OutOutputs) const;

		/**
		 * 静かに置いたときの釣り合い姿勢。初期姿勢を作るのに使う。
		 * **「たぶんここ」で置かない。**
		 *
		 * @return 収束したら true。**収束しなかったことを黙って返さない**
		 */
		bool Settle(const double GroundM[WheelCount], FRideState& OutState,
		            FRideOutputs& OutOutputs, int32 MaxSteps = 20000) const;

		double MassKg() const { return Mass; }
		double RideRateNPerM(int32 Wheel) const { return RideRate[Wheel]; }
		double StaticLoadN(int32 Wheel) const { return StaticLoad[Wheel]; }
		double NaturalFrequencyHz(int32 Wheel) const;
		/** ばねから導いた前ロール剛性配分。**vehicle.json の仮定値とは別物。** */
		double RollStiffnessDistributionFront() const;

		/** 車体固定系の車輪位置（x 前方 / y 左方、重心が原点）。 */
		void WheelPosition(int32 Wheel, double& OutXM, double& OutYM) const
		{
			OutXM = PositionX[Wheel];
			OutYM = PositionY[Wheel];
		}

	private:
		void ContactLoads(const FRideState& State, const double GroundM[WheelCount],
		                  double OutLoadsN[WheelCount], bool bOutContact[WheelCount],
		                  double OutHeightM[WheelCount]) const;

		bool bReady = false;

		double Mass = 0.0;
		double CgHeightM = 0.0;
		double IxxKgm2 = 0.0;
		double IyyKgm2 = 0.0;
		double WheelbaseM = 0.0;
		double TrackFrontM = 0.0;
		double TrackRearM = 0.0;

		double PositionX[WheelCount] = {};
		double PositionY[WheelCount] = {};

		double WheelRate[WheelCount] = {};
		double RideRate[WheelCount] = {};
		double Damping[WheelCount] = {};
		double StaticLoad[WheelCount] = {};
		double FreeHeightM[WheelCount] = {};
	};
}
