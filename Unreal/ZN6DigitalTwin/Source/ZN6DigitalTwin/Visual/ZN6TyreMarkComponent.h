// タイヤ痕。
//
// **これは絵であって物理ではない**（憲法ルール18）。痕を残しても
// グリップは変わらない。実際のタイヤはゴムを置いていくぶん摩擦が変わるが、
// その量を測った資料が無いので**モデルに入れない。**
//
// 何を根拠に出すかだけは物理から取る:
//
//   - **滑り**（摩擦円の利用率）が閾値を超えたら出す
//   - **接地している輪だけ**。浮いている輪は地面に触れていない
//   - 濃さは滑りの強さで決める
//
// 「アクセルを踏んだら出す」ようには**しない。** それだと痕が
// 物理と関係ない飾りになり、見ても何も分からなくなる。

#pragma once

#include "CoreMinimal.h"
#include "Components/SceneComponent.h"
#include "Physics/ZN6Vehicle.h"
#include "ZN6TyreMarkComponent.generated.h"

class UDecalComponent;
class UMaterialInterface;

/** 痕の出し方。**車両仕様ではない。** */
USTRUCT()
struct FZN6TyreMarkFeel
{
	GENERATED_BODY()

	/** ここを超えたら痕を出す [-]（摩擦円の利用率）。 */
	UPROPERTY(EditAnywhere)
	float SlipThreshold = 0.55f;

	/** 濃さが最大になる利用率 [-]。 */
	UPROPERTY(EditAnywhere)
	float SlipFull = 1.0f;

	/** いちばん濃いときの不透明度 [-]。 */
	UPROPERTY(EditAnywhere)
	float MaxOpacity = 0.85f;

	/** 痕を1つ置く間隔 [m]。**細かすぎると数がすぐ尽きる。** */
	UPROPERTY(EditAnywhere)
	float SpacingM = 0.35f;

	/** 痕1つの長さ [m]。間隔より少し長くして隙間を埋める。 */
	UPROPERTY(EditAnywhere)
	float LengthM = 0.55f;

	/** 痕の幅 [m]。タイヤ幅（215mm）に合わせる。 */
	UPROPERTY(EditAnywhere)
	float WidthM = 0.215f;

	/** 消えるまでの時間 [s]。0 なら消えない。 */
	UPROPERTY(EditAnywhere)
	float FadeSeconds = 45.0f;

	/**
	 * 同時に出せる痕の数。
	 *
	 * **上限を必ず持つ。** 持たないと、滑り続けるだけで際限なく
	 * コンポーネントが増えてフレームレートが落ちていく。
	 * 古いものから使い回す。
	 */
	UPROPERTY(EditAnywhere)
	int32 MaxMarks = 720;
};

UCLASS(ClassGroup = (ZN6), meta = (BlueprintSpawnableComponent))
class ZN6DIGITALTWIN_API UZN6TyreMarkComponent : public USceneComponent
{
	GENERATED_BODY()

public:
	UZN6TyreMarkComponent();

	/** デカールのマテリアルを読む。**無ければ痕は出ない**（警告する）。 */
	bool Initialise(FString& OutError);
	bool IsReady() const { return bReady; }

	/**
	 * 1フレーム分。**物理の後に呼ぶ。**
	 *
	 * @param WheelWorldCm  4輪の接地点（ワールド、cm）
	 * @param Utilisation   4輪の摩擦円利用率
	 * @param bContact      接地しているか
	 * @param HeadingRad    車の向き（物理の座標系）
	 */
	void Update(float DeltaSeconds, const FVector WheelWorldCm[ZN6::WheelCount],
	            const double Utilisation[ZN6::WheelCount],
	            const bool bContact[ZN6::WheelCount], double HeadingRad);

	/** 全部消す。**位置をリセットしたときに呼ぶ。** */
	void ClearMarks();

	int32 GetLiveMarkCount() const { return LiveMarks; }

private:
	/** 1つ置く。使い回しの都合で必ずここを通す。 */
	void PlaceMark(const FVector& WorldCm, double HeadingRad, float Opacity);

	UPROPERTY(EditAnywhere, Category = "ZN6|Visual")
	FZN6TyreMarkFeel Feel;

	UPROPERTY(Transient)
	TArray<UDecalComponent*> Decals;

	UPROPERTY(Transient)
	TObjectPtr<UMaterialInterface> MarkMaterial;

	/** 次に使い回す番号。**古いものから潰す。** */
	int32 NextIndex = 0;
	int32 LiveMarks = 0;

	/** 車輪ごとの「前回置いた場所」。間隔を測るのに使う。 */
	FVector LastPlacedCm[ZN6::WheelCount] = {};
	bool bHasLastPlaced[ZN6::WheelCount] = {};

	/** 痕ごとの残り時間 [s]。 */
	TArray<float> RemainingS;

	bool bReady = false;
};
