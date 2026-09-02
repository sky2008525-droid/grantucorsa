// 画面の検査。
//
// **見た目の良し悪しは判定しない。** 視覚層のオラクルは見る人の主観で
// あって、テストが決めるものではない（Docs/AGENT_TOPOLOGY.md §4）。
//
// ここで見るのは、**壊れていないこと**だけ:
//
//   1. どんな値を渡しても描画が落ちないか（NaN・巨大値・空の配列）
//   2. 実際に描画要素が出ているか（何も描かずに「成功」しないか）
//   3. メニューの状態遷移が行き止まりにならないか
//   4. **セッティング画面のスライダーが範囲を超えないか**
//   5. **HUD の区画が画面から出ない・互いに重ならない**（どの画面サイズでも）
//   6. **レッドゾーンの境界が `vehicle.json` の redline と一致する**
//   7. **ベスト周との差と区間タイムが、渡した値と合っているか**
//
// 5〜7 は「読みやすいか」ではない。**そこは主観だが、これは主観ではない。**

#include "CoreMinimal.h"
#include "Misc/AutomationTest.h"
#include "Misc/Paths.h"
#include "Input/HittestGrid.h"
#include "Rendering/DrawElements.h"

#include "Physics/ZN6Setup.h"
#include "Physics/ZN6VehicleData.h"
#include "UI/SZN6Hud.h"
#include "UI/SZN6Menu.h"

#if WITH_DEV_AUTOMATION_TESTS

namespace
{
	FString UiRepoRoot()
	{
		return FPaths::ConvertRelativePathToFull(FPaths::ProjectDir() / TEXT("../.."));
	}

	/**
	 * ウィジェットを1回描いて、**使ったレイヤ数**を返す。
	 *
	 * 描画要素の数を直接数える口が無いので、返ってきたレイヤ ID を使う。
	 * 何も描かなければ渡した ID がそのまま返るので、0 になる。
	 * 「落ちないこと」と「何かを描いたこと」の両方をこれ1つで見られる。
	 */
	template <typename TWidget>
	int32 PaintOnce(const TSharedRef<TWidget>& Widget, const FVector2D& Size)
	{
		const FGeometry Geometry = FGeometry::MakeRoot(Size, FSlateLayoutTransform());
		FSlateWindowElementList Elements(nullptr);
		const FSlateRect Cull(0.0f, 0.0f, static_cast<float>(Size.X),
		                      static_cast<float>(Size.Y));

		// **描画のためだけの当たり判定グリッド。** ウィジェットの外に
		// 用意する必要がある（OnPaint は FPaintArgs を要求する）。
		FHittestGrid Grid;

		return Widget->OnPaint(
			FPaintArgs(nullptr, Grid, FVector2D::ZeroVector, 0.0, 0.0f),
			Geometry, Cull, Elements, /*LayerId=*/0, FWidgetStyle(),
			/*bParentEnabled=*/true);
	}
}

// ---------------------------------------------------------------------------

IMPLEMENT_SIMPLE_AUTOMATION_TEST(
	FZN6HudPaints,
	"ZN6.UI.HUD がどんな値でも描ける",
	EAutomationTestFlags::EditorContext | EAutomationTestFlags::EngineFilter)

bool FZN6HudPaints::RunTest(const FString& Parameters)
{
	TSharedRef<SZN6Hud> Hud = SNew(SZN6Hud);
	const FVector2D Size(1920.0, 1080.0);

	// --- メニュー中は計器を出さない ---
	{
		ZN6::FHudSnapshot Snapshot;
		Snapshot.Phase = ZN6::ERacePhase::Menu;
		Hud->SetSnapshot(Snapshot);
		TestEqual(TEXT("メニュー中は何も描かない"), PaintOnce(Hud, Size), 0);
	}

	// --- 走行中は描く ---
	{
		ZN6::FHudSnapshot Snapshot;
		Snapshot.Phase = ZN6::ERacePhase::Racing;
		Snapshot.SpeedKmh = 128.0;
		Snapshot.EngineRpm = 5200.0;
		Snapshot.Gear = 4;
		Snapshot.Throttle = 0.8;
		Snapshot.LapTimeS = 62.345;
		Snapshot.BestLapS = 61.001;
		for (int32 Wheel = 0; Wheel < 4; ++Wheel)
		{
			Snapshot.Utilisation[Wheel] = 0.4 + Wheel * 0.15;
		}
		Hud->SetSnapshot(Snapshot);

		const int32 Count = PaintOnce(Hud, Size);
		TestTrue(*FString::Printf(TEXT("走行中は描画が行われる（レイヤ %d）"), Count),
		         Count > 5);
	}

	// --- コースを渡すとミニマップが増える ---
	{
		TArray<FVector2D> Centreline;
		for (int32 Index = 0; Index < 120; ++Index)
		{
			const double Angle = 2.0 * PI * Index / 120.0;
			Centreline.Add(FVector2D(200.0 * FMath::Cos(Angle),
			                         140.0 * FMath::Sin(Angle)));
		}
		Hud->SetCentreline(MoveTemp(Centreline));

		const int32 Count = PaintOnce(Hud, Size);
		TestTrue(*FString::Printf(TEXT("ミニマップが描かれる（レイヤ %d）"), Count),
		         Count > 5);
	}

	// --- 壊れた値でも落ちない ---
	//
	// **NaN や巨大値は物理側の異常だが、画面がそこで落ちてはいけない。**
	// 落ちると、異常そのものを見る手立てが無くなる。
	{
		ZN6::FHudSnapshot Broken;
		Broken.Phase = ZN6::ERacePhase::Racing;
		Broken.SpeedKmh = std::numeric_limits<double>::quiet_NaN();
		Broken.EngineRpm = 1e12;
		Broken.RedlineRpm = 0.0;         // 0 割りを誘う
		Broken.IdleRpm = 0.0;
		Broken.Gear = -99;
		Broken.LapTimeS = -5.0;
		Broken.BestLapS = std::numeric_limits<double>::infinity();
		Broken.CarXM = std::numeric_limits<double>::quiet_NaN();
		for (int32 Wheel = 0; Wheel < 4; ++Wheel)
		{
			Broken.Utilisation[Wheel] = std::numeric_limits<double>::quiet_NaN();
		}
		Broken.Sector = 77;
		Broken.SteerRad = std::numeric_limits<double>::quiet_NaN();
		Broken.MaxSteerRad = 0.0;        // ここも 0 割りを誘う
		Broken.Throttle = std::numeric_limits<double>::infinity();
		Broken.LapProgress = -3.0;
		Hud->SetSnapshot(Broken);

		PaintOnce(Hud, Size);
		TestTrue(TEXT("壊れた値でも描画が落ちない"), true);

		// **握りつぶさない。** 直したことを画面に出せる状態になっている
		// こと（憲法ルール6）。
		TestTrue(TEXT("壊れた値を受け取ったことが残る"), Hud->HadInvalidInput());
		TestFalse(TEXT("壊れた周では差の基準を作らない"), Hud->HasDeltaReference());
	}

	// --- まともな値に戻せば異常の印も消える ---
	{
		ZN6::FHudSnapshot Snapshot;
		Snapshot.Phase = ZN6::ERacePhase::Racing;
		Snapshot.SpeedKmh = 100.0;
		Snapshot.EngineRpm = 4000.0;
		Hud->SetSnapshot(Snapshot);
		TestFalse(TEXT("値が戻れば異常の印も消える"), Hud->HadInvalidInput());
	}

	// --- いろいろな画面サイズで落ちない ---
	//
	// **極端に小さい画面も含める。** 目盛りの幅が 0 以下になる。
	{
		ZN6::FHudSnapshot Snapshot;
		Snapshot.Phase = ZN6::ERacePhase::Racing;
		Snapshot.EngineRpm = 7000.0;
		Snapshot.SpeedKmh = 180.0;
		Hud->SetSnapshot(Snapshot);

		for (const FVector2D& Screen : { FVector2D(64.0, 48.0), FVector2D(320.0, 240.0),
		                                 FVector2D(1280.0, 720.0), FVector2D(3440.0, 1440.0),
		                                 FVector2D(1080.0, 1920.0) })
		{
			PaintOnce(Hud, Screen);
		}
		TestTrue(TEXT("どの画面サイズでも描画が落ちない"), true);
	}

	// --- redline が来ていないとき ---
	//
	// **既定値でごまかさない。** 目盛りは無効になり、描画は続く。
	{
		ZN6::FHudSnapshot Snapshot;
		Snapshot.Phase = ZN6::ERacePhase::Racing;
		Snapshot.RedlineRpm = 0.0;
		Snapshot.EngineRpm = 3000.0;
		Hud->SetSnapshot(Snapshot);
		TestTrue(TEXT("redline が無くても描ける"), PaintOnce(Hud, Size) > 5);
	}

	return true;
}

// ---------------------------------------------------------------------------

IMPLEMENT_SIMPLE_AUTOMATION_TEST(
	FZN6HudLayout,
	"ZN6.UI.HUD の区画が画面から出ない・重ならない",
	EAutomationTestFlags::EditorContext | EAutomationTestFlags::EngineFilter)

bool FZN6HudLayout::RunTest(const FString& Parameters)
{
	// **「読めるか」は主観だが、「画面から出ているか」は主観ではない。**
	//
	// 位置を各 Paint 関数に散らしていたときは、画面サイズを変えるたびに
	// どこかが重なっていた。区画を1箇所で決めたので、ここで機械的に見る。
	const FVector2D Screens[] = {
		FVector2D(1920.0, 1080.0),   // 基準
		FVector2D(1600.0,  900.0),   // 撮影に使うサイズ
		FVector2D(1280.0,  720.0),
		FVector2D(3440.0, 1440.0),   // 横長
		FVector2D(1080.0, 1920.0),   // 縦長
		FVector2D(1024.0,  768.0),   // 4:3
		FVector2D( 640.0,  480.0),
		FVector2D(  64.0,   48.0),   // 極端に小さい
	};

	constexpr float Epsilon = 0.05f;
	int32 Problems = 0;

	for (const FVector2D& Screen : Screens)
	{
		const FVector2f Size(static_cast<float>(Screen.X), static_cast<float>(Screen.Y));
		const SZN6Hud::FLayout Layout = SZN6Hud::ComputeLayout(Size);
		const TArray<FSlateRect> Rects = Layout.All();

		for (int32 Index = 0; Index < Rects.Num(); ++Index)
		{
			const FSlateRect& R = Rects[Index];

			if (R.Right < R.Left || R.Bottom < R.Top)
			{
				AddError(FString::Printf(TEXT("%s が裏返っている（%0.0fx%0.0f）"),
				                         SZN6Hud::FLayout::NameOf(Index),
				                         Screen.X, Screen.Y));
				++Problems;
			}

			if (R.Left < -Epsilon || R.Top < -Epsilon
			    || R.Right > Size.X + Epsilon || R.Bottom > Size.Y + Epsilon)
			{
				AddError(FString::Printf(
					TEXT("%s が画面から出た（%0.0fx%0.0f）: %0.1f,%0.1f - %0.1f,%0.1f"),
					SZN6Hud::FLayout::NameOf(Index), Screen.X, Screen.Y,
					R.Left, R.Top, R.Right, R.Bottom));
				++Problems;
			}

			for (int32 Other = Index + 1; Other < Rects.Num(); ++Other)
			{
				const FSlateRect& B = Rects[Other];
				const bool bApart = R.Right <= B.Left + Epsilon
				                 || B.Right <= R.Left + Epsilon
				                 || R.Bottom <= B.Top + Epsilon
				                 || B.Bottom <= R.Top + Epsilon;
				if (!bApart)
				{
					AddError(FString::Printf(
						TEXT("%s と %s が重なった（%0.0fx%0.0f）"),
						SZN6Hud::FLayout::NameOf(Index),
						SZN6Hud::FLayout::NameOf(Other), Screen.X, Screen.Y));
					++Problems;
				}
			}
		}
	}

	TestEqual(TEXT("画面外・重なりは 0 件"), Problems, 0);
	return Problems == 0;
}

// ---------------------------------------------------------------------------

IMPLEMENT_SIMPLE_AUTOMATION_TEST(
	FZN6HudRedline,
	"ZN6.UI.レッドゾーンの境界が vehicle.json の redline と一致する",
	EAutomationTestFlags::EditorContext | EAutomationTestFlags::EngineFilter)

bool FZN6HudRedline::RunTest(const FString& Parameters)
{
	// **HUD は自前の redline を持たない。**
	//
	// 持つと `vehicle.json` を直したときに、物理だけが変わって画面が
	// 古い値のまま残る。「7000 くらいから赤」のような定数を HUD に
	// 書かないための検査。
	ZN6::FVehicleData Data;
	FString Error;
	if (!Data.LoadFromFile(UiRepoRoot() / TEXT("Vehicles/ZN6/vehicle.json"), Error))
	{
		AddError(FString::Printf(TEXT("vehicle.json を読めない: %s"), *Error));
		return false;
	}

	double Redline = 0.0;
	if (!Data.GetValue(TEXT("engine.redline"), TEXT("1/min"), Redline, Error))
	{
		AddError(FString::Printf(TEXT("engine.redline を読めない: %s"), *Error));
		return false;
	}

	const SZN6Hud::FTachScale Scale = SZN6Hud::MakeTachScale(Redline);
	TestTrue(TEXT("車の redline で目盛りが作れる"), Scale.IsValid());
	TestTrue(*FString::Printf(TEXT("赤帯は redline (%0.0f) から始まる"), Redline),
	         FMath::IsNearlyEqual(Scale.RedlineRpm, Redline, 1e-6));

	// **赤帯に幅があること。** 幅が無いと線1本になって見えない
	// （前の版がそうなっていた）。
	TestTrue(TEXT("目盛りの右端は redline より上"), Scale.ScaleMaxRpm > Redline);
	TestTrue(TEXT("赤帯が目盛りの端に潰れていない"),
	         Scale.Fraction(Redline) > 0.5 && Scale.Fraction(Redline) < 0.99);

	// シフトランプは redline の手前から。**演出なので値そのものは問わない。**
	TestTrue(TEXT("シフトランプは redline の手前で点き始める"),
	         Scale.ShiftStartRpm > 0.0 && Scale.ShiftStartRpm < Redline);

	// **別の redline を渡せば境界も動くこと。** 動かなければ、
	// HUD がどこかに自前の値を持っている。
	for (const double Other : { 4500.0, 6000.0, 7400.0, 9000.0, 15000.0 })
	{
		const SZN6Hud::FTachScale Test = SZN6Hud::MakeTachScale(Other);
		if (!FMath::IsNearlyEqual(Test.RedlineRpm, Other, 1e-6))
		{
			AddError(FString::Printf(
				TEXT("redline %0.0f を渡したのに赤帯の始まりが %0.0f だった"),
				Other, Test.RedlineRpm));
			return false;
		}
	}
	TestTrue(TEXT("赤帯の始まりは渡された redline に追従する"), true);

	// **無ければ「無い」と扱う。** 既定値で埋めない。
	TestFalse(TEXT("redline 0 では目盛りを作らない"),
	          SZN6Hud::MakeTachScale(0.0).IsValid());
	TestFalse(TEXT("redline が負では目盛りを作らない"),
	          SZN6Hud::MakeTachScale(-1.0).IsValid());
	TestFalse(TEXT("redline が NaN では目盛りを作らない"),
	          SZN6Hud::MakeTachScale(
	              std::numeric_limits<double>::quiet_NaN()).IsValid());

	// --- シフトランプ ---
	//
	// **点いた瞬間を画面で撮るのは難しい**（1速で 6660〜7400rpm を通る
	// 時間はごく短い）ので、ここは目で見るのではなく数で押さえる。
	{
		const int32 Last = SZN6Hud::ShiftLightCount - 1;

		// 点き始めの手前では1つも点かない。
		for (int32 Index = 0; Index < SZN6Hud::ShiftLightCount; ++Index)
		{
			if (SZN6Hud::IsShiftLightLit(Scale, Scale.ShiftStartRpm - 1.0, Index))
			{
				AddError(FString::Printf(
					TEXT("点き始め (%0.0f rpm) の手前でランプ %d が点いた"),
					Scale.ShiftStartRpm, Index));
				return false;
			}
		}

		// **redline でちょうど全部点く。**
		for (int32 Index = 0; Index < SZN6Hud::ShiftLightCount; ++Index)
		{
			if (!SZN6Hud::IsShiftLightLit(Scale, Redline, Index))
			{
				AddError(FString::Printf(
					TEXT("redline (%0.0f rpm) でランプ %d が点いていない"),
					Redline, Index));
				return false;
			}
		}
		TestFalse(TEXT("redline の少し手前では最後のランプが点かない"),
		          SZN6Hud::IsShiftLightLit(Scale, Redline - 1.0, Last));

		// 順に点くこと。**下が消えたまま上が点かない。**
		const double Middle = (Scale.ShiftStartRpm + Redline) * 0.5;
		bool bSeenUnlit = false;
		for (int32 Index = 0; Index < SZN6Hud::ShiftLightCount; ++Index)
		{
			const bool bLit = SZN6Hud::IsShiftLightLit(Scale, Middle, Index);
			if (!bLit) { bSeenUnlit = true; }
			else if (bSeenUnlit)
			{
				AddError(TEXT("消えているランプの先が点いた（順に点いていない）"));
				return false;
			}
		}
		TestTrue(TEXT("シフトランプは下から順に点く"), true);

		// **redline が変われば点き始めも変わる。**
		const SZN6Hud::FTachScale Low = SZN6Hud::MakeTachScale(5000.0);
		TestFalse(TEXT("redline 5000 なら 6600rpm 相当の閾値では点かない"),
		          SZN6Hud::IsShiftLightLit(Low, 4400.0, 0));
		TestTrue(TEXT("redline 5000 なら 5000rpm で全部点く"),
		         SZN6Hud::IsShiftLightLit(Low, 5000.0, Last));

		// 目盛りが無効なら点かない。
		TestFalse(TEXT("redline が無ければシフトランプも点かない"),
		          SZN6Hud::IsShiftLightLit(SZN6Hud::MakeTachScale(0.0), 9999.0, 0));
	}

	// **HUD へ渡ってくる既定値も車と一致していること。**
	//
	// 現状 `ZN6VehicleActor` は `FHudSnapshot::RedlineRpm` を埋めていない。
	// つまり画面に出ているのはこの既定値である。`vehicle.json` を直したのに
	// ここが取り残されたら、この検査が落ちる。
	const ZN6::FHudSnapshot Default;
	TestTrue(*FString::Printf(
	             TEXT("FHudSnapshot の既定 redline (%0.0f) が vehicle.json (%0.0f) と一致する"),
	             Default.RedlineRpm, Redline),
	         FMath::IsNearlyEqual(Default.RedlineRpm, Redline, 1e-6));

	return true;
}

// ---------------------------------------------------------------------------

IMPLEMENT_SIMPLE_AUTOMATION_TEST(
	FZN6HudDelta,
	"ZN6.UI.ベスト周との差と区間タイムが渡した値と合う",
	EAutomationTestFlags::EditorContext | EAutomationTestFlags::EngineFilter)

bool FZN6HudDelta::RunTest(const FString& Parameters)
{
	// **差は推測ではなく引き算であること。**
	//
	// 60 秒で1周したあと、63 秒ペースで走れば、中間地点での差は +1.5 秒に
	// なるはず。ずれるなら、どこかで値を作っている。
	TSharedRef<SZN6Hud> Hud = SNew(SZN6Hud);

	constexpr int32 Steps = 600;
	TArray<ZN6::FLapRecord> Laps;

	auto RunLap = [&](double LapSeconds, double SessionStartS)
	{
		for (int32 Index = 0; Index <= Steps; ++Index)
		{
			const double Fraction = static_cast<double>(Index) / Steps;
			ZN6::FHudSnapshot Snapshot;
			Snapshot.Phase = ZN6::ERacePhase::Racing;
			Snapshot.CurrentLap = Laps.Num() + 1;
			Snapshot.LapProgress = Fraction;
			Snapshot.LapTimeS = LapSeconds * Fraction;
			Snapshot.SessionTimeS = SessionStartS + LapSeconds * Fraction;
			Snapshot.Sector = (Fraction < 1.0 / 3.0) ? 0
			                : (Fraction < 2.0 / 3.0) ? 1 : 2;
			Snapshot.Laps = Laps;
			Snapshot.BestLapS = Laps.Num() > 0 ? Laps[0].TimeS : 0.0;
			Hud->SetSnapshot(Snapshot);
		}
	};

	auto CloseLap = [&](double LapSeconds, bool bBest)
	{
		ZN6::FLapRecord Record;
		Record.LapNumber = Laps.Num() + 1;
		Record.TimeS = LapSeconds;
		for (int32 Index = 0; Index < 3; ++Index)
		{
			Record.SectorS[Index] = LapSeconds / 3.0;
		}
		Record.bBest = bBest;
		Laps.Add(Record);

		ZN6::FHudSnapshot Snapshot;
		Snapshot.Phase = ZN6::ERacePhase::Racing;
		Snapshot.CurrentLap = Laps.Num() + 1;
		Snapshot.LapProgress = 0.0;
		Snapshot.LapTimeS = 0.0;
		Snapshot.SessionTimeS = LapSeconds * Laps.Num();
		Snapshot.Sector = 0;
		Snapshot.Laps = Laps;
		Snapshot.BestLapS = LapSeconds;
		Hud->SetSnapshot(Snapshot);
	};

	// --- 基準になる周が無いうちは差を出さない ---
	TestFalse(TEXT("走り出す前は差の基準が無い"), Hud->HasDeltaReference());

	// --- 1周目: 60 秒 ---
	RunLap(60.0, 0.0);
	TestFalse(TEXT("ゴールするまでは差の基準が無い"), Hud->HasDeltaReference());
	CloseLap(60.0, /*bBest=*/true);
	TestTrue(TEXT("ベスト周ができたら差の基準になる"), Hud->HasDeltaReference());

	// **周が閉じた直後は、その周の区間タイムを出しておく。**
	TestTrue(*FString::Printf(TEXT("直前の周の S1 が出る（%0.3f）"),
	                          Hud->CurrentSectorTimeS(0)),
	         FMath::IsNearlyEqual(Hud->CurrentSectorTimeS(0), 20.0, 1e-6));

	// --- 2周目: 63 秒ペース。中間で +1.5 秒のはず ---
	{
		ZN6::FHudSnapshot Snapshot;
		Snapshot.Phase = ZN6::ERacePhase::Racing;
		Snapshot.CurrentLap = 2;
		Snapshot.Laps = Laps;
		Snapshot.BestLapS = 60.0;

		// 区間が変わるところまで進める（S1 が刻まれる）
		for (int32 Index = 0; Index <= Steps; ++Index)
		{
			const double Fraction = static_cast<double>(Index) / Steps;
			if (Fraction > 0.5)
			{
				break;
			}
			Snapshot.LapProgress = Fraction;
			Snapshot.LapTimeS = 63.0 * Fraction;
			Snapshot.SessionTimeS = 60.0 + 63.0 * Fraction;
			Snapshot.Sector = (Fraction < 1.0 / 3.0) ? 0 : 1;
			Hud->SetSnapshot(Snapshot);
		}

		// ビンは 256 段なので、1 段ぶん（60/255 = 0.24 秒）の量子化誤差が乗る。
		const double Delta = Hud->LiveDeltaS();
		TestTrue(*FString::Printf(TEXT("中間での差が +1.5 秒付近（%+0.3f）"), Delta),
		         FMath::Abs(Delta - 1.5) < 0.3);

		// 区間タイム。60 秒周の S1 は 20 秒、63 秒ペースなら 21 秒。
		const double S1 = Hud->CurrentSectorTimeS(0);
		TestTrue(*FString::Printf(TEXT("2周目の S1 が 21 秒付近（%0.3f）"), S1),
		         FMath::Abs(S1 - 21.0) < 0.3);
		// **遅かった区間はベストにならない。** 前の周の 20 秒が残る。
		TestFalse(TEXT("遅い区間で自己ベストを塗り替えない"),
		          FMath::IsNearlyEqual(S1, 20.0, 0.05));
	}

	// --- 同じペースで走れば差はほぼ 0 ---
	{
		TSharedRef<SZN6Hud> Same = SNew(SZN6Hud);
		TArray<ZN6::FLapRecord> SameLaps;

		auto Feed = [&](double LapSeconds, double SessionStartS, double UpTo)
		{
			for (int32 Index = 0; Index <= Steps; ++Index)
			{
				const double Fraction = static_cast<double>(Index) / Steps;
				if (Fraction > UpTo)
				{
					break;
				}
				ZN6::FHudSnapshot Snapshot;
				Snapshot.Phase = ZN6::ERacePhase::Racing;
				Snapshot.CurrentLap = SameLaps.Num() + 1;
				Snapshot.LapProgress = Fraction;
				Snapshot.LapTimeS = LapSeconds * Fraction;
				Snapshot.SessionTimeS = SessionStartS + LapSeconds * Fraction;
				Snapshot.Sector = (Fraction < 1.0 / 3.0) ? 0
				                : (Fraction < 2.0 / 3.0) ? 1 : 2;
				Snapshot.Laps = SameLaps;
				Same->SetSnapshot(Snapshot);
			}
		};

		Feed(60.0, 0.0, 1.0);

		ZN6::FLapRecord Record;
		Record.LapNumber = 1;
		Record.TimeS = 60.0;
		Record.SectorS[0] = Record.SectorS[1] = Record.SectorS[2] = 20.0;
		Record.bBest = true;
		SameLaps.Add(Record);
		{
			ZN6::FHudSnapshot Snapshot;
			Snapshot.Phase = ZN6::ERacePhase::Racing;
			Snapshot.CurrentLap = 2;
			Snapshot.Laps = SameLaps;
			Snapshot.SessionTimeS = 60.0;
			Same->SetSnapshot(Snapshot);
		}

		Feed(60.0, 60.0, 0.5);
		TestTrue(*FString::Printf(TEXT("同じペースなら差はほぼ 0（%+0.3f）"),
		                          Same->LiveDeltaS()),
		         FMath::Abs(Same->LiveDeltaS()) < 0.3);
	}

	// --- メニューへ戻したら忘れる ---
	{
		ZN6::FHudSnapshot Snapshot;
		Snapshot.Phase = ZN6::ERacePhase::Menu;
		Hud->SetSnapshot(Snapshot);
		TestFalse(TEXT("メニューへ戻ると差の基準を捨てる"), Hud->HasDeltaReference());
		TestTrue(TEXT("メニューへ戻ると区間タイムも消える"),
		         Hud->CurrentSectorTimeS(0) == 0.0);
	}

	return true;
}

// ---------------------------------------------------------------------------

IMPLEMENT_SIMPLE_AUTOMATION_TEST(
	FZN6MenuNavigation,
	"ZN6.UI.メニューが行き止まりにならない",
	EAutomationTestFlags::EditorContext | EAutomationTestFlags::EngineFilter)

bool FZN6MenuNavigation::RunTest(const FString& Parameters)
{
	ZN6::FVehicleData Data;
	FString Error;
	if (!Data.LoadFromFile(UiRepoRoot() / TEXT("Vehicles/ZN6/vehicle.json"), Error))
	{
		AddError(FString::Printf(TEXT("vehicle.json を読めない: %s"), *Error));
		return false;
	}

	ZN6::FSetupLimits Limits;
	if (!Limits.Init(Data, Error))
	{
		AddError(FString::Printf(TEXT("調整範囲を作れない: %s"), *Error));
		return false;
	}

	int32 SetupChanges = 0;
	ZN6::FCarSetup LastSetup;

	TSharedRef<SZN6Menu> Menu = SNew(SZN6Menu)
		.OnSetupChanged_Lambda([&](const ZN6::FCarSetup& NewSetup)
		{
			++SetupChanges;
			LastSetup = NewSetup;
		});
	Menu->SetLimits(Limits);

	const FVector2D Size(1920.0, 1080.0);
	const FGeometry Geometry = FGeometry::MakeRoot(Size, FSlateLayoutTransform());

	auto Press = [&](const FKey& Key)
	{
		const FKeyEvent Event(Key, FModifierKeysState(), 0, false, 0, 0);
		Menu->OnKeyDown(Geometry, Event);
	};

	// --- 閉じているうちは何も描かない ---
	TestFalse(TEXT("既定は閉じている"), Menu->IsOpen());
	TestEqual(TEXT("閉じているときは描かない"), PaintOnce(Menu, Size), 0);

	// --- 開く ---
	Menu->Open(SZN6Menu::EPage::Main);
	TestTrue(TEXT("開ける"), Menu->IsOpen());
	TestTrue(*FString::Printf(TEXT("メニューを描く（レイヤ %d）"), PaintOnce(Menu, Size)),
	         PaintOnce(Menu, Size) > 2);

	// --- 選択が巻く ---
	//
	// **端で止まると、一覧の反対側へ行くのに何度も押すことになる。**
	for (int32 Step = 0; Step < 20; ++Step)
	{
		Press(EKeys::Down);
	}
	TestTrue(TEXT("下へ押し続けても壊れない"), Menu->IsOpen());
	for (int32 Step = 0; Step < 20; ++Step)
	{
		Press(EKeys::Up);
	}
	TestTrue(TEXT("上へ押し続けても壊れない"), Menu->IsOpen());

	// --- セッティング画面へ ---
	Menu->Open(SZN6Menu::EPage::Setup);
	TestTrue(TEXT("セッティング画面を描く"), PaintOnce(Menu, Size) > 2);

	// **どの項目をどれだけ動かしても、範囲を超えないこと。**
	for (int32 Row = 0; Row < static_cast<int32>(ZN6::ESetupItem::Count); ++Row)
	{
		// 各項目まで移動
		Menu->Open(SZN6Menu::EPage::Setup);
		for (int32 Step = 0; Step < Row; ++Step)
		{
			Press(EKeys::Down);
		}

		// 端まで振り切る
		for (int32 Step = 0; Step < 60; ++Step)
		{
			Press(EKeys::Right);
		}
		for (int32 Step = 0; Step < 120; ++Step)
		{
			Press(EKeys::Left);
		}

		TArray<FString> Problems;
		Limits.Validate(Menu->GetSetup(), Problems);
		if (Problems.Num() > 0)
		{
			AddError(FString::Printf(TEXT("項目 %d を振り切ったら範囲外になった: %s"),
			                         Row, *FString::Join(Problems, TEXT(" / "))));
			return false;
		}
	}
	TestTrue(TEXT("どれだけ動かしても範囲を超えない"), true);
	TestTrue(*FString::Printf(TEXT("変更が外へ伝わる（%d 回）"), SetupChanges),
	         SetupChanges > 0);

	// --- 画質設定 ---
	Menu->Open(SZN6Menu::EPage::Graphics);
	TestTrue(TEXT("画質設定を描く"), PaintOnce(Menu, Size) > 2);
	for (int32 Step = 0; Step < 30; ++Step)
	{
		Press(EKeys::Right);
		Press(EKeys::Down);
	}
	TestTrue(TEXT("画質設定を触っても落ちない"), Menu->IsOpen());

	// --- リザルト ---
	{
		ZN6::FHudSnapshot Snapshot;
		Snapshot.BestLapS = 61.5;
		for (int32 Lap = 1; Lap <= 3; ++Lap)
		{
			ZN6::FLapRecord Record;
			Record.LapNumber = Lap;
			Record.TimeS = 62.0 - Lap * 0.2;
			Record.SectorS[0] = 20.0;
			Record.SectorS[1] = 21.0;
			Record.SectorS[2] = 21.0 - Lap * 0.2;
			Record.bBest = (Lap == 3);
			Snapshot.Laps.Add(Record);
		}
		Menu->SetSnapshot(Snapshot);
		Menu->Open(SZN6Menu::EPage::Result);
		TestTrue(TEXT("リザルトを描く"), PaintOnce(Menu, Size) > 2);

		// 記録が空でも落ちない
		Menu->SetSnapshot(ZN6::FHudSnapshot());
		PaintOnce(Menu, Size);
		TestTrue(TEXT("記録が無くても落ちない"), true);
	}

	// --- Esc で必ずメインへ戻れる ---
	//
	// **どの画面からも戻れること。** 戻れない画面があると詰む。
	for (const SZN6Menu::EPage Page : { SZN6Menu::EPage::Setup,
	                                    SZN6Menu::EPage::Graphics,
	                                    SZN6Menu::EPage::Result })
	{
		Menu->Open(Page);
		Press(EKeys::Escape);
		TestTrue(TEXT("Esc でメインへ戻る"),
		         Menu->CurrentPage() == SZN6Menu::EPage::Main);
	}

	// メインで Esc を押すと閉じる
	Press(EKeys::Escape);
	TestFalse(TEXT("メインで Esc を押すと閉じる"), Menu->IsOpen());

	return true;
}

#endif  // WITH_DEV_AUTOMATION_TESTS
