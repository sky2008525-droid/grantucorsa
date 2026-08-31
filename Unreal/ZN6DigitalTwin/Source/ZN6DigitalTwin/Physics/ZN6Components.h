// 物理コンポーネント（エンジン / 駆動系 / タイヤ / 空力）。
//
// 対応する Python 実装:
//   FEngine     <- Physics/engine.py
//   FDrivetrain <- Physics/drivetrain.py
//   FTire       <- Physics/tire.py
//   FAerodynamics <- Physics/aero.py
//
// **数値は一切ここに書かない。** すべて vehicle.json から読む
// （.claude/rules/physics.md）。Init が false を返したら、そのモデルは
// 動かせない状態にある。デフォルト値で代用しないこと。

#pragma once

#include "CoreMinimal.h"
#include "ZN6Pchip.h"
#include "ZN6VehicleData.h"

namespace ZN6
{
	/** 前進ギア。Physics/drivetrain.py の FORWARD_GEARS と同じ並び。 */
	extern const TCHAR* const ForwardGears[6];
	inline constexpr int32 ForwardGearCount = 6;

	// -----------------------------------------------------------------------
	// エンジン（FA20）
	// -----------------------------------------------------------------------
	//
	// 内部の扱い:
	//   T_indicated(rpm) = T_wot(rpm) + T_friction(omega)
	//   T_net(rpm, throttle) = throttle * T_indicated(rpm) - T_friction(omega)
	//
	// こうすると throttle=1 で T_wot に、throttle=0 で -T_friction（エンジン
	// ブレーキ）になる。**T_wot を測定値としてそのまま使いつつ、摩擦を
	// 二重に引かない**ための形。
	class FEngine
	{
	public:
		bool Init(FVehicleData& Data, FString& OutError);

		/** 全開時のクランク軸トルク [N*m]。カーブの範囲外は端点で保持する。 */
		double WotTorqueNm(double Rpm) const;

		/** 内部摩擦・ポンピングロスによる抵抗トルク [N*m]（正の値）。 */
		double FrictionTorqueNm(double OmegaRads) const;

		/** クランク軸の正味トルク [N*m]。Throttle は 0.0-1.0。 */
		double TorqueNm(double OmegaRads, double Throttle) const;

		/** (最高出力 [W], その回転数 [1/min])。公式値との突き合わせ用。 */
		void PeakPowerW(double& OutWatt, double& OutRpm) const;

		double GetRedlineRpm() const { return RedlineRpm; }
		double GetIdleRpm() const { return IdleRpm; }

	private:
		FPchipInterpolator Curve;
		TArray<double> CurveRpm;
		TArray<double> CurveTorqueNm;

		double RedlineRpm = 0.0;
		double IdleRpm = 0.0;
		double FrictionCoeffNms = 0.0;
		double InertiaKgm2 = 0.0;
	};

	// -----------------------------------------------------------------------
	// 駆動系（FR）
	// -----------------------------------------------------------------------
	//
	//   Engine -> Clutch -> Gearbox -> Propeller Shaft -> Final Drive / Diff
	//          -> Drive Shaft -> 後輪
	class FDrivetrain
	{
	public:
		bool Init(FVehicleData& Data, FString& OutError);

		/** エンジン回転 / 車輪回転 の総減速比。 */
		double TotalRatio(int32 GearIndex) const;

		double EngineOmegaRads(double WheelOmegaRads, int32 GearIndex) const;

		/**
		 * 駆動輪に届くトルク [N*m]（後輪合計）。
		 *
		 * 効率は駆動側にのみ掛ける。エンジンブレーキ（負のトルク）に効率を
		 * 掛けると、損失が車を加速させる向きに働いてしまう。
		 */
		double WheelTorqueNm(double EngineTorqueNm, int32 GearIndex) const;

		/**
		 * エンジン回転慣性を車輪軸に換算した値 [kg*m^2]。
		 * **1速では ratio^2 ~= 222 倍**になり、無視できない。
		 */
		double ReflectedInertiaAtWheelKgm2(int32 GearIndex) const;

		/** 回転慣性を並進質量に換算した値 [kg]。 */
		double EquivalentMassKg(int32 GearIndex, double WheelRadiusM) const;

	private:
		/**
		 * 基準車両のグレードとファイナルの整合を確認する。
		 *
		 * ZN6 のファイナルは単一値ではない。G 6MT のみ 3.727、
		 * GT / GT"Limited" / 6AT 全車は 4.100。取り違えると約10%の駆動力誤差が
		 * 入り、0-100km/h の検証で原因不明のズレとして現れる（罠①）。
		 */
		bool CheckFinalDriveVariant(FVehicleData& Data, FString& OutError) const;

		double GearRatios[ForwardGearCount] = {};
		double FinalDrive = 0.0;
		double Efficiency = 0.0;
		double EngineInertiaKgm2 = 0.0;
	};

	// -----------------------------------------------------------------------
	// タイヤ（Fiala / ブラシ）
	// -----------------------------------------------------------------------
	class FTire
	{
	public:
		bool Init(FVehicleData& Data, double InNominalLoadN, FString& OutError);

		/**
		 * 垂直荷重に依存する摩擦係数。
		 *   mu(Fz) = mu0 * (1 - k * (Fz - Fz_nominal))
		 *
		 * **荷重が増えるほど mu は下がる。** これが無いと荷重移動の効果が
		 * 正しく出ない。
		 */
		double Mu(double FzN) const;

		/** その荷重で出せる縦力の上限 [N]（摩擦円の半径）。 */
		double MaxLongitudinalForceN(double FzN) const;

		double GetEffectiveRadiusM() const { return EffectiveRadiusM; }

	private:
		double Mu0 = 0.0;
		double LoadSensitivityPerN = 0.0;
		double EffectiveRadiusM = 0.0;
		double NominalLoadN = 0.0;
	};

	// -----------------------------------------------------------------------
	// 空力（抗力のみ）
	// -----------------------------------------------------------------------
	//
	// 揚力係数は unknown のため扱わない。ダウンフォースをゼロとして扱うことは
	// 「無視できる」という主張ではなく、**データが無いという事実の反映**である。
	class FAerodynamics
	{
	public:
		bool Init(FVehicleData& Data, FString& OutError);

		/** 進行方向と逆向きの抗力の大きさ [N]（常に正）。 */
		double DragForceN(double SpeedMps) const;

	private:
		double Cd = 0.0;
		double FrontalAreaM2 = 0.0;
	};
}
