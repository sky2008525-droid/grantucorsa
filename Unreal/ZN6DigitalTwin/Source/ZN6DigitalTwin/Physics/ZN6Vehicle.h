// 4輪車両モデル（平面3自由度 + 準静的荷重移動）。Physics/vehicle.py の移植。
//
// 状態:
//   Vx, Vy      車体固定座標系の速度 [m/s]（x 前方、y 左方）
//   YawRate     ヨーレート [rad/s]
//   X, Y, Heading  地面固定座標系の位置と方位
//   WheelOmega  各輪の回転速度 [rad/s]
//   EngineOmega **エンジンの独立した回転状態**
//
// **6自由度ではなく平面3自由度にした理由**
//
// 上下・ロール・ピッチの**動特性**を入れるのに必要な値が揃っていない。
// vehicle.json の実際の状態は次のとおり（「すべて unknown」ではない）:
//
//   spring_rate_front / rear   estimated だが、**モーションレシオが unknown
//                              なのでホイールレートが決まらない**（WARNING 参照）
//   damper_front / rear        **"unknown"**（値が無いことを明記してある）
//   arb_front / rear           measured
//   ロールセンタ高さ           項目そのものが無い
//
// つまりロール剛性も減衰も導けない。入れれば捏造になる（憲法ルール1）。
// 代わりに荷重移動を**準静的**に扱う。ロール角の時間応答は出ないが、
// **定常的な4輪の荷重は正しく出る**。
//
// 描画側（AZN6VehicleActor）は、この荷重移動を車体の傾きとして見せている。
// **あれは実車のロール角ではなく、荷重移動の可視化**であり、係数は
// vehicle.json の外に演出値として置いてある（憲法ルール18）。
// データが揃ったら本物のロール自由度に置き換えること（issue #19）。
//
// **Python 版と数値が一致することが Phase 8 の判定基準。**
// 積分の順序・部分ステップ数・ロック判定のしきい値を勝手に「改善」しないこと。

#pragma once

#include "CoreMinimal.h"
#include "ZN6Components.h"
#include "ZN6VehicleData.h"

// **数学関数は FMath ではなく標準ライブラリを使う。** 理由は
// ZN6Components.cpp の先頭コメント（math.hypot と sqrt(x*x+y*y) の違い）。
#include <cmath>

namespace ZN6
{
	/** 車輪の並び。FL=前左, FR=前右, RL=後左, RR=後右。 */
	enum class EWheel : uint8 { FL = 0, FR = 1, RL = 2, RR = 3 };
	inline constexpr int32 WheelCount = 4;
	extern const TCHAR* const WheelNames[WheelCount];

	struct FControlInput
	{
		double Throttle = 0.0;
		double Brake = 0.0;
		double SteerRad = 0.0;
		int32 GearIndex = 0;

		/**
		 * 0.0 = 完全に切る / 1.0 = 完全に繋ぐ。**bool ではない。**
		 * 途中の値が半クラッチ。クラッチ蹴りを表現するのに要る。
		 */
		double Clutch = 1.0;

		/** サイドブレーキの引き量 0.0-1.0。**後輪のみ**に効く。 */
		double Handbrake = 0.0;
	};

	struct FVehicleState
	{
		double VxMps = 0.0;
		double VyMps = 0.0;
		double YawRateRads = 0.0;
		double XM = 0.0;
		double YM = 0.0;
		double HeadingRad = 0.0;
		double WheelOmegaRads[WheelCount] = {};

		/**
		 * **エンジンの回転は独立した状態変数。**
		 * 車輪速度から逆算していたときは、クラッチを切ってもエンジンが
		 * 空吹かしできず、繋いでもトルクの叩き込みが起きなかった。
		 */
		double EngineOmegaRads = 0.0;

		double SpeedMps() const { return std::hypot(VxMps, VyMps); }

		/** 車体すべり角 beta。大きいほどスピンに近い。 */
		double SideslipRad() const
		{
			return std::atan2(VyMps, FMath::Max(FMath::Abs(VxMps), 0.5));
		}
	};

	/** 1ステップの内部量。テレメトリとデバッグ用。 */
	struct FVehicleOutputs
	{
		double AxMps2 = 0.0;
		double AyMps2 = 0.0;
		double YawAccelRads2 = 0.0;
		double EngineRpm = 0.0;
		double EngineTorqueNm = 0.0;
		double ClutchTorqueNm = 0.0;
		double ClutchSlipRads = 0.0;

		double TireFzN[WheelCount] = {};
		double TireFxN[WheelCount] = {};
		double TireFyN[WheelCount] = {};
		double SlipRatio[WheelCount] = {};
		double SlipAngleRad[WheelCount] = {};
		double Utilisation[WheelCount] = {};
	};

	/** ZN6（FR）の平面運動モデル。 */
	class FVehicle
	{
	public:
		bool Init(FVehicleData& Data, bool bUseLsd, FString& OutError);

		/**
		 * 状態を Dt 進め、新しい状態と内部量を返す。
		 *
		 * SlopeG* は斜面が車体に与える重力成分 [m/s^2]（車体固定系）、
		 * NormalScale は法線荷重の係数。**既定は平地**で、そのときの結果は
		 * 地形を入れる前と完全に一致する（参照値が変わらない）。
		 *
		 * 地形の値は ZN6Terrain が高さ場から求める。
		 * **描画メッシュからは読まない**（憲法ルール4）。
		 */
		void Step(const FVehicleState& State, const FControlInput& Control, double DtS,
		          FVehicleState& OutState, FVehicleOutputs& OutOutputs,
		          double SlopeGxMps2 = 0.0, double SlopeGyMps2 = 0.0,
		          double NormalScale = 1.0);

		/**
		 * 準静的な4輪の垂直荷重 [N]。FR なので加速で駆動輪（後輪）に乗る。
		 *
		 * **Ax / Ay は加速度計が読む値（タイヤ力/質量）を渡すこと。**
		 * 斜面で停車していると、タイヤ力が重力と釣り合って Ax = g*sin(傾き)
		 * になり、坂の下側の軸へ荷重が移るという正しい結果が出る。
		 * 重力を別に足すと二重に数えることになる。
		 */
		void WheelLoadsN(double AxMps2, double AyMps2, double OutLoadsN[WheelCount],
		                 double NormalScale = 1.0) const;

		FVehicleState InitialState(double SpeedMps, int32 GearIndex) const;

		double GetConfidence() const { return Confidence; }
		bool IsValidatable() const { return bValidatable; }

	private:
		/** 車輪位置での接地点速度を、車輪座標系で返す。 */
		void WheelVelocity(const FVehicleState& State, int32 WheelIndex, double SteerRad,
		                   double& OutVxMps, double& OutVyMps) const;

		/**
		 * エンジン回転を Dt 進める。**ロック／スリップを切り替える。**
		 *
		 *   ロック中:   クラッチは剛体。エンジン回転は変速機入力に拘束される
		 *   スリップ中: エンジンは独立した状態を持ち、クラッチは容量ぶんだけ伝える
		 *
		 * 剛なバネで両者を繋いだまま陽解法で解くと、エンジンと車輪が2質量系
		 * として発振する。ロック時に拘束へ切り替えることでこれを避ける。
		 */
		void IntegrateEngine(double EngineOmegaRads, double GearboxOmegaRads,
		                     const FControlInput& Control, double DtS,
		                     double& OutEngineOmega, double& OutClutchTorque,
		                     double& OutEngineTorque, bool& bOutLocked) const;

		FEngine Engine;
		FDrivetrain Drivetrain;
		FBrakes Brakes;
		FClutch Clutch;
		FAerodynamics Aero;
		FDifferential Differential;
		FTire Tire;

		double MassKg = 0.0;
		double IzzKgm2 = 0.0;
		double WheelbaseM = 0.0;
		double TrackFrontM = 0.0;
		double TrackRearM = 0.0;
		double CgHeightM = 0.0;
		double LfM = 0.0;
		double LrM = 0.0;
		double RollDistFront = 0.0;
		double Crr = 0.0;
		double WheelInertiaKgm2 = 0.0;
		double EngineInertiaKgm2 = 0.0;
		double IdleOmegaRads = 0.0;
		double WheelRadiusM = 0.0;
		double StaticFrontN = 0.0;
		double StaticRearN = 0.0;

		/** 車輪の位置（車体固定座標系。x 前方、y 左方）。 */
		double WheelPosX[WheelCount] = {};
		double WheelPosY[WheelCount] = {};

		/**
		 * 前ステップの加速度。**準静的荷重を1ステップ遅らせて解くために持つ。**
		 * Python 側の `self._last_ax` / `_last_ay` に対応する（Step で更新される）。
		 */
		double LastAxMps2 = 0.0;
		double LastAyMps2 = 0.0;

		double Confidence = 0.0;
		bool bValidatable = false;
	};
}
