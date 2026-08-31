#include "SZN6Menu.h"

#include "GameFramework/GameUserSettings.h"
#include "Rendering/DrawElements.h"
#include "Styling/CoreStyle.h"
#include "UI/ZN6Style.h"

namespace
{
	using namespace ZN6UI;

	void Line(FSlateWindowElementList& Out, int32 Layer, const FGeometry& Geometry,
	          const FVector2f& A, const FVector2f& B, const FLinearColor& Colour,
	          float Thickness)
	{
		TArray<FVector2f> Points;
		Points.Add(A);
		Points.Add(B);
		FSlateDrawElement::MakeLines(Out, Layer, Geometry.ToPaintGeometry(), Points,
		                             ESlateDrawEffect::None, Colour, true, Thickness);
	}

	void Text(FSlateWindowElementList& Out, int32 Layer, const FGeometry& Geometry,
	          const FString& Value, const FVector2f& Position,
	          const FSlateFontInfo& FontInfo, const FLinearColor& Colour)
	{
		FSlateDrawElement::MakeText(
			Out, Layer,
			Geometry.ToPaintGeometry(FVector2f(1.0f, 1.0f),
			                         FSlateLayoutTransform(FVector2f(Position))),
			Value, FontInfo, ESlateDrawEffect::None, Colour);
	}

	void Box(FSlateWindowElementList& Out, int32 Layer, const FGeometry& Geometry,
	         const FSlateBrush* Brush, const FVector2f& Origin, const FVector2f& Size,
	         const FLinearColor& Colour)
	{
		FSlateDrawElement::MakeBox(
			Out, Layer,
			Geometry.ToPaintGeometry(Size, FSlateLayoutTransform(FVector2f(Origin))),
			Brush, ESlateDrawEffect::None, Colour);
	}

	/** メインメニューの項目。**並び順がそのまま画面の並び順。** */
	struct FMainEntry { const TCHAR* Label; const TCHAR* Note; };
	const FMainEntry MainEntries[] = {
		{ TEXT("RACE"),      TEXT("カウントダウンから 3 周") },
		{ TEXT("FREE RUN"),  TEXT("待たずに走る。周回数は無制限") },
		{ TEXT("CAR SETUP"), TEXT("車高・アライメント・ばね・ブレーキ") },
		{ TEXT("GRAPHICS"),  TEXT("画質と表示") },
		{ TEXT("QUIT"),      TEXT("終了") },
	};
	constexpr int32 MainEntryCount = UE_ARRAY_COUNT(MainEntries);
}

void SZN6Menu::Construct(const FArguments& InArgs)
{
	OnStartRace = InArgs._OnStartRace;
	OnFreeRun = InArgs._OnFreeRun;
	OnResume = InArgs._OnResume;
	OnQuit = InArgs._OnQuit;
	OnSetupChanged = InArgs._OnSetupChanged;

	WhiteBrush = FCoreStyle::Get().GetBrush("WhiteBrush");
	SetCanTick(false);
	SetVisibility(EVisibility::Collapsed);
}

void SZN6Menu::Open(EPage InPage)
{
	bOpen = true;
	Page = InPage;
	Selected = 0;
	SetVisibility(EVisibility::Visible);
	FSlateApplication::Get().SetKeyboardFocus(SharedThis(this));
}

void SZN6Menu::Close()
{
	bOpen = false;
	SetVisibility(EVisibility::Collapsed);
}

int32 SZN6Menu::RowCount() const
{
	switch (Page)
	{
	case EPage::Main:     return MainEntryCount;
	case EPage::Setup:    return static_cast<int32>(ZN6::ESetupItem::Count);
	case EPage::Graphics: return GraphicsRowCount();
	default:              return 0;
	}
}

// ---------------------------------------------------------------------------
// 入力

void SZN6Menu::MoveSelection(int32 Delta)
{
	const int32 Count = RowCount();
	if (Count <= 0)
	{
		return;
	}
	// **端で止めずに巻く。** 長い一覧を戻るのが速い。
	Selected = (Selected + Delta + Count) % Count;
}

void SZN6Menu::Adjust(int32 Direction)
{
	if (Page == EPage::Graphics)
	{
		AdjustGraphics(Selected, Direction);
		return;
	}

	if (Page != EPage::Setup || !bHasLimits)
	{
		return;
	}

	const ZN6::ESetupItem Item = static_cast<ZN6::ESetupItem>(Selected);
	const ZN6::FSetupRange& Range = Limits.Range(Item);
	if (!Range.IsAdjustable())
	{
		return;
	}

	// 20 段で端から端まで。**細かすぎると目的の値に届かない。**
	const double Step = (Range.High - Range.Low) / 20.0;
	double Value = Setup.Get(Item);

	// ブレーキバイアスの負は「既定を使う」の意味。触ったら既定値から始める。
	if (Item == ZN6::ESetupItem::BrakeBias && Value < 0.0)
	{
		Value = Range.Default;
	}

	Setup.Set(Item, Range.Clamp(Value + Step * Direction));
	OnSetupChanged.ExecuteIfBound(Setup);
}

void SZN6Menu::Activate()
{
	if (Page == EPage::Main)
	{
		switch (Selected)
		{
		case 0: Close(); OnStartRace.ExecuteIfBound(); break;
		case 1: Close(); OnFreeRun.ExecuteIfBound(); break;
		case 2: Open(EPage::Setup); break;
		case 3: Open(EPage::Graphics); break;
		case 4: OnQuit.ExecuteIfBound(); break;
		default: break;
		}
		return;
	}

	if (Page == EPage::Graphics)
	{
		ApplyGraphics();
		return;
	}

	if (Page == EPage::Result)
	{
		Open(EPage::Main);
	}
}

void SZN6Menu::GoBack()
{
	if (Page == EPage::Main)
	{
		// メニューの一番上で閉じる = 走行へ戻る
		Close();
		OnResume.ExecuteIfBound();
		return;
	}
	Open(EPage::Main);
}

FReply SZN6Menu::OnKeyDown(const FGeometry& Geometry, const FKeyEvent& Key)
{
	if (!bOpen)
	{
		return FReply::Unhandled();
	}

	const FKey Pressed = Key.GetKey();

	if (Pressed == EKeys::Up || Pressed == EKeys::W)          { MoveSelection(-1); }
	else if (Pressed == EKeys::Down || Pressed == EKeys::S)   { MoveSelection(1); }
	else if (Pressed == EKeys::Left || Pressed == EKeys::A)   { Adjust(-1); }
	else if (Pressed == EKeys::Right || Pressed == EKeys::D)  { Adjust(1); }
	else if (Pressed == EKeys::Enter || Pressed == EKeys::SpaceBar) { Activate(); }
	else if (Pressed == EKeys::Escape || Pressed == EKeys::BackSpace) { GoBack(); }
	else
	{
		return FReply::Unhandled();
	}

	return FReply::Handled();
}

int32 SZN6Menu::RowAtPosition(const FGeometry& Geometry,
                              const FVector2D& Screen) const
{
	const FVector2D Local = Geometry.AbsoluteToLocal(Screen);
	for (int32 Index = 0; Index < RowRects.Num(); ++Index)
	{
		if (RowRects[Index].ContainsPoint(FVector2f(Local)))
		{
			return Index;
		}
	}
	return INDEX_NONE;
}

FReply SZN6Menu::OnMouseMove(const FGeometry& Geometry, const FPointerEvent& Mouse)
{
	if (!bOpen)
	{
		return FReply::Unhandled();
	}

	// **重ねた行を選択状態にする。** キーボードの選択と同じ場所を使うので、
	// マウスで指したあとキーボードに持ち替えても続きから動かせる。
	const int32 Row = RowAtPosition(Geometry, Mouse.GetScreenSpacePosition());
	if (Row != INDEX_NONE)
	{
		Selected = Row;
	}
	return FReply::Handled();
}

FReply SZN6Menu::OnMouseButtonDown(const FGeometry& Geometry, const FPointerEvent& Mouse)
{
	if (!bOpen)
	{
		return FReply::Unhandled();
	}

	// **押した時点でフォーカスを取り直す。** 何かの拍子に外れていても、
	// クリックすればキーボードが効く状態に戻せる。
	FReply Reply = FReply::Handled().SetUserFocus(SharedThis(this), EFocusCause::Mouse);

	if (Mouse.GetEffectingButton() == EKeys::RightMouseButton)
	{
		GoBack();
		return Reply;
	}

	const int32 Row = RowAtPosition(Geometry, Mouse.GetScreenSpacePosition());
	if (Row == INDEX_NONE)
	{
		return Reply;
	}
	Selected = Row;

	const FVector2f LocalF(Geometry.AbsoluteToLocal(Mouse.GetScreenSpacePosition()));

	// 左右の帯を押したら値を増減。それ以外は決定。
	if (LeftArrowRects.IsValidIndex(Row) && LeftArrowRects[Row].ContainsPoint(LocalF))
	{
		Adjust(-1);
	}
	else if (RightArrowRects.IsValidIndex(Row)
	         && RightArrowRects[Row].ContainsPoint(LocalF))
	{
		Adjust(1);
	}
	else
	{
		Activate();
	}

	return Reply;
}

// ---------------------------------------------------------------------------
// 画質設定

void SZN6Menu::ApplyGraphics() const
{
	if (UGameUserSettings* Settings = GEngine ? GEngine->GetGameUserSettings() : nullptr)
	{
		// **確認せずに解像度を変えない。** ここで適用するのは
		// 画質と垂直同期とフレーム上限だけ。
		Settings->ApplySettings(/*bCheckForCommandLineOverrides=*/false);
		Settings->SaveSettings();
	}
}

FString SZN6Menu::GraphicsLabel(int32 Row) const
{
	switch (Row)
	{
	case 0: return TEXT("全体の画質");
	case 1: return TEXT("影");
	case 2: return TEXT("描画距離");
	case 3: return TEXT("垂直同期");
	case 4: return TEXT("フレーム上限");
	default: return TEXT("");
	}
}

FString SZN6Menu::GraphicsValue(int32 Row) const
{
	UGameUserSettings* Settings = GEngine ? GEngine->GetGameUserSettings() : nullptr;
	if (Settings == nullptr)
	{
		// **「取れなかった」を既定値で誤魔化さない。**
		return TEXT("(設定を取得できない)");
	}

	static const TCHAR* Levels[] = { TEXT("低"), TEXT("中"), TEXT("高"),
	                                 TEXT("最高"), TEXT("シネマ") };
	auto LevelName = [&](int32 Value)
	{
		return (Value >= 0 && Value < UE_ARRAY_COUNT(Levels))
			? Levels[Value] : TEXT("カスタム");
	};

	switch (Row)
	{
	case 0: return LevelName(Settings->GetOverallScalabilityLevel());
	case 1: return LevelName(Settings->GetShadowQuality());
	case 2: return LevelName(Settings->GetViewDistanceQuality());
	case 3: return Settings->IsVSyncEnabled() ? TEXT("ON") : TEXT("OFF");
	case 4:
	{
		const float Limit = Settings->GetFrameRateLimit();
		return Limit <= 0.0f ? TEXT("無制限")
		                     : FString::Printf(TEXT("%.0f fps"), Limit);
	}
	default: return TEXT("");
	}
}

void SZN6Menu::AdjustGraphics(int32 Row, int32 Direction)
{
	UGameUserSettings* Settings = GEngine ? GEngine->GetGameUserSettings() : nullptr;
	if (Settings == nullptr)
	{
		return;
	}

	auto Step = [&](int32 Current) { return FMath::Clamp(Current + Direction, 0, 4); };

	switch (Row)
	{
	case 0: Settings->SetOverallScalabilityLevel(Step(Settings->GetOverallScalabilityLevel())); break;
	case 1: Settings->SetShadowQuality(Step(Settings->GetShadowQuality())); break;
	case 2: Settings->SetViewDistanceQuality(Step(Settings->GetViewDistanceQuality())); break;
	case 3: Settings->SetVSyncEnabled(!Settings->IsVSyncEnabled()); break;
	case 4:
	{
		// 無制限 -> 30 -> 60 -> 90 -> 120 -> 144 -> 240 -> 無制限
		static const float Choices[] = { 0.0f, 30.0f, 60.0f, 90.0f, 120.0f, 144.0f, 240.0f };
		constexpr int32 ChoiceCount = UE_ARRAY_COUNT(Choices);
		const float Current = Settings->GetFrameRateLimit();
		int32 Index = 0;
		for (int32 I = 0; I < ChoiceCount; ++I)
		{
			if (FMath::IsNearlyEqual(Choices[I], Current, 0.5f))
			{
				Index = I;
				break;
			}
		}
		Index = (Index + Direction + ChoiceCount) % ChoiceCount;
		Settings->SetFrameRateLimit(Choices[Index]);
		break;
	}
	default: break;
	}

	// **即座に反映する。** 「適用」を押すまで変わらないと、
	// 何が変わったのか分からない。
	ApplyGraphics();
}

// ---------------------------------------------------------------------------
// 描画

int32 SZN6Menu::PaintChrome(const FGeometry& Geometry, FSlateWindowElementList& Out,
                            int32 Layer, const FVector2f& Screen) const
{
	// 全面を暗く。**走っている画は薄く透かす。**
	Box(Out, Layer, Geometry, WhiteBrush, FVector2f::ZeroVector, Screen, Overlay());

	// 左上に大きく車名。**どのゲームか一目で分かる。**
	Text(Out, Layer + 1, Geometry, TEXT("ZN6"),
	     FVector2f(PadL() * 2.0f, PadL() * 1.6f), NumeralFont(64), TextPrimary());
	Text(Out, Layer + 1, Geometry, TEXT("TOYOTA 86  DIGITAL TWIN"),
	     FVector2f(PadL() * 2.0f + 4.0f, PadL() * 1.6f + 70.0f),
	     LabelFont(13), Accent());

	// 差し色の細い縦線
	Line(Out, Layer + 1, Geometry,
	     FVector2f(PadL() * 2.0f - 12.0f, PadL() * 1.6f + 6.0f),
	     FVector2f(PadL() * 2.0f - 12.0f, PadL() * 1.6f + 84.0f), Accent(), 3.0f);

	return Layer + 2;
}

int32 SZN6Menu::PaintHint(const FGeometry& Geometry, FSlateWindowElementList& Out,
                          int32 Layer, const FVector2f& Screen,
                          const FString& Hint) const
{
	Line(Out, Layer, Geometry,
	     FVector2f(PadL() * 2.0f, Screen.Y - 58.0f),
	     FVector2f(Screen.X - PadL() * 2.0f, Screen.Y - 58.0f), PanelEdge(), 1.0f);
	Text(Out, Layer, Geometry, Hint,
	     FVector2f(PadL() * 2.0f, Screen.Y - 44.0f), LabelFont(12), TextFaint());
	return Layer + 1;
}

int32 SZN6Menu::PaintMain(const FGeometry& Geometry, FSlateWindowElementList& Out,
                          int32 Layer, const FVector2f& Origin,
                          const FVector2f& Size) const
{
	float Y = Origin.Y;
	for (int32 Index = 0; Index < MainEntryCount; ++Index)
	{
		const bool bSelected = (Index == Selected);

		// 押せる範囲。**描く矩形と同じ計算から作る。**
		RowRects.Add(FSlateRect(Origin.X - 18.0f, Y - 4.0f,
		                        Origin.X + Size.X * 0.62f, Y + 36.0f));
		LeftArrowRects.Add(FSlateRect());     // メインには増減が無い
		RightArrowRects.Add(FSlateRect());

		if (bSelected)
		{
			// 選択中の行だけ、左に太い差し色の帯を出す
			Box(Out, Layer, Geometry, WhiteBrush, FVector2f(Origin.X - 18.0f, Y - 4.0f),
			    FVector2f(5.0f, 40.0f), Accent());
			Box(Out, Layer, Geometry, WhiteBrush, FVector2f(Origin.X - 18.0f, Y - 4.0f),
			    FVector2f(Size.X * 0.62f, 40.0f), AccentDim() * FLinearColor(1, 1, 1, 0.35f));
		}

		Text(Out, Layer + 1, Geometry, MainEntries[Index].Label,
		     FVector2f(Origin.X, Y), NumeralFont(28),
		     bSelected ? TextPrimary() : TextSecondary());
		Text(Out, Layer + 1, Geometry, MainEntries[Index].Note,
		     FVector2f(Origin.X + 232.0f, Y + 12.0f), LabelFont(12),
		     bSelected ? Accent() : TextFaint());

		Y += 52.0f;
	}

	// 今のセッティングを添える。**何を積んで走るのかが分かる。**
	Text(Out, Layer + 1, Geometry, TEXT("SETUP"),
	     FVector2f(Origin.X, Y + 24.0f), LabelFont(11), TextFaint());
	Text(Out, Layer + 1, Geometry, Setup.Describe(),
	     FVector2f(Origin.X, Y + 42.0f), LabelFont(13),
	     Setup.IsDefault() ? TextSecondary() : Warn());

	return Layer + 2;
}

int32 SZN6Menu::PaintSlider(const FGeometry& Geometry, FSlateWindowElementList& Out,
                            int32 Layer, const FVector2f& Origin, float Width,
                            const FString& Label, const FString& Value,
                            double Fraction, bool bSelected, bool bAdjustable) const
{
	const FLinearColor LabelColour = bAdjustable
		? (bSelected ? TextPrimary() : TextSecondary())
		: TextFaint();

	Text(Out, Layer, Geometry, Label, FVector2f(Origin.X, Origin.Y),
	     LabelFont(14), LabelColour);
	Text(Out, Layer, Geometry, Value,
	     FVector2f(Origin.X + Width - 96.0f, Origin.Y), LabelFont(14),
	     bAdjustable ? (bSelected ? Accent() : TextSecondary()) : TextFaint());

	// 目盛りの帯
	const float BarY = Origin.Y + 22.0f;
	const float BarWidth = Width - 110.0f;

	// 押せる範囲。行全体で選択、帯の左半分/右半分で増減。
	RowRects.Add(FSlateRect(Origin.X - 14.0f, Origin.Y - 6.0f,
	                        Origin.X + Width, BarY + 14.0f));
	if (bAdjustable)
	{
		LeftArrowRects.Add(FSlateRect(Origin.X, BarY - 8.0f,
		                              Origin.X + BarWidth * 0.5f, BarY + 14.0f));
		RightArrowRects.Add(FSlateRect(Origin.X + BarWidth * 0.5f, BarY - 8.0f,
		                               Origin.X + BarWidth, BarY + 14.0f));
	}
	else
	{
		LeftArrowRects.Add(FSlateRect());
		RightArrowRects.Add(FSlateRect());
	}

	Box(Out, Layer, Geometry, WhiteBrush, FVector2f(Origin.X, BarY),
	    FVector2f(BarWidth, 4.0f), GaugeTrack());

	if (bAdjustable)
	{
		const float At = BarWidth * FMath::Clamp(static_cast<float>(Fraction), 0.0f, 1.0f);
		Box(Out, Layer + 1, Geometry, WhiteBrush, FVector2f(Origin.X, BarY),
		    FVector2f(At, 4.0f), bSelected ? Accent() : AccentDim());
		// つまみ
		Box(Out, Layer + 2, Geometry, WhiteBrush,
		    FVector2f(Origin.X + At - 3.0f, BarY - 5.0f), FVector2f(6.0f, 14.0f),
		    bSelected ? TextPrimary() : TextSecondary());
	}

	return Layer + 3;
}

int32 SZN6Menu::PaintSetup(const FGeometry& Geometry, FSlateWindowElementList& Out,
                           int32 Layer, const FVector2f& Origin,
                           const FVector2f& Size) const
{
	if (!bHasLimits)
	{
		Text(Out, Layer, Geometry, TEXT("調整範囲を読めていない"),
		     Origin, LabelFont(14), Danger());
		return Layer + 1;
	}

	const float Width = Size.X * 0.52f;
	float Y = Origin.Y;

	for (int32 Index = 0; Index < static_cast<int32>(ZN6::ESetupItem::Count); ++Index)
	{
		const ZN6::ESetupItem Item = static_cast<ZN6::ESetupItem>(Index);
		const ZN6::FSetupRange& Range = Limits.Range(Item);

		double Value = Setup.Get(Item);
		if (Item == ZN6::ESetupItem::BrakeBias && Value < 0.0)
		{
			Value = Range.Default;
		}

		const double Fraction = Range.IsAdjustable()
			? (Value - Range.Low) / (Range.High - Range.Low) : 0.0;

		const FString Shown = Range.IsAdjustable()
			? FString::Printf(TEXT("%.*f %s"), Range.DisplayDigits,
			                  Value * Range.DisplayScale, *Range.DisplayUnit)
			: TEXT("調整不可");

		Layer = PaintSlider(Geometry, Out, Layer, FVector2f(Origin.X, Y), Width,
		                    Range.Label, Shown, Fraction, Index == Selected,
		                    Range.IsAdjustable());
		Y += 44.0f;
	}

	// 選択中の項目の注記。**なぜその範囲なのかを見せる。**
	const ZN6::FSetupRange& Current =
		Limits.Range(static_cast<ZN6::ESetupItem>(FMath::Clamp(
			Selected, 0, static_cast<int32>(ZN6::ESetupItem::Count) - 1)));

	const float NoteX = Origin.X + Width + 48.0f;
	Text(Out, Layer, Geometry, TEXT("NOTE"), FVector2f(NoteX, Origin.Y),
	     LabelFont(11), TextFaint());

	// 長い注記を折り返す。**Slate の自動折り返しを使わない**ので手で切る。
	{
		FString Rest = Current.Note;
		float NoteY = Origin.Y + 20.0f;
		constexpr int32 PerLine = 30;
		while (!Rest.IsEmpty() && NoteY < Origin.Y + 160.0f)
		{
			const int32 Take = FMath::Min(PerLine, Rest.Len());
			Text(Out, Layer, Geometry, Rest.Left(Take), FVector2f(NoteX, NoteY),
			     LabelFont(12), TextSecondary());
			Rest = Rest.RightChop(Take);
			NoteY += 18.0f;
		}
	}

	// **効かない項目を隠さない。**
	//
	// 「無い」のか「実装漏れ」なのかを区別できるようにするため、
	// 理由つきで並べる。
	float UnsupportedY = Origin.Y + 190.0f;
	Text(Out, Layer, Geometry, TEXT("調整できないもの（理由つき）"),
	     FVector2f(NoteX, UnsupportedY), LabelFont(11), Warn());
	UnsupportedY += 20.0f;

	for (const TPair<FString, FString>& Entry : ZN6::UnsupportedItems())
	{
		Text(Out, Layer, Geometry, Entry.Key, FVector2f(NoteX, UnsupportedY),
		     LabelFont(12), TextFaint());
		Text(Out, Layer, Geometry, Entry.Value.Left(34),
		     FVector2f(NoteX + 108.0f, UnsupportedY), LabelFont(11), TextFaint());
		UnsupportedY += 17.0f;
	}

	return Layer + 1;
}

int32 SZN6Menu::PaintGraphics(const FGeometry& Geometry, FSlateWindowElementList& Out,
                              int32 Layer, const FVector2f& Origin,
                              const FVector2f& Size) const
{
	const float Width = Size.X * 0.52f;
	float Y = Origin.Y;

	for (int32 Row = 0; Row < GraphicsRowCount(); ++Row)
	{
		const bool bSelected = (Row == Selected);

		// 行全体で選択、左半分/右半分で増減
		RowRects.Add(FSlateRect(Origin.X - 14.0f, Y - 6.0f, Origin.X + Width, Y + 30.0f));
		LeftArrowRects.Add(FSlateRect(Origin.X, Y - 6.0f,
		                              Origin.X + Width * 0.5f, Y + 30.0f));
		RightArrowRects.Add(FSlateRect(Origin.X + Width * 0.5f, Y - 6.0f,
		                               Origin.X + Width, Y + 30.0f));
		Text(Out, Layer, Geometry, GraphicsLabel(Row), FVector2f(Origin.X, Y),
		     LabelFont(15), bSelected ? TextPrimary() : TextSecondary());
		Text(Out, Layer, Geometry, GraphicsValue(Row),
		     FVector2f(Origin.X + Width - 130.0f, Y), LabelFont(15),
		     bSelected ? Accent() : TextSecondary());

		if (bSelected)
		{
			Box(Out, Layer - 1, Geometry, WhiteBrush,
			    FVector2f(Origin.X - 14.0f, Y - 5.0f), FVector2f(4.0f, 28.0f), Accent());
		}
		Y += 44.0f;
	}

	Text(Out, Layer, Geometry,
	     TEXT("画質はエンジンの設定（UGameUserSettings）に委ねている。"),
	     FVector2f(Origin.X + Width + 48.0f, Origin.Y), LabelFont(12), TextSecondary());
	Text(Out, Layer, Geometry,
	     TEXT("自前で持つとエンジン側と二重管理になる。"),
	     FVector2f(Origin.X + Width + 48.0f, Origin.Y + 18.0f),
	     LabelFont(12), TextSecondary());
	Text(Out, Layer, Geometry,
	     TEXT("**画質を変えても物理は変わらない。**"),
	     FVector2f(Origin.X + Width + 48.0f, Origin.Y + 44.0f), LabelFont(12), Accent());
	Text(Out, Layer, Geometry,
	     TEXT("固定刻みで積分しているので、重い設定でも"),
	     FVector2f(Origin.X + Width + 48.0f, Origin.Y + 62.0f),
	     LabelFont(12), TextSecondary());
	Text(Out, Layer, Geometry,
	     TEXT("ラップタイムは同じになる。"),
	     FVector2f(Origin.X + Width + 48.0f, Origin.Y + 80.0f),
	     LabelFont(12), TextSecondary());

	return Layer + 1;
}

int32 SZN6Menu::PaintResult(const FGeometry& Geometry, FSlateWindowElementList& Out,
                            int32 Layer, const FVector2f& Origin,
                            const FVector2f& Size) const
{
	Text(Out, Layer, Geometry, TEXT("RESULT"), FVector2f(Origin.X, Origin.Y),
	     NumeralFont(34), TextPrimary());

	float Y = Origin.Y + 56.0f;
	Text(Out, Layer, Geometry, TEXT("LAP"), FVector2f(Origin.X, Y),
	     LabelFont(11), TextFaint());
	Text(Out, Layer, Geometry, TEXT("TIME"), FVector2f(Origin.X + 70.0f, Y),
	     LabelFont(11), TextFaint());
	Text(Out, Layer, Geometry, TEXT("S1 / S2 / S3"), FVector2f(Origin.X + 220.0f, Y),
	     LabelFont(11), TextFaint());
	Y += 22.0f;

	for (const ZN6::FLapRecord& Lap : Snapshot.Laps)
	{
		const FLinearColor Colour = Lap.bBest ? Accent() : TextSecondary();
		Text(Out, Layer, Geometry, FString::FromInt(Lap.LapNumber),
		     FVector2f(Origin.X, Y), LabelFont(15), Colour);
		Text(Out, Layer, Geometry, FormatLapTime(Lap.TimeS),
		     FVector2f(Origin.X + 70.0f, Y), LabelFont(15), Colour);
		Text(Out, Layer, Geometry,
		     FString::Printf(TEXT("%.3f / %.3f / %.3f"),
		                     Lap.SectorS[0], Lap.SectorS[1], Lap.SectorS[2]),
		     FVector2f(Origin.X + 220.0f, Y), LabelFont(13), TextFaint());
		Y += 26.0f;
	}

	if (Snapshot.Laps.Num() == 0)
	{
		Text(Out, Layer, Geometry, TEXT("記録なし"), FVector2f(Origin.X, Y),
		     LabelFont(14), TextFaint());
	}

	Y += 20.0f;
	Text(Out, Layer, Geometry,
	     FString::Printf(TEXT("BEST  %s"), *FormatLapTime(Snapshot.BestLapS)),
	     FVector2f(Origin.X, Y), NumeralFont(22), Accent());

	// **どのセッティングで出したタイムかを一緒に残す。**
	// 書かないと、後から比べようがない。
	Y += 44.0f;
	Text(Out, Layer, Geometry, TEXT("SETUP"), FVector2f(Origin.X, Y),
	     LabelFont(11), TextFaint());
	Text(Out, Layer, Geometry, Setup.Describe(), FVector2f(Origin.X + 56.0f, Y),
	     LabelFont(13), TextSecondary());

	Y += 24.0f;
	Text(Out, Layer, Geometry,
	     FString::Printf(TEXT("model confidence %.2f%s"), Snapshot.Confidence,
	                     Snapshot.bValidatable ? TEXT("")
	                                           : TEXT("  (assumed values in use)")),
	     FVector2f(Origin.X, Y), LabelFont(11),
	     Snapshot.bValidatable ? TextFaint() : Warn());

	return Layer + 1;
}

int32 SZN6Menu::OnPaint(const FPaintArgs& Args, const FGeometry& AllottedGeometry,
                        const FSlateRect& MyCullingRect,
                        FSlateWindowElementList& OutDrawElements, int32 LayerId,
                        const FWidgetStyle& InWidgetStyle, bool bParentEnabled) const
{
	if (!bOpen || WhiteBrush == nullptr)
	{
		return LayerId;
	}

	// **当たり判定は毎フレーム描画から作り直す。**
	// 描く場所と押せる場所を別々に計算すると、必ずどこかでずれる。
	RowRects.Reset();
	LeftArrowRects.Reset();
	RightArrowRects.Reset();

	const FVector2f Screen = FVector2f(AllottedGeometry.GetLocalSize());
	int32 Layer = PaintChrome(AllottedGeometry, OutDrawElements, LayerId, Screen);

	const FVector2f Origin(PadL() * 2.0f + 20.0f, 210.0f);
	const FVector2f Size(Screen.X - Origin.X - PadL() * 2.0f, Screen.Y - Origin.Y - 90.0f);

	FString Hint;
	switch (Page)
	{
	case EPage::Main:
		Layer = PaintMain(AllottedGeometry, OutDrawElements, Layer + 1, Origin, Size);
		Hint = TEXT("W/S・↑↓ で選択    Enter で決定    Esc で走行へ戻る"
		            "    ／ マウスでも選べる（クリックで決定・右クリックで戻る）");
		break;
	case EPage::Setup:
		Layer = PaintSetup(AllottedGeometry, OutDrawElements, Layer + 1, Origin, Size);
		Hint = TEXT("↑↓ で項目    ←→ で増減    Esc で戻る"
		            "    ／ 帯の左半分・右半分をクリックしても増減できる"
		            "    （変更はすぐ車に反映される）");
		break;
	case EPage::Graphics:
		Layer = PaintGraphics(AllottedGeometry, OutDrawElements, Layer + 1, Origin, Size);
		Hint = TEXT("↑↓ で項目    ←→ で変更    Esc で戻る");
		break;
	case EPage::Result:
		Layer = PaintResult(AllottedGeometry, OutDrawElements, Layer + 1, Origin, Size);
		Hint = TEXT("Enter または Esc でメニューへ");
		break;
	}

	return PaintHint(AllottedGeometry, OutDrawElements, Layer, Screen, Hint);
}
