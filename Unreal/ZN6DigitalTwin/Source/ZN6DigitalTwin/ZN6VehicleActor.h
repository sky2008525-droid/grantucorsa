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
#include "GameFramework/Pawn.h"
#include "Physics/ZN6Vehicle.h"
#include "ZN6VehicleActor.generated.h"

class UCameraComponent;
class USpringArmComponent;
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

/**
 * 運転操作を物理の入力へ変換する層。
 *
 * **ここに書く値は車両仕様ではない**（憲法ルール18「現実の車両仕様と
 * ゲーム上の演出を明確に分離する」）。キーボードは踏み込み量を持たない
 * ので、操作感のために補間や制限を入れる必要がある。それは操作系の
 * 都合であって、実車の特性ではない。**vehicle.json に混ぜないこと。**
 */
USTRUCT()
struct FZN6DriverFeel
{
	GENERATED_BODY()

	/**
	 * 操舵をどれだけ速く動かすか [rad/s]。
	 *
	 * キーボードは 0 か 1 しか出せない。そのまま入れると最大舵角へ
	 * 一瞬で飛び、**FR なので即スピンして1コーナーも曲がれない。**
	 */
	UPROPERTY(EditAnywhere, Category = "ZN6|Driver")
	float SteerRateRadPerS = 1.1f;

	/** キーを離したときに中立へ戻る速さ [rad/s]。 */
	UPROPERTY(EditAnywhere, Category = "ZN6|Driver")
	float SteerReturnRateRadPerS = 2.2f;

	/**
	 * 高速では舵角を絞る [1/(m/s)]。
	 *
	 * 実車のステアリングギア比は一定だが、**キーボードには「少しだけ
	 * 切る」が無い。** 速度が上がるほど最大舵角を下げないと、直線で
	 * 少し当てただけでスピンする。**これは操作系の補助であって、
	 * 車の特性ではない。**
	 */
	UPROPERTY(EditAnywhere, Category = "ZN6|Driver")
	float SteerSpeedFalloffPerMps = 0.045f;

	/** アクセル・ブレーキの立ち上がり [1/s]。 */
	UPROPERTY(EditAnywhere, Category = "ZN6|Driver")
	float PedalRatePerS = 4.0f;
};

UCLASS()
class ZN6DIGITALTWIN_API AZN6VehicleActor : public APawn
{
	GENERATED_BODY()

public:
	AZN6VehicleActor();

	virtual void BeginPlay() override;
	virtual void Tick(float DeltaSeconds) override;
	virtual void SetupPlayerInputComponent(UInputComponent* PlayerInputComponent) override;

	/**
	 * vehicle.json を読み込んで物理モデルを初期化する。
	 * @return 失敗したら false（**デフォルト値で代用しない**）
	 */
	bool InitialisePhysics(const FString& VehicleJsonPath, FString& OutError);

	/**
	 * Blender が書いた manifest.json から車輪の取り付け位置を読む。
	 *
	 * **位置をここにベタ書きしないこと。** 3Dモデルを差し替えたら
	 * 取り付け位置も変わる。Blender/decompose_vehicle.py が出した値を
	 * 唯一の情報源にする（vehicle.json と同じ考え方）。
	 *
	 * なお、ここで読むのは**描画用モデル自身の車輪位置**であって、
	 * 物理の車輪位置（vehicle.json の wheelbase / track）ではない。
	 * 憲法ルール4により両者は独立してよい。
	 */
	bool LoadVisualManifest(const FString& ManifestPath, FString& OutError);

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

	/** 描画用の車輪回転角 [rad]。**物理には存在しない量。** */
	double GetVisualWheelAngleRad(int32 WheelIndex) const
	{
		return (WheelIndex >= 0 && WheelIndex < ZN6::WheelCount)
			? VisualWheelAngleRad[WheelIndex] : 0.0;
	}

	/** 車輪の取り付け位置 [m]（物理座標系）。manifest から読んだ値。 */
	FVector GetWheelAttachM(int32 WheelIndex) const
	{
		return (WheelIndex >= 0 && WheelIndex < ZN6::WheelCount)
			? WheelAttachM[WheelIndex] : FVector::ZeroVector;
	}

	/** 現在の操作入力（テストと HUD 用）。 */
	const ZN6::FControlInput& GetControl() const { return Control; }

	/** スタートラインへ戻す。**物理と描画の両方を初期化する。** */
	void ResetToStart();

	/** 最大舵角 [rad]。vehicle.json の最小回転半径から導いた値。 */
	double GetMaxSteerRad() const { return MaxSteerRad; }

	// --- テスト用の入り口 ---------------------------------------------------
	//
	// **入力の変換は PlayerController 無しでも検査できるようにする。**
	// 実際にキーを押さないと確かめられない作りにすると、変速の範囲外や
	// 舵角の即時最大化のような壊れ方が誰にも気づかれない。
	void ShiftUpForTest() { ShiftUp(); }
	void ShiftDownForTest() { ShiftDown(); }
	void SetSteerInputForTest(float Value) { RawSteer = Value; }
	void SetThrottleInputForTest(float Value) { RawThrottle = Value; }
	void SetBrakeInputForTest(float Value) { RawBrake = Value; }
	void ApplyDriverInputForTest(float DeltaSeconds) { ApplyDriverInput(DeltaSeconds); }

protected:
	/** 追従カメラ。**描画専用で、物理には一切関与しない。** */
	UPROPERTY(VisibleAnywhere, Category = "ZN6|Visual")
	TObjectPtr<USpringArmComponent> CameraBoom;

	UPROPERTY(VisibleAnywhere, Category = "ZN6|Visual")
	TObjectPtr<UCameraComponent> ChaseCamera;

	/** 操作感の設定。**車両仕様ではない**（憲法ルール18）。 */
	UPROPERTY(EditAnywhere, Category = "ZN6|Driver")
	FZN6DriverFeel DriverFeel;

	/** 画面にテレメトリを出すか。 */
	UPROPERTY(EditAnywhere, Category = "ZN6|Driver")
	bool bShowTelemetry = true;

private:
	// --- 入力ハンドラ -------------------------------------------------------
	void InputThrottle(float Value);
	void InputBrake(float Value);
	void InputSteer(float Value);
	void InputClutch(float Value);
	void InputHandbrake(float Value);
	void ShiftUp();
	void ShiftDown();

	/** 生の入力を、時間をかけて Control へ反映する。 */
	void ApplyDriverInput(float DeltaSeconds);

	void DrawTelemetry() const;

	// 生の入力（-1..1 / 0..1）。**Control とは別に持つ。**
	float RawThrottle = 0.0f;
	float RawBrake = 0.0f;
	float RawSteer = 0.0f;
	float RawClutch = 0.0f;
	float RawHandbrake = 0.0f;

protected:
	/** **描画専用。** 物理はこのコンポーネントを一切参照しない。 */
	UPROPERTY(VisibleAnywhere, Category = "ZN6|Visual")
	TObjectPtr<UStaticMeshComponent> BodyMesh;

	/**
	 * 車輪の描画メッシュ。FL / FR / RL / RR の順（ZN6::EWheel と同じ）。
	 *
	 * **物理の車輪と1対1で対応させること。** 順番がずれると、
	 * 左に曲がっているのに右の車輪が切れる、という絵になる。
	 */
	UPROPERTY(VisibleAnywhere, Category = "ZN6|Visual")
	TArray<TObjectPtr<UStaticMeshComponent>> WheelMeshes;

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

	/**
	 * 車輪の取り付け位置 [m]（物理座標系: X 前方 / Y 左 / Z 上）。
	 * manifest.json から読む。読めていなければ描画位置を動かさない。
	 */
	FVector WheelAttachM[ZN6::WheelCount] = {};
	bool bVisualManifestLoaded = false;

	/**
	 * **描画専用の車輪回転角 [rad]。**
	 *
	 * 物理の状態は角速度（WheelOmegaRads）までしか持たない。角度は
	 * 運動方程式に現れないので、物理側に持たせると「使われない状態変数」
	 * が増えるだけになる。ここで積分して描画にだけ使う。
	 *
	 * **この値を物理へ戻さないこと。** 憲法ルール4（物理と表示の分離）。
	 */
	double VisualWheelAngleRad[ZN6::WheelCount] = {};

	/**
	 * 最大舵角 [rad]。
	 *
	 * **vehicle.json の `dimensions.min_turning_radius`（official）から導く。**
	 * 自転車モデルで atan(wheelbase / turning_radius)。最小回転半径は
	 * 外側前輪の軌跡半径なので厳密には一致しないが、**勝手な値を置くより
	 * 一次資料から導いたほうがよい**（憲法ルール1・2）。
	 *
	 * 読めなかったときは 0 のままにして、操舵を効かせない。
	 * **「それらしい既定値」で埋めないこと。**
	 */
	double MaxSteerRad = 0.0;
};
