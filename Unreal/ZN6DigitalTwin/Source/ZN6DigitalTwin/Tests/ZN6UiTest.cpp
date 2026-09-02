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
		Hud->SetSnapshot(Broken);

		PaintOnce(Hud, Size);
		TestTrue(TEXT("壊れた値でも描画が落ちない"), true);
	}

	// --- 極端に小さい画面でも落ちない ---
	{
		ZN6::FHudSnapshot Snapshot;
		Snapshot.Phase = ZN6::ERacePhase::Racing;
		Hud->SetSnapshot(Snapshot);
		PaintOnce(Hud, FVector2D(64.0, 48.0));
		TestTrue(TEXT("小さい画面でも描画が落ちない"), true);
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
