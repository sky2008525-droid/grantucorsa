// 走行中の画面（計器・タイム・ミニマップ・カウントダウン）。
//
// **全部を1つのウィジェットで描く。** 子ウィジェットに分けると、
// 計器の針とタイムの位置関係を揃えるのにレイアウトの都合が入り込む。
// ここは自由に描きたいので `OnPaint` で直接引く。
//
// データは `FHudSnapshot` からのみ読む。**車のオブジェクトを持たない。**
//
// ---------------------------------------------------------------------------
// 配置の考え方（実在のレーシングゲームの作法を参考にした。出典は .cpp 冒頭）
//
//   下中央  もっとも見る計器。**横棒の回転計 + シフトランプ + 速度 + ギア**
//   左下    入力（ペダルトレース・ステア）
//   右下    4輪の限界の近さと G
//   右上    タイム（現在・デルタ・ベスト・区間）
//   左上    ミニマップ
//   上中央  警告（コース外・ラップ無効・値の異常）
//
// **位置は `ComputeLayout` 1箇所で決める。** 各 Paint 関数が自分で
// 座標を作ると、画面サイズを変えたときにどこかが重なる。重なりと
// 画面外は主観ではないので、`Tests/ZN6UiTest.cpp` で機械的に見る。

#pragma once

#include "CoreMinimal.h"
#include "Layout/SlateRect.h"
#include "UI/ZN6HudSnapshot.h"
#include "Widgets/DeclarativeSyntaxSupport.h"
#include "Widgets/SCompoundWidget.h"

class SZN6Hud : public SCompoundWidget
{
public:
	SLATE_BEGIN_ARGS(SZN6Hud) {}
	SLATE_END_ARGS()

	void Construct(const FArguments& InArgs);

	// -----------------------------------------------------------------------
	// 画面の区画
	//
	// **すべての部品はこの矩形の中だけを描く。**

	struct FLayout
	{
		FSlateRect Map;        // 左上: ミニマップ
		FSlateRect Status;     // 上中央: 警告
		FSlateRect Timing;     // 右上: タイム
		FSlateRect Cluster;    // 下中央: 回転計・速度・ギア
		FSlateRect Inputs;     // 左下: 入力
		FSlateRect Grip;       // 右下: 4輪と G
		FSlateRect Countdown;  // 中央: カウントダウン
		FSlateRect Footer;     // 左下端: 信頼度

		/** 基準（1920x1080）に対する倍率。文字の大きさもこれで決める。 */
		float Scale = 1.0f;

		/** 重なり検査のために全部を列挙する。 */
		TArray<FSlateRect> All() const;
		static const TCHAR* NameOf(int32 Index);
	};

	/**
	 * 画面サイズから区画を決める。
	 *
	 * **等倍で縮める。** 縦横で別の倍率を掛けると、狭い画面で計器が
	 * 潰れて読めなくなる。基準の 1920x1080 で重なっていなければ、
	 * どの画面サイズでも重ならない（縮小と端寄せしかしないため）。
	 */
	static FLayout ComputeLayout(const FVector2f& ScreenSize);

	// -----------------------------------------------------------------------
	// 回転計の目盛り
	//
	// **レッドゾーンの境界は `FHudSnapshot::RedlineRpm` そのもの。**
	// HUD 側で「だいたい 7000 くらい」と持たない。持つと `vehicle.json` を
	// 直したときに画面だけ古い値のまま残る。

	struct FTachScale
	{
		/** 赤帯の始まり。**車の redline と一致する。** */
		double RedlineRpm = 0.0;
		/** シフトランプが点き始める回転数。**これは演出であって最適変速点ではない。** */
		double ShiftStartRpm = 0.0;
		/** 目盛りの右端。redline より上に赤帯の幅を残す。 */
		double ScaleMaxRpm = 0.0;

		bool IsValid() const { return RedlineRpm > 0.0 && ScaleMaxRpm > RedlineRpm; }

		/** 回転数を 0..1 に写す。目盛りの外へは出さない。 */
		double Fraction(double Rpm) const;
	};

	/**
	 * redline から目盛りを作る。
	 *
	 * **redline が無い（0 や NaN）ときは無効な目盛りを返す。**
	 * 適当な既定値で埋めない。埋めると、値が来ていないことが見えなくなる。
	 */
	static FTachScale MakeTachScale(double RedlineRpm);

	/** シフトランプの数。 */
	static constexpr int32 ShiftLightCount = 16;

	/**
	 * シフトランプの `Index` 番目が点くか。
	 *
	 * **閾値は目盛りから作る。** つまり redline から作る。
	 * 実際の画面で点いた瞬間を撮るのは難しいので、ここだけは
	 * 目で見るのではなく検査で押さえる。
	 */
	static bool IsShiftLightLit(const FTachScale& Scale, double Rpm, int32 Index);

	// -----------------------------------------------------------------------

	/**
	 * 毎フレーム、車から詰めた値を渡す。**一方向。**
	 *
	 * ここで受け取った値から、HUD 側だけで作れる表示（入力の履歴、
	 * ベスト周との差、区間タイム）を組み立てる。**物理へは書き戻さない。**
	 */
	void SetSnapshot(const ZN6::FHudSnapshot& InSnapshot);

	/**
	 * ミニマップに描く中心線を渡す。**1回だけ。**
	 * 毎フレーム渡すと、千点の配列を毎回コピーすることになる。
	 */
	void SetCentreline(TArray<FVector2D>&& InPointsM);

	// --- 検査用の口 -------------------------------------------------------
	//
	// **表示の良し悪しは見る人が決める。** ここで外に出すのは、
	// 主観でない部分（値が有限か、参照周ができたか、差が幾らか）だけ。

	/** 受け取った値に NaN・無限大が混ざっていたか。**画面にも出す。** */
	bool HadInvalidInput() const { return bInvalidInput; }
	/** ベスト周の記録が溜まって、差を出せる状態か。 */
	bool HasDeltaReference() const { return bDeltaReferenceReady; }
	/** ベスト周との差 [s]。`HasDeltaReference()` が false のときは 0。 */
	double LiveDeltaS() const { return DeltaS; }
	/** 現在の周の区間タイム [s]。まだ通っていない区間は 0。 */
	double CurrentSectorTimeS(int32 Sector) const;

	virtual int32 OnPaint(const FPaintArgs& Args, const FGeometry& AllottedGeometry,
	                      const FSlateRect& MyCullingRect,
	                      FSlateWindowElementList& OutDrawElements, int32 LayerId,
	                      const FWidgetStyle& InWidgetStyle,
	                      bool bParentEnabled) const override;

	virtual FVector2D ComputeDesiredSize(float) const override
	{
		return FVector2D(1920.0, 1080.0);
	}

private:
	// 各部品。**それぞれが渡された矩形の中だけを描く。**
	int32 PaintCluster(const FGeometry& Geometry, FSlateWindowElementList& Out,
	                   int32 Layer, const FLayout& Layout) const;
	int32 PaintShiftLights(const FGeometry& Geometry, FSlateWindowElementList& Out,
	                       int32 Layer, const FSlateRect& Area, float Scale) const;
	int32 PaintRevBar(const FGeometry& Geometry, FSlateWindowElementList& Out,
	                  int32 Layer, const FSlateRect& Area, float Scale) const;
	int32 PaintInputs(const FGeometry& Geometry, FSlateWindowElementList& Out,
	                  int32 Layer, const FLayout& Layout) const;
	int32 PaintTiming(const FGeometry& Geometry, FSlateWindowElementList& Out,
	                  int32 Layer, const FLayout& Layout) const;
	int32 PaintMiniMap(const FGeometry& Geometry, FSlateWindowElementList& Out,
	                   int32 Layer, const FLayout& Layout) const;
	int32 PaintGrip(const FGeometry& Geometry, FSlateWindowElementList& Out,
	                int32 Layer, const FLayout& Layout) const;
	int32 PaintStatus(const FGeometry& Geometry, FSlateWindowElementList& Out,
	                  int32 Layer, const FLayout& Layout) const;
	int32 PaintCountdown(const FGeometry& Geometry, FSlateWindowElementList& Out,
	                     int32 Layer, const FLayout& Layout) const;
	int32 PaintConfidence(const FGeometry& Geometry, FSlateWindowElementList& Out,
	                      int32 Layer, const FLayout& Layout) const;

	/** 半透明のパネルと細い枠。**HUD の地。** */
	int32 PaintPanel(const FGeometry& Geometry, FSlateWindowElementList& Out,
	                 int32 Layer, const FSlateRect& Area, float Opacity = 1.0f) const;

	// --- 受け取った値から HUD 側で組み立てるもの ---------------------------
	//
	// **すべて `FHudSnapshot` にある値だけから作る。** 無い値
	// （タイヤ温度、燃料）は作れないので出さない。

	/** 入力の履歴を1点進める。 */
	void PushInputTrace(const ZN6::FHudSnapshot& In);
	/** 区間タイムを刻む・ベスト周を拾う・差を出す。 */
	void UpdateTiming(const ZN6::FHudSnapshot& In);
	/** セッションが変わったら全部忘れる。 */
	void ResetDerived();

	ZN6::FHudSnapshot Snapshot;

	/** 受け取った値に有限でないものが混ざっていた。**隠さず画面に出す。** */
	bool bInvalidInput = false;

	// --- 入力の履歴（スクロールするトレース）-------------------------------
	//
	// 参考にした作法: アクセルは緑、ブレーキは赤の折れ線を、数秒ぶん
	// 横に流す（.cpp 冒頭の出典 [4][5]）。**キーボードでも「いつ緩めたか」
	// が見えるようにする。**
	static constexpr int32 TraceSamples = 200;
	/** 履歴の長さ [s]。参考にした作法は 5 秒前後（出典 [1]）。 */
	static constexpr double TraceWindowS = 5.0;

	float TraceThrottle[TraceSamples] = {};
	float TraceBrake[TraceSamples] = {};
	float TraceSteer[TraceSamples] = {};
	int32 TraceCount = 0;
	int32 TraceHead = 0;
	double LastTraceTimeS = -1.0;

	// --- ベスト周との差 -----------------------------------------------------
	//
	// **1周の進捗ごとに「そこを通った時刻」を覚えておく。**
	// ベスト周のそれと引き算すれば、今どれだけ速いか遅いかが出る。
	// 実在のゲームがやっているのと同じ作法（出典 [2][3]）。
	//
	// これは記録した値の引き算であって、推測ではない。
	static constexpr int32 DeltaBins = 256;
	/** そのビンを通過した時刻 [s]。-1 は未通過。 */
	TArray<float> CurrentTrace;
	TArray<float> ReferenceTrace;
	int32 CurrentFilledBins = 0;
	bool bDeltaReferenceReady = false;
	double DeltaS = 0.0;
	bool bDeltaValidNow = false;

	// --- 区間タイム ---------------------------------------------------------
	//
	// `FRaceDirector` は今の周の区間タイムを外に出していないので、
	// **区間番号が変わった時刻の差**で刻む。値は `LapTimeS` そのものなので、
	// 独自の時計を持たない。
	double CurrentSectorS[3] = {};
	double SectorOpenedAtS = 0.0;
	int32 PrevSector = 0;
	/** セッション中のその区間の最速 [s]。0 はまだ無い。 */
	double SessionBestSectorS[3] = {};
	/** ベスト周のその区間 [s]。0 はまだ無い。 */
	double ReferenceSectorS[3] = {};
	/** 0=未計測 1=セッション最速 2=ベスト周より速い 3=それ以外。 */
	int32 SectorFlag[3] = {};
	/**
	 * 今出している区間タイムが**前の周のもの**か。
	 *
	 * 周が変わった瞬間に空にすると、ゴール直後に3区間とも `--.---` になって
	 * 「さっきの周がどうだったか」を読む間が無い。次の区間を刻むまで残す。
	 */
	bool bSectorsFromCompletedLap = false;

	int32 SeenLapCount = 0;
	int32 SeenLapNumber = 0;

	/** 中心線（世界座標 [m]）と、それを囲む矩形。 */
	TArray<FVector2D> CentrelineM;
	FVector2D MapMinM = FVector2D::ZeroVector;
	FVector2D MapMaxM = FVector2D::ZeroVector;

	const FSlateBrush* WhiteBrush = nullptr;
};
