// セッション進行の検査（スタート・カウントダウン・周回計測）。
//
// **ここは物理ではない**ので、実測値との比較はしない。
// 代わりに「時計と状態遷移が壊れていないか」を見る:
//
//   1. カウントダウン中は操作を受け付けないか
//   2. **一時停止で時計が止まるか**（止まらないとタイムが伸びる）
//   3. ゴール線の近くで止まっていても周回が増えないか
//   4. 区間タイムの合計がラップタイムに一致するか
//   5. コース外に出た周がベストにならないか

#include "CoreMinimal.h"
#include "Misc/AutomationTest.h"
#include "Misc/Paths.h"

#include "Game/ZN6RaceDirector.h"
#include "Physics/ZN6Track.h"

#if WITH_DEV_AUTOMATION_TESTS

namespace
{
	FString RaceRepoRoot()
	{
		return FPaths::ConvertRelativePathToFull(FPaths::ProjectDir() / TEXT("../.."));
	}

	bool LoadTrack(FAutomationTestBase& Test, ZN6::FTrackEdge& OutTrack)
	{
		FString Error;
		if (!OutTrack.LoadFromFile(
				RaceRepoRoot() / TEXT("Tracks/physics_test_track.json"), Error))
		{
			Test.AddError(FString::Printf(TEXT("コース定義を読めない: %s"), *Error));
			return false;
		}
		return true;
	}

	/** 中心線を s の順にたどって、その位置を進行役へ渡す。 */
	void DriveAlong(ZN6::FRaceDirector& Race, const ZN6::FTrackEdge& Track,
	                double FromSM, double ToSM, double DtS, double StepM)
	{
		const int32 Count = Track.CentrelineCount();
		const double LengthM = Track.LengthM();
		for (double SM = FromSM; SM < ToSM; SM += StepM)
		{
			const double Wrapped = FMath::Fmod(SM, LengthM);
			const int32 Index = FMath::Clamp(
				FMath::RoundToInt(static_cast<float>(Wrapped / LengthM * Count)),
				0, Count - 1);
			double XM = 0.0;
			double YM = 0.0;
			Track.CentrelinePoint(Index, XM, YM);
			Race.Advance(DtS, XM, YM, StepM / DtS);
		}
	}
}

// ---------------------------------------------------------------------------

IMPLEMENT_SIMPLE_AUTOMATION_TEST(
	FZN6RaceStartGate,
	"ZN6.Race.カウントダウン中は操作を受け付けない",
	EAutomationTestFlags::EditorContext | EAutomationTestFlags::EngineFilter)

bool FZN6RaceStartGate::RunTest(const FString& Parameters)
{
	ZN6::FTrackEdge Track;
	if (!LoadTrack(*this, Track))
	{
		return false;
	}

	ZN6::FRaceRules Rules;
	Rules.CountdownSeconds = 3;
	Rules.TotalLaps = 2;

	ZN6::FRaceDirector Race;
	Race.Init(&Track, Rules);

	// --- メニューでは走れない ---
	TestTrue(TEXT("既定はメニュー"), Race.Phase() == ZN6::ERacePhase::Menu);
	TestEqual(TEXT("メニューでは操作を受け付けない"), Race.InputScale(), 0.0);
	TestEqual(TEXT("メニューではカウントダウンの数字を出さない"),
	          Race.CountdownNumber(), -1);

	// --- カウントダウン ---
	TestTrue(TEXT("メニューから開始できる"), Race.StartCountdown());
	TestFalse(TEXT("二重に開始できない"), Race.StartCountdown());
	TestTrue(TEXT("カウントダウンに入る"), Race.Phase() == ZN6::ERacePhase::Countdown);
	TestEqual(TEXT("最初は 3"), Race.CountdownNumber(), 3);
	TestEqual(TEXT("カウントダウン中は操作を受け付けない"), Race.InputScale(), 0.0);

	// 3・2・1 と減っていくこと
	double XM = 0.0;
	double YM = 0.0;
	Track.CentrelinePoint(0, XM, YM);

	int32 SeenThree = 0;
	int32 SeenTwo = 0;
	int32 SeenOne = 0;
	for (int32 Step = 0; Step < 1000; ++Step)          // 2 秒ぶん
	{
		Race.Advance(0.002, XM, YM, 0.0);
		switch (Race.CountdownNumber())
		{
		case 3: ++SeenThree; break;
		case 2: ++SeenTwo; break;
		case 1: ++SeenOne; break;
		default: break;
		}
	}
	TestTrue(TEXT("3 が出る"), SeenThree > 0);
	TestTrue(TEXT("2 が出る"), SeenTwo > 0);
	TestTrue(TEXT("1 が出る"), SeenOne > 0);
	TestTrue(TEXT("2 秒では終わらない"), Race.Phase() == ZN6::ERacePhase::Countdown);

	// 残りを消化するとスタート。
	// **切り替わった直後に測る。** そのまま走らせてから測ると、走った
	// ぶんの時間が入って「カウントダウンぶんが入っている」のと区別が
	// つかない（最初それで 0.998 s を見て落ちた）。
	int32 StepsToStart = 0;
	while (Race.Phase() == ZN6::ERacePhase::Countdown && StepsToStart < 5000)
	{
		Race.Advance(0.002, XM, YM, 0.0);
		++StepsToStart;
	}
	TestTrue(TEXT("3 秒で走行に入る"), Race.Phase() == ZN6::ERacePhase::Racing);
	TestEqual(TEXT("走行中は操作を受け付ける"), Race.InputScale(), 1.0);

	// **スタートの瞬間から計測する。** カウントダウンぶんを含めない。
	TestTrue(*FString::Printf(TEXT("計測はスタートから（%.4f s）"), Race.SessionTimeS()),
	         Race.SessionTimeS() < 1e-9);

	// --- フリー走行は待たされない ---
	ZN6::FRaceDirector Free;
	Free.Init(&Track, Rules);
	TestTrue(TEXT("フリー走行を開始できる"), Free.StartFreeRun());
	TestTrue(TEXT("すぐ走行に入る"), Free.Phase() == ZN6::ERacePhase::Racing);
	TestEqual(TEXT("すぐ操作できる"), Free.InputScale(), 1.0);

	return true;
}

// ---------------------------------------------------------------------------

IMPLEMENT_SIMPLE_AUTOMATION_TEST(
	FZN6RacePause,
	"ZN6.Race.一時停止で時計が止まる",
	EAutomationTestFlags::EditorContext | EAutomationTestFlags::EngineFilter)

bool FZN6RacePause::RunTest(const FString& Parameters)
{
	ZN6::FTrackEdge Track;
	if (!LoadTrack(*this, Track))
	{
		return false;
	}

	ZN6::FRaceDirector Race;
	Race.Init(&Track, ZN6::FRaceRules());
	Race.StartFreeRun();

	double XM = 0.0;
	double YM = 0.0;
	Track.CentrelinePoint(0, XM, YM);

	for (int32 Step = 0; Step < 500; ++Step)
	{
		Race.Advance(0.002, XM, YM, 0.0);
	}
	const double Before = Race.SessionTimeS();
	TestTrue(*FString::Printf(TEXT("1 秒進む（%.4f s）"), Before),
	         FMath::Abs(Before - 1.0) < 1e-6);

	// --- 止める ---
	TestTrue(TEXT("走行中は一時停止できる"), Race.Pause());
	TestFalse(TEXT("二重に停止できない"), Race.Pause());
	TestEqual(TEXT("停止中は操作を受け付けない"), Race.InputScale(), 0.0);

	for (int32 Step = 0; Step < 500; ++Step)
	{
		Race.Advance(0.002, XM, YM, 0.0);
	}
	TestEqual(TEXT("停止中は時計が止まる"), Race.SessionTimeS(), Before);

	// --- 再開 ---
	TestTrue(TEXT("再開できる"), Race.Resume());
	TestFalse(TEXT("二重に再開できない"), Race.Resume());
	for (int32 Step = 0; Step < 500; ++Step)
	{
		Race.Advance(0.002, XM, YM, 0.0);
	}
	TestTrue(*FString::Printf(TEXT("再開すると進む（%.4f s）"), Race.SessionTimeS()),
	         FMath::Abs(Race.SessionTimeS() - 2.0) < 1e-6);

	return true;
}

// ---------------------------------------------------------------------------

IMPLEMENT_SIMPLE_AUTOMATION_TEST(
	FZN6RaceLapCounting,
	"ZN6.Race.周回と区間の計測",
	EAutomationTestFlags::EditorContext | EAutomationTestFlags::EngineFilter)

bool FZN6RaceLapCounting::RunTest(const FString& Parameters)
{
	ZN6::FTrackEdge Track;
	if (!LoadTrack(*this, Track))
	{
		return false;
	}

	ZN6::FRaceRules Rules;
	Rules.TotalLaps = 2;

	ZN6::FRaceDirector Race;
	Race.Init(&Track, Rules);
	Race.StartCountdown();

	// カウントダウンを消化する
	double StartX = 0.0;
	double StartY = 0.0;
	Track.CentrelinePoint(0, StartX, StartY);
	for (int32 Step = 0; Step < 2000; ++Step)
	{
		Race.Advance(0.002, StartX, StartY, 0.0);
	}
	if (!TestTrue(TEXT("走行に入っている"), Race.Phase() == ZN6::ERacePhase::Racing))
	{
		return false;
	}

	// --- ゴール線の近くで止まっていても周回は増えない ---
	//
	// **位置で判定すると、ここで何周も加算される。**
	for (int32 Step = 0; Step < 2000; ++Step)
	{
		Race.Advance(0.002, StartX, StartY, 0.0);
	}
	TestEqual(TEXT("止まっていても周回が増えない"), Race.LapsDone(), 0);

	// --- 1周する ---
	const double LengthM = Track.LengthM();
	DriveAlong(Race, Track, 0.0, LengthM * 2.0, 0.01, 1.0);

	TestTrue(*FString::Printf(TEXT("周回が記録される（%d 周）"), Race.LapsDone()),
	         Race.LapsDone() >= 1);

	if (Race.Laps().Num() > 0)
	{
		const ZN6::FLapRecord& First = Race.Laps()[0];
		TestEqual(TEXT("最初の周は 1 周目"), First.LapNumber, 1);
		TestTrue(*FString::Printf(TEXT("ラップタイムが正（%.3f s）"), First.TimeS),
		         First.TimeS > 0.0);

		// **区間の合計がラップタイムに一致すること。**
		// ずれていたら、区間の閉じ方が間違っている。
		double SectorSum = 0.0;
		for (int32 Index = 0; Index < ZN6::FRaceRules::SectorCount; ++Index)
		{
			TestTrue(*FString::Printf(TEXT("区間 %d が正（%.3f s）"),
			                          Index, First.SectorS[Index]),
			         First.SectorS[Index] > 0.0);
			SectorSum += First.SectorS[Index];
		}
		TestTrue(
			*FString::Printf(TEXT("区間の合計がラップタイムに一致（%.4f / %.4f s）"),
			                 SectorSum, First.TimeS),
			FMath::Abs(SectorSum - First.TimeS) < 0.05);

		TestTrue(TEXT("最初の周は自己ベスト"), First.bBest);
		TestTrue(*FString::Printf(TEXT("ベストが記録される（%.3f s）"), Race.BestLapS()),
		         Race.BestLapS() > 0.0);
	}

	// --- 規定周回で終わる ---
	DriveAlong(Race, Track, 0.0, LengthM * 3.0, 0.01, 1.0);
	TestTrue(TEXT("規定周回で終わる"), Race.Phase() == ZN6::ERacePhase::Finished);
	TestEqual(TEXT("終わったら操作を受け付けない"), Race.InputScale(), 0.0);

	// 終わった後は時計が止まる
	const double FinalTime = Race.SessionTimeS();
	for (int32 Step = 0; Step < 500; ++Step)
	{
		Race.Advance(0.002, StartX, StartY, 0.0);
	}
	TestEqual(TEXT("終了後は時計が止まる"), Race.SessionTimeS(), FinalTime);

	return true;
}

// ---------------------------------------------------------------------------

IMPLEMENT_SIMPLE_AUTOMATION_TEST(
	FZN6RaceOffTrack,
	"ZN6.Race.コース外に出た周はベストにしない",
	EAutomationTestFlags::EditorContext | EAutomationTestFlags::EngineFilter)

bool FZN6RaceOffTrack::RunTest(const FString& Parameters)
{
	ZN6::FTrackEdge Track;
	if (!LoadTrack(*this, Track))
	{
		return false;
	}

	ZN6::FRaceDirector Race;
	Race.Init(&Track, ZN6::FRaceRules());
	Race.StartFreeRun();

	double XM = 0.0;
	double YM = 0.0;
	Track.CentrelinePoint(0, XM, YM);

	// 中心線の上ならコース内
	Race.Advance(0.002, XM, YM, 0.0);
	TestFalse(TEXT("中心線の上はコース内"), Race.IsOffTrack());
	TestFalse(TEXT("まだ無効になっていない"), Race.CurrentLapInvalidated());

	// **大きく外れたらコース外。**
	Race.Advance(0.002, XM, YM - 200.0, 0.0);
	TestTrue(TEXT("200 m 横はコース外"), Race.IsOffTrack());
	TestTrue(TEXT("その周は参考記録になる"), Race.CurrentLapInvalidated());

	// 戻ってもその周の印は残る（**戻れば無かったことになる、はおかしい**）
	Race.Advance(0.002, XM, YM, 0.0);
	TestFalse(TEXT("戻ればコース内"), Race.IsOffTrack());
	TestTrue(TEXT("その周の印は残る"), Race.CurrentLapInvalidated());

	// --- 横ずれの符号 ---
	//
	// **符号が逆だと、ミニマップで左右が入れ替わる。**
	double SM = 0.0;
	double LateralM = 0.0;
	Track.NearestPoint(0.0, 3.0, SM, LateralM);
	TestTrue(*FString::Printf(TEXT("中心線の左は正（%.3f m）"), LateralM),
	         LateralM > 0.0);
	Track.NearestPoint(0.0, -3.0, SM, LateralM);
	TestTrue(*FString::Printf(TEXT("中心線の右は負（%.3f m）"), LateralM),
	         LateralM < 0.0);

	return true;
}

#endif  // WITH_DEV_AUTOMATION_TESTS
