// UE5 の Actor と物理モデルの接続点。
//
// **この Actor は物理を持たない。** 物理は Physics/ZN6Vehicle（Python から
// 移植した平面3自由度モデル）が全て担当し、Actor はその結果を描画へ写すだけ。
// UE の Chaos 物理は使わない。
//
// SPEC_ZN6.md §10.3 の判定基準:
//   - 物理計算と描画が分離されている（描画メッシュが物理に影響しない）
//   - Physics tick が目標周波数で回っている
//
// **憲法ルール4「物理計算と表示用3Dモデルを完全に分離する」の実装。**
// 情報の流れは必ず 物理 -> 描画 の一方向。描画メッシュの頂点・トランスフォーム・
// コリジョンを物理計算に読み戻してはいけない。

#pragma once

#include "CoreMinimal.h"
#include "GameFramework/Actor.h"
#include "Physics/ZN6Vehicle.h"
#include "ZN6VehicleActor.generated.h"

class UStaticMeshComponent;

/**
 * 固定刻みで物理を回すためのアキュムレータ。
 *
 * **描画のフレーム時間で物理を進めない。** フレームレートが変われば
 * 結果が変わってしまい、再現性が無くなる。フレーム時間を貯めて、
 * 固定刻みぶん溜まるたびに物理を1ステップ進める。
 */
USTRUCT()
struct FZN6FixedStepAccumulator
{
	GENERATED_BODY()

	/**
	 * 物理の固定刻み [s]。既定 1 ms（1,000 Hz）。
	 *
	 * **2 ms にしてはいけない。** 車輪回転の陽解法積分が低速で不安定になる
	 * （issue #24）。1 ms でも静止発進は不安定だが、2 ms より桁で良い。
	 * #24 が閉じたら見直すこと。
	 */
	UPROPERTY(EditAnywhere, Category = "ZN6|Physics")
	float FixedStepS = 0.001f;

	/**
	 * 1フレームで進める最大ステップ数。
	 *
	 * **これが無いと「死のスパイラル」に入る。** フレームが重い ->
	 * 溜まった時間ぶん多くステップを回す -> さらに重くなる、の悪循環。
	 * 上限に当たったぶんは捨てる（シミュレーション時間が実時間より遅れる）。
	 */
	UPROPERTY(EditAnywhere, Category = "ZN6|Physics")
	int32 MaxStepsPerFrame = 200;

	double AccumulatedS = 0.0;

	/** 直前のフレームで実際に回したステップ数（計測用）。 */
	int32 LastStepCount = 0;

	/** 上限に当たって捨てた時間の合計 [s]（計測用。0 でないなら間に合っていない）。 */
	double DroppedS = 0.0;

	/**
	 * フレーム時間を渡し、回すべきステップ数を返す。
	 * 返した数だけ呼び出し側が固定刻みで物理を進めること。
	 */
	int32 Consume(double FrameDeltaS);
};

UCLASS()
class ZN6DIGITALTWIN_API AZN6VehicleActor : public AActor
{
	GENERATED_BODY()

public:
	AZN6VehicleActor();

	virtual void BeginPlay() override;
	virtual void Tick(float DeltaSeconds) override;

	/**
	 * vehicle.json を読み込んで物理モデルを初期化する。
	 * @return 失敗したら false（**デフォルト値で代用しない**）
	 */
	bool InitialisePhysics(const FString& VehicleJsonPath, FString& OutError);

	/** 物理だけを1フレームぶん進める（描画を伴わない。テスト用）。 */
	void AdvancePhysics(double FrameDeltaS);

	/** 描画コンポーネントへ物理の結果を書く。**逆方向は無い。** */
	void SyncVisualToPhysics();

	const ZN6::FVehicleState& GetPhysicsState() const { return PhysicsState; }
	const ZN6::FVehicleOutputs& GetPhysicsOutputs() const { return PhysicsOutputs; }
	const FZN6FixedStepAccumulator& GetAccumulator() const { return Accumulator; }
	bool IsPhysicsReady() const { return bPhysicsReady; }

	void SetControl(const ZN6::FControlInput& InControl) { Control = InControl; }
	void SetPhysicsState(const ZN6::FVehicleState& InState) { PhysicsState = InState; }

	ZN6::FVehicleState MakeInitialState(double SpeedMps, int32 GearIndex) const;

	/** 物理を進めた累計時間 [s]。フレームレートに依存しないことの確認に使う。 */
	double GetSimulatedTimeS() const { return SimulatedTimeS; }

	/** 累計ステップ数。目標周波数で回っているかの判定に使う。 */
	int64 GetTotalStepCount() const { return TotalStepCount; }

protected:
	/** **描画専用。** 物理はこのコンポーネントを一切参照しない。 */
	UPROPERTY(VisibleAnywhere, Category = "ZN6|Visual")
	TObjectPtr<UStaticMeshComponent> VisualMesh;

	UPROPERTY(EditAnywhere, Category = "ZN6|Physics")
	FZN6FixedStepAccumulator Accumulator;

private:
	ZN6::FVehicleData VehicleData;
	ZN6::FVehicle Vehicle;
	ZN6::FVehicleState PhysicsState;
	ZN6::FVehicleOutputs PhysicsOutputs;
	ZN6::FControlInput Control;

	bool bPhysicsReady = false;
	double SimulatedTimeS = 0.0;
	int64 TotalStepCount = 0;
};
