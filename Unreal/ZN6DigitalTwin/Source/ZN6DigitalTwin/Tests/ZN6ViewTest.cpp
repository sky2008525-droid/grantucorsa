// 視点の切り替えの検査。
//
// **カメラは描画専用で、物理には一切関与しない**（憲法ルール4）。
// だからここで見るのは「絵が変わるか」ではなく、
//
//   - 有効なカメラが常に**ちょうど1つ**であること
//     （2つ以上だと APawn がどちらを選ぶかは並び順まかせになり、
//      視点の切り替えが効いたり効かなかったりする）
//   - 一周して戻ること
//   - **視点を変えても物理が1ビットも変わらないこと**
//
// の3つである。

#include "CoreMinimal.h"
#include "Misc/AutomationTest.h"
#include "Misc/Paths.h"
#include "Engine/World.h"
#include "Camera/CameraComponent.h"

#include "ZN6VehicleActor.h"

#if WITH_DEV_AUTOMATION_TESTS

namespace
{
	FString VehicleJsonPathForViewTest()
	{
		return FPaths::ConvertRelativePathToFull(FPaths::ProjectDir() / TEXT("../..")) /
		       TEXT("Vehicles/ZN6/vehicle.json");
	}

	AZN6VehicleActor* SpawnForViewTest(FAutomationTestBase& Test, UWorld*& OutWorld)
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
		if (!Actor->InitialisePhysics(VehicleJsonPathForViewTest(), Error))
		{
			Test.AddError(FString::Printf(TEXT("物理モデルを初期化できない: %s"), *Error));
			return nullptr;
		}
		Actor->StartFreeRun();
		return Actor;
	}

	int32 ActiveCameraCount(AZN6VehicleActor* Actor)
	{
		int32 Count = 0;
		TArray<UCameraComponent*> Cameras;
		Actor->GetComponents<UCameraComponent>(Cameras);
		for (const UCameraComponent* Camera : Cameras)
		{
			if (Camera != nullptr && Camera->IsActive())
			{
				++Count;
			}
		}
		return Count;
	}
}

IMPLEMENT_SIMPLE_AUTOMATION_TEST(
	FZN6ViewCycle,
	"ZN6.View.有効なカメラは常にちょうど1つ",
	EAutomationTestFlags::EditorContext | EAutomationTestFlags::EngineFilter)

bool FZN6ViewCycle::RunTest(const FString& Parameters)
{
	UWorld* World = nullptr;
	AZN6VehicleActor* Actor = SpawnForViewTest(*this, World);
	if (Actor == nullptr)
	{
		if (World != nullptr) { World->DestroyWorld(false); }
		return false;
	}

	Actor->SetViewForTest(EZN6View::Chase);
	TestEqual(TEXT("最初は追従"), Actor->GetViewForTest(), EZN6View::Chase);

	const int32 ViewCount = static_cast<int32>(EZN6View::Count);
	TestTrue(TEXT("視点が2つ以上ある"), ViewCount >= 2);

	for (int32 Step = 0; Step < ViewCount; ++Step)
	{
		TestEqual(*FString::Printf(TEXT("%d 番目で有効なカメラは1つ"), Step),
		          ActiveCameraCount(Actor), 1);
		Actor->CycleViewForTest();
	}

	// **一周して戻る。** 端で止まると、最後の視点から抜けられない。
	TestEqual(TEXT("一周して追従へ戻る"), Actor->GetViewForTest(), EZN6View::Chase);

	World->DestroyWorld(false);
	return true;
}

IMPLEMENT_SIMPLE_AUTOMATION_TEST(
	FZN6ViewDoesNotTouchPhysics,
	"ZN6.View.視点を変えても物理が変わらない",
	EAutomationTestFlags::EditorContext | EAutomationTestFlags::EngineFilter)

bool FZN6ViewDoesNotTouchPhysics::RunTest(const FString& Parameters)
{
	UWorld* World = nullptr;
	AZN6VehicleActor* Actor = SpawnForViewTest(*this, World);
	if (Actor == nullptr)
	{
		if (World != nullptr) { World->DestroyWorld(false); }
		return false;
	}

	constexpr float Dt = 1.0f / 60.0f;

	auto Run = [Actor](EZN6View InView)
	{
		Actor->ResetToStart();
		Actor->SetViewForTest(InView);
		Actor->SetThrottleInputForTest(1.0f);
		Actor->SetSteerInputForTest(0.4f);
		for (int32 Frame = 0; Frame < 180; ++Frame)
		{
			Actor->ApplyDriverInputForTest(Dt);
			Actor->AdvancePhysics(static_cast<double>(Dt));
		}
		return Actor->GetPhysicsState();
	};

	const ZN6::FVehicleState Chase = Run(EZN6View::Chase);
	const ZN6::FVehicleState Cockpit = Run(EZN6View::Cockpit);
	const ZN6::FVehicleState Bumper = Run(EZN6View::Bumper);

	// **ビット単位で同じであること。** 「ほぼ同じ」ではない。
	// カメラが物理に触っていれば、必ずどこかの桁が動く。
	TestEqual(TEXT("運転席視点でも同じ x"), Cockpit.XM, Chase.XM, 0.0);
	TestEqual(TEXT("運転席視点でも同じ y"), Cockpit.YM, Chase.YM, 0.0);
	TestEqual(TEXT("運転席視点でも同じ方位"), Cockpit.HeadingRad, Chase.HeadingRad, 0.0);
	TestEqual(TEXT("バンパー視点でも同じ x"), Bumper.XM, Chase.XM, 0.0);
	TestEqual(TEXT("バンパー視点でも同じ y"), Bumper.YM, Chase.YM, 0.0);

	// 走っていなければ比較の意味が無い
	TestTrue(TEXT("実際に走っている"), FMath::Abs(Chase.XM) > 5.0);

	World->DestroyWorld(false);
	return true;
}

#endif  // WITH_DEV_AUTOMATION_TESTS
