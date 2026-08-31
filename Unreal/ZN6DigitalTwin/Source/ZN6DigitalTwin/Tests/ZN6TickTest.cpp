// SPEC_ZN6.md §10.3 Phase 8 の判定基準のうち、tick と分離に関するもの。
//
//   - 物理計算と描画が分離されている（描画メッシュが物理に影響しない）
//   - Physics tick が目標周波数で回っている
//   - Performance (FPS, Physics tick) validated
//
// **「分離されている」を目視やコードレビューで確認したことにしない。**
// 描画側を実際に動かして物理が変わらないことを測る。

#include "CoreMinimal.h"
#include "Misc/AutomationTest.h"
#include "Misc/Paths.h"
#include "Components/StaticMeshComponent.h"
#include "Engine/World.h"
#include "HAL/PlatformTime.h"

#include "Physics/ZN6Terrain.h"
#include "Physics/ZN6Units.h"
#include "ZN6VehicleActor.h"

#if WITH_DEV_AUTOMATION_TESTS

namespace
{
	FString VehicleJsonPathForTickTest()
	{
		return FPaths::ConvertRelativePathToFull(FPaths::ProjectDir() / TEXT("../..")) /
		       TEXT("Vehicles/ZN6/vehicle.json");
	}

	/** 実行中のワールドに Actor を1つ作って物理を初期化する。 */
	AZN6VehicleActor* SpawnInitialised(FAutomationTestBase& Test, UWorld*& OutWorld)
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
		if (!Actor->InitialisePhysics(VehicleJsonPathForTickTest(), Error))
		{
			Test.AddError(FString::Printf(TEXT("物理モデルを初期化できない: %s"), *Error));
			return nullptr;
		}

		// **走れる状態にしてから返す。**
		// 既定はメニューで、そこでは操作を受け付けない（カウントダウン前に
		// 走り出さないため）。ここを忘れると「アクセルを踏んでも進まない」
		// テストが落ちる。スタートの門そのものは ZN6.Race で検査する。
		Actor->StartFreeRun();
		return Actor;
	}

	void DestroyWorld(UWorld* World)
	{
		if (World != nullptr)
		{
			World->DestroyWorld(false);
		}
	}

	ZN6::FControlInput MakeCorneringControl()
	{
		ZN6::FControlInput Control;
		Control.GearIndex = 2;      // 3速
		Control.Throttle = 0.30;
		Control.SteerRad = 0.05;
		Control.Clutch = 1.0;
		return Control;
	}
}

// ---------------------------------------------------------------------------
// 物理がフレームレートに依存しないこと
// ---------------------------------------------------------------------------
//
// **これが「分離されている」の実質的な証明。** 描画のフレーム時間で物理を
// 進めていたら、フレームレートを変えた瞬間に結果が変わる。

IMPLEMENT_SIMPLE_AUTOMATION_TEST(
	FZN6PhysicsIsFrameRateIndependent,
	"ZN6.Tick.物理がフレームレートに依存しない",
	EAutomationTestFlags::EditorContext | EAutomationTestFlags::EngineFilter)

bool FZN6PhysicsIsFrameRateIndependent::RunTest(const FString& Parameters)
{
	// 同じ 2.0 秒ぶんを、違うフレームレートで進める。
	// 固定刻みで回っていれば、どれも同じ状態に到達するはず。
	const double TotalTimeS = 2.0;
	const double FrameRates[] = { 30.0, 60.0, 120.0, 144.0 };

	TArray<ZN6::FVehicleState> Results;

	for (const double Fps : FrameRates)
	{
		UWorld* World = nullptr;
		AZN6VehicleActor* Actor = SpawnInitialised(*this, World);
		if (Actor == nullptr)
		{
			DestroyWorld(World);
			return false;
		}

		Actor->SetPhysicsState(Actor->MakeInitialState(60.0 / 3.6, 2));
		Actor->SetControl(MakeCorneringControl());

		const double FrameDeltaS = 1.0 / Fps;
		const int32 FrameCount = static_cast<int32>(TotalTimeS * Fps);
		for (int32 Frame = 0; Frame < FrameCount; ++Frame)
		{
			Actor->AdvancePhysics(FrameDeltaS);
		}

		Results.Add(Actor->GetPhysicsState());

		// 進めた時間が要求どおりか（アキュムレータの取りこぼしが無いか）
		TestTrue(
			*FString::Printf(TEXT("%.0f fps: 進めた時間 %.6f s が要求 %.6f s と一致する"),
			                 Fps, Actor->GetSimulatedTimeS(), TotalTimeS),
			FMath::Abs(Actor->GetSimulatedTimeS() - TotalTimeS) < 0.002);

		TestEqual(
			*FString::Printf(TEXT("%.0f fps: 取りこぼした時間がゼロ"), Fps),
			Actor->GetAccumulator().DroppedS, 0.0);

		DestroyWorld(World);
	}

	// 全てのフレームレートで同じ状態に到達しているか。
	// **固定刻みなので端数の扱いでステップ数が1つ違いうる。** 1ステップぶん
	// （1 ms）の差は許すが、それ以上ずれたらフレームレートに依存している。
	constexpr double ToleranceMps = 0.02;
	constexpr double ToleranceRads = 0.02;

	for (int32 Index = 1; Index < Results.Num(); ++Index)
	{
		const ZN6::FVehicleState& Base = Results[0];
		const ZN6::FVehicleState& Other = Results[Index];

		TestTrue(
			*FString::Printf(TEXT("%.0f fps の vx が 30 fps と一致する（%.6f vs %.6f）"),
			                 FrameRates[Index], Other.VxMps, Base.VxMps),
			FMath::Abs(Other.VxMps - Base.VxMps) < ToleranceMps);

		TestTrue(
			*FString::Printf(TEXT("%.0f fps の vy が 30 fps と一致する（%.6f vs %.6f）"),
			                 FrameRates[Index], Other.VyMps, Base.VyMps),
			FMath::Abs(Other.VyMps - Base.VyMps) < ToleranceMps);

		TestTrue(
			*FString::Printf(TEXT("%.0f fps のヨーレートが 30 fps と一致する（%.6f vs %.6f）"),
			                 FrameRates[Index], Other.YawRateRads, Base.YawRateRads),
			FMath::Abs(Other.YawRateRads - Base.YawRateRads) < ToleranceRads);
	}

	return true;
}

// ---------------------------------------------------------------------------
// 描画メッシュが物理に影響しないこと
// ---------------------------------------------------------------------------
//
// 憲法ルール4「物理計算と表示用3Dモデルを完全に分離する」の検査。
// **描画側を実際に動かして、物理の結果がビット単位で変わらないことを見る。**

IMPLEMENT_SIMPLE_AUTOMATION_TEST(
	FZN6VisualDoesNotAffectPhysics,
	"ZN6.Tick.描画メッシュが物理に影響しない",
	EAutomationTestFlags::EditorContext | EAutomationTestFlags::EngineFilter)

bool FZN6VisualDoesNotAffectPhysics::RunTest(const FString& Parameters)
{
	auto RunFor = [this](bool bDisturbVisual, ZN6::FVehicleState& OutState) -> bool
	{
		UWorld* World = nullptr;
		AZN6VehicleActor* Actor = SpawnInitialised(*this, World);
		if (Actor == nullptr)
		{
			DestroyWorld(World);
			return false;
		}

		Actor->SetPhysicsState(Actor->MakeInitialState(60.0 / 3.6, 2));
		Actor->SetControl(MakeCorneringControl());

		for (int32 Frame = 0; Frame < 120; ++Frame)
		{
			if (bDisturbVisual)
			{
				// **描画側を毎フレーム乱暴に動かす。**
				// Actor 自体の位置・回転・スケールを勝手に書き換える。
				// 物理がここを読み戻していれば、結果が変わるはず。
				Actor->SetActorLocation(FVector(Frame * 137.0, Frame * -91.0, Frame * 13.0));
				Actor->SetActorRotation(FRotator(Frame * 3.0, Frame * 7.0, Frame * 11.0));
				Actor->SetActorScale3D(FVector(1.0 + Frame * 0.01));
			}

			Actor->AdvancePhysics(1.0 / 60.0);
			Actor->SyncVisualToPhysics();
		}

		OutState = Actor->GetPhysicsState();
		DestroyWorld(World);
		return true;
	};

	ZN6::FVehicleState Undisturbed;
	ZN6::FVehicleState Disturbed;

	if (!RunFor(false, Undisturbed) || !RunFor(true, Disturbed))
	{
		return false;
	}

	// **完全一致を要求する。** 「ほぼ同じ」では分離の証明にならない。
	TestEqual(TEXT("vx が完全に一致する"), Disturbed.VxMps, Undisturbed.VxMps);
	TestEqual(TEXT("vy が完全に一致する"), Disturbed.VyMps, Undisturbed.VyMps);
	TestEqual(TEXT("ヨーレートが完全に一致する"), Disturbed.YawRateRads, Undisturbed.YawRateRads);
	TestEqual(TEXT("x が完全に一致する"), Disturbed.XM, Undisturbed.XM);
	TestEqual(TEXT("y が完全に一致する"), Disturbed.YM, Undisturbed.YM);
	TestEqual(TEXT("方位が完全に一致する"), Disturbed.HeadingRad, Undisturbed.HeadingRad);
	TestEqual(TEXT("エンジン回転が完全に一致する"), Disturbed.EngineOmegaRads, Undisturbed.EngineOmegaRads);

	return true;
}

// ---------------------------------------------------------------------------
// Physics tick が目標周波数で回っていること
// ---------------------------------------------------------------------------

IMPLEMENT_SIMPLE_AUTOMATION_TEST(
	FZN6PhysicsTickFrequency,
	"ZN6.Tick.Physics tickが目標周波数で回る",
	EAutomationTestFlags::EditorContext | EAutomationTestFlags::EngineFilter)

bool FZN6PhysicsTickFrequency::RunTest(const FString& Parameters)
{
	UWorld* World = nullptr;
	AZN6VehicleActor* Actor = SpawnInitialised(*this, World);
	if (Actor == nullptr)
	{
		DestroyWorld(World);
		return false;
	}

	const double FixedStepS = static_cast<double>(Actor->GetAccumulator().FixedStepS);

	// **刻みが issue #24 の安定条件を満たしているか。**
	// 2 ms 以上だと低速で車輪が振動する。ここを緩めるときは #24 を読むこと。
	TestTrue(
		*FString::Printf(TEXT("物理の固定刻み %.4f s が 2 ms 未満（issue #24）"), FixedStepS),
		FixedStepS < 0.002);

	Actor->SetPhysicsState(Actor->MakeInitialState(60.0 / 3.6, 2));
	Actor->SetControl(MakeCorneringControl());

	// 60 fps で 1 秒ぶん進める
	const double TotalTimeS = 1.0;
	const int32 FrameCount = 60;
	for (int32 Frame = 0; Frame < FrameCount; ++Frame)
	{
		Actor->AdvancePhysics(1.0 / 60.0);
	}

	const int64 ExpectedSteps = static_cast<int64>(TotalTimeS / FixedStepS);
	const int64 ActualSteps = Actor->GetTotalStepCount();

	TestTrue(
		*FString::Printf(TEXT("1秒で %lld ステップ回る（期待 %lld、= %.0f Hz）"),
		                 ActualSteps, ExpectedSteps, 1.0 / FixedStepS),
		FMath::Abs(ActualSteps - ExpectedSteps) <= 1);

	TestEqual(TEXT("上限に当たって時間を捨てていない"), Actor->GetAccumulator().DroppedS, 0.0);

	// --- 実測: 物理1ステップにかかる時間 ---
	//
	// **これは性能の合否判定ではなく記録。** マシンによって変わる値を
	// 閾値にすると、環境が違うだけで落ちるテストになる。
	// リアルタイムに間に合うかどうかだけを見る。
	const int32 BenchmarkSteps = 20000;
	const double StartTime = FPlatformTime::Seconds();
	for (int32 Step = 0; Step < BenchmarkSteps; ++Step)
	{
		Actor->AdvancePhysics(FixedStepS);
	}
	const double ElapsedS = FPlatformTime::Seconds() - StartTime;

	const double PerStepUs = ElapsedS / BenchmarkSteps * 1e6;
	const double RealtimeRatio = (BenchmarkSteps * FixedStepS) / ElapsedS;

	AddInfo(FString::Printf(
		TEXT("物理 1 ステップ %.3f us / %.0f Hz を回すのに実時間の %.2f%% / リアルタイム比 %.1f 倍"),
		PerStepUs, 1.0 / FixedStepS, 100.0 / RealtimeRatio, RealtimeRatio));

	// リアルタイムに間に合わないなら、そもそも成立しない
	TestTrue(
		*FString::Printf(TEXT("物理がリアルタイムに間に合う（%.1f 倍速）"), RealtimeRatio),
		RealtimeRatio > 1.0);

	DestroyWorld(World);
	return true;
}

// ---------------------------------------------------------------------------
// 重いフレームで死のスパイラルに入らないこと
// ---------------------------------------------------------------------------

IMPLEMENT_SIMPLE_AUTOMATION_TEST(
	FZN6NoSpiralOfDeath,
	"ZN6.Tick.重いフレームで死のスパイラルに入らない",
	EAutomationTestFlags::EditorContext | EAutomationTestFlags::EngineFilter)

bool FZN6NoSpiralOfDeath::RunTest(const FString& Parameters)
{
	UWorld* World = nullptr;
	AZN6VehicleActor* Actor = SpawnInitialised(*this, World);
	if (Actor == nullptr)
	{
		DestroyWorld(World);
		return false;
	}

	Actor->SetPhysicsState(Actor->MakeInitialState(60.0 / 3.6, 2));
	Actor->SetControl(MakeCorneringControl());

	// 5 秒ぶん溜まった状態（ハングやローディング明けを想定）
	Actor->AdvancePhysics(5.0);

	const int32 MaxSteps = Actor->GetAccumulator().MaxStepsPerFrame;
	TestTrue(
		*FString::Printf(TEXT("1フレームのステップ数が上限 %d 以内（実際 %d）"),
		                 MaxSteps, Actor->GetAccumulator().LastStepCount),
		Actor->GetAccumulator().LastStepCount <= MaxSteps);

	// **捨てたことを記録しているか。** 黙って落とすと、シミュレーション時間が
	// 実時間より遅れていることに気づけない。
	TestTrue(
		*FString::Printf(TEXT("捨てた時間を記録している（%.3f s）"),
		                 Actor->GetAccumulator().DroppedS),
		Actor->GetAccumulator().DroppedS > 0.0);

	DestroyWorld(World);
	return true;
}

// ---------------------------------------------------------------------------
// 描画用の車輪が物理と対応していること
// ---------------------------------------------------------------------------
//
// 車輪を分解して描画するようにしたので、**添字の取り違えと符号の誤りを
// 検出する**。左に曲がっているのに右の車輪が切れる、加速しているのに
// 車輪が逆回転する、といった壊れ方はコンパイルを通ってしまう。

IMPLEMENT_SIMPLE_AUTOMATION_TEST(
	FZN6VisualWheelsFollowPhysics,
	"ZN6.Tick.描画の車輪が物理に追従する",
	EAutomationTestFlags::EditorContext | EAutomationTestFlags::EngineFilter)

bool FZN6VisualWheelsFollowPhysics::RunTest(const FString& Parameters)
{
	UWorld* World = nullptr;
	AZN6VehicleActor* Actor = SpawnInitialised(*this, World);
	if (Actor == nullptr)
	{
		DestroyWorld(World);
		return false;
	}

	const FString ManifestPath =
		FPaths::ConvertRelativePathToFull(FPaths::ProjectDir() / TEXT("../..")) /
		TEXT("Vehicles/ZN6/Export/manifest.json");

	FString Error;
	if (!Actor->LoadVisualManifest(ManifestPath, Error))
	{
		AddError(FString::Printf(TEXT("manifest を読めない: %s"), *Error));
		DestroyWorld(World);
		return false;
	}

	// --- 取り付け位置が車として成立しているか ---
	//
	// **具体的な数値をここに書かない。** モデルを差し替えたら変わる。
	// 「前輪が後輪より前」「左右が対称」という**関係**だけを検査する。
	const FVector FL = Actor->GetWheelAttachM(static_cast<int32>(ZN6::EWheel::FL));
	const FVector FR = Actor->GetWheelAttachM(static_cast<int32>(ZN6::EWheel::FR));
	const FVector RL = Actor->GetWheelAttachM(static_cast<int32>(ZN6::EWheel::RL));
	const FVector RR = Actor->GetWheelAttachM(static_cast<int32>(ZN6::EWheel::RR));

	TestTrue(FString::Printf(TEXT("前輪が後輪より前にある（%.3f > %.3f）"), FL.X, RL.X),
	         FL.X > RL.X);
	TestTrue(FString::Printf(TEXT("FL が左（Y>0）にある（%.3f）"), FL.Y), FL.Y > 0.0);
	TestTrue(FString::Printf(TEXT("FR が右（Y<0）にある（%.3f）"), FR.Y), FR.Y < 0.0);
	TestTrue(FString::Printf(TEXT("RL が左（Y>0）にある（%.3f）"), RL.Y), RL.Y > 0.0);
	TestTrue(FString::Printf(TEXT("RR が右（Y<0）にある（%.3f）"), RR.Y), RR.Y < 0.0);
	TestTrue(FString::Printf(TEXT("前輪の左右が対称（%.4f vs %.4f）"), FL.Y, -FR.Y),
	         FMath::Abs(FL.Y + FR.Y) < 0.02);

	// 車輪の中心高さ = 転がり半径。**ここがずれると車が浮く／埋まる。**
	TestTrue(FString::Printf(TEXT("車輪中心が接地半径の高さにある（%.4f m）"), FL.Z),
	         FL.Z > 0.25 && FL.Z < 0.40);

	// --- 前進すると車輪が前転する ---
	Actor->SetPhysicsState(Actor->MakeInitialState(60.0 / 3.6, 2));
	Actor->SetControl(MakeCorneringControl());

	for (int32 Frame = 0; Frame < 30; ++Frame)
	{
		Actor->AdvancePhysics(1.0 / 60.0);
	}

	for (int32 Index = 0; Index < ZN6::WheelCount; ++Index)
	{
		TestTrue(
			*FString::Printf(TEXT("%s の車輪速度が正（%.3f rad/s）"),
			                 ZN6::WheelNames[Index],
			                 Actor->GetPhysicsState().WheelOmegaRads[Index]),
			Actor->GetPhysicsState().WheelOmegaRads[Index] > 0.0);

		// **角度は角速度の積分。** 符号が食い違っていれば逆回転して見える。
		TestTrue(
			*FString::Printf(TEXT("%s の描画角が正（%.3f rad）"),
			                 ZN6::WheelNames[Index],
			                 Actor->GetVisualWheelAngleRad(Index)),
			Actor->GetVisualWheelAngleRad(Index) > 0.0);
	}

	// --- 描画角を進めても物理は変わらない ---
	//
	// **憲法ルール4の検査。** 描画専用の状態が物理へ漏れていないこと。
	const ZN6::FVehicleState Before = Actor->GetPhysicsState();
	Actor->SyncVisualToPhysics();
	const ZN6::FVehicleState After = Actor->GetPhysicsState();

	TestEqual(TEXT("描画同期で vx が変わらない"), After.VxMps, Before.VxMps);
	TestEqual(TEXT("描画同期で vy が変わらない"), After.VyMps, Before.VyMps);
	TestEqual(TEXT("描画同期でヨーレートが変わらない"), After.YawRateRads, Before.YawRateRads);

	DestroyWorld(World);
	return true;
}

// ---------------------------------------------------------------------------
// 運転操作の層
// ---------------------------------------------------------------------------
//
// キーボード入力を物理の入力へ変換する部分。**壊れても走れてしまう**ので
// 気づきにくい: 変速で範囲外のギアが入る、舵角が一瞬で最大になる、
// 最大舵角が実車と無関係な値になる、など。

IMPLEMENT_SIMPLE_AUTOMATION_TEST(
	FZN6DriverInputIsSane,
	"ZN6.Tick.運転操作が物理の入力として妥当",
	EAutomationTestFlags::EditorContext | EAutomationTestFlags::EngineFilter)

bool FZN6DriverInputIsSane::RunTest(const FString& Parameters)
{
	UWorld* World = nullptr;
	AZN6VehicleActor* Actor = SpawnInitialised(*this, World);
	if (Actor == nullptr)
	{
		DestroyWorld(World);
		return false;
	}

	// --- 最大舵角が実車の値から導かれているか ---
	//
	// **具体的な数値を期待値に書かない。** vehicle.json を直せば変わる。
	// 「最小回転半径とホイールベースから導いた値と一致する」ことを見る。
	const double MaxSteerRad = Actor->GetMaxSteerRad();
	const double Expected = FMath::Atan2(2.570, 5.400);   // wheelbase / min_turning_radius
	TestTrue(
		*FString::Printf(TEXT("最大舵角 %.4f rad (%.1f deg) が最小回転半径から導かれている"),
		                 MaxSteerRad, FMath::RadiansToDegrees(MaxSteerRad)),
		FMath::Abs(MaxSteerRad - Expected) < 1e-6);

	// **0 のままなら操舵が効かない。** 読み込み失敗を検出する。
	TestTrue(TEXT("最大舵角がゼロでない"), MaxSteerRad > 0.1);

	// --- 変速が範囲を外れないか ---
	//
	// 範囲外のギアは FDrivetrain::TotalRatio の check で落ちる。
	// **落ちる前に止めること。**
	Actor->SetPhysicsState(Actor->MakeInitialState(60.0 / 3.6, 2));
	for (int32 Count = 0; Count < 20; ++Count)
	{
		Actor->ShiftUpForTest();
	}
	TestEqual(TEXT("上限を超えて変速しない"),
	          Actor->GetControl().GearIndex, ZN6::ForwardGearCount - 1);

	for (int32 Count = 0; Count < 20; ++Count)
	{
		Actor->ShiftDownForTest();
	}
	TestEqual(TEXT("下限を下回って変速しない"), Actor->GetControl().GearIndex, 0);

	// --- 舵角が一瞬で最大にならないか ---
	//
	// キーボードは 0/1 しか出せない。生で渡すと FR では即スピンする。
	Actor->SetPhysicsState(Actor->MakeInitialState(80.0 / 3.6, 3));
	Actor->SetSteerInputForTest(1.0f);

	Actor->ApplyDriverInputForTest(1.0f / 60.0f);
	const double AfterOneFrame = FMath::Abs(Actor->GetControl().SteerRad);
	TestTrue(
		*FString::Printf(TEXT("1フレームで最大舵角へ飛ばない（%.4f rad）"), AfterOneFrame),
		AfterOneFrame < MaxSteerRad * 0.5);

	// 押し続ければいずれ上限に達し、**上限を超えない**
	for (int32 Frame = 0; Frame < 240; ++Frame)
	{
		Actor->ApplyDriverInputForTest(1.0f / 60.0f);
	}
	const double Settled = FMath::Abs(Actor->GetControl().SteerRad);
	TestTrue(
		*FString::Printf(TEXT("押し続けると舵が入る（%.4f rad）"), Settled),
		Settled > 0.02);
	TestTrue(
		*FString::Printf(TEXT("最大舵角を超えない（%.4f <= %.4f）"), Settled, MaxSteerRad),
		Settled <= MaxSteerRad + 1e-9);

	// --- 離すと中立へ戻るか ---
	Actor->SetSteerInputForTest(0.0f);
	for (int32 Frame = 0; Frame < 240; ++Frame)
	{
		Actor->ApplyDriverInputForTest(1.0f / 60.0f);
	}
	TestTrue(
		*FString::Printf(TEXT("離すと中立へ戻る（%.5f rad）"),
		                 Actor->GetControl().SteerRad),
		FMath::Abs(Actor->GetControl().SteerRad) < 1e-6);

	DestroyWorld(World);
	return true;
}

// ---------------------------------------------------------------------------
// アクセルを踏んだら実際に走り出すか
// ---------------------------------------------------------------------------
//
// **「操作が物理の入力に変換される」ことと「車が走る」ことは別。**
// 変換が正しくても、ギアやクラッチの初期値、発進時の数値不安定（issue #24）
// で動かないことがありうる。人がキーを押さないと分からない状態にしない。

IMPLEMENT_SIMPLE_AUTOMATION_TEST(
	FZN6CarActuallyDrives,
	"ZN6.Tick.アクセルを踏むと発進する",
	EAutomationTestFlags::EditorContext | EAutomationTestFlags::EngineFilter)

bool FZN6CarActuallyDrives::RunTest(const FString& Parameters)
{
	UWorld* World = nullptr;
	AZN6VehicleActor* Actor = SpawnInitialised(*this, World);
	if (Actor == nullptr)
	{
		DestroyWorld(World);
		return false;
	}

	// 実際の起動時と同じ状態から始める。**停止・1速・クラッチは繋がったまま。**
	Actor->ResetToStart();

	const double StartX = Actor->GetPhysicsState().XM;
	constexpr float FrameDt = 1.0f / 60.0f;

	// --- 全開で 5 秒 ---
	Actor->SetThrottleInputForTest(1.0f);
	for (int32 Frame = 0; Frame < 300; ++Frame)
	{
		Actor->ApplyDriverInputForTest(FrameDt);
		Actor->AdvancePhysics(FrameDt);
	}

	const ZN6::FVehicleState& Moving = Actor->GetPhysicsState();
	const double Travelled = Moving.XM - StartX;
	const double SpeedKmh = Moving.SpeedMps() * ZN6::KmhPerMps;

	// **しきい値は緩くてよい。** ここで見たいのは「動くか」であって
	// 加速性能ではない（それは ZN6.Physics の 0-100km/h が見ている）。
	TestTrue(
		*FString::Printf(TEXT("5秒の全開で前進する（%.1f m）"), Travelled),
		Travelled > 5.0);
	TestTrue(
		*FString::Printf(TEXT("速度が乗る（%.1f km/h）"), SpeedKmh),
		Moving.SpeedMps() > 5.0);

	// 前進していること。**後退していたらギアか符号が逆。**
	TestTrue(
		*FString::Printf(TEXT("前向きに進む（vx = %.2f m/s）"), Moving.VxMps),
		Moving.VxMps > 0.0);

	AddInfo(FString::Printf(
		TEXT("5秒全開: %.1f m 進み %.1f km/h（%d速、エンジン %.0f rpm）"),
		Travelled, SpeedKmh, Actor->GetControl().GearIndex + 1,
		ZN6::RadsToRpm(Moving.EngineOmegaRads)));

	// --- ブレーキで減速するか ---
	const double BeforeBrakeMps = Moving.SpeedMps();
	Actor->SetThrottleInputForTest(0.0f);
	Actor->SetBrakeInputForTest(1.0f);
	for (int32 Frame = 0; Frame < 120; ++Frame)
	{
		Actor->ApplyDriverInputForTest(FrameDt);
		Actor->AdvancePhysics(FrameDt);
	}

	const double AfterBrakeMps = Actor->GetPhysicsState().SpeedMps();
	TestTrue(
		*FString::Printf(TEXT("ブレーキで減速する（%.1f -> %.1f m/s）"),
		                 BeforeBrakeMps, AfterBrakeMps),
		AfterBrakeMps < BeforeBrakeMps);

	DestroyWorld(World);
	return true;
}

// ---------------------------------------------------------------------------
// 車体姿勢（荷重移動の可視化）
// ---------------------------------------------------------------------------
//
// **これは実車のロール角ではない。** ロール剛性を出すのに要る
// モーションレシオ・減衰・ロールセンタ高さが揃っていないため、角度そのものは
// 検査しない。検査するのは**向きと、物理へ戻っていないこと。**

IMPLEMENT_SIMPLE_AUTOMATION_TEST(
	FZN6BodyAttitudeFollowsLoadTransfer,
	"ZN6.Tick.車体姿勢が荷重移動に追従し物理へ戻らない",
	EAutomationTestFlags::EditorContext | EAutomationTestFlags::EngineFilter)

bool FZN6BodyAttitudeFollowsLoadTransfer::RunTest(const FString& Parameters)
{
	UWorld* World = nullptr;
	AZN6VehicleActor* Actor = SpawnInitialised(*this, World);
	if (Actor == nullptr)
	{
		DestroyWorld(World);
		return false;
	}

	constexpr float FrameDt = 1.0f / 60.0f;

	// --- 左旋回で外側（右）へ傾くか ---
	Actor->SetPhysicsState(Actor->MakeInitialState(80.0 / 3.6, 3));
	Actor->SetControl(MakeCorneringControl());        // steer +0.05 = 左旋回
	for (int32 Frame = 0; Frame < 120; ++Frame)
	{
		Actor->AdvancePhysics(FrameDt);
	}

	const double Ay = Actor->GetPhysicsOutputs().AyMps2;
	const double Roll = Actor->GetVisualRollRad();

	TestTrue(
		*FString::Printf(TEXT("左旋回で横加速度が正（%.2f m/s^2）"), Ay),
		Ay > 0.5);
	// **符号が逆だと内側へ傾く。**
	//
	// UE の正の Roll は右側が下がる。左旋回では外側（右）が下がるので正。
	// **ここは一度間違えた。** 私が「負が外側」と思い込み、その思い込みを
	// そのまま assert に書いたので、内側へ傾いたままテストが通った。
	// 実際に走らせて指摘されるまで気づけなかった。
	TestTrue(
		*FString::Printf(TEXT("左旋回では外側（右＝正のロール）へ傾く（%.3f rad）"), Roll),
		Roll > 0.001);

	// --- 加速でノーズが上がるか ---
	Actor->ResetToStart();
	Actor->SetThrottleInputForTest(1.0f);
	for (int32 Frame = 0; Frame < 180; ++Frame)
	{
		Actor->ApplyDriverInputForTest(FrameDt);
		Actor->AdvancePhysics(FrameDt);
	}
	TestTrue(
		*FString::Printf(TEXT("加速でノーズが上がる（ピッチ %.3f rad、ax %.2f）"),
		                 Actor->GetVisualPitchRad(), Actor->GetPhysicsOutputs().AxMps2),
		Actor->GetVisualPitchRad() > 0.001);

	// --- 姿勢が物理へ戻っていないこと ---
	//
	// **憲法ルール3の検査。** 姿勢の係数は演出値（実車由来ではない）なので、
	// これが荷重移動へ影響すると、検証済みの 0-100km/h や制動距離が
	// 演出値で変わってしまう。
	//
	// 姿勢の設定を極端に変えて同じ走行を再現し、物理がビット単位で
	// 変わらないことを見る。
	auto RunWithFeel = [this](float RollDegPerG, ZN6::FVehicleState& OutState) -> bool
	{
		UWorld* Local = nullptr;
		AZN6VehicleActor* Car = SpawnInitialised(*this, Local);
		if (Car == nullptr)
		{
			DestroyWorld(Local);
			return false;
		}
		FZN6BodyAttitudeFeel Feel;
		Feel.RollDegPerG = RollDegPerG;
		Feel.PitchDegPerG = RollDegPerG;
		Car->SetAttitudeFeelForTest(Feel);

		Car->SetPhysicsState(Car->MakeInitialState(80.0 / 3.6, 3));
		Car->SetControl(MakeCorneringControl());
		for (int32 Frame = 0; Frame < 120; ++Frame)
		{
			Car->AdvancePhysics(1.0 / 60.0);
			Car->SyncVisualToPhysics();
		}
		OutState = Car->GetPhysicsState();
		DestroyWorld(Local);
		return true;
	};

	ZN6::FVehicleState Mild, Wild;
	if (!RunWithFeel(0.0f, Mild) || !RunWithFeel(45.0f, Wild))
	{
		DestroyWorld(World);
		return false;
	}

	TestEqual(TEXT("姿勢の設定を変えても vx が変わらない"), Wild.VxMps, Mild.VxMps);
	TestEqual(TEXT("姿勢の設定を変えても vy が変わらない"), Wild.VyMps, Mild.VyMps);
	TestEqual(TEXT("姿勢の設定を変えてもヨーレートが変わらない"),
	          Wild.YawRateRads, Mild.YawRateRads);

	DestroyWorld(World);
	return true;
}

// ---------------------------------------------------------------------------
// 地形（接地と斜面の重力）
// ---------------------------------------------------------------------------
//
// **符号を推測で書かない。** 下り坂で前へ加速する、上り坂で減速する、
// という向きは、間違えても「それらしく」動いてしまう。

IMPLEMENT_SIMPLE_AUTOMATION_TEST(
	FZN6TerrainAffectsTheCar,
	"ZN6.Tick.地形が車に効く",
	EAutomationTestFlags::EditorContext | EAutomationTestFlags::EngineFilter)

bool FZN6TerrainAffectsTheCar::RunTest(const FString& Parameters)
{
	// --- 斜面の重力（Python 版と同じ式か）---
	{
		double Forward = 0.0;
		double Left = 0.0;
		double Scale = 0.0;

		ZN6::BodyGravity(0.0, 0.0, 0.0, Forward, Left, Scale);
		TestEqual(TEXT("平地では前後の重力成分がゼロ"), Forward, 0.0);
		TestEqual(TEXT("平地では左右の重力成分がゼロ"), Left, 0.0);
		TestEqual(TEXT("平地では法線係数が 1"), Scale, 1.0);

		// dz/dx < 0 は「前方が低い」= 下り坂
		ZN6::BodyGravity(-0.20, 0.0, 0.0, Forward, Left, Scale);
		TestTrue(*FString::Printf(TEXT("下り坂で前へ加速する（%.3f m/s^2）"), Forward),
		         Forward > 0.0);

		double UphillForward = 0.0;
		ZN6::BodyGravity(0.20, 0.0, 0.0, UphillForward, Left, Scale);
		TestTrue(*FString::Printf(TEXT("上り坂で後ろ向きになる（%.3f m/s^2）"), UphillForward),
		         UphillForward < 0.0);

		// **保存則。** 面内成分と法線成分を合成すると g に戻る。
		ZN6::BodyGravity(0.5, 0.3, 0.7, Forward, Left, Scale);
		const double Tangential = FMath::Sqrt(Forward * Forward + Left * Left);
		const double Normal = ZN6::GravityMps2 * Scale;
		TestTrue(
			*FString::Printf(TEXT("成分を合成すると g に戻る（%.9f）"),
			                 FMath::Sqrt(Tangential * Tangential + Normal * Normal)),
			FMath::Abs(FMath::Sqrt(Tangential * Tangential + Normal * Normal)
			           - ZN6::GravityMps2) < 1e-9);
	}

	// --- 高さ場 ---
	const FString HeightfieldPath =
		FPaths::ConvertRelativePathToFull(FPaths::ProjectDir() / TEXT("../..")) /
		TEXT("Tracks/Export/heightfield.json");

	ZN6::FHeightfield Field;
	FString Error;
	if (!Field.LoadFromFile(HeightfieldPath, Error))
	{
		AddError(FString::Printf(TEXT("高さ場を読めない: %s"), *Error));
		return false;
	}

	// **走行域は平ら。** 物理が平面3自由度である以上、行ける場所は平面。
	for (double X = -100.0; X <= 420.0; X += 40.0)
	{
		for (double Y = 0.0; Y <= 110.0; Y += 20.0)
		{
			const double Height = Field.HeightAt(X, Y);
			TestTrue(
				*FString::Printf(TEXT("走行域 (%.0f, %.0f) が平ら（%.4f m）"), X, Y, Height),
				FMath::Abs(Height + 0.05) < 1e-6);
		}
	}

	// 遠景には起伏がある（無ければ「地形に沿う」検査に意味が無い）
	double Lowest = 1e9;
	double Highest = -1e9;
	for (double X = -600.0; X <= 900.0; X += 300.0)
	{
		for (double Y = -350.0; Y <= 500.0; Y += 200.0)
		{
			const double Height = Field.HeightAt(X, Y);
			Lowest = FMath::Min(Lowest, Height);
			Highest = FMath::Max(Highest, Height);
		}
	}
	TestTrue(*FString::Printf(TEXT("遠景に起伏がある（%.2f m）"), Highest - Lowest),
	         Highest - Lowest > 1.0);

	// --- 車体が地面の高さに乗るか ---
	UWorld* World = nullptr;
	AZN6VehicleActor* Actor = SpawnInitialised(*this, World);
	if (Actor == nullptr)
	{
		DestroyWorld(World);
		return false;
	}

	const FString ManifestPath =
		FPaths::ConvertRelativePathToFull(FPaths::ProjectDir() / TEXT("../..")) /
		TEXT("Vehicles/ZN6/Export/manifest.json");
	Actor->LoadVisualManifest(ManifestPath, Error);

	if (!Actor->LoadHeightfield(HeightfieldPath, Error))
	{
		AddError(FString::Printf(TEXT("Actor が高さ場を読めない: %s"), *Error));
		DestroyWorld(World);
		return false;
	}

	// コース上（平ら）
	ZN6::FVehicleState OnTrack = Actor->MakeInitialState(0.0, 0);
	OnTrack.XM = 100.0;
	OnTrack.YM = 20.0;
	Actor->SetPhysicsState(OnTrack);
	Actor->AdvancePhysics(1.0 / 60.0);
	TestTrue(
		*FString::Printf(TEXT("コース上では地面が平ら（%.4f m）"),
		                 Actor->GetGroundHeightM()),
		FMath::Abs(Actor->GetGroundHeightM() + 0.05) < 1e-3);
	TestTrue(TEXT("コース上では地形の傾きがゼロ"),
	         FMath::Abs(Actor->GetTerrainPitchRad()) < 1e-6
	         && FMath::Abs(Actor->GetTerrainRollRad()) < 1e-6);

	// 起伏の上（**ここで高さが変わらなければ、地形が効いていない**）
	ZN6::FVehicleState OffTrack = Actor->MakeInitialState(0.0, 0);
	OffTrack.XM = -500.0;
	OffTrack.YM = -300.0;
	Actor->SetPhysicsState(OffTrack);
	Actor->AdvancePhysics(1.0 / 60.0);

	const double OffHeight = Actor->GetGroundHeightM();
	TestTrue(
		*FString::Printf(TEXT("起伏の上では高さが変わる（%.3f m）"), OffHeight),
		FMath::Abs(OffHeight + 0.05) > 0.2);
	TestTrue(
		*FString::Printf(TEXT("起伏の上では車体が傾く（ピッチ %.4f / ロール %.4f rad）"),
		                 Actor->GetTerrainPitchRad(), Actor->GetTerrainRollRad()),
		FMath::Abs(Actor->GetTerrainPitchRad())
		+ FMath::Abs(Actor->GetTerrainRollRad()) > 1e-4);

	// --- 傾きの「向き」を検査する ------------------------------------------
	//
	// **大きさだけを見ていたせいで、上り坂で機首が下がっていた。**
	// 上の検査は「傾きがゼロでない」しか言っていないので、符号を逆に
	// しても通ってしまう（実際に通っていた）。
	//
	// ロールでも同じ間違いをしている（`AdvanceVisualAttitude` のコメント）。
	// **向きのあるものは、大きさではなく向きを検査すること。**
	{
		// 傾斜のはっきりした場所を高さ場から探す。**座標を書き写さない。**
		// 地形を作り直したときに、テストだけ古い場所を見続けるのを避ける。
		double ProbeX = 0.0;
		double ProbeY = 0.0;
		double ProbeDzDx = 0.0;
		double ProbeDzDy = 0.0;
		bool bFound = false;

		for (double X = -500.0; X <= 800.0 && !bFound; X += 20.0)
		{
			for (double Y = -400.0; Y <= 500.0 && !bFound; Y += 20.0)
			{
				double DzDx = 0.0;
				double DzDy = 0.0;
				Field.SlopeAt(X, Y, DzDx, DzDy);
				// 前後・左右のどちらの符号もはっきりしている場所を選ぶ
				if (FMath::Abs(DzDx) > 0.05 && FMath::Abs(DzDy) > 0.05)
				{
					ProbeX = X;
					ProbeY = Y;
					ProbeDzDx = DzDx;
					ProbeDzDy = DzDy;
					bFound = true;
				}
			}
		}

		if (!bFound)
		{
			AddError(TEXT("傾斜のはっきりした場所が高さ場に無い"));
			DestroyWorld(World);
			return false;
		}

		AddInfo(FString::Printf(TEXT("傾斜の検査地点 (%.0f, %.0f) dz/dx=%.4f dz/dy=%.4f"),
		                        ProbeX, ProbeY, ProbeDzDx, ProbeDzDy));

		auto SampleAt = [&](double HeadingRad)
		{
			ZN6::FVehicleState Slope = Actor->MakeInitialState(0.0, 0);
			Slope.XM = ProbeX;
			Slope.YM = ProbeY;
			Slope.HeadingRad = HeadingRad;
			Actor->SetPhysicsState(Slope);
			Actor->AdvancePhysics(1.0 / 60.0);
		};

		// --- ピッチ ---
		//
		// 向き 0（+X を向く）で dz/dx > 0 なら、前が高い = 上り坂。
		// **上り坂では機首が上がる**（UE の正のピッチ）。
		SampleAt(0.0);
		const double UphillPitch = ProbeDzDx > 0.0
			? Actor->GetTerrainPitchRad() : -Actor->GetTerrainPitchRad();
		TestTrue(
			*FString::Printf(
				TEXT("上り坂で機首が上がる（ピッチ %.5f rad / dz/dx %.4f）"),
				Actor->GetTerrainPitchRad(), ProbeDzDx),
			UphillPitch > 1e-4);

		// 同じ場所で逆を向けば下り坂になる。**符号が反転すること。**
		SampleAt(ZN6::Pi);
		const double DownhillPitch = ProbeDzDx > 0.0
			? Actor->GetTerrainPitchRad() : -Actor->GetTerrainPitchRad();
		TestTrue(
			*FString::Printf(TEXT("向きを反転すると下り坂になる（ピッチ %.5f rad）"),
			                 Actor->GetTerrainPitchRad()),
			DownhillPitch < -1e-4);

		// --- ロール ---
		//
		// 向き 0 なら車の左は +Y。dz/dy > 0 は左の地面が高いということ。
		// **左が高ければ右側が下がる**（UE の正のロールは右下がり）。
		SampleAt(0.0);
		const double LeftHighRoll = ProbeDzDy > 0.0
			? Actor->GetTerrainRollRad() : -Actor->GetTerrainRollRad();
		TestTrue(
			*FString::Printf(
				TEXT("左の地面が高ければ右へ傾く（ロール %.5f rad / dz/dy %.4f）"),
				Actor->GetTerrainRollRad(), ProbeDzDy),
			LeftHighRoll > 1e-4);
	}

	DestroyWorld(World);
	return true;
}

#endif // WITH_DEV_AUTOMATION_TESTS
