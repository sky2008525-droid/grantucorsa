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

#endif // WITH_DEV_AUTOMATION_TESTS
