// 入力の変換の検査（H パターン / アナログ機器 / 段の並び）。
//
// **実機（DualSense / T300RS / TH8A）は手元に無い。**
// したがってここで確かめられるのは「OS からキーが来たあと、こちら側が
// それをどう物理へ渡すか」だけである。**挿したら動くことの証明ではない**
// （`Docs/INPUT_DEVICES.md` §0）。
//
// それでも、ここが壊れていれば実機を挿しても必ず壊れている:
//
//   - 相対の口（1段上げる）に絶対の情報（今3速）を入れると、取りこぼした
//     ときにシフターの位置とゲーム内の段がずれたまま戻らない
//   - アナログ機器に平滑化を掛けると、**すでに手に入っている踏み込み量を
//     捨てる**（ペダル 0.25 秒遅れ、操舵フルロック 0.40 秒遅れ）
//   - 速度で舵角を絞ると、ハンドルの同じ角度が速度によって違う舵角になる

#include "CoreMinimal.h"
#include "Misc/AutomationTest.h"
#include "Misc/Paths.h"
#include "Engine/World.h"

#include "Physics/ZN6Components.h"
#include "ZN6VehicleActor.h"

#if WITH_DEV_AUTOMATION_TESTS

namespace
{
	FString VehicleJsonPathForInputTest()
	{
		return FPaths::ConvertRelativePathToFull(FPaths::ProjectDir() / TEXT("../..")) /
		       TEXT("Vehicles/ZN6/vehicle.json");
	}

	AZN6VehicleActor* SpawnForInputTest(FAutomationTestBase& Test, UWorld*& OutWorld)
	{
		OutWorld = UWorld::CreateWorld(EWorldType::Game, /*bInformEngineOfWorld=*/false);
		if (OutWorld == nullptr)
		{
			Test.AddError(TEXT("テスト用ワールドを作れない"));
			return nullptr;
		}
		AZN6VehicleActor* Actor = OutWorld->SpawnActor<AZN6VehicleActor>();
		if (Actor == nullptr)
		{
			Test.AddError(TEXT("AZN6VehicleActor を spawn できない"));
			return nullptr;
		}
		FString Error;
		if (!Actor->InitialisePhysics(VehicleJsonPathForInputTest(), Error))
		{
			Test.AddError(FString::Printf(TEXT("物理モデルを初期化できない: %s"), *Error));
			return nullptr;
		}
		// **走れる状態にしてから返す。** 既定はメニューで、そこでは入力の門
		// （Race.InputScale）が 0 になり、何を渡しても 0 になる。
		Actor->StartFreeRun();
		return Actor;
	}

	void DestroyWorldForInputTest(UWorld* World)
	{
		if (World != nullptr)
		{
			World->DestroyWorld(false);
		}
	}
}

// ---------------------------------------------------------------------------

IMPLEMENT_SIMPLE_AUTOMATION_TEST(
	FZN6GearSequenceIncludesNeutralAndReverse,
	"ZN6.Input.シーケンシャルの並びに N と R がある",
	EAutomationTestFlags::EditorContext | EAutomationTestFlags::EngineFilter)

bool FZN6GearSequenceIncludesNeutralAndReverse::RunTest(const FString& Parameters)
{
	UWorld* World = nullptr;
	AZN6VehicleActor* Actor = SpawnForInputTest(*this, World);
	if (Actor == nullptr)
	{
		DestroyWorldForInputTest(World);
		return false;
	}

	// 1速から下げていくと N、さらに下げて R。**そこで止まる。**
	Actor->SelectGearForTest(0);
	Actor->ShiftDownForTest();
	TestEqual(TEXT("1速の下は N"), Actor->GetGearIndexForTest(), ZN6::GearNeutral);
	Actor->ShiftDownForTest();
	TestEqual(TEXT("N の下は R"), Actor->GetGearIndexForTest(), ZN6::GearReverse);
	Actor->ShiftDownForTest();
	TestEqual(TEXT("R より下は無い"), Actor->GetGearIndexForTest(), ZN6::GearReverse);

	// 上げると R -> N -> 1速 -> ... -> 6速 で止まる
	Actor->ShiftUpForTest();
	TestEqual(TEXT("R の上は N"), Actor->GetGearIndexForTest(), ZN6::GearNeutral);
	Actor->ShiftUpForTest();
	TestEqual(TEXT("N の上は 1速"), Actor->GetGearIndexForTest(), 0);
	for (int32 Index = 0; Index < 10; ++Index)
	{
		Actor->ShiftUpForTest();
	}
	TestEqual(TEXT("6速より上は無い"), Actor->GetGearIndexForTest(),
	          ZN6::ForwardGearCount - 1);

	DestroyWorldForInputTest(World);
	return true;
}

// ---------------------------------------------------------------------------

IMPLEMENT_SIMPLE_AUTOMATION_TEST(
	FZN6AbsoluteGearSelection,
	"ZN6.Input.H パターンの絶対指定が入る",
	EAutomationTestFlags::EditorContext | EAutomationTestFlags::EngineFilter)

bool FZN6AbsoluteGearSelection::RunTest(const FString& Parameters)
{
	UWorld* World = nullptr;
	AZN6VehicleActor* Actor = SpawnForInputTest(*this, World);
	if (Actor == nullptr)
	{
		DestroyWorldForInputTest(World);
		return false;
	}

	// **どこから何段目へでも1回で入る。** 相対の口ではこれが作れない。
	Actor->SelectGearForTest(4);
	TestEqual(TEXT("5速へ直接"), Actor->GetGearIndexForTest(), 4);
	Actor->SelectGearForTest(0);
	TestEqual(TEXT("5速から1速へ直接"), Actor->GetGearIndexForTest(), 0);
	Actor->SelectGearForTest(ZN6::GearReverse);
	TestEqual(TEXT("1速から R へ直接"), Actor->GetGearIndexForTest(), ZN6::GearReverse);

	// **知らない段は黙って受け取らない。** 直前の段のまま。
	Actor->SelectGearForTest(6);
	TestEqual(TEXT("7速は無い"), Actor->GetGearIndexForTest(), ZN6::GearReverse);
	Actor->SelectGearForTest(-3);
	TestEqual(TEXT("-3 も無い"), Actor->GetGearIndexForTest(), ZN6::GearReverse);

	DestroyWorldForInputTest(World);
	return true;
}

// ---------------------------------------------------------------------------

IMPLEMENT_SIMPLE_AUTOMATION_TEST(
	FZN6GearAxisDoesNotStealTheGear,
	"ZN6.Input.繋がっていないシフターの軸が段を奪わない",
	EAutomationTestFlags::EditorContext | EAutomationTestFlags::EngineFilter)

bool FZN6GearAxisDoesNotStealTheGear::RunTest(const FString& Parameters)
{
	UWorld* World = nullptr;
	AZN6VehicleActor* Actor = SpawnForInputTest(*this, World);
	if (Actor == nullptr)
	{
		DestroyWorldForInputTest(World);
		return false;
	}

	// **シフターが無いと、この軸は毎フレーム 0 のまま来る。**
	//
	// それを「ニュートラルに入れろ」と読むと、起動するたび段が N へ落ちる。
	// 実際に -ZN6Gear=R で起動して撮った画面が N のままで、車が 1 mm も
	// 動かなかった。**テストは全部通っていた。**
	Actor->SelectGearForTest(ZN6::GearReverse);
	for (int32 Frame = 0; Frame < 120; ++Frame)
	{
		Actor->GearAxisForTest(0.0f);
	}
	TestEqual(TEXT("軸が 0 のままなら段は変わらない"),
	          Actor->GetGearIndexForTest(), ZN6::GearReverse);

	// キーボードで入れた段も奪われない
	Actor->SelectGearForTest(2);
	for (int32 Frame = 0; Frame < 120; ++Frame)
	{
		Actor->GearAxisForTest(0.0f);
	}
	TestEqual(TEXT("3速のまま"), Actor->GetGearIndexForTest(), 2);

	// **実際に動いたら追従する。** 4 -> 前進4速、0 -> N、負 -> R。
	Actor->GearAxisForTest(4.0f);
	TestEqual(TEXT("軸 4 で 4速"), Actor->GetGearIndexForTest(), 3);
	Actor->GearAxisForTest(0.0f);
	TestEqual(TEXT("軸 0 へ動いたら N"), Actor->GetGearIndexForTest(),
	          ZN6::GearNeutral);
	Actor->GearAxisForTest(-1.0f);
	TestEqual(TEXT("軸が負なら R"), Actor->GetGearIndexForTest(), ZN6::GearReverse);

	DestroyWorldForInputTest(World);
	return true;
}

// ---------------------------------------------------------------------------

IMPLEMENT_SIMPLE_AUTOMATION_TEST(
	FZN6DisplayGearLabels,
	"ZN6.Input.画面に出す段が R / N / 1-6 になる",
	EAutomationTestFlags::EditorContext | EAutomationTestFlags::EngineFilter)

bool FZN6DisplayGearLabels::RunTest(const FString& Parameters)
{
	UWorld* World = nullptr;
	AZN6VehicleActor* Actor = SpawnForInputTest(*this, World);
	if (Actor == nullptr)
	{
		DestroyWorldForInputTest(World);
		return false;
	}

	// HUD は「負 = R / 0 = N / 正 = 前進の段数」で受ける
	// （UI/SZN6Hud.cpp の PaintSpeedAndGear）。
	Actor->SelectGearForTest(ZN6::GearReverse);
	TestTrue(TEXT("R は負"), Actor->GetDisplayGearForTest() < 0);
	Actor->SelectGearForTest(ZN6::GearNeutral);
	TestEqual(TEXT("N は 0"), Actor->GetDisplayGearForTest(), 0);
	Actor->SelectGearForTest(2);
	TestEqual(TEXT("3速は 3"), Actor->GetDisplayGearForTest(), 3);

	DestroyWorldForInputTest(World);
	return true;
}

// ---------------------------------------------------------------------------

IMPLEMENT_SIMPLE_AUTOMATION_TEST(
	FZN6AnalogInputKeepsPedalTravel,
	"ZN6.Input.アナログ機器では踏み込み量を捨てない",
	EAutomationTestFlags::EditorContext | EAutomationTestFlags::EngineFilter)

bool FZN6AnalogInputKeepsPedalTravel::RunTest(const FString& Parameters)
{
	UWorld* World = nullptr;
	AZN6VehicleActor* Actor = SpawnForInputTest(*this, World);
	if (Actor == nullptr)
	{
		DestroyWorldForInputTest(World);
		return false;
	}

	constexpr float Dt = 1.0f / 60.0f;

	// --- キーボード（既定）: 1フレームでは目標に届かない ---
	Actor->SetAnalogInputForTest(false);
	Actor->SetThrottleInputForTest(0.40f);
	Actor->ApplyDriverInputForTest(Dt);
	TestTrue(TEXT("キーボードでは時間をかけて寄せる"),
	         Actor->GetControl().Throttle < 0.39);

	// --- アナログ: 踏んだ量がそのまま入る ---
	Actor->SetAnalogInputForTest(true);
	Actor->SetThrottleInputForTest(0.40f);
	Actor->ApplyDriverInputForTest(Dt);
	TestEqual(TEXT("アナログではそのまま"), Actor->GetControl().Throttle, 0.40, 1e-6);

	// **半分踏んだら半分になること。** 0/1 に丸められていないこと。
	Actor->SetThrottleInputForTest(0.13f);
	Actor->ApplyDriverInputForTest(Dt);
	TestEqual(TEXT("13% は 13%"), Actor->GetControl().Throttle, 0.13, 1e-6);

	DestroyWorldForInputTest(World);
	return true;
}

// ---------------------------------------------------------------------------

IMPLEMENT_SIMPLE_AUTOMATION_TEST(
	FZN6AnalogSteerIsNotScaledBySpeed,
	"ZN6.Input.アナログ操舵は速度で絞らない",
	EAutomationTestFlags::EditorContext | EAutomationTestFlags::EngineFilter)

bool FZN6AnalogSteerIsNotScaledBySpeed::RunTest(const FString& Parameters)
{
	UWorld* World = nullptr;
	AZN6VehicleActor* Actor = SpawnForInputTest(*this, World);
	if (Actor == nullptr)
	{
		DestroyWorldForInputTest(World);
		return false;
	}

	constexpr float Dt = 1.0f / 60.0f;

	// **同じ「いっぱいに切った」入力を、止まっているときと 100 km/h で。**
	//
	// ハンドルを持っている人にとって、同じ角度が速度で違う舵角を意味する
	// のは壊れた挙動である。キーボードには「少しだけ切る」が無いので絞りが
	// 要るが、その補助をハンドルにも掛けてはいけない。
	Actor->SetAnalogInputForTest(true);
	Actor->SetSteerInputForTest(1.0f);

	Actor->SetPhysicsState(Actor->MakeInitialState(0.0, 0));
	Actor->ApplyDriverInputForTest(Dt);
	const double SteerAtRest = Actor->GetSteerRadForTest();

	Actor->SetPhysicsState(Actor->MakeInitialState(100.0 / 3.6, 4));
	Actor->ApplyDriverInputForTest(Dt);
	const double SteerAtSpeed = Actor->GetSteerRadForTest();

	TestTrue(TEXT("止まっているときに舵角が出ている"), SteerAtRest > 0.1);
	TestEqual(TEXT("100 km/h でも同じ舵角"), SteerAtSpeed, SteerAtRest, 1e-9);

	// --- キーボードでは絞りが効いたまま ---
	Actor->SetAnalogInputForTest(false);
	Actor->SetPhysicsState(Actor->MakeInitialState(100.0 / 3.6, 4));
	for (int32 Frame = 0; Frame < 200; ++Frame)
	{
		Actor->ApplyDriverInputForTest(Dt);
	}
	TestTrue(TEXT("キーボードでは高速で舵角が絞られる"),
	         Actor->GetSteerRadForTest() < SteerAtRest * 0.9);

	DestroyWorldForInputTest(World);
	return true;
}

#endif  // WITH_DEV_AUTOMATION_TESTS
