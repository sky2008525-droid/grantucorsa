// 走行中の画面。
//
// ---------------------------------------------------------------------------
// 何を参考にしたか
//
// 実在のレーシングゲーム／シムの HUD で「一般的な作法」として定着している
// ものだけを取り入れた。**特定の作品の画面をそのまま写していない。**
//
//   [1] ACC 用オーバーレイ集の解説。中央に 270 度のタコメーターとギア・速度、
//       左に 5 秒ぶんのステア入力トレース、右にアクセル／ブレーキ／クラッチの
//       棒と G のダイヤル。シフトランプは「緑 → 橙 → 赤と満ちて、
//       変速点で点滅する」。デルタは「ベスト周に対する連続したグラフ」。
//       https://track-impulse.com/acc-overlays
//   [2] Assetto Corsa の Sidekick HUD の解説。デルタは進捗バーで、
//       「速ければ緑が右へ、遅ければ赤が左へ伸びる」。シフトランプは
//       変速点で黄、吹け切りで赤。区間タイムと入力表示を持つ。
//       https://www.nutrimatic.cc/assetto-corsa/sidekick-v1-14/
//   [3] F1 の計時画面の色の意味。紫 = その区間の最速、緑 = 自己ベスト、
//       黄 = 自己ベストに届かなかった。
//       https://racingnews365.com/what-sectors-are-f1-and-what-do-the-different-colours-mean
//   [4] Assetto Corsa 用ペダルテレメトリの実装。アクセル・ブレーキ・
//       クラッチ・サイド・ステアを折れ線で流す。
//       https://github.com/serg-and/ac-pedal-telemetry-overlay
//   [5] SimHub 用入力テレメトリの説明。ブレーキが赤、アクセルが緑の折れ線。
//       https://www.overtake.gg/downloads/ace-input-telemetry-simhub-overlay.81702/
//
//   読めなかったもの: Gran Turismo 7 の公式マニュアルの走行画面の頁
//   （https://www.gran-turismo.com/us/gt7/manual/race/02）。本文が
//   JavaScript で組み立てられており、取得できたのは表題だけだった。
//   **読めていないので「GT7 はこう置いている」とは書かない。**
//
// ---------------------------------------------------------------------------
// 前の版から何を変えたか
//
//   - 回転計を円弧から**横棒**にした。細い目盛りの円弧は、走りながら
//     見ると「どこまで来たか」が読めない。棒なら端（レッドゾーン）までの
//     残りが長さで分かる
//   - **シフトランプ（LED の列）を足した。** レッドゾーン手前から順に
//     点いて、レブリミットで点滅する
//   - **速度をいちばん大きい文字にした。**
//   - ベスト周との**差を出す**ようにした。周回の進捗ごとに通過時刻を
//     覚えておき、ベスト周のそれと引く。数字と、伸びる棒の両方で出す
//   - **区間タイムを出す。** 色は [3] の作法を1台用に読み替えた
//   - 入力を**流れるトレース**にした。細い棒4本では「いつ緩めたか」が
//     見えない
//   - ミニマップの自車を**三角**にして向きを出した
//
// **数値は作らない。** タイヤ温度・燃料・順位は `FHudSnapshot` に無いので
// 出さない（憲法ルール1）。シフトランプの点灯開始点だけは物理由来の値では
// ないので、**演出であることをコメントに書く**（憲法ルール18）。

#include "SZN6Hud.h"

#include "Rendering/DrawElements.h"
#include "Styling/CoreStyle.h"
#include "UI/ZN6Style.h"

namespace
{
	using namespace ZN6UI;

	// --- このファイルだけで使う色 ------------------------------------------
	//
	// **本来は `ZN6Style.h` に置くべき色。** 今回の作業範囲がこのファイルに
	// 限られているため、暫定でここに置いている。移すこと。

	/** 区間最速。F1 の計時画面の紫（出典 [3]）。 */
	FLinearColor SectorBest() { return FLinearColor(0.66f, 0.42f, 0.98f, 1.0f); }
	/** レブリミットの点滅。**赤とは別の色**にして「限界」と「切った」を分ける。 */
	FLinearColor ShiftFlash() { return FLinearColor(0.55f, 0.80f, 1.00f, 1.0f); }

	// --- 描画の下請け -------------------------------------------------------

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

	void Polyline(FSlateWindowElementList& Out, int32 Layer, const FGeometry& Geometry,
	              const TArray<FVector2f>& Points, const FLinearColor& Colour,
	              float Thickness)
	{
		if (Points.Num() < 2)
		{
			return;
		}
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
		// **幅か高さが 0 以下の箱を積まない。** 極端に小さい画面でそうなる。
		if (Size.X <= 0.0f || Size.Y <= 0.0f)
		{
			return;
		}
		FSlateDrawElement::MakeBox(
			Out, Layer,
			Geometry.ToPaintGeometry(Size, FSlateLayoutTransform(FVector2f(Origin))),
			Brush, ESlateDrawEffect::None, Colour);
	}

	/** 見出し用（Regular）の文字を右揃え・中央揃えで置くための、おおよその幅。 */
	float ApproxTextWidth(const FString& Value, int32 FontSize)
	{
		// **正確に測らない。** MeasureService を通すと毎フレーム重くなるうえ、
		// 右揃えの見た目が崩れない程度に見積もれれば足りる。
		return Value.Len() * FontSize * 0.58f;
	}

	/**
	 * 大きい数字（Bold）用の、おおよその幅。
	 *
	 * **Regular と同じ係数で見積もってはいけない。** 0.58 で右揃えしたら、
	 * 速度の「55」が「km/h」に食い込み、デルタの数字がパネルの枠から
	 * はみ出した（実際に撮って分かった）。太い数字はこれだけ広い。
	 *
	 * 多めに見積もる。**多いぶんには左へ寄るだけで、ぶつからない。**
	 */
	float ApproxNumeralWidth(const FString& Value, int32 FontSize)
	{
		return Value.Len() * FontSize * 0.78f;
	}

	/** 倍率を掛けた文字の大きさ。**0 を作らない。** */
	int32 Pt(float DesignSize, float Scale)
	{
		return FMath::Max(1, FMath::RoundToInt(DesignSize * Scale));
	}

	FVector2f RectSize(const FSlateRect& Rect)
	{
		return FVector2f(FMath::Max(0.0f, Rect.Right - Rect.Left),
		                 FMath::Max(0.0f, Rect.Bottom - Rect.Top));
	}

	/** 有限でない値を弾く。**NaN も無限大もここで止める。** */
	bool IsFiniteValue(double Value)
	{
		// `FMath::IsFinite` の double 版に頼らず比較で見る。
		// NaN はどちらの比較も false になるので、これ1つで両方を捕まえる。
		return Value > -TNumericLimits<double>::Max()
		    && Value < TNumericLimits<double>::Max();
	}

	/** 有限でなければ差し替え、差し替えたことを記録する。 */
	double Sane(double Value, double Fallback, bool& bOutInvalid)
	{
		if (!IsFiniteValue(Value))
		{
			bOutInvalid = true;
			return Fallback;
		}
		return Value;
	}

	/** 秒を `M:SS.mmm` に。ZN6Style の書式をそのまま使う。 */
	FString LapText(double Seconds) { return FormatLapTime(Seconds); }

	/** 区間タイムは分を出さない。**3桁の秒のほうが並べて読みやすい。** */
	FString SectorText(double Seconds)
	{
		if (Seconds <= 0.0)
		{
			return TEXT("--.---");
		}
		if (Seconds >= 100.0)
		{
			return FString::Printf(TEXT("%.2f"), Seconds);
		}
		return FString::Printf(TEXT("%.3f"), Seconds);
	}
}

// ---------------------------------------------------------------------------
// 区画
// ---------------------------------------------------------------------------

TArray<FSlateRect> SZN6Hud::FLayout::All() const
{
	return { Map, Status, Timing, Cluster, Inputs, Grip, Countdown, Footer };
}

const TCHAR* SZN6Hud::FLayout::NameOf(int32 Index)
{
	static const TCHAR* Names[] = {
		TEXT("Map"), TEXT("Status"), TEXT("Timing"), TEXT("Cluster"),
		TEXT("Inputs"), TEXT("Grip"), TEXT("Countdown"), TEXT("Footer")
	};
	return (Index >= 0 && Index < static_cast<int32>(UE_ARRAY_COUNT(Names)))
		? Names[Index] : TEXT("?");
}

SZN6Hud::FLayout SZN6Hud::ComputeLayout(const FVector2f& ScreenSize)
{
	// 基準は 1920x1080。**そこで重なっていなければ、どの画面でも重ならない。**
	//
	// 縮尺は縦横の小さいほうに合わせる（等倍）。左のものは左端から、
	// 右のものは右端から、中央のものは中心から測る。こうすると、画面が
	// 基準より横長でも縦長でも**隙間が広がるだけ**で、詰まることがない。
	constexpr float BaseW = 1920.0f;
	constexpr float BaseH = 1080.0f;

	const float W = FMath::Max(ScreenSize.X, 1.0f);
	const float H = FMath::Max(ScreenSize.Y, 1.0f);
	const float S = FMath::Min(FMath::Min(W / BaseW, H / BaseH), 2.0f);

	auto FromLeft   = [&](float X) { return X * S; };
	auto FromRight  = [&](float X) { return W - (BaseW - X) * S; };
	auto FromCentre = [&](float X) { return W * 0.5f + (X - BaseW * 0.5f) * S; };
	auto FromTop    = [&](float Y) { return Y * S; };
	auto FromBottom = [&](float Y) { return H - (BaseH - Y) * S; };

	FLayout Layout;
	Layout.Scale = S;

	// 左上: ミニマップ
	Layout.Map = FSlateRect(FromLeft(26.0f), FromTop(26.0f),
	                        FromLeft(262.0f), FromTop(262.0f));
	// 左上のすぐ下: 信頼度。**計器から離すが、隠さない。**
	Layout.Footer = FSlateRect(FromLeft(26.0f), FromTop(276.0f),
	                           FromLeft(350.0f), FromTop(322.0f));
	// 上中央: 警告
	Layout.Status = FSlateRect(FromCentre(700.0f), FromTop(22.0f),
	                           FromCentre(1220.0f), FromTop(84.0f));
	// 右上: タイム
	Layout.Timing = FSlateRect(FromRight(1554.0f), FromTop(26.0f),
	                           FromRight(1894.0f), FromTop(346.0f));
	// 下中央: 計器。**いちばん見るものを、いちばん見る場所に。**
	Layout.Cluster = FSlateRect(FromCentre(580.0f), FromBottom(840.0f),
	                            FromCentre(1340.0f), FromBottom(1054.0f));
	// 左下: 入力
	Layout.Inputs = FSlateRect(FromLeft(26.0f), FromBottom(840.0f),
	                           FromLeft(346.0f), FromBottom(1054.0f));
	// 右下: 4輪と G
	Layout.Grip = FSlateRect(FromRight(1574.0f), FromBottom(840.0f),
	                         FromRight(1894.0f), FromBottom(1054.0f));
	// 中央: カウントダウン。**高さは画面の割合で置く。**
	// 上下に短い画面でも、上の警告と下の計器の間に必ず収まる。
	Layout.Countdown = FSlateRect(FromCentre(800.0f), H * 0.24f,
	                              FromCentre(1120.0f), H * 0.24f + 190.0f * S);

	return Layout;
}

// ---------------------------------------------------------------------------
// 回転計の目盛り
// ---------------------------------------------------------------------------

double SZN6Hud::FTachScale::Fraction(double Rpm) const
{
	if (!IsValid())
	{
		return 0.0;
	}
	return FMath::Clamp(Rpm / ScaleMaxRpm, 0.0, 1.0);
}

SZN6Hud::FTachScale SZN6Hud::MakeTachScale(double RedlineRpm)
{
	FTachScale Scale;
	if (!IsFiniteValue(RedlineRpm) || RedlineRpm <= 0.0)
	{
		// **既定値で埋めない。** 埋めると、値が来ていないことが画面から
		// 消える（憲法ルール6）。無効な目盛りを返し、呼ぶ側がそう描く。
		return Scale;
	}

	Scale.RedlineRpm = RedlineRpm;

	// 目盛りの右端は 1000rpm の切りのいいところ。**redline より必ず上。**
	// レッドゾーンに幅が無いと、境界が線1本になって見えない。
	const double Wanted = RedlineRpm * 1.02;
	Scale.ScaleMaxRpm = FMath::CeilToDouble(Wanted / 1000.0) * 1000.0;
	if (Scale.ScaleMaxRpm <= RedlineRpm)
	{
		Scale.ScaleMaxRpm = RedlineRpm + 1000.0;
	}

	// シフトランプが点き始める回転数（`FTachScale::ShiftStartRpm`）。
	//
	// **これは演出であって、最適変速点の計算ではない**（憲法ルール18）。
	// 本当の最適変速点はトルクカーブと各段のギア比から出るもので、
	// `FHudSnapshot` にその値は無い。無い値を計算したふりをしない。
	constexpr double ShiftLightSpan = 0.90;
	Scale.ShiftStartRpm = RedlineRpm * ShiftLightSpan;

	return Scale;
}

bool SZN6Hud::IsShiftLightLit(const FTachScale& Scale, double Rpm, int32 Index)
{
	if (!Scale.IsValid() || Index < 0 || Index >= ShiftLightCount
	    || !IsFiniteValue(Rpm))
	{
		return false;
	}

	// **点き始めから redline までを等分する。** 最後の1つが点くのが
	// ちょうど redline。
	const double Span = FMath::Max(Scale.RedlineRpm - Scale.ShiftStartRpm, 1.0);
	const double Threshold = Scale.ShiftStartRpm
		+ Span * (Index + 1) / static_cast<double>(ShiftLightCount);
	return Rpm >= Threshold;
}

// ---------------------------------------------------------------------------
// 組み立て
// ---------------------------------------------------------------------------

void SZN6Hud::Construct(const FArguments& InArgs)
{
	WhiteBrush = FCoreStyle::Get().GetBrush("WhiteBrush");
	SetCanTick(false);
	SetVisibility(EVisibility::HitTestInvisible);   // **入力を奪わない。**

	CurrentTrace.Init(-1.0f, DeltaBins);
	ReferenceTrace.Init(-1.0f, DeltaBins);
}

void SZN6Hud::ResetDerived()
{
	CurrentTrace.Init(-1.0f, DeltaBins);
	ReferenceTrace.Init(-1.0f, DeltaBins);
	CurrentFilledBins = 0;
	bDeltaReferenceReady = false;
	bDeltaValidNow = false;
	DeltaS = 0.0;

	for (int32 Index = 0; Index < 3; ++Index)
	{
		CurrentSectorS[Index] = 0.0;
		SessionBestSectorS[Index] = 0.0;
		ReferenceSectorS[Index] = 0.0;
		SectorFlag[Index] = 0;
	}
	SectorOpenedAtS = 0.0;
	PrevSector = 0;
	bSectorsFromCompletedLap = false;

	TraceCount = 0;
	TraceHead = 0;
	LastTraceTimeS = -1.0;

	SeenLapCount = 0;
	SeenLapNumber = 0;
}

double SZN6Hud::CurrentSectorTimeS(int32 Sector) const
{
	return (Sector >= 0 && Sector < 3) ? CurrentSectorS[Sector] : 0.0;
}

void SZN6Hud::SetSnapshot(const ZN6::FHudSnapshot& InSnapshot)
{
	// **有限でない値をここで止める。** 物理側の異常はありうるが、
	// それで画面が壊れると異常そのものを見る手立てが無くなる。
	// **ただし黙って直さない。** 直したことを `bInvalidInput` に残し、
	// 画面にも出す（憲法ルール6）。
	Snapshot = InSnapshot;
	bInvalidInput = false;

	bool& Bad = bInvalidInput;
	Snapshot.SpeedKmh   = Sane(Snapshot.SpeedKmh, 0.0, Bad);
	Snapshot.EngineRpm  = FMath::Clamp(Sane(Snapshot.EngineRpm, 0.0, Bad), 0.0, 1.0e5);
	Snapshot.IdleRpm    = Sane(Snapshot.IdleRpm, 0.0, Bad);
	Snapshot.Throttle   = FMath::Clamp(Sane(Snapshot.Throttle, 0.0, Bad), 0.0, 1.0);
	Snapshot.Brake      = FMath::Clamp(Sane(Snapshot.Brake, 0.0, Bad), 0.0, 1.0);
	Snapshot.ClutchEngagement =
		FMath::Clamp(Sane(Snapshot.ClutchEngagement, 1.0, Bad), 0.0, 1.0);
	Snapshot.Handbrake  = FMath::Clamp(Sane(Snapshot.Handbrake, 0.0, Bad), 0.0, 1.0);
	Snapshot.SteerRad   = Sane(Snapshot.SteerRad, 0.0, Bad);
	Snapshot.MaxSteerRad = FMath::Max(Sane(Snapshot.MaxSteerRad, 0.6, Bad), 1.0e-3);
	Snapshot.SlipAngleDeg = Sane(Snapshot.SlipAngleDeg, 0.0, Bad);
	Snapshot.LateralG   = Sane(Snapshot.LateralG, 0.0, Bad);
	Snapshot.LongitudinalG = Sane(Snapshot.LongitudinalG, 0.0, Bad);
	Snapshot.CountdownRemainingS = Sane(Snapshot.CountdownRemainingS, 0.0, Bad);
	Snapshot.LapTimeS   = FMath::Max(Sane(Snapshot.LapTimeS, 0.0, Bad), 0.0);
	Snapshot.BestLapS   = FMath::Max(Sane(Snapshot.BestLapS, 0.0, Bad), 0.0);
	Snapshot.SessionTimeS = FMath::Max(Sane(Snapshot.SessionTimeS, 0.0, Bad), 0.0);
	Snapshot.LapProgress = FMath::Clamp(Sane(Snapshot.LapProgress, 0.0, Bad), 0.0, 1.0);
	Snapshot.CarXM      = Sane(Snapshot.CarXM, 0.0, Bad);
	Snapshot.CarYM      = Sane(Snapshot.CarYM, 0.0, Bad);
	Snapshot.CarHeadingRad = Sane(Snapshot.CarHeadingRad, 0.0, Bad);
	Snapshot.Confidence = FMath::Clamp(Sane(Snapshot.Confidence, 0.0, Bad), 0.0, 1.0);

	// **redline は差し替えない。** 差し替えると HUD が自前の回転数を
	// 持つことになり、`vehicle.json` を直しても画面が古いままになる。
	// 来ていなければ「来ていない」と描く。
	if (!IsFiniteValue(Snapshot.RedlineRpm) || Snapshot.RedlineRpm <= 0.0)
	{
		if (Snapshot.RedlineRpm != 0.0)
		{
			bInvalidInput = true;
		}
		Snapshot.RedlineRpm = 0.0;
	}

	// 表示上のギアは -1..12 の外に来ない約束。外れたら知らせる。
	if (Snapshot.Gear < -1 || Snapshot.Gear > 12)
	{
		bInvalidInput = true;
		Snapshot.Gear = FMath::Clamp(Snapshot.Gear, -1, 12);
	}

	// 区間番号は 0..2。**配列の添字に使うので、外れたら直す。**
	if (Snapshot.Sector < 0 || Snapshot.Sector > 2)
	{
		bInvalidInput = true;
		Snapshot.Sector = FMath::Clamp(Snapshot.Sector, 0, 2);
	}

	for (int32 Wheel = 0; Wheel < 4; ++Wheel)
	{
		Snapshot.Utilisation[Wheel] =
			FMath::Clamp(Sane(Snapshot.Utilisation[Wheel], 0.0, Bad), 0.0, 4.0);
	}

	for (ZN6::FLapRecord& Record : Snapshot.Laps)
	{
		Record.TimeS = FMath::Max(Sane(Record.TimeS, 0.0, Bad), 0.0);
		for (int32 Index = 0; Index < 3; ++Index)
		{
			Record.SectorS[Index] = FMath::Max(Sane(Record.SectorS[Index], 0.0, Bad), 0.0);
		}
	}

	PushInputTrace(Snapshot);
	UpdateTiming(Snapshot);
}

void SZN6Hud::PushInputTrace(const ZN6::FHudSnapshot& In)
{
	// **時間で刻む。** フレームごとに1点入れると、重い日と軽い日で
	// トレースの長さ（何秒ぶんか）が変わってしまう。
	constexpr double IntervalS = TraceWindowS / static_cast<double>(TraceSamples);

	if (In.SessionTimeS + KINDA_SMALL_NUMBER < LastTraceTimeS)
	{
		// 時計が巻き戻った = 別のセッション
		TraceCount = 0;
		TraceHead = 0;
		LastTraceTimeS = -1.0;
	}

	if (LastTraceTimeS >= 0.0 && In.SessionTimeS - LastTraceTimeS < IntervalS)
	{
		return;
	}
	LastTraceTimeS = In.SessionTimeS;

	TraceThrottle[TraceHead] = static_cast<float>(In.Throttle);
	TraceBrake[TraceHead] = static_cast<float>(In.Brake);
	TraceSteer[TraceHead] = FMath::Clamp(
		static_cast<float>(In.SteerRad / In.MaxSteerRad), -1.0f, 1.0f);

	TraceHead = (TraceHead + 1) % TraceSamples;
	TraceCount = FMath::Min(TraceCount + 1, TraceSamples);
}

void SZN6Hud::UpdateTiming(const ZN6::FHudSnapshot& In)
{
	// --- セッションが変わったら忘れる ---
	if (In.Phase == ZN6::ERacePhase::Menu
	    || In.Laps.Num() < SeenLapCount
	    || In.CurrentLap < SeenLapNumber)
	{
		ResetDerived();
	}

	// --- 周回が閉じた ---
	//
	// **区間タイムは記録そのものを使う。** 自分で刻んだ値と、
	// `FRaceDirector` が残した値が食い違うと、どちらが本当か分からなくなる。
	if (In.Laps.Num() > SeenLapCount && In.Laps.Num() > 0)
	{
		const ZN6::FLapRecord& Done = In.Laps.Last();

		for (int32 Index = 0; Index < 3; ++Index)
		{
			const double Time = Done.SectorS[Index];
			if (Time > 0.0)
			{
				// 色は「そのとき何位だったか」で決めるので、
				// **セッション最速を更新する前に判定する。**
				if (SessionBestSectorS[Index] <= 0.0 || Time < SessionBestSectorS[Index])
				{
					SectorFlag[Index] = 1;
					SessionBestSectorS[Index] = Time;
				}
				else if (ReferenceSectorS[Index] > 0.0 && Time < ReferenceSectorS[Index])
				{
					SectorFlag[Index] = 2;
				}
				else
				{
					SectorFlag[Index] = 3;
				}
			}
			CurrentSectorS[Index] = Time;
		}

		// **ベスト周を差の基準にする。** コース外に出た周は
		// `bBest` が付かないので、基準にもならない。
		if (Done.bBest)
		{
			// 終端のビンは、ゴール線の手前で周が閉じるぶん埋まらない。
			// **周回タイムそのもので埋める。** これは記録した値であって
			// 補外ではない。
			for (int32 Index = 0; Index < DeltaBins; ++Index)
			{
				if (CurrentTrace[Index] < 0.0f)
				{
					CurrentTrace[Index] = (Index < CurrentFilledBins)
						? 0.0f : static_cast<float>(Done.TimeS);
				}
			}
			// 1周のうち大半を記録できていなければ基準にしない。
			if (CurrentFilledBins > DeltaBins / 2)
			{
				ReferenceTrace = CurrentTrace;
				bDeltaReferenceReady = true;
				for (int32 Index = 0; Index < 3; ++Index)
				{
					ReferenceSectorS[Index] = Done.SectorS[Index];
				}
			}
		}

		CurrentTrace.Init(-1.0f, DeltaBins);
		CurrentFilledBins = 0;
		SectorOpenedAtS = 0.0;
		PrevSector = In.Sector;
		bSectorsFromCompletedLap = true;
	}

	// --- 区間が閉じた（走行中）---
	if (In.Sector != PrevSector)
	{
		// **前へ進んだときだけ刻む。** 逆走で区間タイムが出ないように。
		if (In.Sector == PrevSector + 1 && PrevSector >= 0 && PrevSector < 3)
		{
			if (bSectorsFromCompletedLap)
			{
				// 前の周の表示を残していた。新しい周の値に入れ替える。
				for (int32 Index = 0; Index < 3; ++Index)
				{
					CurrentSectorS[Index] = 0.0;
					SectorFlag[Index] = 0;
				}
				bSectorsFromCompletedLap = false;
			}

			const double Time = In.LapTimeS - SectorOpenedAtS;
			if (Time > 0.0)
			{
				if (SessionBestSectorS[PrevSector] <= 0.0
				    || Time < SessionBestSectorS[PrevSector])
				{
					SectorFlag[PrevSector] = 1;
					SessionBestSectorS[PrevSector] = Time;
				}
				else if (ReferenceSectorS[PrevSector] > 0.0
				         && Time < ReferenceSectorS[PrevSector])
				{
					SectorFlag[PrevSector] = 2;
				}
				else
				{
					SectorFlag[PrevSector] = 3;
				}
				CurrentSectorS[PrevSector] = Time;
			}
			SectorOpenedAtS = In.LapTimeS;
		}
		PrevSector = In.Sector;
	}

	// --- 今の周の通過時刻を覚える ---
	const int32 Bin = FMath::Clamp(
		FMath::FloorToInt32(static_cast<float>(In.LapProgress) * (DeltaBins - 1)),
		0, DeltaBins - 1);
	if (In.Phase == ZN6::ERacePhase::Racing)
	{
		for (int32 Index = CurrentFilledBins; Index <= Bin; ++Index)
		{
			CurrentTrace[Index] = static_cast<float>(In.LapTimeS);
		}
		CurrentFilledBins = FMath::Max(CurrentFilledBins, Bin + 1);
	}

	// --- ベスト周との差 ---
	//
	// 「同じ地点をベスト周は何秒で通ったか」との引き算。
	// **推測ではなく、記録した値の差。**
	bDeltaValidNow = false;
	DeltaS = 0.0;
	if (bDeltaReferenceReady && In.Phase == ZN6::ERacePhase::Racing)
	{
		const float X = static_cast<float>(In.LapProgress) * (DeltaBins - 1);
		const int32 Low = FMath::Clamp(FMath::FloorToInt32(X), 0, DeltaBins - 1);
		const int32 High = FMath::Min(Low + 1, DeltaBins - 1);
		const float A = ReferenceTrace[Low];
		const float B = ReferenceTrace[High];
		if (A >= 0.0f && B >= 0.0f)
		{
			const float T = FMath::Clamp(X - Low, 0.0f, 1.0f);
			const double Reference = FMath::Lerp(A, B, T);
			DeltaS = In.LapTimeS - Reference;
			bDeltaValidNow = true;
		}
	}

	SeenLapCount = In.Laps.Num();
	SeenLapNumber = In.CurrentLap;
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

// ---------------------------------------------------------------------------
// 地
// ---------------------------------------------------------------------------

int32 SZN6Hud::PaintPanel(const FGeometry& Geometry, FSlateWindowElementList& Out,
                          int32 Layer, const FSlateRect& Area, float Opacity) const
{
	const FVector2f Origin(Area.Left, Area.Top);
	const FVector2f Size = RectSize(Area);

	FLinearColor Fill = PanelBackground();
	Fill.A *= Opacity;
	Box(Out, Layer, Geometry, WhiteBrush, Origin, Size, Fill);

	// 細い枠。**塗りだけだと背景に溶ける。**
	FLinearColor Edge = PanelEdge();
	Edge.A *= Opacity;
	const FVector2f TopLeft = Origin;
	const FVector2f TopRight(Area.Right, Area.Top);
	const FVector2f BottomLeft(Area.Left, Area.Bottom);
	const FVector2f BottomRight(Area.Right, Area.Bottom);
	Line(Out, Layer + 1, Geometry, TopLeft, TopRight, Edge, 1.0f);
	Line(Out, Layer + 1, Geometry, BottomLeft, BottomRight, Edge, 1.0f);
	Line(Out, Layer + 1, Geometry, TopLeft, BottomLeft, Edge, 1.0f);
	Line(Out, Layer + 1, Geometry, TopRight, BottomRight, Edge, 1.0f);

	return Layer + 2;
}

// ---------------------------------------------------------------------------
// 下中央: シフトランプ・回転計・速度・ギア
// ---------------------------------------------------------------------------

int32 SZN6Hud::PaintShiftLights(const FGeometry& Geometry, FSlateWindowElementList& Out,
                                int32 Layer, const FSlateRect& Area, float Scale) const
{
	// **LED の列。** 参考にした作法は「緑 → 橙 → 赤と順に満ちて、
	// 変速点で列全体が点滅する」（出典 [1][2]）。
	//
	// 点き始めは redline の 0.90 倍。**これは演出**であって、
	// 最適変速点の計算ではない（`MakeTachScale` のコメント）。
	const FTachScale Tach = MakeTachScale(Snapshot.RedlineRpm);
	const FVector2f Size = RectSize(Area);
	if (Size.X <= 0.0f || Size.Y <= 0.0f)
	{
		return Layer;
	}

	constexpr int32 Leds = ShiftLightCount;
	const float Gap = FMath::Max(1.0f, 5.0f * Scale);
	const float LedWidth = (Size.X - Gap * (Leds - 1)) / Leds;
	if (LedWidth <= 0.0f)
	{
		return Layer;
	}

	// レブリミットに達したら列ごと点滅させる。**時計は貰った値を使う。**
	const bool bOverRev = Tach.IsValid() && Snapshot.EngineRpm >= Tach.RedlineRpm;
	const bool bFlashOn =
		FMath::Frac(static_cast<float>(Snapshot.SessionTimeS) * 12.0f) < 0.5f;

	for (int32 Index = 0; Index < Leds; ++Index)
	{
		const FVector2f At(Area.Left + Index * (LedWidth + Gap), Area.Top);

		const bool bLit = IsShiftLightLit(Tach, Snapshot.EngineRpm, Index);

		FLinearColor Colour = GaugeTrack();
		if (bOverRev)
		{
			// 吹け切り。**点滅で「今すぐ上げろ」を出す。**
			Colour = bFlashOn ? ShiftFlash() : Danger();
		}
		else if (bLit)
		{
			Colour = (Index < Leds / 3) ? Good()
			       : (Index < Leds * 2 / 3) ? Warn()
			       : Danger();
		}

		Box(Out, Layer, Geometry, WhiteBrush, At, FVector2f(LedWidth, Size.Y), Colour);
	}

	return Layer + 1;
}

int32 SZN6Hud::PaintRevBar(const FGeometry& Geometry, FSlateWindowElementList& Out,
                           int32 Layer, const FSlateRect& Area, float Scale) const
{
	// **円弧をやめて横棒にした。** 円弧の細い目盛りは、走りながらでは
	// 「レッドゾーンまであとどれだけか」が読めない。
	const FTachScale Tach = MakeTachScale(Snapshot.RedlineRpm);
	const FVector2f Origin(Area.Left, Area.Top);
	const FVector2f Size = RectSize(Area);
	if (Size.X <= 0.0f || Size.Y <= 0.0f)
	{
		return Layer;
	}

	Box(Out, Layer, Geometry, WhiteBrush, Origin, Size, GaugeTrack());

	if (!Tach.IsValid())
	{
		// **redline が来ていない。** 適当な目盛りを描かず、そう書く。
		Text(Out, Layer + 1, Geometry, TEXT("NO REDLINE DATA"),
		     FVector2f(Origin.X + 8.0f * Scale, Origin.Y + Size.Y * 0.2f),
		     LabelFont(Pt(14.0f, Scale)), Warn());
		return Layer + 2;
	}

	const float RedAt = static_cast<float>(Tach.Fraction(Tach.RedlineRpm));
	const float ShiftAt = static_cast<float>(Tach.Fraction(Tach.ShiftStartRpm));

	// 予告帯（橙）とレッドゾーン（赤）。**空でも常に見えている。**
	FLinearColor WarnBand = Warn();
	WarnBand.A = 0.16f;
	Box(Out, Layer + 1, Geometry, WhiteBrush,
	    FVector2f(Origin.X + Size.X * ShiftAt, Origin.Y),
	    FVector2f(Size.X * (RedAt - ShiftAt), Size.Y), WarnBand);

	FLinearColor RedBand = Danger();
	RedBand.A = 0.34f;
	Box(Out, Layer + 1, Geometry, WhiteBrush,
	    FVector2f(Origin.X + Size.X * RedAt, Origin.Y),
	    FVector2f(Size.X * (1.0f - RedAt), Size.Y), RedBand);

	// 今の回転。
	const float At = static_cast<float>(Tach.Fraction(Snapshot.EngineRpm));
	const FLinearColor FillColour =
		(Snapshot.EngineRpm >= Tach.RedlineRpm) ? Danger()
		: (Snapshot.EngineRpm >= Tach.ShiftStartRpm) ? Warn()
		: Accent();
	Box(Out, Layer + 2, Geometry, WhiteBrush, Origin,
	    FVector2f(Size.X * At, Size.Y), FillColour);

	// 1000rpm ごとの目盛りと数字。
	const int32 Steps = FMath::Clamp(
		FMath::FloorToInt32(static_cast<float>(Tach.ScaleMaxRpm / 1000.0)), 0, 30);
	FLinearColor TickColour = TextPrimary();
	TickColour.A = 0.28f;
	for (int32 K = 1; K <= Steps; ++K)
	{
		const float TickAt = static_cast<float>(Tach.Fraction(K * 1000.0));
		const float X = Origin.X + Size.X * TickAt;
		Line(Out, Layer + 3, Geometry, FVector2f(X, Origin.Y),
		     FVector2f(X, Origin.Y + Size.Y), TickColour, 1.0f);

		const FString Label = FString::FromInt(K);
		const int32 FontSize = Pt(15.0f, Scale);
		Text(Out, Layer + 3, Geometry, Label,
		     FVector2f(X - ApproxTextWidth(Label, FontSize) * 0.5f,
		               Origin.Y + Size.Y + 2.0f * Scale),
		     LabelFont(FontSize), K * 1000.0 >= Tach.RedlineRpm ? Danger() : TextSecondary());
	}

	// **レッドゾーンの境界に太い線を1本。** ここが redline そのもの。
	const float RedX = Origin.X + Size.X * RedAt;
	Line(Out, Layer + 4, Geometry, FVector2f(RedX, Origin.Y - 3.0f * Scale),
	     FVector2f(RedX, Origin.Y + Size.Y + 3.0f * Scale), Danger(),
	     FMath::Max(2.0f, 3.0f * Scale));

	// **目盛りの数字より下には何も置かない。** ここに回転数や「GEAR」を
	// 置いていたら、実際の画面で「7」と「rpm3496」が重なった。
	return Layer + 5;
}

int32 SZN6Hud::PaintCluster(const FGeometry& Geometry, FSlateWindowElementList& Out,
                            int32 Layer, const FLayout& Layout) const
{
	const float S = Layout.Scale;
	const FSlateRect& Area = Layout.Cluster;
	int32 Next = PaintPanel(Geometry, Out, Layer, Area, 1.15f);

	const float L = Area.Left + 18.0f * S;
	const float R = Area.Right - 18.0f * S;
	const float T = Area.Top;

	// 段の高さは実際に撮って決めた。**目盛りの数字の下に何も置かない。**
	//
	//    8..30   シフトランプ
	//   38..74   回転計の棒
	//   76..95   1000rpm ごとの数字（PaintRevBar が描く）
	//  100..196  速度 / 回転数 / ギア
	Next = PaintShiftLights(Geometry, Out, Next,
	                        FSlateRect(L, T + 8.0f * S, R, T + 30.0f * S), S);
	Next = PaintRevBar(Geometry, Out, Next,
	                   FSlateRect(L, T + 38.0f * S, R, T + 74.0f * S), S);

	// --- 速度。**画面でいちばん大きい数字。** ---
	const FString SpeedText = FString::Printf(TEXT("%d"),
		FMath::RoundToInt(FMath::Clamp(static_cast<float>(Snapshot.SpeedKmh),
		                               -9999.0f, 9999.0f)));
	const int32 SpeedFont = Pt(76.0f, S);
	const float SpeedRight = Area.Left + 400.0f * S;
	Text(Out, Next, Geometry, SpeedText,
	     FVector2f(SpeedRight - ApproxNumeralWidth(SpeedText, SpeedFont), T + 100.0f * S),
	     NumeralFont(SpeedFont), TextPrimary());
	Text(Out, Next, Geometry, TEXT("km/h"),
	     FVector2f(SpeedRight + 12.0f * S, T + 156.0f * S),
	     LabelFont(Pt(17.0f, S)), TextSecondary());

	// --- 真ん中の列: 回転数の数字と、クラッチ・サイドの印 ---
	//
	// **棒だけだと「今いくつか」が言えない。** 速度とギアの間に置く。
	const float MidX = Area.Left + 470.0f * S;
	const FString RpmText = FString::Printf(TEXT("%d"),
		FMath::RoundToInt(static_cast<float>(Snapshot.EngineRpm)));
	Text(Out, Next, Geometry, RpmText, FVector2f(MidX, T + 104.0f * S),
	     NumeralFont(Pt(22.0f, S)), TextSecondary());
	Text(Out, Next, Geometry, TEXT("rpm"), FVector2f(MidX, T + 134.0f * S),
	     LabelFont(Pt(12.0f, S)), TextFaint());

	// ペダルの棒は左下にあるが、**変速に直結するクラッチだけは
	// 計器の近くにも出す。**
	if (Snapshot.ClutchEngagement < 0.95)
	{
		Text(Out, Next, Geometry, TEXT("CLUTCH"),
		     FVector2f(MidX, T + 152.0f * S), LabelFont(Pt(13.0f, S)), Accent());
	}
	if (Snapshot.Handbrake > 0.05)
	{
		Text(Out, Next, Geometry, TEXT("HAND"),
		     FVector2f(MidX, T + 172.0f * S), LabelFont(Pt(13.0f, S)), Warn());
	}

	// --- ギア。**箱に入れて、速度と読み違えないようにする。** ---
	const FSlateRect GearBox(Area.Left + 590.0f * S, T + 100.0f * S,
	                         Area.Left + 742.0f * S, T + 196.0f * S);
	const bool bOverRev = Snapshot.RedlineRpm > 0.0
	                   && Snapshot.EngineRpm >= Snapshot.RedlineRpm;
	FLinearColor GearFill = bOverRev ? Danger() : Accent();
	GearFill.A = bOverRev ? 0.30f : 0.14f;
	Box(Out, Next, Geometry, WhiteBrush, FVector2f(GearBox.Left, GearBox.Top),
	    RectSize(GearBox), GearFill);

	FString GearText;
	if (Snapshot.Gear < 0)       { GearText = TEXT("R"); }
	else if (Snapshot.Gear == 0) { GearText = TEXT("N"); }
	else                         { GearText = FString::FromInt(Snapshot.Gear); }

	const int32 GearFont = Pt(72.0f, S);
	Text(Out, Next + 1, Geometry, GearText,
	     FVector2f((GearBox.Left + GearBox.Right) * 0.5f
	               - ApproxNumeralWidth(GearText, GearFont) * 0.5f,
	               GearBox.Top + 4.0f * S),
	     NumeralFont(GearFont), bOverRev ? Danger() : TextPrimary());
	// **見出しは箱の中に入れる。** 箱の上に出すと目盛りの数字とぶつかる。
	Text(Out, Next + 1, Geometry, TEXT("GEAR"),
	     FVector2f(GearBox.Left + 6.0f * S, GearBox.Top + 4.0f * S),
	     LabelFont(Pt(11.0f, S)), TextFaint());

	return Next + 2;
}

// ---------------------------------------------------------------------------
// 左下: 入力
// ---------------------------------------------------------------------------

int32 SZN6Hud::PaintInputs(const FGeometry& Geometry, FSlateWindowElementList& Out,
                           int32 Layer, const FLayout& Layout) const
{
	const float S = Layout.Scale;
	const FSlateRect& Area = Layout.Inputs;
	int32 Next = PaintPanel(Geometry, Out, Layer, Area, 1.15f);

	const float L = Area.Left + 16.0f * S;
	const float R = Area.Right - 16.0f * S;
	const float T = Area.Top;
	const float Width = FMath::Max(R - L, 1.0f);

	Text(Out, Next, Geometry, TEXT("INPUT"), FVector2f(L, T + 8.0f * S),
	     LabelFont(Pt(12.0f, S)), TextFaint());

	// --- 流れるトレース ---
	//
	// **細い棒4本では「いつ緩めたか」が見えない。** 数秒ぶんの履歴を
	// 横に流すのが一般的な作法（出典 [4][5]）。アクセルが緑、
	// ブレーキが赤。
	const FSlateRect Trace(L, T + 26.0f * S, R, T + 96.0f * S);
	const FVector2f TraceSize = RectSize(Trace);
	Box(Out, Next, Geometry, WhiteBrush, FVector2f(Trace.Left, Trace.Top),
	    TraceSize, GaugeTrack());

	if (TraceCount >= 2 && TraceSize.X > 0.0f && TraceSize.Y > 0.0f)
	{
		TArray<FVector2f> ThrottlePoints;
		TArray<FVector2f> BrakePoints;
		TArray<FVector2f> SteerPoints;
		ThrottlePoints.Reserve(TraceCount);
		BrakePoints.Reserve(TraceCount);
		SteerPoints.Reserve(TraceCount);

		for (int32 K = 0; K < TraceCount; ++K)
		{
			const int32 Index = (TraceHead - TraceCount + K + TraceSamples) % TraceSamples;
			const float X = Trace.Left + TraceSize.X * K / static_cast<float>(TraceCount - 1);
			ThrottlePoints.Add(FVector2f(
				X, Trace.Bottom - TraceSize.Y * TraceThrottle[Index]));
			BrakePoints.Add(FVector2f(
				X, Trace.Bottom - TraceSize.Y * TraceBrake[Index]));
			// ステアは中央からの振れ。**踏み込み量とは別の意味なので薄く。**
			SteerPoints.Add(FVector2f(
				X, Trace.Top + TraceSize.Y * (0.5f - 0.45f * TraceSteer[Index])));
		}

		FLinearColor SteerColour = Accent();
		SteerColour.A = 0.45f;
		Polyline(Out, Next + 1, Geometry, SteerPoints, SteerColour,
		         FMath::Max(1.0f, 1.5f * S));
		Polyline(Out, Next + 2, Geometry, ThrottlePoints, Good(),
		         FMath::Max(1.0f, 2.0f * S));
		Polyline(Out, Next + 2, Geometry, BrakePoints, Danger(),
		         FMath::Max(1.0f, 2.0f * S));
	}

	// --- 現在値の棒 ---
	struct FBar { double Value; FLinearColor Colour; const TCHAR* Label; };
	const FBar Bars[] = {
		{ Snapshot.Throttle,               Good(),   TEXT("THR") },
		{ Snapshot.Brake,                  Danger(), TEXT("BRK") },
		{ 1.0 - Snapshot.ClutchEngagement, Accent(), TEXT("CLU") },
		{ Snapshot.Handbrake,              Warn(),   TEXT("HND") },
	};

	// 段の高さは実際に撮って決めた。**ステアの棒とペダルの見出しが
	// 同じ行に来ていた。**
	//
	//   104..152  ペダルの棒       155..167  見出し
	//   178..190  ステアの棒
	const float BarWidth = 22.0f * S;
	const float BarGap = 14.0f * S;
	const float BarTop = T + 104.0f * S;
	const float BarHeight = 48.0f * S;
	for (int32 Index = 0; Index < UE_ARRAY_COUNT(Bars); ++Index)
	{
		const float X = L + Index * (BarWidth + BarGap);
		Box(Out, Next + 3, Geometry, WhiteBrush, FVector2f(X, BarTop),
		    FVector2f(BarWidth, BarHeight), GaugeTrack());

		const float Filled = BarHeight * FMath::Clamp(
			static_cast<float>(Bars[Index].Value), 0.0f, 1.0f);
		if (Filled > 0.5f)
		{
			Box(Out, Next + 4, Geometry, WhiteBrush,
			    FVector2f(X, BarTop + BarHeight - Filled),
			    FVector2f(BarWidth, Filled), Bars[Index].Colour);
		}
		Text(Out, Next + 4, Geometry, Bars[Index].Label,
		     FVector2f(X - 2.0f * S, BarTop + BarHeight + 3.0f * S),
		     LabelFont(Pt(10.0f, S)), TextFaint());
	}

	// --- 数字。**棒は一目で、数字は正確に。** ---
	const float NumberX = L + 156.0f * S;
	Text(Out, Next + 4, Geometry,
	     FString::Printf(TEXT("THR %3d%%"),
	                     FMath::RoundToInt(static_cast<float>(Snapshot.Throttle * 100.0))),
	     FVector2f(NumberX, BarTop + 0.0f * S), LabelFont(Pt(15.0f, S)), Good());
	Text(Out, Next + 4, Geometry,
	     FString::Printf(TEXT("BRK %3d%%"),
	                     FMath::RoundToInt(static_cast<float>(Snapshot.Brake * 100.0))),
	     FVector2f(NumberX, BarTop + 22.0f * S), LabelFont(Pt(15.0f, S)), Danger());
	Text(Out, Next + 4, Geometry,
	     FString::Printf(TEXT("STR %+4d deg"),
	                     FMath::RoundToInt(FMath::RadiansToDegrees(
	                         static_cast<float>(Snapshot.SteerRad)))),
	     FVector2f(NumberX, BarTop + 42.0f * S), LabelFont(Pt(15.0f, S)), TextSecondary());

	// --- ステアの棒。**中央からどちらへどれだけ切っているか。** ---
	const float SteerTop = T + 178.0f * S;
	const float SteerHeight = 12.0f * S;
	Box(Out, Next + 4, Geometry, WhiteBrush, FVector2f(L, SteerTop),
	    FVector2f(Width, SteerHeight), GaugeTrack());

	const float Centre = L + Width * 0.5f;
	const float SteerFraction = FMath::Clamp(
		static_cast<float>(Snapshot.SteerRad / Snapshot.MaxSteerRad), -1.0f, 1.0f);
	const float SteerSpan = Width * 0.5f * SteerFraction;
	if (FMath::Abs(SteerSpan) > 0.5f)
	{
		const float X0 = FMath::Min(Centre, Centre + SteerSpan);
		Box(Out, Next + 5, Geometry, WhiteBrush, FVector2f(X0, SteerTop),
		    FVector2f(FMath::Abs(SteerSpan), SteerHeight), Accent());
	}
	Line(Out, Next + 6, Geometry, FVector2f(Centre, SteerTop - 2.0f * S),
	     FVector2f(Centre, SteerTop + SteerHeight + 2.0f * S), TextPrimary(),
	     FMath::Max(1.0f, 1.5f * S));

	return Next + 7;
}

// ---------------------------------------------------------------------------
// 右下: 4輪の限界と G
// ---------------------------------------------------------------------------

int32 SZN6Hud::PaintGrip(const FGeometry& Geometry, FSlateWindowElementList& Out,
                         int32 Layer, const FLayout& Layout) const
{
	const float S = Layout.Scale;
	const FSlateRect& Area = Layout.Grip;
	int32 Next = PaintPanel(Geometry, Out, Layer, Area, 1.15f);

	const float L = Area.Left + 16.0f * S;
	const float T = Area.Top;

	Text(Out, Next, Geometry, TEXT("GRIP USED"), FVector2f(L, T + 8.0f * S),
	     LabelFont(Pt(12.0f, S)), TextFaint());

	// --- 4輪。**車の形に並べる**ので、どの輪かが直感で分かる。 ---
	//
	// 浮いている輪は塗らずに×。**接地していないことが見えないと、
	// 「なぜ効かないのか」が分からない。**
	const float Cell = 40.0f * S;
	const float GapX = 40.0f * S;
	const float GapY = 30.0f * S;
	const float WheelTop = T + 26.0f * S;

	for (int32 Wheel = 0; Wheel < 4; ++Wheel)
	{
		const bool bFront = (Wheel < 2);
		const bool bLeft = (Wheel % 2 == 0);
		const FVector2f At(L + (bLeft ? 0.0f : Cell + GapX),
		                   WheelTop + (bFront ? 0.0f : Cell + GapY));

		Box(Out, Next, Geometry, WhiteBrush, At, FVector2f(Cell, Cell), GaugeTrack());

		if (!Snapshot.bContact[Wheel])
		{
			Line(Out, Next + 1, Geometry, At, FVector2f(At.X + Cell, At.Y + Cell),
			     Warn(), FMath::Max(1.0f, 2.0f * S));
			Line(Out, Next + 1, Geometry, FVector2f(At.X + Cell, At.Y),
			     FVector2f(At.X, At.Y + Cell), Warn(), FMath::Max(1.0f, 2.0f * S));
			continue;
		}

		const float Used = FMath::Clamp(
			static_cast<float>(Snapshot.Utilisation[Wheel]), 0.0f, 1.0f);
		const FLinearColor Colour = Used > 0.92f ? Danger()
		                          : Used > 0.75f ? Warn()
		                          : Accent();
		const float Filled = Cell * Used;
		if (Filled > 0.5f)
		{
			Box(Out, Next + 1, Geometry, WhiteBrush,
			    FVector2f(At.X, At.Y + Cell - Filled), FVector2f(Cell, Filled), Colour);
		}
		// 使用率の数字。**色だけだと 0.80 と 0.90 の区別がつかない。**
		// 枡の中央に置く。塗りの上に来るので、白で抜く。
		const FString UsedText = FString::Printf(TEXT("%d"),
		                                         FMath::RoundToInt(Used * 100.0f));
		const int32 UsedFont = Pt(14.0f, S);
		Text(Out, Next + 2, Geometry, UsedText,
		     FVector2f(At.X + Cell * 0.5f - ApproxTextWidth(UsedText, UsedFont) * 0.5f,
		               At.Y + Cell * 0.5f - UsedFont * 0.62f),
		     LabelFont(UsedFont), TextPrimary());
	}

	// --- G のダイヤル。**横G・前後Gを点1つで。** ---
	//
	// 参考にした作法では計器の脇に G のダイヤルを置く（出典 [1]）。
	const float Radius = 44.0f * S;
	const FVector2f GCentre(Area.Left + 232.0f * S, T + 78.0f * S);
	constexpr float FullScaleG = 1.5f;

	TArray<FVector2f> Ring;
	for (int32 Index = 0; Index <= 36; ++Index)
	{
		const float Angle = 2.0f * PI * Index / 36.0f;
		Ring.Add(FVector2f(GCentre.X + Radius * FMath::Cos(Angle),
		                   GCentre.Y + Radius * FMath::Sin(Angle)));
	}
	FLinearColor RingColour = TextPrimary();
	RingColour.A = 0.22f;
	Polyline(Out, Next + 2, Geometry, Ring, RingColour, 1.0f);
	Line(Out, Next + 2, Geometry, FVector2f(GCentre.X - Radius, GCentre.Y),
	     FVector2f(GCentre.X + Radius, GCentre.Y), RingColour, 1.0f);
	Line(Out, Next + 2, Geometry, FVector2f(GCentre.X, GCentre.Y - Radius),
	     FVector2f(GCentre.X, GCentre.Y + Radius), RingColour, 1.0f);

	// 点。**横Gは右が正、前後Gは加速が上。**
	const float Gx = FMath::Clamp(static_cast<float>(Snapshot.LateralG) / FullScaleG,
	                              -1.2f, 1.2f);
	const float Gy = FMath::Clamp(static_cast<float>(Snapshot.LongitudinalG) / FullScaleG,
	                              -1.2f, 1.2f);
	const float Dot = FMath::Max(2.0f, 5.0f * S);
	Box(Out, Next + 3, Geometry, WhiteBrush,
	    FVector2f(GCentre.X + Radius * Gx - Dot, GCentre.Y - Radius * Gy - Dot),
	    FVector2f(Dot * 2.0f, Dot * 2.0f), Accent());
	// **目盛りの説明は輪の下へ。** 輪に重ねると線と混ざって読めない。
	const FString ScaleText = FString::Printf(TEXT("%.1f G full scale"), FullScaleG);
	const int32 ScaleFont = Pt(10.0f, S);
	Text(Out, Next + 3, Geometry, ScaleText,
	     FVector2f(GCentre.X - ApproxTextWidth(ScaleText, ScaleFont) * 0.5f,
	               GCentre.Y + Radius + 6.0f * S),
	     LabelFont(ScaleFont), TextFaint());

	// --- 数字 ---
	const int32 NumberFont = Pt(13.0f, S);
	Text(Out, Next + 3, Geometry,
	     FString::Printf(TEXT("LAT %+5.2f G"), Snapshot.LateralG),
	     FVector2f(L, T + 152.0f * S), LabelFont(NumberFont), TextSecondary());
	Text(Out, Next + 3, Geometry,
	     FString::Printf(TEXT("LON %+5.2f G"), Snapshot.LongitudinalG),
	     FVector2f(L, T + 170.0f * S), LabelFont(NumberFont), TextSecondary());
	Text(Out, Next + 3, Geometry,
	     FString::Printf(TEXT("SLIP %+5.1f deg"), Snapshot.SlipAngleDeg),
	     FVector2f(L, T + 188.0f * S), LabelFont(NumberFont),
	     FMath::Abs(Snapshot.SlipAngleDeg) > 8.0 ? Danger() : TextSecondary());

	return Next + 4;
}

// ---------------------------------------------------------------------------
// 右上: タイム
// ---------------------------------------------------------------------------

int32 SZN6Hud::PaintTiming(const FGeometry& Geometry, FSlateWindowElementList& Out,
                           int32 Layer, const FLayout& Layout) const
{
	const float S = Layout.Scale;
	const FSlateRect& Area = Layout.Timing;
	int32 Next = PaintPanel(Geometry, Out, Layer, Area, 1.15f);

	const float L = Area.Left + 16.0f * S;
	const float R = Area.Right - 16.0f * S;
	const float T = Area.Top;
	const float Width = FMath::Max(R - L, 1.0f);

	// --- 周回 ---
	const FString LapLabel = Snapshot.TotalLaps > 0
		? FString::Printf(TEXT("LAP %d / %d"), Snapshot.CurrentLap, Snapshot.TotalLaps)
		: FString::Printf(TEXT("LAP %d"), Snapshot.CurrentLap);
	Text(Out, Next, Geometry, LapLabel, FVector2f(L, T + 10.0f * S),
	     LabelFont(Pt(13.0f, S)), TextSecondary());
	if (Snapshot.bLapInvalidated)
	{
		const FString Invalid = TEXT("INVALID");
		Text(Out, Next, Geometry, Invalid,
		     FVector2f(R - ApproxTextWidth(Invalid, Pt(13.0f, S)), T + 10.0f * S),
		     LabelFont(Pt(13.0f, S)), Danger());
	}

	// --- 今の周のタイム ---
	Text(Out, Next, Geometry, LapText(Snapshot.LapTimeS),
	     FVector2f(L, T + 30.0f * S), NumeralFont(Pt(36.0f, S)), TextPrimary());

	// --- ベスト周との差 ---
	//
	// **数字と棒の両方で出す。** 参考にした作法では、速ければ緑が右へ、
	// 遅ければ赤が左へ伸びる（出典 [2]）。
	Text(Out, Next, Geometry, TEXT("DELTA"), FVector2f(L, T + 86.0f * S),
	     LabelFont(Pt(11.0f, S)), TextFaint());

	const float BarTop = T + 118.0f * S;
	const float BarHeight = 14.0f * S;
	Box(Out, Next, Geometry, WhiteBrush, FVector2f(L, BarTop),
	    FVector2f(Width, BarHeight), GaugeTrack());

	if (bDeltaValidNow)
	{
		const FLinearColor DeltaColour = DeltaS <= 0.0 ? Good() : Danger();
		const FString DeltaText = FormatDelta(DeltaS);
		const int32 DeltaFont = Pt(30.0f, S);
		Text(Out, Next + 1, Geometry, DeltaText,
		     FVector2f(R - ApproxNumeralWidth(DeltaText, DeltaFont), T + 78.0f * S),
		     NumeralFont(DeltaFont), DeltaColour);

		// 棒。**±2 秒で振り切る。**
		constexpr double FullScaleS = 2.0;
		const float Centre = L + Width * 0.5f;
		const float Span = Width * 0.5f * FMath::Clamp(
			static_cast<float>(-DeltaS / FullScaleS), -1.0f, 1.0f);
		if (FMath::Abs(Span) > 0.5f)
		{
			const float X0 = FMath::Min(Centre, Centre + Span);
			Box(Out, Next + 1, Geometry, WhiteBrush, FVector2f(X0, BarTop),
			    FVector2f(FMath::Abs(Span), BarHeight), DeltaColour);
		}
		Line(Out, Next + 2, Geometry, FVector2f(Centre, BarTop - 2.0f * S),
		     FVector2f(Centre, BarTop + BarHeight + 2.0f * S), TextPrimary(),
		     FMath::Max(1.0f, 1.5f * S));
	}
	else
	{
		// **基準になる周がまだ無い。** 0.000 と出さない。
		const FString NoDelta = TEXT("--.---");
		const int32 DeltaFont = Pt(30.0f, S);
		Text(Out, Next + 1, Geometry, NoDelta,
		     FVector2f(R - ApproxNumeralWidth(NoDelta, DeltaFont), T + 78.0f * S),
		     NumeralFont(DeltaFont), TextFaint());
		Text(Out, Next + 1, Geometry, TEXT("no reference lap yet"),
		     FVector2f(L, T + 138.0f * S), LabelFont(Pt(10.0f, S)), TextFaint());
	}

	// --- ベストと直前の周 ---
	const int32 RowFont = Pt(16.0f, S);
	Text(Out, Next + 2, Geometry, TEXT("BEST"), FVector2f(L, T + 160.0f * S),
	     LabelFont(Pt(11.0f, S)), TextFaint());
	const FString BestText = LapText(Snapshot.BestLapS);
	Text(Out, Next + 2, Geometry, BestText,
	     FVector2f(R - ApproxTextWidth(BestText, RowFont), T + 157.0f * S),
	     LabelFont(RowFont), Snapshot.BestLapS > 0.0 ? SectorBest() : TextFaint());

	Text(Out, Next + 2, Geometry, TEXT("LAST"), FVector2f(L, T + 182.0f * S),
	     LabelFont(Pt(11.0f, S)), TextFaint());
	const double LastLapS = Snapshot.Laps.Num() > 0 ? Snapshot.Laps.Last().TimeS : 0.0;
	const FString LastText = LapText(LastLapS);
	Text(Out, Next + 2, Geometry, LastText,
	     FVector2f(R - ApproxTextWidth(LastText, RowFont), T + 179.0f * S),
	     LabelFont(RowFont), LastLapS > 0.0 ? TextPrimary() : TextFaint());

	// --- 区間タイム ---
	//
	// 色は F1 の計時画面の作法（出典 [3]）を**1台のセッション向けに
	// 読み替えたもの**。単独走行では「他人より速い」が無いので:
	//
	//   紫 = セッション中その区間の最速を更新した
	//   緑 = ベスト周の同じ区間より速かった
	//   黄 = それ以外
	//
	// **これは色の意味の読み替えであって、記録の作り替えではない。**
	const float SectorTop = T + 206.0f * S;
	const float SectorGap = 10.0f * S;
	const float SectorWidth = (Width - SectorGap * 2.0f) / 3.0f;
	for (int32 Sector = 0; Sector < 3; ++Sector)
	{
		const float X = L + Sector * (SectorWidth + SectorGap);
		const bool bHere = (Sector == Snapshot.Sector)
		                && Snapshot.Phase == ZN6::ERacePhase::Racing;

		FLinearColor Colour = TextFaint();
		switch (SectorFlag[Sector])
		{
			case 1:  Colour = SectorBest(); break;
			case 2:  Colour = Good();       break;
			case 3:  Colour = Warn();       break;
			default: Colour = TextFaint();  break;
		}

		Box(Out, Next + 2, Geometry, WhiteBrush, FVector2f(X, SectorTop),
		    FVector2f(SectorWidth, 46.0f * S), GaugeTrack());
		// 今いる区間に印。
		Box(Out, Next + 3, Geometry, WhiteBrush, FVector2f(X, SectorTop),
		    FVector2f(SectorWidth, 3.0f * S), bHere ? Accent() : AccentDim());

		Text(Out, Next + 3, Geometry, FString::Printf(TEXT("S%d"), Sector + 1),
		     FVector2f(X + 4.0f * S, SectorTop + 6.0f * S),
		     LabelFont(Pt(11.0f, S)), TextFaint());

		const FString Value = SectorText(CurrentSectorS[Sector]);
		const int32 SectorFont = Pt(17.0f, S);
		Text(Out, Next + 3, Geometry, Value,
		     FVector2f(X + SectorWidth - ApproxTextWidth(Value, SectorFont) - 4.0f * S,
		               SectorTop + 22.0f * S),
		     LabelFont(SectorFont), Colour);
	}

	// --- 1周の進捗 ---
	const float ProgressTop = T + 264.0f * S;
	Box(Out, Next + 3, Geometry, WhiteBrush, FVector2f(L, ProgressTop),
	    FVector2f(Width, 6.0f * S), GaugeTrack());
	Box(Out, Next + 4, Geometry, WhiteBrush, FVector2f(L, ProgressTop),
	    FVector2f(Width * static_cast<float>(Snapshot.LapProgress), 6.0f * S),
	    AccentDim());

	return Next + 5;
}

// ---------------------------------------------------------------------------
// 左上: ミニマップ
// ---------------------------------------------------------------------------

int32 SZN6Hud::PaintMiniMap(const FGeometry& Geometry, FSlateWindowElementList& Out,
                            int32 Layer, const FLayout& Layout) const
{
	const float S = Layout.Scale;
	const FSlateRect& Area = Layout.Map;
	int32 Next = PaintPanel(Geometry, Out, Layer, Area, 1.15f);

	const FVector2f Size = RectSize(Area);
	if (CentrelineM.Num() < 2 || Size.X <= 0.0f || Size.Y <= 0.0f)
	{
		Text(Out, Next, Geometry, TEXT("NO TRACK DATA"),
		     FVector2f(Area.Left + 12.0f * S, Area.Top + Size.Y * 0.5f - 6.0f * S),
		     LabelFont(Pt(11.0f, S)), TextFaint());
		return Next + 1;
	}

	// 世界座標 -> パネル内。**縦横で同じ倍率**にする。
	// 別々にすると、コースの形が引き伸ばされて別のコースに見える。
	const FVector2D SpanM = MapMaxM - MapMinM;
	const float Inset = 14.0f * S;
	const float Usable = FMath::Max(FMath::Min(Size.X, Size.Y) - 2.0f * Inset, 1.0f);
	const double LargestM = FMath::Max(FMath::Max(SpanM.X, SpanM.Y), 1.0);
	const float Scale = Usable / static_cast<float>(LargestM);

	const FVector2f MapSize(static_cast<float>(SpanM.X) * Scale,
	                        static_cast<float>(SpanM.Y) * Scale);
	const FVector2f MapOrigin(Area.Left + (Size.X - MapSize.X) * 0.5f,
	                          Area.Top + (Size.Y - MapSize.Y) * 0.5f);

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
	Polyline(Out, Next, Geometry, Points, Accent(), FMath::Max(1.5f, 2.5f * S));

	// スタート/ゴール線
	const FVector2f Start = ToScreen(CentrelineM[0]);
	const float Mark = FMath::Max(3.0f, 5.0f * S);
	Box(Out, Next + 1, Geometry, WhiteBrush,
	    FVector2f(Start.X - Mark, Start.Y - Mark * 0.4f),
	    FVector2f(Mark * 2.0f, Mark * 0.8f), TextPrimary());

	// --- 自車。**三角にして向きを出す。** ---
	//
	// 点と短い線では、線が点に埋もれて向きが読めなかった。
	const FVector2f Car = ToScreen(FVector2D(Snapshot.CarXM, Snapshot.CarYM));
	const float Heading = static_cast<float>(Snapshot.CarHeadingRad);
	const float Nose = FMath::Max(7.0f, 11.0f * S);
	const float Tail = Nose * 0.62f;

	// 画面の y が下向きなので、sin の符号を反転する。
	const FVector2f Forward(FMath::Cos(Heading), -FMath::Sin(Heading));
	const FVector2f Side(-Forward.Y, Forward.X);

	TArray<FVector2f> Arrow;
	Arrow.Add(Car + Forward * Nose);
	Arrow.Add(Car - Forward * Tail + Side * Tail);
	Arrow.Add(Car - Forward * Tail * 0.35f);
	Arrow.Add(Car - Forward * Tail - Side * Tail);
	const FVector2f ArrowFirst = Arrow[0];
	Arrow.Add(ArrowFirst);

	const FLinearColor CarColour = Snapshot.bOffTrack ? Warn() : TextPrimary();
	Polyline(Out, Next + 2, Geometry, Arrow, CarColour, FMath::Max(2.0f, 2.5f * S));

	return Next + 3;
}

// ---------------------------------------------------------------------------
// 上中央: 警告
// ---------------------------------------------------------------------------

int32 SZN6Hud::PaintStatus(const FGeometry& Geometry, FSlateWindowElementList& Out,
                           int32 Layer, const FLayout& Layout) const
{
	// **言うことが無いときは何も出さない。** 常設すると視線を取る。
	FString Message;
	FLinearColor Colour = TextPrimary();

	if (bInvalidInput)
	{
		// **値の異常を握りつぶさない**（憲法ルール6）。
		Message = TEXT("TELEMETRY FAULT - non-finite values received");
		Colour = Danger();
	}
	else if (Snapshot.bLapInvalidated)
	{
		Message = TEXT("LAP INVALIDATED");
		Colour = Danger();
	}
	else if (Snapshot.bOffTrack)
	{
		Message = TEXT("OFF TRACK");
		Colour = Warn();
	}
	else if (Snapshot.Phase == ZN6::ERacePhase::Finished)
	{
		Message = TEXT("FINISHED");
		Colour = Accent();
	}
	else
	{
		return Layer;
	}

	const float S = Layout.Scale;
	const FSlateRect& Area = Layout.Status;
	int32 Next = PaintPanel(Geometry, Out, Layer, Area, 1.0f);

	const int32 FontSize = Pt(22.0f, S);
	Text(Out, Next, Geometry, Message,
	     FVector2f((Area.Left + Area.Right) * 0.5f
	               - ApproxTextWidth(Message, FontSize) * 0.5f,
	               Area.Top + 16.0f * S),
	     LabelFont(FontSize), Colour);

	return Next + 1;
}

// ---------------------------------------------------------------------------
// 中央: カウントダウン
// ---------------------------------------------------------------------------

int32 SZN6Hud::PaintCountdown(const FGeometry& Geometry, FSlateWindowElementList& Out,
                              int32 Layer, const FLayout& Layout) const
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
	const FSlateRect& Area = Layout.Countdown;
	const int32 FontSize = Pt(140.0f, Layout.Scale);
	Text(Out, Layer, Geometry, Label,
	     FVector2f((Area.Left + Area.Right) * 0.5f
	               - ApproxNumeralWidth(Label, FontSize) * 0.5f, Area.Top),
	     NumeralFont(FontSize), Colour);

	return Layer + 1;
}

// ---------------------------------------------------------------------------
// 左上の下: 信頼度
// ---------------------------------------------------------------------------

int32 SZN6Hud::PaintConfidence(const FGeometry& Geometry, FSlateWindowElementList& Out,
                               int32 Layer, const FLayout& Layout) const
{
	// **この数字がどれくらい確かかを画面にも出す。**
	//
	// 隠すと、出典のある値と仮定値が同じ顔で並ぶ（Docs/AGENT_TOPOLOGY.md §3）。
	// 実測比較に使えない状態のときは、はっきりそう書く。
	const float S = Layout.Scale;
	const FSlateRect& Area = Layout.Footer;
	const int32 FontSize = Pt(11.0f, S);

	Text(Out, Layer, Geometry,
	     FString::Printf(TEXT("model confidence %.2f"), Snapshot.Confidence),
	     FVector2f(Area.Left, Area.Top), LabelFont(FontSize), TextFaint());

	if (!Snapshot.bValidatable)
	{
		Text(Out, Layer, Geometry, TEXT("assumed values in use"),
		     FVector2f(Area.Left, Area.Top + 14.0f * S), LabelFont(FontSize), Warn());
		Text(Out, Layer, Geometry, TEXT("not comparable to measurements"),
		     FVector2f(Area.Left, Area.Top + 28.0f * S), LabelFont(FontSize), Warn());
	}

	return Layer + 1;
}

// ---------------------------------------------------------------------------

int32 SZN6Hud::OnPaint(const FPaintArgs& Args, const FGeometry& AllottedGeometry,
                       const FSlateRect& MyCullingRect,
                       FSlateWindowElementList& OutDrawElements, int32 LayerId,
                       const FWidgetStyle& InWidgetStyle, bool bParentEnabled) const
{
	if (WhiteBrush == nullptr)
	{
		return LayerId;
	}

	// メニューでは HUD を出さない。**メニューの絵が計器で汚れる。**
	if (Snapshot.Phase == ZN6::ERacePhase::Menu)
	{
		return LayerId;
	}

	const FLayout Layout = ComputeLayout(FVector2f(AllottedGeometry.GetLocalSize()));
	int32 Layer = LayerId;

	Layer = PaintMiniMap(AllottedGeometry, OutDrawElements, Layer, Layout);
	Layer = PaintConfidence(AllottedGeometry, OutDrawElements, Layer, Layout);
	Layer = PaintTiming(AllottedGeometry, OutDrawElements, Layer, Layout);
	Layer = PaintCluster(AllottedGeometry, OutDrawElements, Layer, Layout);
	Layer = PaintInputs(AllottedGeometry, OutDrawElements, Layer, Layout);
	Layer = PaintGrip(AllottedGeometry, OutDrawElements, Layer, Layout);
	Layer = PaintStatus(AllottedGeometry, OutDrawElements, Layer, Layout);
	Layer = PaintCountdown(AllottedGeometry, OutDrawElements, Layer, Layout);

	return Layer;
}
