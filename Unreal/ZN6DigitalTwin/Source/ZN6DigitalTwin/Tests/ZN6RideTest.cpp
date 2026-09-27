// 接地モデルの検査（Tests/test_ride.py の対応物）。
//
// **「見た目が正しいか」では判定しない。** 接地は目で見ても分からない。
// 保存則と拘束条件で見る（.claude/rules/physics.md）:
//
//   1. 静止した車の接地力の合計が車重に一致するか
//   2. 地面は押せるが引けないか（接地力が負にならない）
//   3. 落とせば落ちるか（浮いている間の加速度がちょうど g か）
//   4. 定常状態で準静的モデルと前後の荷重移動が一致するか
//   5. 段差で車輪が浮くか

#include "CoreMinimal.h"
#include "Misc/AutomationTest.h"
#include "Misc/Paths.h"

#include "Physics/ZN6Ride.h"
#include "Physics/ZN6Units.h"
#include "Physics/ZN6Vehicle.h"
#include "Physics/ZN6VehicleData.h"

#if WITH_DEV_AUTOMATION_TESTS

namespace
{
	FString RideRepoRoot()
	{
		return FPaths::ConvertRelativePathToFull(FPaths::ProjectDir() / TEXT("../.."));
	}

	bool MakeRide(FAutomationTestBase& Test, ZN6::FVehicleData& OutData,
	              ZN6::FRideModel& OutRide)
	{
		FString Error;
		if (!OutData.LoadFromFile(RideRepoRoot() / TEXT("Vehicles/ZN6/vehicle.json"), Error))
		{
			Test.AddError(FString::Printf(TEXT("vehicle.json を読めない: %s"), *Error));
			return false;
		}
		if (!OutRide.Init(OutData, Error))
		{
			Test.AddError(FString::Printf(TEXT("接地モデルを初期化できない: %s"), *Error));
			return false;
		}
		return true;
	}

	/** 平地。**4輪とも高さ 0。** */
	struct FFlatGround
	{
		double M[ZN6::WheelCount] = { 0.0, 0.0, 0.0, 0.0 };
	};
}

// ---------------------------------------------------------------------------

IMPLEMENT_SIMPLE_AUTOMATION_TEST(
	FZN6RideEquilibrium,
	"ZN6.Ride.静止した車が車輪に支えられている",
	EAutomationTestFlags::EditorContext | EAutomationTestFlags::EngineFilter)

bool FZN6RideEquilibrium::RunTest(const FString& Parameters)
{
	ZN6::FVehicleData Data;
	ZN6::FRideModel Ride;
	if (!MakeRide(*this, Data, Ride))
	{
		return false;
	}

	// --- 諸元の読み方 ---
	//
	// 上下固有振動数は乗用車の範囲に入ること。**実測との比較ではない**
	// （測っていない）。物理的にあり得ない値を採用しないための検査。
	for (int32 Wheel = 0; Wheel < ZN6::WheelCount; ++Wheel)
	{
		const double FreqHz = Ride.NaturalFrequencyHz(Wheel);
		TestTrue(*FString::Printf(TEXT("車輪 %d の上下固有振動数 %.3f Hz が範囲内"),
		                          Wheel, FreqHz),
		         FreqHz > 0.8 && FreqHz < 2.0);
	}
	AddInfo(FString::Printf(
		TEXT("上下固有振動数 前 %.3f Hz / 後 %.3f Hz、ロール剛性配分（ばねから導出）%.4f"),
		Ride.NaturalFrequencyHz(static_cast<int32>(ZN6::EWheel::FL)),
		Ride.NaturalFrequencyHz(static_cast<int32>(ZN6::EWheel::RL)),
		Ride.RollStiffnessDistributionFront()));

	// --- 釣り合い ---
	const FFlatGround Flat;
	ZN6::FRideState Settled;
	ZN6::FRideOutputs Outputs;
	if (!TestTrue(TEXT("平地で釣り合いに収束する"),
	              Ride.Settle(Flat.M, Settled, Outputs)))
	{
		return false;
	}

	double TotalN = 0.0;
	for (int32 Wheel = 0; Wheel < ZN6::WheelCount; ++Wheel)
	{
		TotalN += Outputs.LoadsN[Wheel];
	}
	const double WeightN = Ride.MassKg() * ZN6::GravityMps2;
	TestTrue(*FString::Printf(TEXT("接地力の合計が車重（%.4f / %.4f N）"), TotalN, WeightN),
	         FMath::Abs(TotalN - WeightN) < 1e-2);

	// **平地の釣り合いが状態の原点であること。**
	// ここがゼロでなければ、止まっている車が勝手に傾く（issue #29）。
	TestTrue(*FString::Printf(TEXT("平地の釣り合いが原点（heave %.3e / pitch %.3e / roll %.3e）"),
	                          Settled.HeaveM, Settled.PitchRad, Settled.RollRad),
	         FMath::Abs(Settled.HeaveM) < 1e-9
	         && FMath::Abs(Settled.PitchRad) < 1e-9
	         && FMath::Abs(Settled.RollRad) < 1e-9);

	// --- 準静的モデルと静荷重が一致すること ---
	//
	// **2つのモデルで静止時の軸重が違ったら、片方は間違っている。**
	ZN6::FVehicle Vehicle;
	FString Error;
	if (!Vehicle.Init(Data, /*bUseLsd=*/true, Error))
	{
		AddError(FString::Printf(TEXT("物理モデルを初期化できない: %s"), *Error));
		return false;
	}

	double QuasiStatic[ZN6::WheelCount] = {};
	Vehicle.WheelLoadsN(0.0, 0.0, QuasiStatic);
	for (int32 Wheel = 0; Wheel < ZN6::WheelCount; ++Wheel)
	{
		TestTrue(
			*FString::Printf(TEXT("車輪 %d の静荷重が準静的モデルと一致（%.4f / %.4f N）"),
			                 Wheel, Outputs.LoadsN[Wheel], QuasiStatic[Wheel]),
			FMath::Abs(Outputs.LoadsN[Wheel] - QuasiStatic[Wheel]) < 1e-3);
	}

	return true;
}

// ---------------------------------------------------------------------------

IMPLEMENT_SIMPLE_AUTOMATION_TEST(
	FZN6RideGravity,
	"ZN6.Ride.落とせば落ちて、車輪に受け止められる",
	EAutomationTestFlags::EditorContext | EAutomationTestFlags::EngineFilter)

bool FZN6RideGravity::RunTest(const FString& Parameters)
{
	ZN6::FVehicleData Data;
	ZN6::FRideModel Ride;
	if (!MakeRide(*this, Data, Ride))
	{
		return false;
	}

	const FFlatGround Flat;
	ZN6::FRideOutputs Outputs;

	// --- 浮いている間は自由落下 ---
	//
	// **接地力が 0 なら、加速度は重力そのもの。**
	// ここが g より小さければ、接地していないのに何かが支えている。
	{
		ZN6::FRideState High;
		High.HeaveM = 1.0;

		ZN6::FRideState Next;
		Ride.Step(High, 0.001, Flat.M, 0.0, 0.0, Next, Outputs);

		bool bAnyContact = false;
		for (int32 Wheel = 0; Wheel < ZN6::WheelCount; ++Wheel)
		{
			bAnyContact = bAnyContact || Outputs.bContact[Wheel];
		}
		TestFalse(TEXT("1 m 持ち上げれば接地していない"), bAnyContact);
		TestTrue(TEXT("浮いていると分かる"), Next.bAirborne);

		const double Accel = (Next.HeaveRateMps - High.HeaveRateMps) / 0.001;
		TestTrue(*FString::Printf(TEXT("自由落下の加速度が g（%.9f / %.9f）"),
		                          Accel, -ZN6::GravityMps2),
		         FMath::Abs(Accel + ZN6::GravityMps2) < 1e-9);
	}

	// --- 落として、跳ねて、釣り合いに戻る ---
	//
	// **これが再現できていないと「地面に置いているだけ」になる。**
	{
		ZN6::FRideState State;
		State.HeaveM = 0.15;

		double LowestM = State.HeaveM;
		bool bTouched = false;

		for (int32 Step = 0; Step < 6000; ++Step)      // 6 秒
		{
			ZN6::FRideState Next;
			Ride.Step(State, 0.001, Flat.M, 0.0, 0.0, Next, Outputs);
			State = Next;
			LowestM = FMath::Min(LowestM, State.HeaveM);
			for (int32 Wheel = 0; Wheel < ZN6::WheelCount; ++Wheel)
			{
				bTouched = bTouched || Outputs.bContact[Wheel];
			}
		}

		TestTrue(TEXT("一度は接地する"), bTouched);
		TestTrue(*FString::Printf(TEXT("沈み込む（最も低い位置 %.4f m）"), LowestM),
		         LowestM < 0.0);
		TestTrue(*FString::Printf(TEXT("釣り合いに戻る（%.5f m）"), State.HeaveM),
		         FMath::Abs(State.HeaveM) < 1e-3);
	}

	// --- 接地力は負にならない ---
	//
	// **地面は押せるが引けない。** max を外すと、浮いた車輪が車体を
	// 下へ引っ張る。見た目には「なんとなく沈む」だけなので気づけない。
	{
		const double Heaves[] = { -0.2, -0.05, 0.0, 0.05, 0.2, 0.5, 2.0 };
		const double Angles[] = { -0.2, 0.0, 0.2 };

		for (const double HeaveM : Heaves)
		{
			for (const double PitchRad : Angles)
			{
				for (const double RollRad : Angles)
				{
					ZN6::FRideState State;
					State.HeaveM = HeaveM;
					State.PitchRad = PitchRad;
					State.RollRad = RollRad;

					ZN6::FRideState Next;
					Ride.Step(State, 0.001, Flat.M, 0.0, 0.0, Next, Outputs);

					for (int32 Wheel = 0; Wheel < ZN6::WheelCount; ++Wheel)
					{
						if (Outputs.LoadsN[Wheel] < 0.0)
						{
							AddError(FString::Printf(
								TEXT("車輪 %d の接地力が負（%.3f N、heave %.2f）"),
								Wheel, Outputs.LoadsN[Wheel], HeaveM));
							return false;
						}
					}
				}
			}
		}
		TestTrue(TEXT("どの姿勢でも接地力が負にならない"), true);
	}

	// --- 段差で車輪が浮く ---
	//
	// 「浮く」が表現できないと、段差もギャップも無い世界になる。
	{
		double Hole[ZN6::WheelCount] = { 0.0, 0.0, 0.0, 0.0 };
		const int32 FL = static_cast<int32>(ZN6::EWheel::FL);
		Hole[FL] = -1.0;                       // 左前だけ 1 m 落ちている

		ZN6::FRideState State;
		bool bLifted = false;
		for (int32 Step = 0; Step < 2000; ++Step)
		{
			ZN6::FRideState Next;
			Ride.Step(State, 0.001, Hole, 0.0, 0.0, Next, Outputs);
			State = Next;
			bLifted = bLifted || !Outputs.bContact[FL];
		}

		TestTrue(TEXT("1 m の穴の上で左前が浮く"), bLifted);
		TestEqual(TEXT("浮いた車輪の接地力はゼロ"), Outputs.LoadsN[FL], 0.0);

		double TotalN = 0.0;
		for (int32 Wheel = 0; Wheel < ZN6::WheelCount; ++Wheel)
		{
			TotalN += Outputs.LoadsN[Wheel];
		}
		const double WeightN = Ride.MassKg() * ZN6::GravityMps2;
		TestTrue(*FString::Printf(TEXT("残り3輪で支える（%.1f / %.1f N）"), TotalN, WeightN),
		         FMath::Abs(TotalN - WeightN) < WeightN * 0.05);
	}

	return true;
}

// ---------------------------------------------------------------------------

IMPLEMENT_SIMPLE_AUTOMATION_TEST(
	FZN6RideLoadTransfer,
	"ZN6.Ride.荷重移動が準静的モデルと一致する",
	EAutomationTestFlags::EditorContext | EAutomationTestFlags::EngineFilter)

bool FZN6RideLoadTransfer::RunTest(const FString& Parameters)
{
	ZN6::FVehicleData Data;
	ZN6::FRideModel Ride;
	if (!MakeRide(*this, Data, Ride))
	{
		return false;
	}

	ZN6::FVehicle Vehicle;
	FString Error;
	if (!Vehicle.Init(Data, /*bUseLsd=*/true, Error))
	{
		AddError(FString::Printf(TEXT("物理モデルを初期化できない: %s"), *Error));
		return false;
	}

	const FFlatGround Flat;
	const int32 FL = static_cast<int32>(ZN6::EWheel::FL);
	const int32 FR = static_cast<int32>(ZN6::EWheel::FR);
	const int32 RL = static_cast<int32>(ZN6::EWheel::RL);
	const int32 RR = static_cast<int32>(ZN6::EWheel::RR);

	// --- 前後 ---
	//
	// **ここは厳密に一致すること。** 準静的モデルの m*ax*h/L は、
	// モーメントの釣り合いを解いた結果そのものである。
	for (const double AxMps2 : { -6.0, -3.0, 0.0, 3.0, 6.0 })
	{
		ZN6::FRideState State;
		ZN6::FRideOutputs Outputs;
		for (int32 Step = 0; Step < 20000; ++Step)     // 20 秒。過渡が収まるまで
		{
			ZN6::FRideState Next;
			Ride.Step(State, 0.001, Flat.M, AxMps2, 0.0, Next, Outputs);
			State = Next;
		}

		double QuasiStatic[ZN6::WheelCount] = {};
		Vehicle.WheelLoadsN(AxMps2, 0.0, QuasiStatic);

		const double FrontRide = Outputs.LoadsN[FL] + Outputs.LoadsN[FR];
		const double FrontQuasi = QuasiStatic[FL] + QuasiStatic[FR];

		TestTrue(
			*FString::Printf(TEXT("ax=%.1f: 前軸 %.3f N（準静的 %.3f N）"),
			                 AxMps2, FrontRide, FrontQuasi),
			FMath::Abs(FrontRide - FrontQuasi) < FMath::Max(FrontQuasi * 1e-6, 1e-3));

		if (AxMps2 > 0.0)
		{
			// FR なので**加速で駆動輪（後輪）に乗る**
			TestTrue(TEXT("加速で機首が上がる"), State.PitchRad > 0.0);
		}
	}

	// --- 左右 ---
	//
	// ay が正 = 左向き加速 = 左旋回。**荷重は外側（右）へ。**
	{
		ZN6::FRideState State;
		ZN6::FRideOutputs Outputs;
		for (int32 Step = 0; Step < 20000; ++Step)
		{
			ZN6::FRideState Next;
			Ride.Step(State, 0.001, Flat.M, 0.0, 5.0, Next, Outputs);
			State = Next;
		}

		TestTrue(*FString::Printf(TEXT("左旋回で右へ傾く（ロール %.5f rad）"), State.RollRad),
		         State.RollRad > 0.0);
		TestTrue(TEXT("前の外輪に荷重が乗る"), Outputs.LoadsN[FR] > Outputs.LoadsN[FL]);
		TestTrue(TEXT("後ろの外輪に荷重が乗る"), Outputs.LoadsN[RR] > Outputs.LoadsN[RL]);
	}

	// --- 過渡がある ---
	//
	// **準静的モデルには無いもの。** 瞬時には移らない。
	{
		ZN6::FRideState State;
		ZN6::FRideOutputs First;
		ZN6::FRideState Next;
		Ride.Step(State, 0.001, Flat.M, 0.0, 6.0, Next, First);
		const double TransferFirst = FMath::Abs(First.LoadsN[FR] - First.LoadsN[FL]);

		State = Next;
		ZN6::FRideOutputs SettledOut;
		for (int32 Step = 0; Step < 20000; ++Step)
		{
			ZN6::FRideState Later;
			Ride.Step(State, 0.001, Flat.M, 0.0, 6.0, Later, SettledOut);
			State = Later;
		}
		const double TransferSettled =
			FMath::Abs(SettledOut.LoadsN[FR] - SettledOut.LoadsN[FL]);

		TestTrue(
			*FString::Printf(TEXT("荷重移動に過渡がある（1歩目 %.1f N / 定常 %.1f N）"),
			                 TransferFirst, TransferSettled),
			TransferFirst < TransferSettled * 0.1);
	}

	// --- 坂 ---
	//
	// 上り坂（前が高い）に置いたら**機首が上がる**。
	// 符号が逆だと上り坂で前のめりになる。UE 側で実際にそうなっていた。
	{
		double FrontXM = 0.0;
		double FrontYM = 0.0;
		double RearXM = 0.0;
		double RearYM = 0.0;
		Ride.WheelPosition(FL, FrontXM, FrontYM);
		Ride.WheelPosition(RL, RearXM, RearYM);

		constexpr double Slope = 0.10;
		double Uphill[ZN6::WheelCount] = {};
		Uphill[FL] = Uphill[FR] = FrontXM * Slope;
		Uphill[RL] = Uphill[RR] = RearXM * Slope;

		ZN6::FRideState Settled;
		ZN6::FRideOutputs Outputs;
		TestTrue(TEXT("坂の上で釣り合いに収束する"),
		         Ride.Settle(Uphill, Settled, Outputs));
		TestTrue(*FString::Printf(TEXT("上り坂で機首が上がる（%.5f rad）"), Settled.PitchRad),
		         Settled.PitchRad > 0.0);
		TestTrue(*FString::Printf(TEXT("坂の傾きと一致する（%.5f / %.5f rad）"),
		                          Settled.PitchRad, FMath::Atan(Slope)),
		         FMath::Abs(Settled.PitchRad - FMath::Atan(Slope)) < 0.01);
	}

	return true;
}

// ---------------------------------------------------------------------------

IMPLEMENT_SIMPLE_AUTOMATION_TEST(
	FZN6RideDrivesTyreLoads,
	"ZN6.Ride.接地力をタイヤへ渡すと浮いた輪が効かなくなる",
	EAutomationTestFlags::EditorContext | EAutomationTestFlags::EngineFilter)

bool FZN6RideDrivesTyreLoads::RunTest(const FString& Parameters)
{
	ZN6::FVehicleData Data;
	ZN6::FRideModel Ride;
	if (!MakeRide(*this, Data, Ride))
	{
		return false;
	}

	FString Error;
	ZN6::FVehicle Vehicle;
	if (!Vehicle.Init(Data, /*bUseLsd=*/true, Error))
	{
		AddError(FString::Printf(TEXT("物理モデルを初期化できない: %s"), *Error));
		return false;
	}

	ZN6::FControlInput Control;
	Control.GearIndex = 2;
	Control.Throttle = 0.5;
	Control.SteerRad = 0.05;
	Control.Clutch = 1.0;

	const int32 FL = static_cast<int32>(ZN6::EWheel::FL);
	const int32 FR = static_cast<int32>(ZN6::EWheel::FR);

	// --- 渡さなければ今までどおり ---
	//
	// **繋いだこと自体で検証済みの結果を動かさない。**
	{
		ZN6::FVehicleState A = Vehicle.InitialState(70.0 / 3.6, 2);
		ZN6::FVehicleState B = A;
		ZN6::FVehicleOutputs OutA;
		ZN6::FVehicleOutputs OutB;

		for (int32 Step = 0; Step < 200; ++Step)
		{
			ZN6::FVehicleState NextA;
			Vehicle.Step(A, Control, 0.002, NextA, OutA);
			A = NextA;
		}
		ZN6::FVehicle Second;
		Second.Init(Data, /*bUseLsd=*/true, Error);
		for (int32 Step = 0; Step < 200; ++Step)
		{
			ZN6::FVehicleState NextB;
			Second.Step(B, Control, 0.002, NextB, OutB,
			            0.0, 0.0, 1.0, /*ContactLoadsN=*/nullptr);
			B = NextB;
		}
		TestEqual(TEXT("nullptr を渡しても結果が同じ（vx）"), B.VxMps, A.VxMps);
		TestEqual(TEXT("nullptr を渡しても結果が同じ（ヨー）"),
		          B.YawRateRads, A.YawRateRads);
	}

	// --- 浮いた輪はタイヤ力を出さない ---
	//
	// **これが今まで出来ていなかった。** 接地モデルが「地面を押していない」
	// と言っているのに、タイヤは準静的な荷重で力を出し続けていた。
	{
		double Quasi[ZN6::WheelCount] = {};
		Vehicle.WheelLoadsN(0.0, 0.0, Quasi);

		double Lifted[ZN6::WheelCount] = {};
		for (int32 Wheel = 0; Wheel < ZN6::WheelCount; ++Wheel)
		{
			Lifted[Wheel] = Quasi[Wheel];
		}
		Lifted[FL] = 0.0;

		ZN6::FVehicleState State = Vehicle.InitialState(60.0 / 3.6, 2);
		ZN6::FVehicleState Next;
		ZN6::FVehicleOutputs Outputs;
		Vehicle.Step(State, Control, 0.002, Next, Outputs, 0.0, 0.0, 1.0, Lifted);

		TestEqual(TEXT("浮いた輪の垂直荷重はゼロ"), Outputs.TireFzN[FL], 0.0);
		TestEqual(TEXT("浮いた輪は前後力を出さない"), Outputs.TireFxN[FL], 0.0);
		TestEqual(TEXT("浮いた輪は横力を出さない"), Outputs.TireFyN[FL], 0.0);
		TestTrue(TEXT("接地している輪は力を出す"),
		         FMath::Abs(Outputs.TireFyN[FR]) > 0.0);
	}

	// --- 4輪とも浮いたら曲がれない ---
	{
		double Airborne[ZN6::WheelCount] = { 0.0, 0.0, 0.0, 0.0 };
		ZN6::FVehicleState State = Vehicle.InitialState(80.0 / 3.6, 3);
		const double BeforeYaw = State.YawRateRads;

		ZN6::FControlInput Turning = Control;
		Turning.GearIndex = 3;
		Turning.Throttle = 1.0;
		Turning.SteerRad = 0.20;

		ZN6::FVehicleOutputs Outputs;
		for (int32 Step = 0; Step < 50; ++Step)
		{
			ZN6::FVehicleState Next;
			Vehicle.Step(State, Turning, 0.002, Next, Outputs,
			             0.0, 0.0, 1.0, Airborne);
			State = Next;
		}

		for (int32 Wheel = 0; Wheel < ZN6::WheelCount; ++Wheel)
		{
			TestEqual(TEXT("空中では前後力ゼロ"), Outputs.TireFxN[Wheel], 0.0);
			TestEqual(TEXT("空中では横力ゼロ"), Outputs.TireFyN[Wheel], 0.0);
		}
		TestTrue(*FString::Printf(TEXT("空中ではヨーが増えない（%.9f）"),
		                          State.YawRateRads - BeforeYaw),
		         FMath::Abs(State.YawRateRads - BeforeYaw) < 1e-9);
	}

	// --- 負の荷重を通さない ---
	{
		double Negative[ZN6::WheelCount] = { -500.0, -500.0, -500.0, -500.0 };
		ZN6::FVehicleState State = Vehicle.InitialState(50.0 / 3.6, 1);
		ZN6::FVehicleState Next;
		ZN6::FVehicleOutputs Outputs;
		Vehicle.Step(State, Control, 0.002, Next, Outputs, 0.0, 0.0, 1.0, Negative);

		for (int32 Wheel = 0; Wheel < ZN6::WheelCount; ++Wheel)
		{
			TestEqual(TEXT("負の荷重は 0 に丸める"), Outputs.TireFzN[Wheel], 0.0);
		}
	}

	return true;
}

#endif  // WITH_DEV_AUTOMATION_TESTS
