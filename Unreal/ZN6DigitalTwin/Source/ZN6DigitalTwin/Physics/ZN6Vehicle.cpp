#include "ZN6Vehicle.h"

#include "ZN6Units.h"

namespace ZN6
{
	const TCHAR* const WheelNames[WheelCount] = { TEXT("FL"), TEXT("FR"), TEXT("RL"), TEXT("RR") };

	namespace
	{
		/**
		 * これ以下の回転差ならクラッチはロックしているとみなす [rad/s]。
		 * （約 20 rpm。実車のクラッチは繋がれば回転差ゼロ）
		 *
		 * **Physics/vehicle.py の LOCK_TOLERANCE_RADS と同じ値にすること。**
		 * clutch.py の SLIP_SCALE_RADS(0.5) とは別物。vehicle.py は
		 * Clutch から容量だけを借りて、剛性はこちらの値で決めている。
		 */
		constexpr double LockToleranceRads = 2.0;

		bool IsFrontWheel(int32 WheelIndex)
		{
			return WheelIndex == static_cast<int32>(EWheel::FL)
			    || WheelIndex == static_cast<int32>(EWheel::FR);
		}

		bool IsRearWheel(int32 WheelIndex)
		{
			return !IsFrontWheel(WheelIndex);
		}
	}

	bool FVehicle::Init(FVehicleData& Data, bool bUseLsd, FString& OutError,
	                    const FCarSetup& InSetup)
	{
		Setup = InSetup;

		if (!Engine.Init(Data, OutError)) { return false; }
		if (!Drivetrain.Init(Data, OutError)) { return false; }
		if (!Brakes.Init(Data, OutError)) { return false; }
		if (Setup.BrakeBias >= 0.0)
		{
			Brakes.SetBiasFront(Setup.BrakeBias);
		}
		if (!Clutch.Init(Data, OutError)) { return false; }
		if (!Aero.Init(Data, OutError)) { return false; }
		if (!Differential.Init(Data, bUseLsd, OutError)) { return false; }

		if (!Data.GetValue(TEXT("mass.curb_mass"), TEXT("kg"), MassKg, OutError)) { return false; }
		if (!Data.GetValue(TEXT("inertia.Izz"), TEXT("kg*m^2"), IzzKgm2, OutError)) { return false; }
		if (!Data.GetValue(TEXT("dimensions.wheelbase"), TEXT("m"), WheelbaseM, OutError)) { return false; }
		if (!Data.GetValue(TEXT("dimensions.track_front"), TEXT("m"), TrackFrontM, OutError)) { return false; }
		if (!Data.GetValue(TEXT("dimensions.track_rear"), TEXT("m"), TrackRearM, OutError)) { return false; }
		// 車高を下げれば重心も下がる。**基準値そのものは書き換えない。**
		if (!Data.GetValue(TEXT("inertia.cg_height"), TEXT("m"),
		                   CgHeightBaselineM, OutError)) { return false; }
		CgHeightM = Setup.CgHeightM(CgHeightBaselineM);
		if (!Data.GetValue(TEXT("inertia.cg_longitudinal_from_front_axle"), TEXT("m"), LfM, OutError)) { return false; }
		if (!Data.GetValue(TEXT("suspension.roll_stiffness_distribution_front"), TEXT("-"),
		                   RollDistFront, OutError)) { return false; }
		if (!Data.GetValue(TEXT("tires.rolling_resistance_coefficient"), TEXT("-"), Crr, OutError)) { return false; }
		if (!Data.GetValue(TEXT("tires.wheel_rotational_inertia"), TEXT("kg*m^2"),
		                   WheelInertiaKgm2, OutError)) { return false; }
		if (!Data.GetValue(TEXT("engine.rotational_inertia"), TEXT("kg*m^2"),
		                   EngineInertiaKgm2, OutError)) { return false; }

		double IdleRpm = 0.0;
		if (!Data.GetValue(TEXT("engine.idle_rpm"), TEXT("1/min"), IdleRpm, OutError)) { return false; }
		IdleOmegaRads = RpmToRads(IdleRpm);

		LrM = WheelbaseM - LfM;

		const double WeightN = MassKg * GravityMps2;
		// **キャンバーを使うときだけ係数を読む**（信頼度を不要に下げない）。
		if (!Tire.Init(Data, WeightN / 4.0, OutError, Setup.UsesCamber()))
		{
			return false;
		}
		WheelRadiusM = Tire.GetEffectiveRadiusM();
		StaticFrontN = WeightN * LrM / WheelbaseM;
		StaticRearN = WeightN * LfM / WheelbaseM;

		WheelPosX[static_cast<int32>(EWheel::FL)] =  LfM;  WheelPosY[static_cast<int32>(EWheel::FL)] =  TrackFrontM / 2.0;
		WheelPosX[static_cast<int32>(EWheel::FR)] =  LfM;  WheelPosY[static_cast<int32>(EWheel::FR)] = -TrackFrontM / 2.0;
		WheelPosX[static_cast<int32>(EWheel::RL)] = -LrM;  WheelPosY[static_cast<int32>(EWheel::RL)] =  TrackRearM / 2.0;
		WheelPosX[static_cast<int32>(EWheel::RR)] = -LrM;  WheelPosY[static_cast<int32>(EWheel::RR)] = -TrackRearM / 2.0;

		Confidence = Data.ResultConfidence();
		bValidatable = Data.IsValidatable();
		return true;
	}

	void FVehicle::WheelLoadsN(double AxMps2, double AyMps2, double OutLoadsN[WheelCount],
	                           double NormalScale) const
	{
		// 前後: 加速で後軸へ。**FR なので駆動輪の荷重が増える。**
		const double LongitudinalTransfer = MassKg * AxMps2 * CgHeightM / WheelbaseM;
		const double FrontTotal = StaticFrontN * NormalScale - LongitudinalTransfer;
		const double RearTotal = StaticRearN * NormalScale + LongitudinalTransfer;

		// 左右: 前後ロール剛性配分で分配する
		const double LateralFront = RollDistFront * MassKg * AyMps2 * CgHeightM / TrackFrontM;
		const double LateralRear = (1.0 - RollDistFront) * MassKg * AyMps2 * CgHeightM / TrackRearM;

		// ay が正（左向き加速 = 左旋回）のとき荷重は右へ移る
		OutLoadsN[static_cast<int32>(EWheel::FL)] = FrontTotal / 2.0 - LateralFront;
		OutLoadsN[static_cast<int32>(EWheel::FR)] = FrontTotal / 2.0 + LateralFront;
		OutLoadsN[static_cast<int32>(EWheel::RL)] = RearTotal / 2.0 - LateralRear;
		OutLoadsN[static_cast<int32>(EWheel::RR)] = RearTotal / 2.0 + LateralRear;

		// 内輪が浮いたら負にせずゼロで止める（片輪浮き）
		for (int32 Index = 0; Index < WheelCount; ++Index)
		{
			OutLoadsN[Index] = FMath::Max(OutLoadsN[Index], 0.0);
		}
	}

	double FVehicle::WheelSteerRad(int32 WheelIndex, double SteerRad) const
	{
		// **後輪にも角度が付きうる**（後輪トー）。既定では 0 になる。
		const double Base = IsFrontWheel(WheelIndex) ? SteerRad : 0.0;
		return Base + Setup.WheelToeRad(WheelIndex);
	}

	void FVehicle::WheelVelocity(const FVehicleState& State, int32 WheelIndex, double SteerRad,
	                             double& OutVxMps, double& OutVyMps) const
	{
		const double X = WheelPosX[WheelIndex];
		const double Y = WheelPosY[WheelIndex];

		double Vx = State.VxMps - State.YawRateRads * Y;
		double Vy = State.VyMps + State.YawRateRads * X;

		// **角度がちょうど 0 なら回さない。** 回転を通すと丸めで最下位ビットが
		// 動きうる。既定のセッティングで以前と完全に一致させるための分岐。
		const double Angle = WheelSteerRad(WheelIndex, SteerRad);
		if (Angle != 0.0)
		{
			const double CosD = std::cos(Angle);
			const double SinD = std::sin(Angle);
			const double RotatedVx =  Vx * CosD + Vy * SinD;
			const double RotatedVy = -Vx * SinD + Vy * CosD;
			Vx = RotatedVx;
			Vy = RotatedVy;
		}

		OutVxMps = Vx;
		OutVyMps = Vy;
	}

	void FVehicle::IntegrateEngine(double EngineOmegaRads, double GearboxOmegaRads,
	                               const FControlInput& Control, double DtS,
	                               double& OutEngineOmega, double& OutClutchTorque,
	                               double& OutEngineTorque, bool& bOutLocked) const
	{
		constexpr int32 Substeps = 4;

		const double Capacity = Clutch.GetCapacityNm() * Control.Clutch;

		// **完全に繋がっていればロック。** 実車のクラッチは容量がエンジン
		// 最大トルクの 1.3-1.8 倍あるので、繋がっていれば滑らない。
		//
		// 回転差でロック判定していたときは、時間刻みを 0.002 -> 0.004 に
		// 変えるだけでラップが 55s -> 82s になった（刻みが粗いと回転差が
		// 判定値を超えてスリップ扱いになるため）。踏み量で判定すれば
		// 時間刻みに依存しない。
		const bool bLocked = Control.Clutch > 0.95;

		if (bLocked)
		{
			// 拘束。エンジンは変速機入力と一体で回る
			//
			// **変速機側がアイドルより遅いときは、アイドル制御がエンジンを
			// 支えている。** 実車の ECU は回転が落ちると燃料を足して回転を保つ。
			const bool bGoverned = GearboxOmegaRads < IdleOmegaRads;
			const double Omega = FMath::Max(GearboxOmegaRads, IdleOmegaRads);
			const double EngineTorque = Engine.TorqueNm(Omega, Control.Throttle);

			// エンジンが出したトルクはそのままクラッチを通る。慣性による
			// 抵抗は、車輪側に反映した等価慣性が受け持つ。
			double ClutchTorque = EngineTorque;
			if (bGoverned)
			{
				// **支えられているエンジンは駆動系を後ろへ引けない。**
				//
				// 回転をアイドルで切り上げているだけだった間は、停車中に
				// 1速でクラッチを繋ぐと閉じスロットルの負トルクが残り、車が
				// 後ろへ 0.3 m/s でずり下がった。前後速度を 0 で切り上げて
				// いたので見えていなかっただけである。
				//
				// 実車ではこの状態は「アイドル制御が支える」か「エンストする」
				// のどちらかで、**後ろへ押す**ことはない。エンストは別途
				// モデル化していないので、ここでは支える側を採る。
				ClutchTorque = FMath::Max(ClutchTorque, 0.0);
			}
			if (FMath::Abs(ClutchTorque) > Capacity)
			{
				ClutchTorque = FMath::Sign(ClutchTorque) * Capacity;
			}

			OutEngineOmega = Omega;
			OutClutchTorque = ClutchTorque;
			OutEngineTorque = EngineTorque;
			bOutLocked = true;
			return;
		}

		// --- 滑っている（切っている / 半クラッチ / 回転差が大きい）---
		double Omega = EngineOmegaRads;
		const double SubDt = DtS / static_cast<double>(Substeps);
		double ClutchTorque = 0.0;
		double EngineTorque = 0.0;

		for (int32 Step = 0; Step < Substeps; ++Step)
		{
			EngineTorque = Engine.TorqueNm(FMath::Max(Omega, 0.0), Control.Throttle);

			if (Capacity <= 0.0)
			{
				ClutchTorque = 0.0;  // 完全に切れている。空吹かし
			}
			else
			{
				// **回転差に比例。容量で頭打ち。**
				// 常に容量いっぱいを掛けていたときは、エンジンがわずかに
				// 遅いだけで大きな制動が入り、車が極端に遅くなった。
				const double Stiffness = Capacity / LockToleranceRads;
				ClutchTorque = Stiffness * (Omega - GearboxOmegaRads);
				ClutchTorque = FMath::Max(FMath::Min(ClutchTorque, Capacity), -Capacity);
			}

			Omega += (EngineTorque - ClutchTorque) / EngineInertiaKgm2 * SubDt;
			Omega = FMath::Max(Omega, IdleOmegaRads);
		}

		OutEngineOmega = Omega;
		OutClutchTorque = ClutchTorque;
		OutEngineTorque = EngineTorque;
		bOutLocked = false;
	}

	void FVehicle::Step(const FVehicleState& State, const FControlInput& Control, double DtS,
	                    FVehicleState& OutState, FVehicleOutputs& OutOutputs,
	                    double SlopeGxMps2, double SlopeGyMps2, double NormalScale,
	                    const double* ContactLoadsN)
	{
		OutOutputs = FVehicleOutputs();

		// --- 垂直荷重 ---
		double Fz[WheelCount];
		if (ContactLoadsN == nullptr)
		{
			// 前ステップの加速度から荷重を決める（準静的）。
			// 反復せず1ステップ遅らせる。dt が十分小さければ差は無視できる。
			WheelLoadsN(LastAxMps2, LastAyMps2, Fz, NormalScale);
		}
		else
		{
			// 接地モデルが解いた力を使う。**負を通さない**
			// （地面は押せるが引けない）。
			for (int32 Wheel = 0; Wheel < WheelCount; ++Wheel)
			{
				Fz[Wheel] = FMath::Max(ContactLoadsN[Wheel], 0.0);
			}
		}

		// --- エンジンとクラッチ ---
		const double RearOmegaMean =
			(State.WheelOmegaRads[static_cast<int32>(EWheel::RL)] +
			 State.WheelOmegaRads[static_cast<int32>(EWheel::RR)]) / 2.0;

		// **ニュートラルでは歯車が噛んでいない。**
		//
		// クラッチペダルをどこまで戻していても、変速機の中で入力軸と出力軸が
		// 繋がっていないので車輪へトルクは行かない。クラッチを切ったのと
		// 同じ扱い（Clutch = 0）にすると、
		//
		//   - クラッチ容量が 0 になり伝達トルクが 0
		//   - bClutchLocked が false になり、車輪慣性にエンジン慣性を足す枝も
		//     自動的に外れる
		//
		// が同時に成り立つ。**「だいたい同じだから」ではなく、動力の通り道が
		// 無いという同じ理由で同じ式になる。**
		const bool bNeutral = (Control.GearIndex == GearNeutral);

		FControlInput EngineControl = Control;
		double GearboxOmega = 0.0;
		if (bNeutral)
		{
			// 変速機入力軸は空転する。意味の無い値を渡さないよう、
			// 回転差が出ないエンジン回転をそのまま入れる（容量 0 なので
			// 結果には効かない）。
			GearboxOmega = State.EngineOmegaRads;
			EngineControl.Clutch = 0.0;
		}
		else
		{
			GearboxOmega = Drivetrain.EngineOmegaRads(RearOmegaMean, Control.GearIndex);
		}

		double EngineOmega = 0.0;
		double ClutchTorque = 0.0;
		double EngineTorque = 0.0;
		bool bClutchLocked = false;
		IntegrateEngine(State.EngineOmegaRads, GearboxOmega, EngineControl, DtS,
		                EngineOmega, ClutchTorque, EngineTorque, bClutchLocked);

		const double AxleTorque = bNeutral
			? 0.0
			: Drivetrain.WheelTorqueNm(ClutchTorque, Control.GearIndex);

		double TorqueRL = 0.0;
		double TorqueRR = 0.0;
		Differential.SplitTorqueNm(AxleTorque,
		                           State.WheelOmegaRads[static_cast<int32>(EWheel::RL)],
		                           State.WheelOmegaRads[static_cast<int32>(EWheel::RR)],
		                           TorqueRL, TorqueRR);

		double DriveTorque[WheelCount] = {};
		DriveTorque[static_cast<int32>(EWheel::RL)] = TorqueRL;
		DriveTorque[static_cast<int32>(EWheel::RR)] = TorqueRR;

		double BrakeFront = 0.0;
		double BrakeRear = 0.0;
		Brakes.AxleTorquesNm(Control.Brake, BrakeFront, BrakeRear);
		// サイドブレーキは**後輪のみ**。後輪をロックさせて横力を消す
		BrakeRear += Brakes.HandbrakeAxleTorqueNm(Control.Handbrake);

		double BrakeTorque[WheelCount] = {};
		BrakeTorque[static_cast<int32>(EWheel::FL)] = BrakeFront / 2.0;
		BrakeTorque[static_cast<int32>(EWheel::FR)] = BrakeFront / 2.0;
		BrakeTorque[static_cast<int32>(EWheel::RL)] = BrakeRear / 2.0;
		BrakeTorque[static_cast<int32>(EWheel::RR)] = BrakeRear / 2.0;

		// --- 各輪のタイヤ力 ---
		double ForceBodyX[WheelCount] = {};
		double ForceBodyY[WheelCount] = {};
		double NewOmega[WheelCount] = {};

		for (int32 Wheel = 0; Wheel < WheelCount; ++Wheel)
		{
			double VxW = 0.0;
			double VyW = 0.0;
			WheelVelocity(State, Wheel, Control.SteerRad, VxW, VyW);

			const double Omega = State.WheelOmegaRads[Wheel];
			const double Kappa = FTire::SlipRatio(Omega, WheelRadiusM, VxW);
			const double Alpha = FTire::SlipAngleRad(VyW, VxW);

			const double CamberLean = Setup.WheelCamberLeanRad(Wheel);

			double FxW = 0.0;
			double FyW = 0.0;
			Tire.ForcesN(Fz[Wheel], Kappa, Alpha, FxW, FyW, CamberLean);

			// 転がり抵抗（進行方向と逆）
			if (FMath::Abs(VxW) > 0.1)
			{
				FxW -= FMath::Sign(VxW) * (Crr * Fz[Wheel]);
			}

			// 車輪の回転運動。
			// ロック中はエンジンと車輪が一体で回るので、エンジン慣性を車輪軸へ
			// 換算して足す（1速では総比^2 = 約221倍になり支配的）。
			// 滑っている間はエンジンが切り離されているので足さない。
			double Inertia = WheelInertiaKgm2;
			if (IsRearWheel(Wheel) && bClutchLocked && !bNeutral)
			{
				Inertia += Drivetrain.ReflectedInertiaAtWheelKgm2(Control.GearIndex) / 2.0;
			}

			const double Brake = (FMath::Abs(Omega) > 0.1)
				? FMath::Sign(Omega) * BrakeTorque[Wheel]
				: 0.0;

			const double OmegaDot = (DriveTorque[Wheel] - Brake - FxW * WheelRadiusM) / Inertia;

			// 半陰的に積分する（issue #24）。**Python 版と同じ式にすること。**
			//
			// 陽解法だと低速で毎ステップ振動した。fx は omega に依存する
			// （kappa = (omega*r - v)/max(|v|,0.5)）のに、その依存を陽に
			// 扱っていたため。静止発進 dt=0.002 で符号反転 284/299 回。
			//
			// fx の omega 依存を線形化して陰的に解く:
			//
			//     d = dt/I * (T - r*fx(omega))
			//     omega_new = omega + d / (1 + dt*r*k/I)   k = d(fx)/d(omega)
			//
			// k は動作点での**接線剛性**。線形域の c_kappa を使うと、
			// 飽和している発進時に過剰減衰する。
			//
			// **定常解は陽解法と同じ。** 分母は増分に掛かるだけで、
			// 増分がゼロになる条件（T = r*fx）を変えない。
			const double DFxDKappa =
				Tire.LongitudinalSlopeNPerSlip(Fz[Wheel], Kappa, Alpha, CamberLean);
			const double DFxDOmega = DFxDKappa * WheelRadiusM / FMath::Max(FMath::Abs(VxW), 0.5);
			const double Damping = 1.0 + DtS * WheelRadiusM * DFxDOmega / Inertia;
			double OmegaNew = Omega + OmegaDot * DtS / Damping;

			// 制動でゼロを跨いだらロックさせる（逆回転させない）
			if (Control.Brake > 0.0 && Omega * OmegaNew < 0.0)
			{
				OmegaNew = 0.0;
			}
			NewOmega[Wheel] = OmegaNew;

			// 車輪座標系 -> 車体座標系。**速度を回したのと同じ角度で戻す。**
			// 別の角度を使うと、力と速度の向きが食い違って仕事が合わなくなる。
			const double Angle = WheelSteerRad(Wheel, Control.SteerRad);
			if (Angle != 0.0)
			{
				const double CosD = std::cos(Angle);
				const double SinD = std::sin(Angle);
				ForceBodyX[Wheel] = FxW * CosD - FyW * SinD;
				ForceBodyY[Wheel] = FxW * SinD + FyW * CosD;
			}
			else
			{
				ForceBodyX[Wheel] = FxW;
				ForceBodyY[Wheel] = FyW;
			}

			OutOutputs.TireFzN[Wheel] = Fz[Wheel];
			OutOutputs.TireFxN[Wheel] = FxW;
			OutOutputs.TireFyN[Wheel] = FyW;
			OutOutputs.SlipRatio[Wheel] = Kappa;
			OutOutputs.SlipAngleRad[Wheel] = Alpha;

			const double Limit = Tire.MaxLongitudinalForceN(Fz[Wheel]);
			OutOutputs.Utilisation[Wheel] = (Limit > 1.0)
				? std::hypot(FxW, FyW) / Limit
				: 0.0;
		}

		// --- 車体の運動 ---
		double SumFx = 0.0;
		double SumFy = 0.0;
		double SumMz = 0.0;
		for (int32 Wheel = 0; Wheel < WheelCount; ++Wheel)
		{
			SumFx += ForceBodyX[Wheel];
			SumFy += ForceBodyY[Wheel];
			SumMz += WheelPosX[Wheel] * ForceBodyY[Wheel] - WheelPosY[Wheel] * ForceBodyX[Wheel];
		}

		SumFx -= FMath::Sign(State.VxMps) * Aero.DragForceN(State.VxMps);

		// **加速度と状態微分を混同しないこと。**
		//   加速度（加速度計が読む値。摩擦円で制限される）  a = F / m
		//   状態微分（車体固定系なので回転項が入る）        vx_dot = ax + vy*r
		// スピン中は vx*r が大きく、vy_dot は摩擦限界をはるかに超えうる。
		// これを ay として記録すると「mu 1.1 で 2.8g」という偽の警告が出る。
		const double AxMps2 = SumFx / MassKg;
		const double AyMps2 = SumFy / MassKg;
		// **重力は状態微分にだけ足す。** AxMps2 は加速度計が読む値
		// （タイヤ力/質量）のままにしておく。そうしないと荷重移動が
		// 二重に数えられる（WheelLoadsN のコメント参照）。
		const double VxDot = AxMps2 + SlopeGxMps2 + State.VyMps * State.YawRateRads;
		const double VyDot = AyMps2 + SlopeGyMps2 - State.VxMps * State.YawRateRads;
		const double YawAccel = SumMz / IzzKgm2;

		LastAxMps2 = AxMps2;
		LastAyMps2 = AyMps2;

		// **前後速度を 0 で切り上げない。**
		//
		// 以前は FMath::Max(..., 0.0) で負を潰していた。前進しか無かった間は
		// 「止まっているのに後ろへずり下がる」のを防いでいたが、**後退に
		// 入れても車が動かない**という形で表に出た（加速度は正しく負を
		// 向いているのに、速度が毎ステップ 0 に戻されていた）。
		//
		// 止まった車が勝手に下がらないことは、タイヤ力と制動トルクが受け持つ
		// べきで、状態変数を切り上げて作る性質ではない。
		OutState.VxMps = State.VxMps + VxDot * DtS;
		OutState.VyMps = State.VyMps + VyDot * DtS;
		OutState.YawRateRads = State.YawRateRads + YawAccel * DtS;
		OutState.XM = State.XM + (State.VxMps * std::cos(State.HeadingRad)
		                        - State.VyMps * std::sin(State.HeadingRad)) * DtS;
		OutState.YM = State.YM + (State.VxMps * std::sin(State.HeadingRad)
		                        + State.VyMps * std::cos(State.HeadingRad)) * DtS;
		OutState.HeadingRad = State.HeadingRad + State.YawRateRads * DtS;
		OutState.EngineOmegaRads = EngineOmega;
		for (int32 Wheel = 0; Wheel < WheelCount; ++Wheel)
		{
			OutState.WheelOmegaRads[Wheel] = NewOmega[Wheel];
		}

		OutOutputs.AxMps2 = AxMps2;
		OutOutputs.AyMps2 = AyMps2;
		OutOutputs.YawAccelRads2 = YawAccel;
		OutOutputs.EngineRpm = RadsToRpm(EngineOmega);
		OutOutputs.EngineTorqueNm = EngineTorque;
		OutOutputs.ClutchTorqueNm = ClutchTorque;
		OutOutputs.ClutchSlipRads = EngineOmega - GearboxOmega;
	}

	FVehicleState FVehicle::InitialState(double SpeedMps, int32 GearIndex) const
	{
		FVehicleState State;
		const double Omega = SpeedMps / WheelRadiusM;

		State.VxMps = SpeedMps;
		// ニュートラルでは噛んでいないので車速からエンジン回転を決められない。
		State.EngineOmegaRads = (GearIndex == GearNeutral)
			? IdleOmegaRads
			: FMath::Max(Drivetrain.EngineOmegaRads(Omega, GearIndex), IdleOmegaRads);
		for (int32 Wheel = 0; Wheel < WheelCount; ++Wheel)
		{
			State.WheelOmegaRads[Wheel] = Omega;
		}
		return State;
	}
}
