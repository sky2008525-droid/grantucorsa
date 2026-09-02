// 走行中の画面（計器・タイム・ミニマップ・カウントダウン）。
//
// **全部を1つのウィジェットで描く。** 子ウィジェットに分けると、
// 計器の針とタイムの位置関係を揃えるのにレイアウトの都合が入り込む。
// ここは自由に描きたいので `OnPaint` で直接引く。
//
// データは `FHudSnapshot` からのみ読む。**車のオブジェクトを持たない。**

#pragma once

#include "CoreMinimal.h"
#include "UI/ZN6HudSnapshot.h"
#include "Widgets/DeclarativeSyntaxSupport.h"
#include "Widgets/SCompoundWidget.h"

class SZN6Hud : public SCompoundWidget
{
public:
	SLATE_BEGIN_ARGS(SZN6Hud) {}
	SLATE_END_ARGS()

	void Construct(const FArguments& InArgs);

	/** 毎フレーム、車から詰めた値を渡す。**一方向。** */
	void SetSnapshot(const ZN6::FHudSnapshot& InSnapshot) { Snapshot = InSnapshot; }

	/**
	 * ミニマップに描く中心線を渡す。**1回だけ。**
	 * 毎フレーム渡すと、千点の配列を毎回コピーすることになる。
	 */
	void SetCentreline(TArray<FVector2D>&& InPointsM);

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
	// 各部品。**それぞれが自分の矩形の中だけを描く。**
	int32 PaintTachometer(const FGeometry& Geometry, FSlateWindowElementList& Out,
	                      int32 Layer, const FVector2f& Centre, float Radius) const;
	int32 PaintSpeedAndGear(const FGeometry& Geometry, FSlateWindowElementList& Out,
	                        int32 Layer, const FVector2f& Centre, float Radius) const;
	int32 PaintPedals(const FGeometry& Geometry, FSlateWindowElementList& Out,
	                  int32 Layer, const FVector2f& Origin) const;
	int32 PaintTiming(const FGeometry& Geometry, FSlateWindowElementList& Out,
	                  int32 Layer, const FVector2f& Origin) const;
	int32 PaintMiniMap(const FGeometry& Geometry, FSlateWindowElementList& Out,
	                   int32 Layer, const FVector2f& Origin, const FVector2f& Size) const;
	int32 PaintGrip(const FGeometry& Geometry, FSlateWindowElementList& Out,
	                int32 Layer, const FVector2f& Origin) const;
	int32 PaintCountdown(const FGeometry& Geometry, FSlateWindowElementList& Out,
	                     int32 Layer, const FVector2f& ScreenSize) const;
	int32 PaintConfidence(const FGeometry& Geometry, FSlateWindowElementList& Out,
	                      int32 Layer, const FVector2f& ScreenSize) const;

	/** 半透明のパネルと細い枠。**HUD の地。** */
	int32 PaintPanel(const FGeometry& Geometry, FSlateWindowElementList& Out,
	                 int32 Layer, const FVector2f& Origin, const FVector2f& Size,
	                 float Opacity = 1.0f) const;

	ZN6::FHudSnapshot Snapshot;

	/** 中心線（世界座標 [m]）と、それを囲む矩形。 */
	TArray<FVector2D> CentrelineM;
	FVector2D MapMinM = FVector2D::ZeroVector;
	FVector2D MapMaxM = FVector2D::ZeroVector;

	const FSlateBrush* WhiteBrush = nullptr;
};
