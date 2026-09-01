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
#include "Audio/ZN6VehicleAudioComponent.h"
#include "Game/ZN6RaceDirector.h"
#include "UI/ZN6HudSnapshot.h"
#include "UI/ZN6Livery.h"
#include "Visual/ZN6TyreMarkComponent.h"
#include "Physics/ZN6Obstacles.h"
#include "Physics/ZN6Ride.h"
#include "Physics/ZN6Track.h"
#include "Physics/ZN6Terrain.h"
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
/**
 * 運転支援。**車両仕様ではなく、操作の補助**（憲法ルール18）。
 *
 * **必ず切れるようにしてある。** 検証は全部切った状態で行う。
 * ここの数値に出典は要らない（実車の制御ではないため）が、
 * **実車の挙動として語らないこと。**
 */
USTRUCT()
struct FZN6DriverAssists
{
	GENERATED_BODY()

	/** 自動変速。**既定は off。** 6MT の車なので、入れるのは補助である。 */
	UPROPERTY(EditAnywhere)
	bool bAutoShift = false;

	/** シフトアップする回転数 [1/min]。レッドラインより下に置く。 */
	UPROPERTY(EditAnywhere)
	float UpshiftRpm = 6800.0f;

	/** シフトダウンする回転数 [1/min]。 */
	UPROPERTY(EditAnywhere)
	float DownshiftRpm = 2600.0f;

	/**
	 * 変速してから次に変速できるまでの間隔 [s]。
	 *
	 * **これが無いと、境目で上下に振動して延々と変速し続ける。**
	 */
	UPROPERTY(EditAnywhere)
	float ShiftIntervalS = 0.45f;
};

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

/**
 * 荷重移動を車体の傾きとして見せるための設定。
 *
 * **これは車両仕様ではない。実車のロール角でもない。**
 *
 * 実車のロール角を出すには、ロール剛性が要る。しかし
 *
 *   - `suspension.spring_rate_*` は estimated だが、**モーションレシオが
 *     unknown なのでホイールレートが決まらない**（vehicle.json の WARNING）
 *   - `suspension.damper_front` / `damper_rear` は **"unknown"**
 *   - ロールセンタ高さは vehicle.json に無い
 *
 * の3つが欠けており、角度を物理的に導けない。憲法ルール1は「数値が
 * 見つからないときの正解は空欄のまま残すこと」としており、実際
 * damper は "unknown" と明記されている。**それを勝手に埋めない。**
 *
 * 一方で**荷重移動そのものは official な実データ**（質量・重心高・
 * ホイールベース・トレッド）から出ており根拠がある。そこでここでは
 * 「荷重移動を目に見える形にする」ことだけを行い、**係数は演出値として
 * vehicle.json の外に置く**（憲法ルール18）。
 *
 * データが揃ったら、これを捨てて本物のロール自由度に置き換えること
 * （issue #19）。
 */
USTRUCT()
struct FZN6BodyAttitudeFeel
{
	GENERATED_BODY()

	/** 横 1G あたり何度傾けるか [deg/G]。**実車の値ではない。** */
	UPROPERTY(EditAnywhere, Category = "ZN6|Attitude")
	float RollDegPerG = 4.5f;

	/** 前後 1G あたり何度ピッチさせるか [deg/G]。**実車の値ではない。** */
	UPROPERTY(EditAnywhere, Category = "ZN6|Attitude")
	float PitchDegPerG = 2.6f;

	/** 応答の速さ [Hz]。実車のロール固有振動数はこのあたり。 */
	UPROPERTY(EditAnywhere, Category = "ZN6|Attitude")
	float ResponseHz = 1.4f;

	/** 減衰比。1 未満で少し揺り返す。 */
	UPROPERTY(EditAnywhere, Category = "ZN6|Attitude")
	float DampingRatio = 0.75f;

	/**
	 * 傾きの回転中心の高さ [m]（接地面から）。
	 *
	 * **ロールセンタではない。** 0 にすると車体の下端が地面へめり込む
	 * ので、見た目が破綻しない位置を選んでいるだけ。
	 */
	UPROPERTY(EditAnywhere, Category = "ZN6|Attitude")
	float PivotHeightM = 0.35f;
};

UCLASS()
class ZN6DIGITALTWIN_API AZN6VehicleActor : public APawn
{
	GENERATED_BODY()

public:
	AZN6VehicleActor();

	virtual void BeginPlay() override;
	virtual void EndPlay(const EEndPlayReason::Type Reason) override;
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

	/**
	 * 地形の高さ場を読む。
	 *
	 * **読めなければ平地として走る。** 既定値をでっち上げるより、
	 * 「地形が無い」ほうが誤解が無い。警告は出す。
	 */
	bool LoadHeightfield(const FString& HeightfieldPath, FString& OutError);

	/**
	 * 樹木と世界境界の当たり判定を読む。
	 *
	 * **読むのは配置データ（placement.json）であって、樹木メッシュの
	 * コリジョンではない**（憲法ルール4）。読めなければ当たり判定なしで
	 * 走る。地形と同じく、既定値をでっち上げない。
	 */
	bool LoadObstacles(const FString& PlacementPath, FString& OutError);

	/** 直近のステップで接触した障害物の数。**0 なら何も触れていない。** */
	int32 GetContactCount() const { return ContactCount; }

	/**
	 * 音を鳴らす準備をする。**読めなくても物理は動く。**
	 *
	 * 音は演出であって物理ではない（憲法ルール18）。ここが失敗しても
	 * 走りは変わらない。**逆に、音が出ていても走りが変わってはいけない。**
	 */
	bool InitialiseAudio(const FString& RepoRoot, FString& OutError);

	UZN6VehicleAudioComponent* GetAudio() const { return Audio; }

	/** 路面の端までの符号つき距離 [m]。内側が正。音のクロスフェードに使う。 */
	double GetDistanceToTrackEdgeM() const;

	/** コース定義を読む。**周回計測とミニマップと音のクロスフェードに使う。** */
	bool LoadTrack(const FString& TrackJsonPath, FString& OutError);

	const ZN6::FTrackEdge& GetTrack() const { return TrackEdge; }
	bool IsTrackLoaded() const { return bTrackEdgeLoaded; }

	// --- セッションの進行 ---------------------------------------------------
	//
	// **ここは物理ではない。** 時間を測って状態を切り替えるだけ。

	UFUNCTION(BlueprintCallable, Category = "ZN6|Race")
	bool StartCountdown() { return Race.StartCountdown(); }

	/** カウントダウン無しで走り出す（フリー走行）。 */
	UFUNCTION(BlueprintCallable, Category = "ZN6|Race")
	bool StartFreeRun() { return Race.StartFreeRun(); }

	UFUNCTION(BlueprintCallable, Category = "ZN6|Race")
	bool PauseRace() { return Race.Pause(); }

	UFUNCTION(BlueprintCallable, Category = "ZN6|Race")
	bool ResumeRace() { return Race.Resume(); }

	/** メニューへ戻す。**車も出発点へ戻す。** */
	UFUNCTION(BlueprintCallable, Category = "ZN6|Race")
	void ReturnToMenu();

	const ZN6::FRaceDirector& GetRace() const { return Race; }

	/** 画面へ渡す値を作る。**一方向。UI からは書き戻さない。** */
	ZN6::FHudSnapshot MakeHudSnapshot() const;

	/**
	 * セッティングを適用する。**物理モデルを作り直す。**
	 *
	 * 走行中に呼ぶことは想定していない（メニューから呼ぶ）。
	 * 車の状態は保つが、姿勢は新しいばねで釣り合わせ直す。
	 */
	void ApplySetup(const ZN6::FCarSetup& InSetup);

	const ZN6::FCarSetup& GetSetup() const { return Setup; }
	const ZN6::FSetupLimits& GetSetupLimits() const { return SetupLimits; }

	/** メニューを開く / 閉じる。 */
	UFUNCTION(BlueprintCallable, Category = "ZN6|UI")
	void ToggleMenu();

	/**
	 * ボディカラーを変える。**演出であって車両仕様ではない**（ルール18）。
	 *
	 * 塗るスロットは、元モデルの塗装色と一致するものだけ
	 * （`ZN6::IsPaintSlot`）。ガラスや灯火まで塗ると単色の塊になる。
	 */
	void SetBodyColour(const FLinearColor& Colour);

	int32 GetPaintIndex() const { return PaintIndex; }
	void SetPaintIndex(int32 Index);

	/** 車体が乗っている地面の高さ [m]（4輪の接地点の平均）。 */
	double GetGroundHeightM() const { return GroundHeightM; }

	/**
	 * 接地モデル（上下・ピッチ・ロール）。
	 *
	 * **これが入るまで、車体は地面の高さに置かれているだけだった。**
	 * 重力で落ちていないので、何にも支えられていなかった。今は
	 * 車輪が地面を押し、押し返された力で車体が支えられている。
	 */
	const ZN6::FRideState& GetRideState() const { return RideState; }
	const ZN6::FRideOutputs& GetRideOutputs() const { return RideOutputs; }
	const ZN6::FRideModel& GetRideModel() const { return Ride; }
	bool IsRideReady() const { return bRideReady; }

	/**
	 * 接地モデルを使うか。**切れるようにしてある**（憲法ルール18）。
	 *
	 * 切ると、地形の傾きを幾何から出す以前の見せ方に戻る。
	 * 検証のためであって、通常は入れたままでよい。
	 */
	UFUNCTION(BlueprintCallable, Category = "ZN6|Physics")
	void SetUseRideModel(bool bEnabled) { bUseRideModel = bEnabled; }

	UFUNCTION(BlueprintPure, Category = "ZN6|Physics")
	bool IsUsingRideModel() const { return bUseRideModel && bRideReady; }

	/**
	 * 接地モデルの力をタイヤの垂直荷重として使うか。
	 *
	 * **入れると、浮いた車輪のグリップが消える。** 切ると準静的な式に戻る
	 * （常に正の荷重なので、段差で跳ねてもタイヤが効いたままになる）。
	 *
	 * **入れるとラップタイムが変わる。** 左右の荷重配分がばねレートから
	 * 導出されるようになり、`roll_stiffness_distribution_front`（assumed
	 * 0.600）ではなくなるため。参照値（Reference/*.json）は準静的のままで、
	 * こちらの影響を受けない。
	 */
	UFUNCTION(BlueprintCallable, Category = "ZN6|Physics")
	void SetRideDrivesTyreLoads(bool bEnabled) { bRideDrivesTyreLoads = bEnabled; }

	/** 自動変速。**補助であって車両仕様ではない**（ルール18）。切れる。 */
	UFUNCTION(BlueprintCallable, Category = "ZN6|Assist")
	void SetAutoShift(bool bEnabled) { Assists.bAutoShift = bEnabled; }

	UFUNCTION(BlueprintPure, Category = "ZN6|Assist")
	bool IsAutoShiftEnabled() const { return Assists.bAutoShift; }

	UFUNCTION(BlueprintPure, Category = "ZN6|Physics")
	bool IsRideDrivingTyreLoads() const
	{
		return bRideDrivesTyreLoads && IsUsingRideModel();
	}

	/**
	 * 今いる地面の上で釣り合い姿勢に落ち着かせる。
	 *
	 * **位置を変えたら呼ぶこと。** 呼ばないと、前の場所の姿勢のまま
	 * 新しい地面に置かれ、そこから落ちたり跳ねたりする。
	 */
	void SettleRide();

	/** 地形による車体の傾き [rad]。**演出ではなく地形そのもの。** */
	double GetTerrainPitchRad() const { return TerrainPitchRad; }
	double GetTerrainRollRad() const { return TerrainRollRad; }

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

	/** 描画用の車体姿勢 [rad]。**物理には存在しない量。** */
	double GetVisualRollRad() const { return VisualRollRad; }
	double GetVisualPitchRad() const { return VisualPitchRad; }

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
	void SetAttitudeFeelForTest(const FZN6BodyAttitudeFeel& InFeel) { AttitudeFeel = InFeel; }

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
	// **既定は off。** HUD が同じ情報を出すようになったので、重ねると
	// ミニマップに被って読めなくなる（実際に撮った画面でそうなっていた）。
	// 数値を生で見たいときだけ入れる。
	bool bShowTelemetry = false;

	/** 荷重移動の可視化。**車両仕様ではない**（憲法ルール18）。 */
	UPROPERTY(EditAnywhere, Category = "ZN6|Attitude")
	FZN6BodyAttitudeFeel AttitudeFeel;

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

	/** 運転支援。**車両仕様ではない。既定は全部 off。** */
	UPROPERTY(EditAnywhere, Category = "ZN6|Assist")
	FZN6DriverAssists Assists;

	/** 最後に変速してからの時間 [s]。連続変速を防ぐ。 */
	float SinceShiftS = 0.0f;

	/** 自動変速を1ステップぶん働かせる。**入っているときだけ。** */
	void TickAutoShift(float DeltaSeconds);

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
	 * **描画専用の車体姿勢 [rad] とその角速度。**
	 *
	 * 物理が出した ax / ay を入力とする2次系。**物理へは戻さない。**
	 * 戻すと、演出値である減衰・比例係数が荷重移動を変え、検証済みの
	 * 0-100km/h や制動距離を汚染する（憲法ルール3が禁じる辻褄合わせ）。
	 */
	double VisualRollRad = 0.0;
	double VisualRollRateRads = 0.0;
	double VisualPitchRad = 0.0;
	double VisualPitchRateRads = 0.0;

	/** 姿勢を Dt 進める（固定刻みの中で呼ぶ）。 */
	void AdvanceVisualAttitude(double DtS);

	/**
	 * 車が乗っている地面を調べ、高さ・傾き・斜面重力を更新する。
	 *
	 * **4輪の接地点を使う。** 重心1点だと、片輪だけ段差に乗った状況で
	 * 車体が傾かない。
	 */
	void SampleGround();

	ZN6::FHeightfield Heightfield;
	bool bHeightfieldLoaded = false;

	/**
	 * コース中心線。**物理へは返さない。**
	 * 音のクロスフェード・周回計測・ミニマップが読む。
	 */
	ZN6::FTrackEdge TrackEdge;
	bool bTrackEdgeLoaded = false;

	/** セッションの進行役。**物理には触らない。** */
	ZN6::FRaceDirector Race;

	/**
	 * タイヤ痕。**絵であって物理ではない**（ルール18）。
	 * 痕を残してもグリップは変わらない。
	 */
	UPROPERTY(VisibleAnywhere, Category = "ZN6|Visual")
	TObjectPtr<UZN6TyreMarkComponent> TyreMarks;

	/** 走行中の画面。**入力は奪わない**（HitTestInvisible）。 */
	TSharedPtr<class SZN6Hud> Hud;

	/** メニュー。**開いている間だけ入力を取る。** */
	TSharedPtr<class SZN6Menu> Menu;

	/**
	 * 入力モードをメニューの開閉に合わせる。
	 *
	 * **これが無いとメニューが一切操作できない。** -game では
	 * PlayerController が入力を持っていくので、ビューポートに足しただけの
	 * Slate ウィジェットにはキーが届かない。
	 */
	void SyncInputModeToMenu();

	/** まだ入力モードを当てられていない（PlayerController が居なかった）。 */
	bool bInputModeDirty = true;
	bool bInputModeAppliedForOpen = false;

	/**
	 * 起動して一定時間後に画面を撮って終了する。
	 *
	 *     UnrealEditor.exe ... -game -ZN6Shot=8 -ZN6ShotName=menu
	 *
	 * **これが無いと、画面に出るものを誰も確認できない。**
	 * SceneCapture2D（Scripts/screenshot_level.py）は 3D シーンしか撮れず、
	 * **Slate で描いている HUD とメニューは1ピクセルも写らない。**
	 * そのせいで「メニューが動かない」ことに気づけなかった。
	 *
	 * 撮ったら終了する。人が見ていない起動を残さないため。
	 */
	void TickAutoScreenshot(float DeltaSeconds);

	/** `-ZN6AutoDrive`。**確認用の自動走行。** 人が居なくても HUD を撮れる。 */
	bool bAutoDrive = false;
	bool bAutoDriveChecked = false;

	float AutoShotDelayS = -1.0f;
	float AutoShotElapsedS = 0.0f;
	bool bAutoShotTaken = false;

	/**
	 * 塗装スロットの動的マテリアル。**車体の色を変えるのに使う。**
	 * 元モデルの塗装色と一致したスロットだけがここに入る。
	 */
	UPROPERTY(Transient)
	TArray<UMaterialInstanceDynamic*> PaintMaterials;
	int32 PaintIndex = 0;

	/** 塗装スロットを探して動的マテリアルを作る。**1回だけ。** */
	void PreparePaintMaterials();

	/** 今のセッティングと、その調整範囲。 */
	ZN6::FCarSetup Setup;
	ZN6::FSetupLimits SetupLimits;
	bool bSetupLimitsReady = false;

	/** 画面をビューポートへ出す / 片付ける。 */
	void CreateHud();
	void DestroyHud();

	/**
	 * 音。**物理の後に更新する。**
	 *
	 * ここが物理へ書き戻すことは無い。`ZN6.Audio.音は物理に影響しない`
	 * がそれを毎回確かめる。
	 */
	UPROPERTY(VisibleAnywhere, Category = "ZN6|Audio")
	TObjectPtr<UZN6VehicleAudioComponent> Audio;

	/**
	 * 接地モデル。**Vehicle.Step の後**に解く。
	 *
	 * FVehicle が前後・左右・ヨーを、こちらが上下・ピッチ・ロールを解く。
	 * **繋がっているのは接地力だけ。**
	 */
	ZN6::FRideModel Ride;
	ZN6::FRideState RideState;
	ZN6::FRideOutputs RideOutputs;
	bool bRideReady = false;
	bool bUseRideModel = true;

	/**
	 * 接地モデルの力をタイヤの垂直荷重に使う。**既定で入れる。**
	 *
	 * 入れないと「車輪が浮く」のが見た目と接地判定だけになり、
	 * 浮いた輪のグリップが消えない。物理として不完全なので既定を on にする。
	 * 参照値は `FVehicle::Step` の既定（準静的）のままなので影響しない。
	 */
	bool bRideDrivesTyreLoads = true;

	/** 前ステップの接地力。**1ステップ遅らせて渡す。** */
	double PreviousContactLoadsN[ZN6::WheelCount] = {};
	bool bHasPreviousContactLoads = false;

	/** 各車輪の下の地面の高さ [m]。SampleGround が更新する。 */
	double WheelGroundM[ZN6::WheelCount] = {};

	/**
	 * 車輪メッシュの基準位置 [cm]（サスペンションの動きを足す前）。
	 *
	 * **レベル側で設定済みの位置を上書きしないため**に、最初の1回だけ
	 * 読み取って覚えておく。毎フレーム SetRelativeLocation で上書きすると、
	 * manifest を読めなかったときに車輪が原点へ飛ぶ。
	 */
	FVector WheelBaseLocationCm[ZN6::WheelCount] = {};
	bool bWheelBaseCaptured = false;

	/** 障害物。**Vehicle.Step の後**に解く。 */
	ZN6::FObstacleField Obstacles;
	ZN6::FCollisionBody CollisionBody;
	bool bObstaclesLoaded = false;
	int32 ContactCount = 0;

	/** 接地面の状態。SampleGround が更新する。 */
	double GroundHeightM = 0.0;
	double TerrainPitchRad = 0.0;
	double TerrainRollRad = 0.0;
	double SlopeGxMps2 = 0.0;
	double SlopeGyMps2 = 0.0;
	double NormalScale = 1.0;

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
