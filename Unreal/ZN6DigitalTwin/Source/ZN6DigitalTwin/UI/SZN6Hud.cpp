#include "SZN6Hud.h"

#include "Rendering/DrawElements.h"
#include "Styling/CoreStyle.h"
#include "UI/ZN6Style.h"

namespace
{
	using namespace ZN6UI;

	/** 角度から円周上の点。**0 度を真下、時計回り**にとる（計器の慣習）。 */
	FVector2f OnArc(const FVector2f& Centre, float Radius, float Fraction,
	                float StartDeg = 140.0f, float SweepDeg = 260.0f)
	{
		const float Deg = StartDeg + SweepDeg * Fraction;
		const float Rad = FMath::DegreesToRadians(Deg);
		return FVector2f(Centre.X + Radius * FMath::Sin(Rad),
		                 Centre.Y - Radius * FMath::Cos(Rad));
	}

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

	/** 文字を右揃えで置くための、おおよその幅。 */
	float ApproxTextWidth(const FString& Value, int32 FontSize)
	{
		// **正確に測らない。** MeasureService を通すと毎フレーム重くなるうえ、
		// 数字の幅はフォントによらずほぼ一定。右揃えの見た目が崩れない
		// 程度に見積もれれば足りる。
		return Value.Len() * FontSize * 0.58f;
	}
}

void SZN6Hud::Construct(const FArguments& InArgs)
{
	WhiteBrush = FCoreStyle::Get().GetBrush("WhiteBrush");
	SetCanTick(false);
	SetVisibility(EVisibility::HitTestInvisible);   // **入力を奪わない。**
}

void SZN6Hud::SetCentreline(TArray<FVector2D>&& InPointsM)
{
	CentrelineM = MoveTemp(InPointsM);
	if (CentrelineM.Num() == 0)
	{
		return;
	}

	MapMinM = MapMaxM = CentrelineM[0];
	for (const FVector2D& Point : CentrelineM)
	{
		MapMinM.X = FMath::Min(MapMinM.X, Point.X);
		MapMinM.Y = FMath::Min(MapMinM.Y, Point.Y);
		MapMaxM.X = FMath::Max(MapMaxM.X, Point.X);
		MapMaxM.Y = FMath::Max(MapMaxM.Y, Point.Y);
	}
}

int32 SZN6Hud::PaintPanel(const FGeometry& Geometry, FSlateWindowElementList& Out,
                          int32 Layer, const FVector2f& Origin, const FVector2f& Size,
                          float Opacity) const
{
	FLinearColor Fill = PanelBackground();
	Fill.A *= Opacity;
	Box(Out, Layer, Geometry, WhiteBrush, Origin, Size, Fill);

	// 細い枠。**塗りだけだと背景に溶ける。**
	FLinearColor Edge = PanelEdge();
	Edge.A *= Opacity;
	const FVector2f TopLeft = Origin;
	const FVector2f TopRight(Origin.X + Size.X, Origin.Y);
	const FVector2f BottomLeft(Origin.X, Origin.Y + Size.Y);
	const FVector2f BottomRight(Origin.X + Size.X, Origin.Y + Size.Y);
	Line(Out, Layer + 1, Geometry, TopLeft, TopRight, Edge, 1.0f);
	Line(Out, Layer + 1, Geometry, BottomLeft, BottomRight, Edge, 1.0f);
	Line(Out, Layer + 1, Geometry, TopLeft, BottomLeft, Edge, 1.0f);
	Line(Out, Layer + 1, Geometry, TopRight, BottomRight, Edge, 1.0f);

	return Layer + 2;
}

int32 SZN6Hud::PaintTachometer(const FGeometry& Geometry, FSlateWindowElementList& Out,
                               int32 Layer, const FVector2f& Centre, float Radius) const
{
	// **回転計は目盛りの列で描く。** 針より、どこまで来ているかが一目で分かる。
	constexpr int32 Ticks = 60;
	const double Span = FMath::Max(Snapshot.RedlineRpm - Snapshot.IdleRpm * 0.0, 1.0);
	const double Fraction = FMath::Clamp(Snapshot.EngineRpm / Span, 0.0, 1.15);

	// レッドラインが目盛りのどこに来るか
	const float RedlineAt = 1.0f;

	for (int32 Index = 0; Index < Ticks; ++Index)
	{
		const float At = static_cast<float>(Index) / (Ticks - 1);
		const bool bLit = At <= Fraction;
		const bool bRed = At >= RedlineAt * 0.92f;

		FLinearColor Colour = GaugeTrack();
		if (bLit)
		{
			Colour = bRed ? Danger() : (At > 0.75f ? Warn() : Accent());
		}

		// 目盛りは外周へ向かう短い線
		const float Inner = Radius * (bRed ? 0.80f : 0.84f);
		const float Outer = Radius * 1.0f;
		Line(Out, Layer, Geometry, OnArc(Centre, Inner, At), OnArc(Centre, Outer, At),
		     Colour, bRed ? 4.0f : 3.0f);
	}

	// 1000rpm ごとの数字
	const int32 Thousands = FMath::FloorToInt(static_cast<float>(Snapshot.RedlineRpm / 1000.0));
	for (int32 K = 0; K <= Thousands; ++K)
	{
		const float At = static_cast<float>(K * 1000.0 / Span);
		if (At > 1.02f)
		{
			continue;
		}
		// **中央の文字から離す。** 0.66 では速度の数字と重なっていた。
		const FVector2f At2 = OnArc(Centre, Radius * 0.80f, At);
		Text(Out, Layer + 1, Geometry, FString::FromInt(K),
		     FVector2f(At2.X - 5.0f, At2.Y - 8.0f), LabelFont(13),
		     At >= 0.92f ? Danger() : TextSecondary());
	}

	return Layer + 2;
}

int32 SZN6Hud::PaintSpeedAndGear(const FGeometry& Geometry, FSlateWindowElementList& Out,
                                 int32 Layer, const FVector2f& Centre, float Radius) const
{
	// ギア。**中央に大きく。** 走行中いちばん見る文字。
	FString GearText;
	if (Snapshot.Gear < 0)      { GearText = TEXT("R"); }
	else if (Snapshot.Gear == 0) { GearText = TEXT("N"); }
	else                         { GearText = FString::FromInt(Snapshot.Gear); }

	// **ギアと速度を縦に離す。** 重ねると読めない（実際に重なっていた）。
	const bool bNearRedline = Snapshot.EngineRpm >= Snapshot.RedlineRpm * 0.95;
	const float GearWidth = ApproxTextWidth(GearText, 62);
	Text(Out, Layer, Geometry, GearText,
	     FVector2f(Centre.X - GearWidth * 0.5f, Centre.Y - 62.0f), NumeralFont(62),
	     bNearRedline ? Danger() : TextPrimary());

	// 速度。ギアの下に、単位は右へ添える。
	const FString SpeedText = FString::Printf(TEXT("%d"),
		FMath::RoundToInt(static_cast<float>(Snapshot.SpeedKmh)));
	const float SpeedWidth = ApproxTextWidth(SpeedText, 44);
	Text(Out, Layer, Geometry, SpeedText,
	     FVector2f(Centre.X - SpeedWidth * 0.5f - 12.0f, Centre.Y + 14.0f),
	     NumeralFont(44), TextPrimary());
	Text(Out, Layer, Geometry, TEXT("km/h"),
	     FVector2f(Centre.X + SpeedWidth * 0.5f - 6.0f, Centre.Y + 38.0f),
	     LabelFont(12), TextSecondary());

	return Layer + 1;
}

int32 SZN6Hud::PaintPedals(const FGeometry& Geometry, FSlateWindowElementList& Out,
                           int32 Layer, const FVector2f& Origin) const
{
	// アクセル・ブレーキ・クラッチ・サイド。**縦棒4本。**
	// キーボードで踏み込み量が見えないと、なぜ滑ったのかが分からない。
	constexpr float BarWidth = 10.0f;
	constexpr float BarHeight = 92.0f;
	constexpr float Gap = 8.0f;

	struct FBar { double Value; FLinearColor Colour; const TCHAR* Label; };
	const FBar Bars[] = {
		{ Snapshot.Throttle,             Good(),   TEXT("T") },
		{ Snapshot.Brake,                Danger(), TEXT("B") },
		{ 1.0 - Snapshot.ClutchEngagement, Accent(), TEXT("C") },
		{ Snapshot.Handbrake,            Warn(),   TEXT("H") },
	};

	for (int32 Index = 0; Index < UE_ARRAY_COUNT(Bars); ++Index)
	{
		const float X = Origin.X + Index * (BarWidth + Gap);

		Box(Out, Layer, Geometry, WhiteBrush, FVector2f(X, Origin.Y),
		    FVector2f(BarWidth, BarHeight), GaugeTrack());

		const float Filled = BarHeight * FMath::Clamp(
			static_cast<float>(Bars[Index].Value), 0.0f, 1.0f);
		if (Filled > 0.5f)
		{
			Box(Out, Layer + 1, Geometry, WhiteBrush,
			    FVector2f(X, Origin.Y + BarHeight - Filled),
			    FVector2f(BarWidth, Filled), Bars[Index].Colour);
		}

		Text(Out, Layer + 2, Geometry, Bars[Index].Label,
		     FVector2f(X + 1.0f, Origin.Y + BarHeight + 4.0f),
		     LabelFont(10), TextFaint());
	}

	return Layer + 3;
}

int32 SZN6Hud::PaintGrip(const FGeometry& Geometry, FSlateWindowElementList& Out,
                         int32 Layer, const FVector2f& Origin) const
{
	// 4輪の限界の近さ。**車の形に並べる**ので、どの輪かが直感で分かる。
	//
	// 浮いている輪は枠だけにする。**接地していないことが見えないと、
	// 「なぜ効かないのか」が分からない。**
	constexpr float Cell = 17.0f;
	constexpr float GapX = 30.0f;
	constexpr float GapY = 26.0f;

	for (int32 Wheel = 0; Wheel < 4; ++Wheel)
	{
		const bool bFront = (Wheel < 2);
		const bool bLeft = (Wheel % 2 == 0);
		const FVector2f At(Origin.X + (bLeft ? 0.0f : Cell + GapX),
		                   Origin.Y + (bFront ? 0.0f : Cell + GapY));

		const float Used = FMath::Clamp(
			static_cast<float>(Snapshot.Utilisation[Wheel]), 0.0f, 1.0f);

		Box(Out, Layer, Geometry, WhiteBrush, At, FVector2f(Cell, Cell), GaugeTrack());

		if (!Snapshot.bContact[Wheel])
		{
			// 浮いている。**塗らずに枠だけ。**
			Line(Out, Layer + 1, Geometry, At,
			     FVector2f(At.X + Cell, At.Y + Cell), Warn(), 2.0f);
			Line(Out, Layer + 1, Geometry, FVector2f(At.X + Cell, At.Y),
			     FVector2f(At.X, At.Y + Cell), Warn(), 2.0f);
			continue;
		}

		const FLinearColor Colour = Used > 0.92f ? Danger()
		                          : Used > 0.75f ? Warn()
		                          : Accent();
		const float Filled = Cell * Used;
		if (Filled > 0.5f)
		{
			Box(Out, Layer + 1, Geometry, WhiteBrush,
			    FVector2f(At.X, At.Y + Cell - Filled), FVector2f(Cell, Filled), Colour);
		}
	}

	// 横G・前後G。数字で出す。
	Text(Out, Layer + 2, Geometry,
	     FString::Printf(TEXT("%.2f G lat"), Snapshot.LateralG),
	     FVector2f(Origin.X, Origin.Y + 2.0f * Cell + GapY + 10.0f),
	     LabelFont(11), TextSecondary());
	Text(Out, Layer + 2, Geometry,
	     FString::Printf(TEXT("%.2f G lon"), Snapshot.LongitudinalG),
	     FVector2f(Origin.X, Origin.Y + 2.0f * Cell + GapY + 26.0f),
	     LabelFont(11), TextSecondary());
	Text(Out, Layer + 2, Geometry,
	     FString::Printf(TEXT("%.1f deg slip"), Snapshot.SlipAngleDeg),
	     FVector2f(Origin.X, Origin.Y + 2.0f * Cell + GapY + 42.0f),
	     LabelFont(11),
	     FMath::Abs(Snapshot.SlipAngleDeg) > 8.0 ? Danger() : TextSecondary());

	return Layer + 3;
}

int32 SZN6Hud::PaintTiming(const FGeometry& Geometry, FSlateWindowElementList& Out,
                           int32 Layer, const FVector2f& Origin) const
{
	const FVector2f Size(268.0f, 132.0f);
	int32 Next = PaintPanel(Geometry, Out, Layer, Origin, Size);

	const float Left = Origin.X + PadM();
	float Y = Origin.Y + 12.0f;

	// 周回
	FString LapText = Snapshot.TotalLaps > 0
		? FString::Printf(TEXT("LAP %d / %d"), Snapshot.CurrentLap, Snapshot.TotalLaps)
		: FString::Printf(TEXT("LAP %d"), Snapshot.CurrentLap);
	Text(Out, Next, Geometry, LapText, FVector2f(Left, Y), LabelFont(12), TextSecondary());

	// **コース外に出た周は、その場で分かるようにする。**
	if (Snapshot.bLapInvalidated)
	{
		Text(Out, Next, Geometry, TEXT("INVALID"),
		     FVector2f(Origin.X + Size.X - 74.0f, Y), LabelFont(12), Danger());
	}

	Y += 22.0f;
	Text(Out, Next, Geometry, FormatLapTime(Snapshot.LapTimeS),
	     FVector2f(Left, Y), NumeralFont(30), TextPrimary());

	Y += 40.0f;
	Text(Out, Next, Geometry, TEXT("BEST"), FVector2f(Left, Y), LabelFont(11), TextFaint());
	Text(Out, Next, Geometry, FormatLapTime(Snapshot.BestLapS),
	     FVector2f(Left + 46.0f, Y - 2.0f), LabelFont(15),
	     Snapshot.BestLapS > 0.0 ? Accent() : TextFaint());

	Y += 22.0f;
	// ベストとの差。**ベストが無いうちは出さない。**
	if (Snapshot.BestLapS > 0.0 && Snapshot.LapTimeS > 0.0)
	{
		const double Delta = Snapshot.LapTimeS - Snapshot.BestLapS;
		Text(Out, Next, Geometry, TEXT("DELTA"), FVector2f(Left, Y),
		     LabelFont(11), TextFaint());
		Text(Out, Next, Geometry, FormatDelta(Delta), FVector2f(Left + 46.0f, Y - 2.0f),
		     LabelFont(15), Delta <= 0.0 ? Good() : Danger());
	}

	// 区間。**今どこにいるかを3つの帯で。**
	const float BarY = Origin.Y + Size.Y - 10.0f;
	const float BarWidth = (Size.X - 2.0f * PadM() - 8.0f) / 3.0f;
	for (int32 Sector = 0; Sector < 3; ++Sector)
	{
		const float X = Left + Sector * (BarWidth + 4.0f);
		const bool bDone = Sector < Snapshot.Sector;
		const bool bHere = Sector == Snapshot.Sector;
		Box(Out, Next + 1, Geometry, WhiteBrush, FVector2f(X, BarY),
		    FVector2f(BarWidth, 3.0f),
		    bHere ? Accent() : (bDone ? AccentDim() : GaugeTrack()));
	}

	return Next + 2;
}

int32 SZN6Hud::PaintMiniMap(const FGeometry& Geometry, FSlateWindowElementList& Out,
                            int32 Layer, const FVector2f& Origin,
                            const FVector2f& Size) const
{
	int32 Next = PaintPanel(Geometry, Out, Layer, Origin, Size);

	if (CentrelineM.Num() < 2)
	{
		Text(Out, Next, Geometry, TEXT("NO TRACK DATA"),
		     FVector2f(Origin.X + PadM(), Origin.Y + Size.Y * 0.5f - 6.0f),
		     LabelFont(11), TextFaint());
		return Next + 1;
	}

	// 世界座標 -> パネル内。**縦横で同じ倍率**にする。
	// 別々にすると、コースの形が引き伸ばされて別のコースに見える。
	const FVector2D SpanM = MapMaxM - MapMinM;
	const float Inset = PadM();
	const float Usable = FMath::Min(Size.X, Size.Y) - 2.0f * Inset;
	const double LargestM = FMath::Max(FMath::Max(SpanM.X, SpanM.Y), 1.0);
	const float Scale = Usable / static_cast<float>(LargestM);

	// 中央に寄せる
	const FVector2f MapSize(static_cast<float>(SpanM.X) * Scale,
	                        static_cast<float>(SpanM.Y) * Scale);
	const FVector2f MapOrigin(Origin.X + (Size.X - MapSize.X) * 0.5f,
	                          Origin.Y + (Size.Y - MapSize.Y) * 0.5f);

	auto ToScreen = [&](const FVector2D& WorldM)
	{
		// **物理の y は左が正、画面の y は下が正。** 符号を反転する。
		// 忘れるとコースが上下に反転して、左コーナーが右に見える。
		return FVector2f(
			MapOrigin.X + static_cast<float>(WorldM.X - MapMinM.X) * Scale,
			MapOrigin.Y + MapSize.Y - static_cast<float>(WorldM.Y - MapMinM.Y) * Scale);
	};

	TArray<FVector2f> Points;
	Points.Reserve(CentrelineM.Num() + 1);
	for (const FVector2D& Point : CentrelineM)
	{
		Points.Add(ToScreen(Point));
	}
	// 閉じる。**Points.Add(Points[0]) と書かないこと。**
	// TArray は「自分の要素を自分へ足す」のを assert で止める（再確保で
	// 参照が無効になりうるため）。実際にエディタごと落ちた。
	const FVector2f First = Points[0];
	Points.Add(First);

	// **薄すぎると何も見えない。** AccentDim では路面と同化していた。
	FSlateDrawElement::MakeLines(Out, Next, Geometry.ToPaintGeometry(), Points,
	                             ESlateDrawEffect::None, Accent(), true, 2.5f);

	// 自車。**向きも出す。** 点だけだとどちらを向いているか分からない。
	const FVector2f Car = ToScreen(FVector2D(Snapshot.CarXM, Snapshot.CarYM));
	const float HeadingRad = static_cast<float>(Snapshot.CarHeadingRad);
	const FVector2f Nose(Car.X + FMath::Cos(HeadingRad) * 9.0f,
	                     Car.Y - FMath::Sin(HeadingRad) * 9.0f);

	// **自車は白で大きく。** コースの線と同じ色だと、どちらが車か分からない。
	const FLinearColor CarColour = Snapshot.bOffTrack ? Warn() : TextPrimary();
	Line(Out, Next + 1, Geometry, Car, Nose, CarColour, 3.0f);
	Box(Out, Next + 1, Geometry, WhiteBrush,
	    FVector2f(Car.X - 4.0f, Car.Y - 4.0f), FVector2f(8.0f, 8.0f), CarColour);

	// スタート/ゴール線
	const FVector2f Start = ToScreen(CentrelineM[0]);
	Box(Out, Next + 1, Geometry, WhiteBrush,
	    FVector2f(Start.X - 4.0f, Start.Y - 1.5f), FVector2f(8.0f, 3.0f), TextPrimary());

	return Next + 2;
}

int32 SZN6Hud::PaintCountdown(const FGeometry& Geometry, FSlateWindowElementList& Out,
                              int32 Layer, const FVector2f& ScreenSize) const
{
	if (Snapshot.Phase != ZN6::ERacePhase::Countdown
	    && Snapshot.Phase != ZN6::ERacePhase::Racing)
	{
		return Layer;
	}

	// 「3・2・1」と、スタート直後の「GO」。
	// **GO は少しだけ残す。** 一瞬で消えると見えない。
	FString Label;
	FLinearColor Colour = TextPrimary();
	float Alpha = 1.0f;

	if (Snapshot.Phase == ZN6::ERacePhase::Countdown)
	{
		Label = FString::FromInt(FMath::Max(Snapshot.CountdownNumber, 1));
		Colour = Snapshot.CountdownNumber <= 1 ? Warn() : TextPrimary();
		// 1秒の中でだんだん薄くする（数字が切り替わるたびに脈打つ）
		const float Within = static_cast<float>(
			FMath::Frac(FMath::Max(Snapshot.CountdownRemainingS, 0.0)));
		Alpha = 0.35f + 0.65f * Within;
	}
	else if (Snapshot.SessionTimeS < 1.2)
	{
		Label = TEXT("GO");
		Colour = Good();
		Alpha = FMath::Clamp(1.0f - static_cast<float>(Snapshot.SessionTimeS) / 1.2f,
		                     0.0f, 1.0f);
	}
	else
	{
		return Layer;
	}

	Colour.A = Alpha;
	const float Width = ApproxTextWidth(Label, 132);
	Text(Out, Layer, Geometry, Label,
	     FVector2f(ScreenSize.X * 0.5f - Width * 0.5f, ScreenSize.Y * 0.30f),
	     NumeralFont(132), Colour);

	return Layer + 1;
}

int32 SZN6Hud::PaintConfidence(const FGeometry& Geometry, FSlateWindowElementList& Out,
                               int32 Layer, const FVector2f& ScreenSize) const
{
	// **この数字がどれくらい確かかを画面にも出す。**
	//
	// 隠すと、出典のある値と仮定値が同じ顔で並ぶ（Docs/AGENT_TOPOLOGY.md §3）。
	// 実測比較に使えない状態のときは、はっきりそう書く。
	const FString Line1 = FString::Printf(TEXT("model confidence %.2f"),
	                                      Snapshot.Confidence);
	const FString Line2 = Snapshot.bValidatable
		? TEXT("")
		: TEXT("assumed values in use - not comparable to measurements");

	const float Y = ScreenSize.Y - 34.0f;
	Text(Out, Layer, Geometry, Line1, FVector2f(PadL(), Y), LabelFont(10), TextFaint());
	if (!Line2.IsEmpty())
	{
		Text(Out, Layer, Geometry, Line2, FVector2f(PadL(), Y + 13.0f),
		     LabelFont(10), Warn());
	}
	return Layer + 1;
}

int32 SZN6Hud::OnPaint(const FPaintArgs& Args, const FGeometry& AllottedGeometry,
                       const FSlateRect& MyCullingRect,
                       FSlateWindowElementList& OutDrawElements, int32 LayerId,
                       const FWidgetStyle& InWidgetStyle, bool bParentEnabled) const
{
	if (WhiteBrush == nullptr)
	{
		return LayerId;
	}

	const FVector2f Screen = FVector2f(AllottedGeometry.GetLocalSize());
	int32 Layer = LayerId;

	// メニューでは HUD を出さない。**メニューの絵が計器で汚れる。**
	if (Snapshot.Phase == ZN6::ERacePhase::Menu)
	{
		return Layer;
	}

	// --- 左下: 回転計・ギア・速度 ---
	//
	// **背景パネルを必ず敷く。** 敷かずに線と文字だけ描いていたら、
	// 昼の明るい路面と空で計器が完全に埋もれていた。撮って初めて見えた。
	const float Radius = 118.0f;
	const FVector2f DialCentre(PadL() + Radius + 18.0f, Screen.Y - Radius - 56.0f);
	Layer = PaintPanel(AllottedGeometry, OutDrawElements, Layer,
	                   FVector2f(PadL() * 0.5f, DialCentre.Y - Radius - PadM()),
	                   FVector2f(Radius * 2.0f + 128.0f, Radius + 190.0f), 1.15f);
	Layer = PaintTachometer(AllottedGeometry, OutDrawElements, Layer, DialCentre, Radius);
	Layer = PaintSpeedAndGear(AllottedGeometry, OutDrawElements, Layer, DialCentre, Radius);

	// --- 左下の右側: ペダル ---
	Layer = PaintPedals(AllottedGeometry, OutDrawElements, Layer,
	                    FVector2f(DialCentre.X + Radius + 26.0f, Screen.Y - 168.0f));

	// --- 右下: 4輪のグリップ ---
	Layer = PaintPanel(AllottedGeometry, OutDrawElements, Layer,
	                   FVector2f(Screen.X - 240.0f, Screen.Y - 230.0f),
	                   FVector2f(240.0f - PadL() * 0.5f, 230.0f - PadL() * 0.5f), 1.15f);
	Layer = PaintGrip(AllottedGeometry, OutDrawElements, Layer,
	                  FVector2f(Screen.X - 220.0f, Screen.Y - 210.0f));

	// --- 右上: タイム ---
	Layer = PaintTiming(AllottedGeometry, OutDrawElements, Layer,
	                    FVector2f(Screen.X - 268.0f - PadL(), PadL()));

	// --- 左上: ミニマップ ---
	Layer = PaintMiniMap(AllottedGeometry, OutDrawElements, Layer,
	                     FVector2f(PadL(), PadL()), FVector2f(210.0f, 210.0f));

	// --- 中央: カウントダウン ---
	Layer = PaintCountdown(AllottedGeometry, OutDrawElements, Layer, Screen);

	// --- 下端: 信頼度 ---
	Layer = PaintConfidence(AllottedGeometry, OutDrawElements, Layer, Screen);

	return Layer;
}
