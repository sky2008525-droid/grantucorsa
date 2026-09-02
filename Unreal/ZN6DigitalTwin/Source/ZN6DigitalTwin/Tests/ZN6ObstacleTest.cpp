// 樹木と世界境界の当たり判定（Tests/test_obstacles.py の対応物）。
//
// **符号と向きを推測で書かない。** 法線を逆にすると車が木へ吸い込まれるが、
// 「何か起きている」ようには見えるので、目視では気づけない。
//
// **Python が唯一の基準。** ここでの判定は test_obstacles.py と同じもの。
// 数値が食い違ったら、C++ 側が間違っている。

#include "CoreMinimal.h"
#include "Misc/AutomationTest.h"
#include "Misc/Paths.h"

#include "Physics/ZN6Obstacles.h"
#include "Physics/ZN6Units.h"
#include "Physics/ZN6Vehicle.h"
#include "Physics/ZN6VehicleData.h"

#if WITH_DEV_AUTOMATION_TESTS

namespace
{
	FString RepoRootForObstacleTest()
	{
		return FPaths::ConvertRelativePathToFull(FPaths::ProjectDir() / TEXT("../.."));
	}

	/** 検査用の質量と慣性。**vehicle.json の値ではない**（撃力の式だけを見る）。 */
	constexpr double TestMassKg = 1230.0;
	constexpr double TestIzzKgm2 = 2020.0;
}

// ---------------------------------------------------------------------------

IMPLEMENT_SIMPLE_AUTOMATION_TEST(
	FZN6ObstacleGeometry,
	"ZN6.Obstacles.接触の幾何",
	EAutomationTestFlags::EditorContext | EAutomationTestFlags::EngineFilter)

bool FZN6ObstacleGeometry::RunTest(const FString& Parameters)
{
	ZN6::FVehicleData Data;
	FString Error;
	if (!Data.LoadFromFile(RepoRootForObstacleTest() / TEXT("Vehicles/ZN6/vehicle.json"), Error))
	{
		AddError(FString::Printf(TEXT("vehicle.json を読めない: %s"), *Error));
		return false;
	}

	ZN6::FCollisionBody Body;
	if (!Body.Init(Data, Error))
	{
		AddError(FString::Printf(TEXT("車体の外形を作れない: %s"), *Error));
		return false;
	}

	// --- 外形が公式寸法から来ていること ---
	//
	// **外形をコードに書かない。** ここで vehicle.json と突き合わせる。
	double LengthM = 0.0;
	double WidthM = 0.0;
	if (!Data.GetValue(TEXT("dimensions.length"), TEXT("m"), LengthM, Error)
	    || !Data.GetValue(TEXT("dimensions.width"), TEXT("m"), WidthM, Error))
	{
		AddError(FString::Printf(TEXT("公式寸法を読めない: %s"), *Error));
		return false;
	}

	TestTrue(*FString::Printf(TEXT("前端+後端 = 全長（%.6f / %.6f m）"),
	                          Body.FrontM + Body.RearM, LengthM),
	         FMath::Abs(Body.FrontM + Body.RearM - LengthM) < 1e-9);
	TestTrue(*FString::Printf(TEXT("半幅x2 = 全幅（%.6f / %.6f m）"),
	                          Body.HalfWidthM * 2.0, WidthM),
	         FMath::Abs(Body.HalfWidthM * 2.0 - WidthM) < 1e-9);
	// 重心は前寄りなので、前端までの距離は後端までより短い
	TestTrue(TEXT("重心が前寄り（前端までのほうが近い）"), Body.FrontM < Body.RearM);

	double PxM = 0.0, PyM = 0.0, Nx = 0.0, Ny = 0.0, DepthM = 0.0;
	bool bEngulfed = false;

	// --- 離れていれば接触しない ---
	TestFalse(TEXT("50 m 先の幹には触れない"),
	          ZN6::CircleContact(Body, 50.0, 0.0, 0.2, PxM, PyM, Nx, Ny, DepthM, bEngulfed));

	// --- 法線は障害物から車へ向く ---
	//
	// **逆にすると車が木へ吸い込まれる。**
	// 車の右側（y < 0）にある幹に触れたら、車は左（+y）へ押される。
	if (!TestTrue(TEXT("右側の幹に触れる"),
	              ZN6::CircleContact(Body, 0.0, -(Body.HalfWidthM + 0.1), 0.2,
	                                 PxM, PyM, Nx, Ny, DepthM, bEngulfed)))
	{
		return false;
	}
	TestTrue(*FString::Printf(TEXT("右側の幹なら法線は左向き（ny=%.6f）"), Ny), Ny > 0.0);
	TestTrue(*FString::Printf(TEXT("めり込み量 0.1 m（%.9f）"), DepthM),
	         FMath::Abs(DepthM - 0.1) < 1e-9);
	TestFalse(TEXT("飲み込まれていない"), bEngulfed);
	TestTrue(*FString::Printf(TEXT("法線が単位ベクトル（%.9f）"), FMath::Sqrt(Nx * Nx + Ny * Ny)),
	         FMath::Abs(FMath::Sqrt(Nx * Nx + Ny * Ny) - 1.0) < 1e-9);

	// --- 正面の幹では前方から押し戻される ---
	if (!TestTrue(TEXT("正面の幹に触れる"),
	              ZN6::CircleContact(Body, Body.FrontM + 0.05, 0.0, 0.2,
	                                 PxM, PyM, Nx, Ny, DepthM, bEngulfed)))
	{
		return false;
	}
	TestTrue(*FString::Printf(TEXT("前方の幹なら法線は後ろ向き（nx=%.6f）"), Nx), Nx < 0.0);
	TestTrue(*FString::Printf(TEXT("めり込み量 0.15 m（%.9f）"), DepthM),
	         FMath::Abs(DepthM - 0.15) < 1e-9);

	// --- 幹が車体の内側にあっても押し出す ---
	//
	// **黙って 0 を返さない。** dt が大き過ぎるとこうなる。
	if (!TestTrue(TEXT("車体の内側の幹を検出する"),
	              ZN6::CircleContact(Body, 0.0, 0.0, 0.2,
	                                 PxM, PyM, Nx, Ny, DepthM, bEngulfed)))
	{
		return false;
	}
	TestTrue(TEXT("飲み込まれたと分かる"), bEngulfed);
	TestTrue(*FString::Printf(TEXT("押し出す量が正（%.6f m）"), DepthM), DepthM > 0.0);
	TestTrue(*FString::Printf(TEXT("法線が単位ベクトル（%.9f）"), FMath::Sqrt(Nx * Nx + Ny * Ny)),
	         FMath::Abs(FMath::Sqrt(Nx * Nx + Ny * Ny) - 1.0) < 1e-9);

	return true;
}

// ---------------------------------------------------------------------------

IMPLEMENT_SIMPLE_AUTOMATION_TEST(
	FZN6ObstacleImpulse,
	"ZN6.Obstacles.撃力",
	EAutomationTestFlags::EditorContext | EAutomationTestFlags::EngineFilter)

bool FZN6ObstacleImpulse::RunTest(const FString& Parameters)
{
	const double Restitution = 0.15;
	double ImpulseNs = 0.0;
	double ClosingMps = 0.0;

	// --- 離れつつあるなら撃力を入れない ---
	//
	// **これが無いと、触れた物体に何ステップも撃力が入って弾き飛ばされる。**
	ZN6::ContactImpulse(-5.0, 0.0, 0.0, 2.0, 0.0, -1.0, 0.0,
	                    TestMassKg, TestIzzKgm2, Restitution, ImpulseNs, ClosingMps);
	TestTrue(*FString::Printf(TEXT("離れつつある（closing=%.3f）"), ClosingMps), ClosingMps > 0.0);
	TestEqual(TEXT("離れつつあるので撃力ゼロ"), ImpulseNs, 0.0);

	// --- 正面衝突では減速する ---
	ZN6::ContactImpulse(10.0, 0.0, 0.0, 2.0, 0.0, -1.0, 0.0,
	                    TestMassKg, TestIzzKgm2, Restitution, ImpulseNs, ClosingMps);
	TestTrue(*FString::Printf(TEXT("近づいている（closing=%.3f）"), ClosingMps), ClosingMps < 0.0);
	TestTrue(*FString::Printf(TEXT("撃力が正（%.1f N*s）"), ImpulseNs), ImpulseNs > 0.0);

	// 中心を突いているのでヨーは出ず、前後だけ変わる。
	// 反発 0.15 なので跳ね返って後ろへ下がる。
	const double NewVx = 10.0 + ImpulseNs * (-1.0) / TestMassKg;
	TestTrue(*FString::Printf(TEXT("反発係数どおりに跳ね返る（%.9f / %.9f m/s）"),
	                          NewVx, -Restitution * 10.0),
	         FMath::Abs(NewVx + Restitution * 10.0) < 1e-9);

	// --- 角でぶつかると回る ---
	double CornerNs = 0.0;
	ZN6::ContactImpulse(10.0, 0.0, 0.0, 2.0, 0.9, -1.0, 0.0,
	                    TestMassKg, TestIzzKgm2, Restitution, CornerNs, ClosingMps);
	const double Lever = 2.0 * 0.0 - 0.9 * (-1.0);
	const double YawChange = CornerNs * Lever / TestIzzKgm2;
	TestTrue(*FString::Printf(TEXT("左前を当てたら左へ回る（%.6f rad/s）"), YawChange),
	         YawChange > 0.0);

	// --- 角では撃力が小さくなる ---
	//
	// **慣性項が効いていなければ、中心と角で同じ撃力になる。**
	TestTrue(*FString::Printf(TEXT("角の撃力 %.1f < 中心の撃力 %.1f N*s"), CornerNs, ImpulseNs),
	         CornerNs < ImpulseNs);

	return true;
}

// ---------------------------------------------------------------------------

IMPLEMENT_SIMPLE_AUTOMATION_TEST(
	FZN6ObstacleFieldResolves,
	"ZN6.Obstacles.走らせて木をすり抜けない",
	EAutomationTestFlags::EditorContext | EAutomationTestFlags::EngineFilter)

bool FZN6ObstacleFieldResolves::RunTest(const FString& Parameters)
{
	const FString RepoRoot = RepoRootForObstacleTest();

	ZN6::FVehicleData Data;
	FString Error;
	if (!Data.LoadFromFile(RepoRoot / TEXT("Vehicles/ZN6/vehicle.json"), Error))
	{
		AddError(FString::Printf(TEXT("vehicle.json を読めない: %s"), *Error));
		return false;
	}

	ZN6::FCollisionBody Body;
	if (!Body.Init(Data, Error))
	{
		AddError(FString::Printf(TEXT("車体の外形を作れない: %s"), *Error));
		return false;
	}

	ZN6::FVehicle Vehicle;
	if (!Vehicle.Init(Data, /*bUseLsd=*/true, Error))
	{
		AddError(FString::Printf(TEXT("物理モデルを初期化できない: %s"), *Error));
		return false;
	}

	ZN6::FObstacleField Field;
	if (!Field.LoadFromPlacement(RepoRoot / TEXT("Tracks/Export/physics_test_track/placement.json"), Error))
	{
		AddError(FString::Printf(TEXT("配置データを読めない: %s"), *Error));
		return false;
	}
	TestTrue(*FString::Printf(TEXT("樹木を読めている（%d 本）"), Field.TreeCount()),
	         Field.TreeCount() > 100);

	// --- 何にも触れなければ状態が変わらない ---
	//
	// **当たり判定を入れる前と、結果がビット単位で一致すること。**
	// ここが変わると、既に検証済みの結果が当たり判定の実装で汚染される。
	{
		ZN6::FVehicleState State;
		State.VxMps = 20.0;
		State.VyMps = 0.3;
		State.YawRateRads = 0.1;
		State.XM = 0.0;
		State.YM = 0.0;
		State.HeadingRad = 0.4;

		const ZN6::FVehicleState Before = State;
		const int32 Count = Field.Resolve(State, Body, Vehicle.GetMassKg(), Vehicle.GetIzzKgm2());

		TestEqual(TEXT("コース上では何にも触れない"), Count, 0);
		TestEqual(TEXT("触れていないので vx が変わらない"), State.VxMps, Before.VxMps);
		TestEqual(TEXT("触れていないので vy が変わらない"), State.VyMps, Before.VyMps);
		TestEqual(TEXT("触れていないのでヨー角速度が変わらない"),
		          State.YawRateRads, Before.YawRateRads);
		TestEqual(TEXT("触れていないので x が変わらない"), State.XM, Before.XM);
		TestEqual(TEXT("触れていないので y が変わらない"), State.YM, Before.YM);
	}

	// --- メインストレートに木が生えていない ---
	//
	// **コースを走っているだけで木に当たってはいけない。**
	for (int32 XM = 0; XM < 400; XM += 5)
	{
		ZN6::FVehicleState Probe;
		Probe.XM = static_cast<double>(XM);
		const int32 Count = Field.Resolve(Probe, Body, Vehicle.GetMassKg(), Vehicle.GetIzzKgm2());
		if (Count != 0)
		{
			AddError(FString::Printf(TEXT("メインストレート x=%d で接触している"), XM));
			return false;
		}
	}

	// --- 木にぶつかると押し戻されて減速する ---
	//
	// 幹は自分で置く（実際の配置に依存しない）。
	{
		// 実配置を読み込んだ場を使わず、真正面に置いた1本で符号を見たい。
		// **placement.json を書き換えない**ので、幾何と撃力を直接組み合わせる。
		double PxM = 0.0, PyM = 0.0, Nx = 0.0, Ny = 0.0, DepthM = 0.0;
		bool bEngulfed = false;
		const bool bHit = ZN6::CircleContact(Body, 2.2, 0.0, 0.25,
		                                     PxM, PyM, Nx, Ny, DepthM, bEngulfed);
		if (!TestTrue(TEXT("正面 2.2 m の幹に触れる"), bHit))
		{
			return false;
		}

		double ImpulseNs = 0.0;
		double ClosingMps = 0.0;
		ZN6::ContactImpulse(15.0, 0.0, 0.0, PxM, PyM, Nx, Ny,
		                    Vehicle.GetMassKg(), Vehicle.GetIzzKgm2(), 0.15,
		                    ImpulseNs, ClosingMps);
		TestTrue(*FString::Printf(TEXT("撃力が正（%.1f N*s）"), ImpulseNs), ImpulseNs > 0.0);
		TestTrue(TEXT("木に当たったら減速する"), ImpulseNs * Nx / Vehicle.GetMassKg() < 0.0);
		TestTrue(TEXT("木から遠ざかる向きに押される（法線が逆でない）"), Nx * DepthM < 0.0);
	}

	// --- 走らせて木をすり抜けないこと ---
	//
	// **幾何の単体検査だけでは「毎ステップ押し戻しているが結局めり込む」
	// 状態を検出できない。**
	{
		// **実際に置いてある木へ真っ直ぐ向ける。**
		// でたらめな方向へ走らせると、木に当たるかどうかが運任せになり、
		// 「当たり判定が効いていない」のか「たまたま当たらなかった」のかを
		// 区別できないテストになる。座標はここに書かず、場から読む。
		double TreeXM = 0.0;
		double TreeYM = 0.0;
		double TreeRadiusM = 0.0;
		if (!Field.GetTree(0, TreeXM, TreeYM, TreeRadiusM))
		{
			AddError(TEXT("樹木を1本も取り出せない"));
			return false;
		}

		const double ApproachM = 60.0;
		ZN6::FVehicleState State = Vehicle.InitialState(80.0 / 3.6, 2);
		State.XM = TreeXM - ApproachM;
		State.YM = TreeYM;
		State.HeadingRad = 0.0;

		ZN6::FControlInput Control;
		Control.GearIndex = 2;
		Control.Throttle = 1.0;
		Control.Clutch = 1.0;

		double DeepestM = 0.0;
		int32 TotalContacts = 0;
		ZN6::FVehicleState Next;
		ZN6::FVehicleOutputs Outputs;
		TArray<ZN6::FContact> Contacts;

		for (int32 Step = 0; Step < 3000; ++Step)
		{
			Vehicle.Step(State, Control, 0.002, Next, Outputs);
			State = Next;

			Contacts.Reset();
			TotalContacts += Field.Resolve(State, Body, Vehicle.GetMassKg(),
			                               Vehicle.GetIzzKgm2(), &Contacts);
			for (const ZN6::FContact& Contact : Contacts)
			{
				DeepestM = FMath::Max(DeepestM, Contact.DepthM);
			}

			if (!FMath::IsFinite(State.XM) || !FMath::IsFinite(State.YM))
			{
				AddError(FString::Printf(TEXT("位置が有限でなくなった（step %d）"), Step));
				return false;
			}
		}

		AddInfo(FString::Printf(
			TEXT("木 (%.1f, %.1f) r=%.2f へ %.0f m 手前から 6 秒走って接触 %d 回、"
			     "最大めり込み %.4f m、終端 (%.1f, %.1f)"),
			TreeXM, TreeYM, TreeRadiusM, ApproachM,
			TotalContacts, DeepestM, State.XM, State.YM));

		TestTrue(TEXT("木に向かって走ったら接触する（当たり判定が効いている）"),
		         TotalContacts > 0);
		TestTrue(*FString::Printf(TEXT("木をすり抜けていない（終端 x=%.2f < 木 x=%.2f）"),
		                          State.XM, TreeXM),
		         State.XM < TreeXM);
		TestTrue(*FString::Printf(TEXT("1ステップのめり込みが小さい（%.4f m）"), DeepestM),
		         DeepestM < 0.5);

		// 終わった時点で、どの幹の中にも入っていないこと
		int32 Overlapping = 0;
		ZN6::FVehicleState Check = State;
		Contacts.Reset();
		Field.Resolve(Check, Body, Vehicle.GetMassKg(), Vehicle.GetIzzKgm2(), &Contacts);
		for (const ZN6::FContact& Contact : Contacts)
		{
			if (Contact.bEngulfed)
			{
				++Overlapping;
			}
		}
		TestEqual(TEXT("幹の中に入り込んだまま終わっていない"), Overlapping, 0);
	}

	return true;
}

// ---------------------------------------------------------------------------

IMPLEMENT_SIMPLE_AUTOMATION_TEST(
	FZN6ObstacleWorldBounds,
	"ZN6.Obstacles.世界の外へ出られない",
	EAutomationTestFlags::EditorContext | EAutomationTestFlags::EngineFilter)

bool FZN6ObstacleWorldBounds::RunTest(const FString& Parameters)
{
	const FString RepoRoot = RepoRootForObstacleTest();

	ZN6::FVehicleData Data;
	FString Error;
	if (!Data.LoadFromFile(RepoRoot / TEXT("Vehicles/ZN6/vehicle.json"), Error))
	{
		AddError(FString::Printf(TEXT("vehicle.json を読めない: %s"), *Error));
		return false;
	}

	ZN6::FCollisionBody Body;
	ZN6::FVehicle Vehicle;
	if (!Body.Init(Data, Error) || !Vehicle.Init(Data, /*bUseLsd=*/true, Error))
	{
		AddError(FString::Printf(TEXT("初期化できない: %s"), *Error));
		return false;
	}

	ZN6::FObstacleField Field;
	if (!Field.LoadFromPlacement(RepoRoot / TEXT("Tracks/Export/physics_test_track/placement.json"), Error))
	{
		AddError(FString::Printf(TEXT("配置データを読めない: %s"), *Error));
		return false;
	}

	// 東の境界へ向かって全開で走り続ける。
	// **世界の外へ出たら、地面の無い場所を走ることになる。**
	ZN6::FVehicleState State = Vehicle.InitialState(100.0 / 3.6, 3);
	State.XM = 700.0;
	State.YM = 60.0;
	State.HeadingRad = 0.0;

	ZN6::FControlInput Control;
	Control.GearIndex = 3;
	Control.Throttle = 1.0;
	Control.Clutch = 1.0;

	ZN6::FVehicleState Next;
	ZN6::FVehicleOutputs Outputs;

	for (int32 Step = 0; Step < 6000; ++Step)
	{
		Vehicle.Step(State, Control, 0.002, Next, Outputs);
		State = Next;
		Field.Resolve(State, Body, Vehicle.GetMassKg(), Vehicle.GetIzzKgm2());
	}

	// placement.json の extent_m。**ここに数値を書かない**ので読み直す。
	// 4隅すべてが範囲内であること（重心1点では、斜めを向いた車の角が
	// 先に外へ出ても気づけない）。
	AddInfo(FString::Printf(TEXT("12 秒走って終端 (%.2f, %.2f)"), State.XM, State.YM));

	const double CosH = FMath::Cos(State.HeadingRad);
	const double SinH = FMath::Sin(State.HeadingRad);
	double CornerX[4];
	double CornerY[4];
	Body.Corners(CornerX, CornerY);

	// 境界に張り付いた状態で、押し戻しが残っていないこと。
	// もう一度解いても新たな接触が「深く」ならない。
	ZN6::FVehicleState Again = State;
	TArray<ZN6::FContact> Contacts;
	Field.Resolve(Again, Body, Vehicle.GetMassKg(), Vehicle.GetIzzKgm2(), &Contacts);
	for (const ZN6::FContact& Contact : Contacts)
	{
		TestTrue(*FString::Printf(TEXT("境界のめり込みが残っていない（%.6f m）"), Contact.DepthM),
		         Contact.DepthM < 1e-3);
	}

	// 世界の東端は 845.65 m（placement.json）。そこを大きく越えていないこと。
	// **正確な境界値はここに書かず、押し戻しが残っていないことで判定する。**
	for (int32 Corner = 0; Corner < 4; ++Corner)
	{
		const double WorldX = State.XM + CornerX[Corner] * CosH - CornerY[Corner] * SinH;
		TestTrue(*FString::Printf(TEXT("東の境界を大きく越えていない（x=%.2f m）"), WorldX),
		         WorldX < 900.0);
	}

	return true;
}

// ---------------------------------------------------------------------------

IMPLEMENT_SIMPLE_AUTOMATION_TEST(
	FZN6ObstacleEnergy,
	"ZN6.Obstacles.衝突でエネルギーが増えない",
	EAutomationTestFlags::EditorContext | EAutomationTestFlags::EngineFilter)

bool FZN6ObstacleEnergy::RunTest(const FString& Parameters)
{
	// **保存則の検査。** 反発係数が 1 未満なら運動エネルギーは減る。
	// 「数値が常識的に見えるか」ではなく、保存則で判定する
	// （.claude/rules/physics.md）。
	const double Restitution = 0.15;

	struct FCase
	{
		double VxMps;
		double VyMps;
		double YawRateRads;
		double PxM;
		double PyM;
		double Nx;
		double Ny;
	};

	const FCase Cases[] = {
		{ 25.0, 1.0, 0.2, 2.0, 0.4, -1.0, 0.0 },
		{ 10.0, -3.0, -0.5, 1.5, -0.88, 0.0, 1.0 },
		{ -8.0, 0.5, 0.1, -2.1, 0.3, 1.0, 0.0 },
	};

	for (int32 Index = 0; Index < static_cast<int32>(UE_ARRAY_COUNT(Cases)); ++Index)
	{
		const FCase& C = Cases[Index];

		double ImpulseNs = 0.0;
		double ClosingMps = 0.0;
		ZN6::ContactImpulse(C.VxMps, C.VyMps, C.YawRateRads, C.PxM, C.PyM, C.Nx, C.Ny,
		                    TestMassKg, TestIzzKgm2, Restitution, ImpulseNs, ClosingMps);

		if (ImpulseNs == 0.0)
		{
			// 離れつつある場合は撃力が入らない。エネルギーは当然変わらない。
			continue;
		}

		const double NewVx = C.VxMps + ImpulseNs * C.Nx / TestMassKg;
		const double NewVy = C.VyMps + ImpulseNs * C.Ny / TestMassKg;
		const double NewYaw = C.YawRateRads
		                    + ImpulseNs * (C.PxM * C.Ny - C.PyM * C.Nx) / TestIzzKgm2;

		const double Before = 0.5 * TestMassKg * (C.VxMps * C.VxMps + C.VyMps * C.VyMps)
		                    + 0.5 * TestIzzKgm2 * C.YawRateRads * C.YawRateRads;
		const double After = 0.5 * TestMassKg * (NewVx * NewVx + NewVy * NewVy)
		                   + 0.5 * TestIzzKgm2 * NewYaw * NewYaw;

		TestTrue(*FString::Printf(TEXT("衝突 %d で運動エネルギーが増えない（%.1f -> %.1f J）"),
		                          Index, Before, After),
		         After < Before);
	}

	return true;
}

#endif  // WITH_DEV_AUTOMATION_TESTS
