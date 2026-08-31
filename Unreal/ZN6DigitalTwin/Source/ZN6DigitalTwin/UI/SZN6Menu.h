// メニュー（走行開始・セッティング・画質設定・リザルト）。
//
// **HUD と同じく Slate で直接描く。** .uasset を作らないので、
// 画面の変更が全部ソースの差分として残る。
//
// 操作はキーボードだけで完結させる。**マウスが要る画面にしない**のは、
// ハンドルから手を離さずに設定を変えられるようにするため。
//
// このウィジェットは**車を知らない。** 何かを決めたらデリゲートで
// 外へ伝えるだけで、物理も描画も自分では触らない。

#pragma once

#include "CoreMinimal.h"
#include "Physics/ZN6Setup.h"
#include "UI/ZN6HudSnapshot.h"
#include "Widgets/DeclarativeSyntaxSupport.h"
#include "Widgets/SCompoundWidget.h"

DECLARE_DELEGATE(FZN6MenuAction);
DECLARE_DELEGATE_OneParam(FZN6SetupChanged, const ZN6::FCarSetup&);

class SZN6Menu : public SCompoundWidget
{
public:
	/** どの画面を出しているか。 */
	enum class EPage : uint8
	{
		Main,
		Setup,
		Graphics,
		Result,
	};

	SLATE_BEGIN_ARGS(SZN6Menu) {}
		SLATE_EVENT(FZN6MenuAction, OnStartRace)
		SLATE_EVENT(FZN6MenuAction, OnFreeRun)
		SLATE_EVENT(FZN6MenuAction, OnResume)
		SLATE_EVENT(FZN6MenuAction, OnQuit)
		SLATE_EVENT(FZN6SetupChanged, OnSetupChanged)
	SLATE_END_ARGS()

	void Construct(const FArguments& InArgs);

	/** 調整範囲を渡す。**1回だけ。** */
	void SetLimits(const ZN6::FSetupLimits& InLimits) { Limits = InLimits; bHasLimits = true; }

	/** 今のセッティングを反映する（外で変わったとき）。 */
	void SetSetup(const ZN6::FCarSetup& InSetup) { Setup = InSetup; }
	const ZN6::FCarSetup& GetSetup() const { return Setup; }

	/** リザルト表示に使う値。 */
	void SetSnapshot(const ZN6::FHudSnapshot& InSnapshot) { Snapshot = InSnapshot; }

	/** 開く / 閉じる。**閉じているときは入力を受けない。** */
	void Open(EPage Page);
	void Close();
	bool IsOpen() const { return bOpen; }
	EPage CurrentPage() const { return Page; }

	virtual int32 OnPaint(const FPaintArgs& Args, const FGeometry& AllottedGeometry,
	                      const FSlateRect& MyCullingRect,
	                      FSlateWindowElementList& OutDrawElements, int32 LayerId,
	                      const FWidgetStyle& InWidgetStyle,
	                      bool bParentEnabled) const override;

	virtual FReply OnKeyDown(const FGeometry& Geometry, const FKeyEvent& Key) override;
	virtual bool SupportsKeyboardFocus() const override { return true; }

	virtual FVector2D ComputeDesiredSize(float) const override
	{
		return FVector2D(1920.0, 1080.0);
	}

private:
	int32 PaintChrome(const FGeometry& Geometry, FSlateWindowElementList& Out,
	                  int32 Layer, const FVector2f& Screen) const;
	int32 PaintMain(const FGeometry& Geometry, FSlateWindowElementList& Out,
	                int32 Layer, const FVector2f& Origin, const FVector2f& Size) const;
	int32 PaintSetup(const FGeometry& Geometry, FSlateWindowElementList& Out,
	                 int32 Layer, const FVector2f& Origin, const FVector2f& Size) const;
	int32 PaintGraphics(const FGeometry& Geometry, FSlateWindowElementList& Out,
	                    int32 Layer, const FVector2f& Origin, const FVector2f& Size) const;
	int32 PaintResult(const FGeometry& Geometry, FSlateWindowElementList& Out,
	                  int32 Layer, const FVector2f& Origin, const FVector2f& Size) const;
	int32 PaintHint(const FGeometry& Geometry, FSlateWindowElementList& Out,
	                int32 Layer, const FVector2f& Screen, const FString& Hint) const;

	/** 1行ぶんのスライダー。 */
	int32 PaintSlider(const FGeometry& Geometry, FSlateWindowElementList& Out,
	                  int32 Layer, const FVector2f& Origin, float Width,
	                  const FString& Label, const FString& Value,
	                  double Fraction, bool bSelected, bool bAdjustable) const;

	void MoveSelection(int32 Delta);
	void Adjust(int32 Direction);
	void Activate();
	void GoBack();

	/** 画面ごとの行数。 */
	int32 RowCount() const;

	// --- 画質設定 ---
	//
	// **UGameUserSettings に委ねる。** 自前で解像度やシャドウを触ると、
	// エンジンの設定と二重管理になる。
	void ApplyGraphics() const;
	int32 GraphicsRowCount() const { return 5; }
	FString GraphicsLabel(int32 Row) const;
	FString GraphicsValue(int32 Row) const;
	void AdjustGraphics(int32 Row, int32 Direction);

	bool bOpen = false;
	EPage Page = EPage::Main;
	int32 Selected = 0;

	ZN6::FCarSetup Setup;
	ZN6::FSetupLimits Limits;
	bool bHasLimits = false;
	ZN6::FHudSnapshot Snapshot;

	FZN6MenuAction OnStartRace;
	FZN6MenuAction OnFreeRun;
	FZN6MenuAction OnResume;
	FZN6MenuAction OnQuit;
	FZN6SetupChanged OnSetupChanged;

	const FSlateBrush* WhiteBrush = nullptr;
};
