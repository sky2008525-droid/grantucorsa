// リプレイとゴーストの検査。
//
// **ここで見るのは「同じ操作を流し直したとき、同じ道を通るか」である。**
//
// 物理は 1 ms の固定刻みで決定的に回っている（`FZN6FixedStepAccumulator`）。
// リプレイはその性質の上に成り立っている。だから、
//
//   **再生がずれたら、それはリプレイの不具合ではなく物理の決定性の
//   不具合である。** 黙って許容の幅を広げないこと（憲法ルール6）。
//
// 許容値をここで緩めるのは、`Docs/SPEC_GT7_GAP.md` §9 が「落とし穴」として
// 名指しした失敗そのものになる。

#include "CoreMinimal.h"
#include "Misc/AutomationTest.h"
#include "Misc/Paths.h"
#include "Engine/World.h"
#include "HAL/FileManager.h"

#include "Game/ZN6Replay.h"
#include "ZN6VehicleActor.h"

#if WITH_DEV_AUTOMATION_TESTS

namespace
{
	FString VehicleJsonPathForReplayTest()
	{
		return FPaths::ConvertRelativePathToFull(FPaths::ProjectDir() / TEXT("../..")) /
		       TEXT("Vehicles/ZN6/vehicle.json");
	}

	AZN6VehicleActor* SpawnForReplayTest(FAutomationTestBase& Test, UWorld*& OutWorld)
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
		if (!Actor->InitialisePhysics(VehicleJsonPathForReplayTest(), Error))
		{
			Test.AddError(FString::Printf(TEXT("物理モデルを初期化できない: %s"), *Error));
			return nullptr;
		}
		Actor->StartFreeRun();
		return Actor;
	}

	void DestroyWorldForReplayTest(UWorld* World)
	{
		if (World != nullptr)
		{
			World->DestroyWorld(false);
		}
	}

	/**
	 * 決まった手順で走らせる。**乱数を使わない。**
	 *
	 * 直線 -> 全開 -> 右へ切る -> 制動、と変化を入れる。定常状態だけを
	 * 流しても、ずれるところがずれない。
	 */
	void DriveScripted(AZN6VehicleActor* Actor, int32 Frames, float FrameDeltaS)
	{
		for (int32 Frame = 0; Frame < Frames; ++Frame)
		{
			const float T = static_cast<float>(Frame) * FrameDeltaS;

			float Throttle = 0.0f;
			float Brake = 0.0f;
			float Steer = 0.0f;
			if (T < 1.5f)       { Throttle = 1.0f; }
			else if (T < 2.5f)  { Throttle = 0.7f; Steer = 0.6f; }
			else if (T < 3.0f)  { Brake = 0.8f; Steer = -0.3f; }
			else                { Throttle = 0.5f; }

			Actor->SetThrottleInputForTest(Throttle);
			Actor->SetBrakeInputForTest(Brake);
			Actor->SetSteerInputForTest(Steer);
			Actor->ApplyDriverInputForTest(FrameDeltaS);
			Actor->AdvancePhysics(static_cast<double>(FrameDeltaS));
		}
	}
}

// ---------------------------------------------------------------------------

IMPLEMENT_SIMPLE_AUTOMATION_TEST(
	FZN6ReplayRoundTrip,
	"ZN6.Replay.保存して読み直しても中身が変わらない",
	EAutomationTestFlags::EditorContext | EAutomationTestFlags::EngineFilter)

bool FZN6ReplayRoundTrip::RunTest(const FString& Parameters)
{
	ZN6::FReplay Original;
	Original.Header.TrackKey = TEXT("physics_test_track");
	Original.Header.VehicleHash = TEXT("abc123");
	Original.Header.SetupHash = TEXT("def456");
	Original.Header.FixedStepS = 0.001;
	Original.Header.GhostStride = 20;
	Original.Header.LapTimeS = 61.234;

	for (int32 Index = 0; Index < 500; ++Index)
	{
		ZN6::FReplayControl C;
		C.Throttle = static_cast<float>(Index) / 500.0f;
		C.Brake = 0.25f;
		C.SteerRad = -0.05f;
		C.Clutch = 1.0f;
		C.Handbrake = 0.0f;
		C.GearIndex = Index % 6;
		Original.Controls.Add(C);
	}
	for (int32 Index = 0; Index < 25; ++Index)
	{
		ZN6::FGhostSample G;
		G.TimeS = Index * 0.02;
		G.XM = Index * 1.5;
		G.YM = -Index * 0.25;
		G.YawRad = Index * 0.01;
		G.SpeedMps = 20.0 + Index;
		Original.Ghost.Add(G);
	}

	const FString Path = FPaths::ProjectSavedDir() / TEXT("ZN6Replays/test_roundtrip.zn6replay");
	FString Error;
	if (!TestTrue(TEXT("保存できる"), Original.Save(Path, Error)))
	{
		AddError(Error);
		return false;
	}

	ZN6::FReplay Loaded;
	if (!TestTrue(TEXT("読み直せる"), Loaded.Load(Path, Error)))
	{
		AddError(Error);
		return false;
	}

	TestEqual(TEXT("操作の数"), Loaded.Controls.Num(), Original.Controls.Num());
	TestEqual(TEXT("軌跡の数"), Loaded.Ghost.Num(), Original.Ghost.Num());
	TestEqual(TEXT("コース名"), Loaded.Header.TrackKey, Original.Header.TrackKey);
	TestEqual(TEXT("ラップタイム"), Loaded.Header.LapTimeS, Original.Header.LapTimeS, 1e-12);

	// **1つでも違えば比較の意味が無い。** 全部見る。
	for (int32 Index = 0; Index < Original.Controls.Num(); ++Index)
	{
		if (Loaded.Controls[Index].Throttle != Original.Controls[Index].Throttle
		    || Loaded.Controls[Index].GearIndex != Original.Controls[Index].GearIndex)
		{
			AddError(FString::Printf(TEXT("%d 番目の操作が違う"), Index));
			break;
		}
	}

	IFileManager::Get().Delete(*Path);
	return true;
}

// ---------------------------------------------------------------------------

IMPLEMENT_SIMPLE_AUTOMATION_TEST(
	FZN6ReplayRejectsBadFiles,
	"ZN6.Replay.壊れたファイルと版違いを読まない",
	EAutomationTestFlags::EditorContext | EAutomationTestFlags::EngineFilter)

bool FZN6ReplayRejectsBadFiles::RunTest(const FString& Parameters)
{
	const FString Dir = FPaths::ProjectSavedDir() / TEXT("ZN6Replays");
	const FString Path = Dir / TEXT("test_broken.zn6replay");

	// でたらめな中身
	TArray<uint8> Junk;
	for (int32 Index = 0; Index < 64; ++Index)
	{
		Junk.Add(static_cast<uint8>(Index));
	}
	IFileManager::Get().MakeDirectory(*Dir, /*Tree=*/true);
	FFileHelper::SaveArrayToFile(Junk, *Path);

	ZN6::FReplay Replay;
	FString Error;
	TestFalse(TEXT("リプレイでないファイルは読まない"), Replay.Load(Path, Error));
	TestTrue(TEXT("理由が付く"), !Error.IsEmpty());

	// 空の記録は保存しない
	ZN6::FReplay Empty;
	TestFalse(TEXT("空の記録は保存しない"), Empty.Save(Path, Error));

	IFileManager::Get().Delete(*Path);
	return true;
}

// ---------------------------------------------------------------------------

IMPLEMENT_SIMPLE_AUTOMATION_TEST(
	FZN6ReplayRefusesDifferentConditions,
	"ZN6.Replay.条件が違う記録を再生しない",
	EAutomationTestFlags::EditorContext | EAutomationTestFlags::EngineFilter)

bool FZN6ReplayRefusesDifferentConditions::RunTest(const FString& Parameters)
{
	UWorld* World = nullptr;
	AZN6VehicleActor* Actor = SpawnForReplayTest(*this, World);
	if (Actor == nullptr)
	{
		DestroyWorldForReplayTest(World);
		return false;
	}

	ZN6::FReplay Replay;
	Replay.Header = Actor->MakeReplayHeader();
	Replay.Header.TrackKey = TEXT("まったく別のコース");
	ZN6::FReplayControl C;
	Replay.Controls.Add(C);

	FString Error;
	TestFalse(TEXT("コースが違えば再生しない"), Actor->StartPlayback(Replay, Error));
	TestTrue(TEXT("理由にコースが出る"), Error.Contains(TEXT("コース")));

	// セッティングが違う場合も同じ
	Replay.Header = Actor->MakeReplayHeader();
	Replay.Header.SetupHash = TEXT("ちがうセッティング");
	TestFalse(TEXT("セッティングが違えば再生しない"), Actor->StartPlayback(Replay, Error));

	// 同じ条件なら通る
	Replay.Header = Actor->MakeReplayHeader();
	TestTrue(TEXT("同じ条件なら再生できる"), Actor->StartPlayback(Replay, Error));

	DestroyWorldForReplayTest(World);
	return true;
}

// ---------------------------------------------------------------------------

IMPLEMENT_SIMPLE_AUTOMATION_TEST(
	FZN6ReplayReproducesTheSamePath,
	"ZN6.Replay.同じ操作を流すと同じ道を通る",
	EAutomationTestFlags::EditorContext | EAutomationTestFlags::EngineFilter)

bool FZN6ReplayReproducesTheSamePath::RunTest(const FString& Parameters)
{
	UWorld* World = nullptr;
	AZN6VehicleActor* Actor = SpawnForReplayTest(*this, World);
	if (Actor == nullptr)
	{
		DestroyWorldForReplayTest(World);
		return false;
	}

	// --- 1. 60 fps で録る ---
	Actor->ResetToStart();
	Actor->StartRecording();
	DriveScripted(Actor, /*Frames=*/240, /*FrameDeltaS=*/1.0f / 60.0f);
	Actor->StopRecording();

	const ZN6::FReplay Recorded = Actor->GetRecordedForTest();
	if (!TestTrue(TEXT("録れている"), Recorded.StepCount() > 1000))
	{
		DestroyWorldForReplayTest(World);
		return false;
	}
	if (!TestTrue(TEXT("軌跡が残っている"), Recorded.Ghost.Num() > 20))
	{
		DestroyWorldForReplayTest(World);
		return false;
	}
	TestTrue(TEXT("実際に走っている（同じ場所に留まっていない）"),
	         FMath::Abs(Recorded.Ghost.Last().XM - Recorded.Ghost[0].XM) > 5.0);

	// --- 2. 違う fps で流し直す ---
	//
	// **これが要点。** フレームは描画の都合で長さが変わるので、
	// フレーム単位で操作を差し戻していると、ここで必ずずれる。
	const float FrameRates[] = { 1.0f / 30.0f, 1.0f / 144.0f, 1.0f / 60.0f };

	for (float FrameDeltaS : FrameRates)
	{
		FString Error;
		if (!TestTrue(TEXT("再生を始められる"), Actor->StartPlayback(Recorded, Error)))
		{
			AddError(Error);
			continue;
		}
		// 再生しながら、通った道をもう一度記録する
		Actor->StartRecording();

		int32 Guard = 0;
		while (Actor->IsPlayingBack() && Guard < 100000)
		{
			Actor->AdvancePhysics(static_cast<double>(FrameDeltaS));
			++Guard;
		}
		Actor->StopRecording();

		const ZN6::FReplay& Again = Actor->GetRecordedForTest();

		// **軌跡は同じ間隔で残るので、番号で突き合わせられる。**
		const int32 Count = FMath::Min(Again.Ghost.Num(), Recorded.Ghost.Num());
		TestTrue(TEXT("同じだけの軌跡が出る"),
		         FMath::Abs(Again.Ghost.Num() - Recorded.Ghost.Num()) <= 1);

		double WorstM = 0.0;
		int32 WorstIndex = 0;
		for (int32 Index = 0; Index < Count; ++Index)
		{
			const double DX = Again.Ghost[Index].XM - Recorded.Ghost[Index].XM;
			const double DY = Again.Ghost[Index].YM - Recorded.Ghost[Index].YM;
			const double Distance = FMath::Sqrt(DX * DX + DY * DY);
			if (Distance > WorstM)
			{
				WorstM = Distance;
				WorstIndex = Index;
			}
		}

		// **1 mm。** これは「だいたい同じ」ではなく「同じ」を意味する幅。
		// ここを緩めたくなったら、緩める前に物理の決定性を疑うこと
		// （`Docs/SPEC_GT7_GAP.md` §9 の「落とし穴」）。
		TestTrue(*FString::Printf(
			         TEXT("%.1f fps で流しても道が変わらない（最大 %.6f m / %d 番目）"),
			         1.0f / FrameDeltaS, WorstM, WorstIndex),
		         WorstM < 0.001);
	}

	DestroyWorldForReplayTest(World);
	return true;
}

// ---------------------------------------------------------------------------

IMPLEMENT_SIMPLE_AUTOMATION_TEST(
	FZN6GhostSampling,
	"ZN6.Replay.ゴーストは記録の範囲の外に立たない",
	EAutomationTestFlags::EditorContext | EAutomationTestFlags::EngineFilter)

bool FZN6GhostSampling::RunTest(const FString& Parameters)
{
	ZN6::FReplay Replay;
	Replay.Header.FixedStepS = 0.001;
	for (int32 Index = 0; Index < 10; ++Index)
	{
		ZN6::FGhostSample G;
		G.TimeS = Index * 0.02;
		G.XM = Index * 2.0;
		G.YM = 0.0;
		G.YawRad = 0.0;
		Replay.Ghost.Add(G);
	}

	ZN6::FGhostSample Out;

	// 中の点は補間される
	TestTrue(TEXT("範囲内は取れる"), Replay.SampleGhost(0.03, Out));
	TestEqual(TEXT("2点の真ん中"), Out.XM, 3.0, 1e-9);

	// **範囲外は取れない。** 端の値で埋めると、記録が尽きたあとも
	// ゴーストが最後の場所に立ち続け「そこで止まった車」に見える。
	TestFalse(TEXT("開始より前は取れない"), Replay.SampleGhost(-0.5, Out));
	TestFalse(TEXT("終わりより後は取れない"), Replay.SampleGhost(1.0, Out));

	// 方位が ±180 度をまたぐところで1回転しない
	ZN6::FReplay Wrap;
	ZN6::FGhostSample A;
	A.TimeS = 0.0;
	A.YawRad = 3.10;
	ZN6::FGhostSample B;
	B.TimeS = 0.02;
	B.YawRad = -3.10;   // 少し先へ進んだだけ（+0.08 rad ほど）
	Wrap.Ghost.Add(A);
	Wrap.Ghost.Add(B);

	TestTrue(TEXT("取れる"), Wrap.SampleGhost(0.01, Out));
	// 素の線形補間なら 0（真後ろ）になる。**そうなっていないこと。**
	TestTrue(*FString::Printf(TEXT("±180 度をまたいで1回転しない（%.3f rad）"), Out.YawRad),
	         FMath::Abs(Out.YawRad) > 3.0);

	return true;
}

#endif  // WITH_DEV_AUTOMATION_TESTS
