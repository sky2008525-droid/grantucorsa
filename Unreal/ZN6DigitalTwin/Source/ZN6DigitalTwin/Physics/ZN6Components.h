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

	/**
	 * ニュートラル。**減速比を持たない。**
	 *
	 * Python 側は段を文字列（`"N"`）で持つ。C++ は添字なので、
	 * 前進 6 段（0..5）の**外側**に負の番号を割り当てる。
	 * 「6速の次」ではないので連番の端には置かない。
	 */
	inline constexpr int32 GearNeutral = -1;

	/**
	 * 後退。公表比 3.437 は**大きさ**で、向きはリバースアイドラが決める。
	 * 符号は `FDrivetrain::TotalRatio` が付ける（vehicle.json は触らない）。
	 */
	inline constexpr int32 GearReverse = -2;

	/** 運転者が選べる段か。**H パターンシフターが送ってくるのはこの集合。** */
	inline bool IsSelectableGear(int32 GearIndex)
	{
		return (GearIndex >= 0 && GearIndex < ForwardGearCount)
			|| GearIndex == GearNeutral || GearIndex == GearReverse;
	}

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

		/**
		 * エンジン回転 / 車輪回転 の総減速比。
		 *
		 * 後退では**負**を返す（エンジンが正転しても車輪は逆へ回る）。
		 * **ニュートラルで呼んではいけない**（比が存在しない）。呼ぶ側が
		 * 「ニュートラルでは駆動系を通らない」と書き分けること。
		 */
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

		/** 後退の比の**大きさ**。符号は TotalRatio が付ける。 */
		double ReverseRatio = 0.0;
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
		/**
		 * @param bReadCamber  キャンバーを使うときだけ true。
		 *                     **常に読むと信頼度が不要に下がる**（assumed / 0.10）。
		 */
		bool Init(FVehicleData& Data, double InNominalLoadN, FString& OutError,
		          bool bReadCamber = false);

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

		/**
		 * (縦力 Fx, 横力 Fy) [N]。ブラシモデルの飽和則。
		 *
		 *   F = mu*Fz * (3z - 3z^2 + z^3)   (z = F_linear / (3*mu*Fz) <= 1)
		 *   F = mu*Fz                        (z > 1)
		 *
		 * 力の向きは線形力のベクトル方向を保つので、**摩擦円の拘束が縦横で
		 * 自動的に共有される**（これが複合スリップ）。FR では後輪の複合
		 * スリップがパワーオーバーステアの発生条件そのもの。
		 */
		void ForcesN(double FzN, double SlipRatio, double SlipAngleRad,
		             double& OutFxN, double& OutFyN,
		             double CamberLeanRad = 0.0) const;

		/**
		 * その動作点での dFx/dkappa [N]（接線剛性）。
		 *
		 * **車輪回転を半陰的に積分するために要る**（issue #24）。
		 * 飽和則を f_linear で微分すると dF/df_linear = (1-z)^2 になるので、
		 * 接線剛性は線形域の c_kappa をこの係数で縮めたもの。
		 *
		 * **線形域の c_kappa をそのまま使ってはいけない。** 飽和している
		 * ときの実際の勾配はずっと小さく、使うと積分が過剰に減衰する。
		 */
		double LongitudinalSlopeNPerSlip(double FzN, double InSlipRatio,
		                                 double InSlipAngleRad,
		                                 double CamberLeanRad = 0.0) const;

		/** スリップ率 kappa = (omega*r - v) / max(|v|, 0.5)。駆動時は正。 */
		static double SlipRatio(double WheelOmegaRads, double RadiusM, double ContactSpeedMps);

		/** スリップ角 alpha = atan2(vy, max(|vx|, 0.5))。 */
		static double SlipAngleRad(double LateralSpeedMps, double LongitudinalSpeedMps);

		double GetEffectiveRadiusM() const { return EffectiveRadiusM; }

	private:
		double Mu0 = 0.0;
		double LoadSensitivityPerN = 0.0;
		double CorneringStiffnessPerLoad = 0.0;
		double LongitudinalStiffnessPerLoad = 0.0;
		double EffectiveRadiusM = 0.0;
		double NominalLoadN = 0.0;

		/**
		 * キャンバー推力の係数。**キャンバーを使うときだけ読む。**
		 *
		 * assumed / 0.10 なので、常に読むとキャンバー 0 の走行まで結果の
		 * 信頼度が 0.10 に落ちる。効いていない値で信頼度を下げるのは
		 * 依存関係の嘘になる。負なら「読んでいない」。
		 */
		double CamberStiffnessPerLoad = -1.0;
	};

	// -----------------------------------------------------------------------
	// クラッチ（滑りを持つ）
	// -----------------------------------------------------------------------
	//
	// **bool の断続ではなく、回転差に応じてトルクを伝えるモデル。**
	// これが無いとクラッチ蹴り・半クラッチ発進・エンストが表現できない。
	class FClutch
	{
	public:
		bool Init(FVehicleData& Data, FString& OutError);
		double GetCapacityNm() const { return CapacityNm; }

	private:
		double CapacityNm = 0.0;
	};

	// -----------------------------------------------------------------------
	// ブレーキ
	// -----------------------------------------------------------------------
	//
	// ディスク径・マスターシリンダー径・パッド摩擦係数はいずれも unknown の
	// ため、油圧系からブレーキトルクを導けない。代わりに **総容量と前後配分**
	// で扱う。実車のブレーキはタイヤの摩擦限界を上回る能力を持つように
	// 設計されるので、**制動距離を決めるのはブレーキ容量ではなくタイヤ mu**。
	class FBrakes
	{
	public:
		bool Init(FVehicleData& Data, FString& OutError);

		/** (前軸合計, 後軸合計) のブレーキトルク [N*m]（正の値）。 */
		void AxleTorquesNm(double Pedal, double& OutFrontNm, double& OutRearNm) const;

		/**
		 * サイドブレーキによる**後軸のみ**のブレーキトルク [N*m]。
		 * 後輪だけをロックさせるため、後輪の横力が消えて車が回り始める。
		 */
		double HandbrakeAxleTorqueNm(double Lever) const;

		/**
		 * 前ブレーキの配分を差し替える（セッティング）。
		 *
		 * **範囲は FSetupLimits が保証する。** ここでは物理的にあり得ない
		 * 値だけを弾く。
		 */
		void SetBiasFront(double Value)
		{
			BiasFront = FMath::Clamp(Value, 0.0, 1.0);
		}
		double GetBiasFront() const { return BiasFront; }

	private:
		double BiasFront = 0.0;
		double MaxTotalTorqueNm = 0.0;
		double HandbrakeTorqueNm = 0.0;
	};

	// -----------------------------------------------------------------------
	// デファレンシャル
	// -----------------------------------------------------------------------
	//
	// 基準車両の GT はトルセンLSD を標準装備する。FR + LSD の効き方は FF と
	// 逆向きで、コーナー脱出のパワーオンで**内輪から外輪へトルクが移り、
	// オーバーステアを助長する**。Open Diff は比較基準としてのみ残す。
	class FDifferential
	{
	public:
		bool Init(FVehicleData& Data, bool bInUseLsd, FString& OutError);

		/** (左, 右) へのトルク配分 [N*m]。 */
		void SplitTorqueNm(double TotalTorqueNm, double OmegaLeftRads, double OmegaRightRads,
		                   double& OutLeftNm, double& OutRightNm) const;

	private:
		bool bUseLsd = true;
		double PreloadNm = 0.0;
		double AccelLockRatio = 0.0;
		double DecelLockRatio = 0.0;
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
