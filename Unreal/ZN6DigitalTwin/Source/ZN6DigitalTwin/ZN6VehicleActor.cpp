#include "ZN6VehicleActor.h"

#include "Camera/CameraComponent.h"
#include "Components/InputComponent.h"
#include "Components/StaticMeshComponent.h"
#include "Dom/JsonObject.h"
#include "Engine/Engine.h"
#include "GameFramework/SpringArmComponent.h"
#include "Misc/FileHelper.h"
#include "Misc/Paths.h"
#include "Physics/ZN6Units.h"
#include "Serialization/JsonReader.h"
#include "Serialization/JsonSerializer.h"

int32 FZN6FixedStepAccumulator::Consume(double FrameDeltaS)
{
	if (FixedStepS <= 0.0f)
	{
		// **黙って 0 を返さない。** 設定が壊れているなら止まるのが正しい。
		checkf(false, TEXT("FixedStepS が 0 以下。物理を進められない。"));
		return 0;
	}

	AccumulatedS += FrameDeltaS;

	int32 Steps = static_cast<int32>(AccumulatedS / FixedStepS);
	if (Steps > MaxStepsPerFrame)
	{
		// 上限に当たったぶんは捨てる（死のスパイラルを避ける）。
		// **捨てた事実を記録する。** 黙って落とすとシミュレーション時間が
		// 実時間より遅れていることに気づけない。
		const double DroppedSteps = static_cast<double>(Steps - MaxStepsPerFrame);
		DroppedS += DroppedSteps * FixedStepS;
		Steps = MaxStepsPerFrame;
		AccumulatedS = 0.0;
	}
	else
	{
		AccumulatedS -= static_cast<double>(Steps) * FixedStepS;
	}

	LastStepCount = Steps;
	return Steps;
}

AZN6VehicleActor::AZN6VehicleActor()
{
	PrimaryActorTick.bCanEverTick = true;

	// ルートは素の SceneComponent。**描画メッシュをルートにしない。**
	// メッシュを差し替えたり位置を変えたりしても、物理側の基準が動かないようにする。
	USceneComponent* Root = CreateDefaultSubobject<USceneComponent>(TEXT("Root"));
	SetRootComponent(Root);

	// **描画メッシュのコリジョンを切る。** UE の物理エンジンがこのメッシュに
	// 干渉すると、憲法ルール4（物理計算と表示用3Dモデルの完全分離）が壊れる。
	// 物理は Physics/ZN6Vehicle だけが担当する。
	auto MakeVisualMesh = [this, Root](const TCHAR* Name) -> UStaticMeshComponent*
	{
		UStaticMeshComponent* Mesh = CreateDefaultSubobject<UStaticMeshComponent>(Name);
		Mesh->SetupAttachment(Root);
		Mesh->SetCollisionEnabled(ECollisionEnabled::NoCollision);
		Mesh->SetSimulatePhysics(false);
		Mesh->SetGenerateOverlapEvents(false);
		return Mesh;
	};

	BodyMesh = MakeVisualMesh(TEXT("BodyMesh"));

	// **ZN6::EWheel と同じ順序で作ること。** 添字がそのまま物理の車輪番号。
	static const TCHAR* const WheelComponentNames[ZN6::WheelCount] = {
		TEXT("WheelFL"), TEXT("WheelFR"), TEXT("WheelRL"), TEXT("WheelRR")
	};
	WheelMeshes.Reserve(ZN6::WheelCount);
	for (int32 Index = 0; Index < ZN6::WheelCount; ++Index)
	{
		WheelMeshes.Add(MakeVisualMesh(WheelComponentNames[Index]));
	}

	// --- 追従カメラ。**描画専用。** ---------------------------------------
	CameraBoom = CreateDefaultSubobject<USpringArmComponent>(TEXT("CameraBoom"));
	CameraBoom->SetupAttachment(Root);
	CameraBoom->TargetArmLength = 750.0f;
	CameraBoom->SocketOffset = FVector(0.0f, 0.0f, 220.0f);
	CameraBoom->bDoCollisionTest = false;   // 木や地面にカメラを寄せない
	// **車体の回転に少し遅れて追従させる。** 完全追従だと、スピン時に
	// world が回って見えて何が起きているか分からない。
	CameraBoom->bEnableCameraRotationLag = true;
	CameraBoom->CameraRotationLagSpeed = 6.0f;
	CameraBoom->bInheritPitch = false;
	CameraBoom->bInheritRoll = false;

	ChaseCamera = CreateDefaultSubobject<UCameraComponent>(TEXT("ChaseCamera"));
	ChaseCamera->SetupAttachment(CameraBoom, USpringArmComponent::SocketName);

	// この Pawn は UE の移動コンポーネントを持たない。
	// **位置は物理モデルだけが決める。**
	bUseControllerRotationYaw = false;
	bUseControllerRotationPitch = false;
	bUseControllerRotationRoll = false;
}

void AZN6VehicleActor::BeginPlay()
{
	Super::BeginPlay();

	if (bPhysicsReady)
	{
		return;
	}

	// vehicle.json はリポジトリ側にある（<repo>/Vehicles/ZN6/vehicle.json）。
	// プロジェクトは <repo>/Unreal/ZN6DigitalTwin/ に置いてある。
	const FString RepoRoot = FPaths::ConvertRelativePathToFull(
		FPaths::ProjectDir() / TEXT("../.."));

	FString Error;
	if (!InitialisePhysics(RepoRoot / TEXT("Vehicles/ZN6/vehicle.json"), Error))
	{
		// **握りつぶさない。** 値が無いならこのモデルは動かせない、が正しい状態。
		UE_LOG(LogTemp, Error, TEXT("ZN6: 物理モデルを初期化できない: %s"), *Error);
	}

	// 車輪の取り付け位置。**読めなくても物理は動く**ので、ここは警告に留める
	// （車輪が原点に重なって描画されるが、物理の正しさには影響しない）。
	if (!LoadVisualManifest(RepoRoot / TEXT("Vehicles/ZN6/Export/manifest.json"), Error))
	{
		UE_LOG(LogTemp, Warning, TEXT("ZN6: 車輪の取り付け位置を読めない: %s"), *Error);
	}

	// 地形。**読めなければ平地として走る**（既定値をでっち上げない）。
	if (!LoadHeightfield(RepoRoot / TEXT("Tracks/Export/heightfield.json"), Error))
	{
		UE_LOG(LogTemp, Warning,
		       TEXT("ZN6: 地形を読めない: %s。平地として走る。"), *Error);
	}

	// 障害物。**読めなければ当たり判定なしで走る。**
	// 木をすり抜けるのは明らかに分かるが、でっち上げた境界に阻まれるのは
	// 原因が分からない。
	if (!LoadObstacles(RepoRoot / TEXT("Tracks/Export/placement.json"), Error))
	{
		UE_LOG(LogTemp, Warning,
		       TEXT("ZN6: 障害物を読めない: %s。当たり判定なしで走る。"), *Error);
	}
}

bool AZN6VehicleActor::InitialisePhysics(const FString& VehicleJsonPath, FString& OutError)
{
	if (!VehicleData.LoadFromFile(VehicleJsonPath, OutError))
	{
		return false;
	}
	if (!Vehicle.Init(VehicleData, /*bUseLsd=*/true, OutError))
	{
		return false;
	}

	// 最大舵角を最小回転半径から導く。**読めなければ 0 のままにする。**
	// 「それらしい既定値」を置くと、操舵が効いているのに実車と無関係な
	// 値で走ることになる（憲法ルール1）。
	double TurningRadiusM = 0.0;
	double WheelbaseM = 0.0;
	FString SteerError;
	if (VehicleData.GetValue(TEXT("dimensions.min_turning_radius"), TEXT("m"),
	                         TurningRadiusM, SteerError)
	    && VehicleData.GetValue(TEXT("dimensions.wheelbase"), TEXT("m"),
	                            WheelbaseM, SteerError)
	    && TurningRadiusM > 0.0)
	{
		MaxSteerRad = FMath::Atan2(WheelbaseM, TurningRadiusM);
	}
	else
	{
		MaxSteerRad = 0.0;
		UE_LOG(LogTemp, Warning,
		       TEXT("ZN6: 最大舵角を導けない（%s）。操舵は効かない。"), *SteerError);
	}

	PhysicsState = Vehicle.InitialState(0.0, 0);
	SimulatedTimeS = 0.0;
	TotalStepCount = 0;
	Accumulator.AccumulatedS = 0.0;
	Accumulator.DroppedS = 0.0;
	for (int32 Index = 0; Index < ZN6::WheelCount; ++Index)
	{
		VisualWheelAngleRad[Index] = 0.0;
	}
	bPhysicsReady = true;
	return true;
}

void AZN6VehicleActor::ResetToStart()
{
	if (!bPhysicsReady)
	{
		return;
	}

	PhysicsState = Vehicle.InitialState(0.0, 0);
	Control = ZN6::FControlInput();
	RawThrottle = RawBrake = RawSteer = RawClutch = RawHandbrake = 0.0f;
	for (int32 Index = 0; Index < ZN6::WheelCount; ++Index)
	{
		VisualWheelAngleRad[Index] = 0.0;
	}
	VisualRollRad = VisualRollRateRads = 0.0;
	VisualPitchRad = VisualPitchRateRads = 0.0;
	Accumulator.AccumulatedS = 0.0;
	SyncVisualToPhysics();
}

bool AZN6VehicleActor::LoadHeightfield(const FString& HeightfieldPath, FString& OutError)
{
	bHeightfieldLoaded = Heightfield.LoadFromFile(HeightfieldPath, OutError);
	return bHeightfieldLoaded;
}

bool AZN6VehicleActor::LoadObstacles(const FString& PlacementPath, FString& OutError)
{
	bObstaclesLoaded = false;

	// **車体の外形が先。** これが作れないなら当たり判定は成立しない。
	if (!CollisionBody.Init(VehicleData, OutError))
	{
		return false;
	}
	if (!Obstacles.LoadFromPlacement(PlacementPath, OutError))
	{
		return false;
	}

	bObstaclesLoaded = true;
	return true;
}

void AZN6VehicleActor::SampleGround()
{
	if (!bHeightfieldLoaded)
	{
		// **平地として扱う。** 既定値をでっち上げない。
		GroundHeightM = 0.0;
		TerrainPitchRad = TerrainRollRad = 0.0;
		SlopeGxMps2 = SlopeGyMps2 = 0.0;
		NormalScale = 1.0;
		return;
	}

	// 4輪の接地点を世界座標で求める。**重心1点では車体が傾かない。**
	const double CosH = FMath::Cos(PhysicsState.HeadingRad);
	const double SinH = FMath::Sin(PhysicsState.HeadingRad);

	double Height[ZN6::WheelCount] = {};
	for (int32 Index = 0; Index < ZN6::WheelCount; ++Index)
	{
		const FVector& Attach = WheelAttachM[Index];
		const double WorldX = PhysicsState.XM + Attach.X * CosH - Attach.Y * SinH;
		const double WorldY = PhysicsState.YM + Attach.X * SinH + Attach.Y * CosH;
		Height[Index] = Heightfield.HeightAt(WorldX, WorldY);
	}

	const int32 FL = static_cast<int32>(ZN6::EWheel::FL);
	const int32 FR = static_cast<int32>(ZN6::EWheel::FR);
	const int32 RL = static_cast<int32>(ZN6::EWheel::RL);
	const int32 RR = static_cast<int32>(ZN6::EWheel::RR);

	GroundHeightM = (Height[FL] + Height[FR] + Height[RL] + Height[RR]) / 4.0;

	// 前後・左右の高さ差から車体の傾きを出す。
	// **後ろが低ければ機首上げ**（ピッチ正）。
	const double FrontZ = (Height[FL] + Height[FR]) / 2.0;
	const double RearZ = (Height[RL] + Height[RR]) / 2.0;
	const double LeftZ = (Height[FL] + Height[RL]) / 2.0;
	const double RightZ = (Height[FR] + Height[RR]) / 2.0;

	const double WheelbaseM = FMath::Max(
		WheelAttachM[FL].X - WheelAttachM[RL].X, 0.1);
	const double TrackM = FMath::Max(
		WheelAttachM[FL].Y - WheelAttachM[FR].Y, 0.1);

	TerrainPitchRad = FMath::Atan2(RearZ - FrontZ, WheelbaseM);
	// **左が高ければ右へ傾く**（UE の正のロールは右下がり）。
	TerrainRollRad = FMath::Atan2(LeftZ - RightZ, TrackM);

	// 斜面方向の重力。**車の位置での勾配を使う。**
	double DzDx = 0.0;
	double DzDy = 0.0;
	Heightfield.SlopeAt(PhysicsState.XM, PhysicsState.YM, DzDx, DzDy);
	ZN6::BodyGravity(DzDx, DzDy, PhysicsState.HeadingRad,
	                 SlopeGxMps2, SlopeGyMps2, NormalScale);
}

bool AZN6VehicleActor::LoadVisualManifest(const FString& ManifestPath, FString& OutError)
{
	FString Text;
	if (!FFileHelper::LoadFileToString(Text, *ManifestPath))
	{
		OutError = FString::Printf(TEXT("manifest を読めない: %s"), *ManifestPath);
		return false;
	}

	TSharedPtr<FJsonObject> Root;
	const TSharedRef<TJsonReader<>> Reader = TJsonReaderFactory<>::Create(Text);
	if (!FJsonSerializer::Deserialize(Reader, Root) || !Root.IsValid())
	{
		OutError = FString::Printf(TEXT("manifest の JSON を解釈できない: %s"), *ManifestPath);
		return false;
	}

	const TSharedPtr<FJsonObject>* Parts = nullptr;
	if (!Root->TryGetObjectField(TEXT("parts"), Parts))
	{
		OutError = TEXT("manifest に parts が無い");
		return false;
	}

	static const TCHAR* const Keys[ZN6::WheelCount] = {
		TEXT("wheel_FL"), TEXT("wheel_FR"), TEXT("wheel_RL"), TEXT("wheel_RR")
	};

	for (int32 Index = 0; Index < ZN6::WheelCount; ++Index)
	{
		const TSharedPtr<FJsonObject>* Part = nullptr;
		if (!(*Parts)->TryGetObjectField(Keys[Index], Part))
		{
			OutError = FString::Printf(TEXT("manifest に %s が無い"), Keys[Index]);
			return false;
		}

		const TArray<TSharedPtr<FJsonValue>>* Attach = nullptr;
		if (!(*Part)->TryGetArrayField(TEXT("attach_m"), Attach) || Attach->Num() != 3)
		{
			OutError = FString::Printf(TEXT("%s の attach_m が3要素でない"), Keys[Index]);
			return false;
		}

		WheelAttachM[Index] = FVector(
			(*Attach)[0]->AsNumber(), (*Attach)[1]->AsNumber(), (*Attach)[2]->AsNumber());
	}

	bVisualManifestLoaded = true;
	return true;
}

ZN6::FVehicleState AZN6VehicleActor::MakeInitialState(double SpeedMps, int32 GearIndex) const
{
	return Vehicle.InitialState(SpeedMps, GearIndex);
}

void AZN6VehicleActor::AdvancePhysics(double FrameDeltaS)
{
	if (!bPhysicsReady)
	{
		return;
	}

	const int32 Steps = Accumulator.Consume(FrameDeltaS);
	const double FixedStep = static_cast<double>(Accumulator.FixedStepS);

	for (int32 Step = 0; Step < Steps; ++Step)
	{
		// **地面を先に調べる。** 車の位置が変わるたびに斜面も変わる。
		SampleGround();

		ZN6::FVehicleState NextState;
		Vehicle.Step(PhysicsState, Control, FixedStep, NextState, PhysicsOutputs,
		             SlopeGxMps2, SlopeGyMps2, NormalScale);
		PhysicsState = NextState;

		// **当たり判定は Step の後。** 触れていなければ状態は変わらないので、
		// 障害物の無い走行の結果は当たり判定を入れる前と一致する。
		if (bObstaclesLoaded)
		{
			ContactCount = Obstacles.Resolve(PhysicsState, CollisionBody,
			                                 Vehicle.GetMassKg(), Vehicle.GetIzzKgm2());
		}

		SimulatedTimeS += FixedStep;
		++TotalStepCount;

		// **描画用の車輪回転角。物理には戻さない。**
		//
		// 固定刻みの中で積分する（フレーム時間で積分すると、車輪の見た目の
		// 回転がフレームレートに依存する）。
		for (int32 Wheel = 0; Wheel < ZN6::WheelCount; ++Wheel)
		{
			VisualWheelAngleRad[Wheel] += PhysicsState.WheelOmegaRads[Wheel] * FixedStep;
		}

		AdvanceVisualAttitude(FixedStep);
	}
}

void AZN6VehicleActor::AdvanceVisualAttitude(double DtS)
{
	// **荷重移動を目に見える形にするだけ。実車のロール角ではない。**
	// 理由は FZN6BodyAttitudeFeel のコメント（ロール剛性を出すのに要る
	// モーションレシオ・減衰・ロールセンタ高さが揃っていない）。
	//
	// 入力は物理が出した加速度。**物理へは戻さない。**

	const double OmegaN = 2.0 * ZN6::Pi * AttitudeFeel.ResponseHz;
	const double Zeta = AttitudeFeel.DampingRatio;

	// ay が正 = 左向き加速 = 左旋回。車体は外側（右）へ傾く。
	//
	// **UE の正の Roll は右側が下がる**（左手系で +X 軸まわりに時計回り）。
	// 左旋回で右側を下げたいので符号は正。
	//
	// 最初これを負にしていて、旋回の内側へ傾いていた。**しかもテストは
	// 通っていた** — 私が「負が外側」と思い込んで、その思い込みを
	// そのまま assert に書いたため。実際に走らせて指摘されるまで
	// 気づけなかった。**符号は目で確かめること。**
	const double TargetRollRad = FMath::DegreesToRadians(
		AttitudeFeel.RollDegPerG * PhysicsOutputs.AyMps2 / ZN6::GravityMps2);

	// ax が正 = 加速。車体は後ろへ沈む = 機首上げ。
	const double TargetPitchRad = FMath::DegreesToRadians(
		AttitudeFeel.PitchDegPerG * PhysicsOutputs.AxMps2 / ZN6::GravityMps2);

	// 減衰つき2次系。**1次の平滑化ではなく2次にする。**
	// 実車の車体は切り返しで揺り返すので、そこが見えないと
	// 「何が起きているか」が分からない。
	auto Advance = [OmegaN, Zeta, DtS](double& Angle, double& Rate, double Target)
	{
		const double Accel = (Target - Angle) * OmegaN * OmegaN
		                   - 2.0 * Zeta * OmegaN * Rate;
		Rate += Accel * DtS;
		Angle += Rate * DtS;
	};

	Advance(VisualRollRad, VisualRollRateRads, TargetRollRad);
	Advance(VisualPitchRad, VisualPitchRateRads, TargetPitchRad);
}

void AZN6VehicleActor::SyncVisualToPhysics()
{
	if (!bPhysicsReady || BodyMesh == nullptr)
	{
		return;
	}

	// **物理 -> 描画の一方向のみ。** ここで描画コンポーネントから値を
	// 読み戻さないこと。
	//
	// 物理は右手系 y 左方 [m]、UE は左手系 y 右方 [cm]。
	// y の符号反転と 100 倍の単位変換をここで行う（**物理側に UE の都合を
	// 持ち込まない**）。
	constexpr double MetresToCentimetres = 100.0;

	// **地面の高さに乗せる。** z=0 固定だと、起伏の上で浮く／埋まる。
	const FVector Location(
		PhysicsState.XM * MetresToCentimetres,
		-PhysicsState.YM * MetresToCentimetres,
		GroundHeightM * MetresToCentimetres);

	// 物理のヨーは左が正、UE のヨーは右が正なので符号を反転する
	const FRotator Rotation(0.0, -FMath::RadiansToDegrees(PhysicsState.HeadingRad), 0.0);

	SetActorLocationAndRotation(Location, Rotation);

	// --- 車体だけを傾ける ---------------------------------------------------
	//
	// **Actor 全体を回さないこと。** 回すと車輪も一緒に傾いて地面から
	// 浮く。実車もバネ上（車体）だけが傾き、車輪は接地したままである。
	//
	// 原点は接地面にあるので、そのまま回すと車体の下端が地面へめり込む。
	// 少し上の点を中心に回す（**ロールセンタではなく、見た目が破綻しない
	// 高さを選んでいるだけ**）。
	{
		// 地形の傾き（**物理の一部**）に、荷重移動の可視化（演出）を足す。
		// 前者は地面そのもの、後者は見せ方。**性質が違うので分けて持つ。**
		const FRotator BodyTilt(
			FMath::RadiansToDegrees(VisualPitchRad + TerrainPitchRad),
			0.0,
			FMath::RadiansToDegrees(VisualRollRad + TerrainRollRad));

		const FVector Pivot(0.0, 0.0, AttitudeFeel.PivotHeightM * MetresToCentimetres);
		BodyMesh->SetRelativeLocation(Pivot - BodyTilt.RotateVector(Pivot));
		BodyMesh->SetRelativeRotation(BodyTilt);
	}

	const double SteerDeg = -FMath::RadiansToDegrees(Control.SteerRad);

	for (int32 Index = 0; Index < ZN6::WheelCount; ++Index)
	{
		if (!WheelMeshes.IsValidIndex(Index) || WheelMeshes[Index] == nullptr)
		{
			continue;
		}

		// **取り付け位置はレベル側で設定済み**（build_level.py が manifest から
		// 書き込む）。ここで上書きするのは、実行時に manifest を読めた場合だけ。
		//
		// 以前はここでしか位置を設定しておらず、**エディタでは BeginPlay が
		// 走らないので4輪とも原点に重なって車体に埋まっていた。**
		// メッシュもマテリアルも正常で `is_visible()` も True なので、
		// 調べても異常が見えない。実際に走らせて「タイヤが見えない」と
		// 指摘されるまで気づけなかった。
		if (bVisualManifestLoaded)
		{
			const FVector& Attach = WheelAttachM[Index];
			WheelMeshes[Index]->SetRelativeLocation(FVector(
				Attach.X * MetresToCentimetres,
				-Attach.Y * MetresToCentimetres,
				Attach.Z * MetresToCentimetres));
		}

		// **転がりは負のピッチ。** UE の正ピッチは +X を +Z へ回す
		// （機首上げ）ので、車輪の頂点が後ろへ動く = 後転になる。
		const double SpinDeg = -FMath::RadiansToDegrees(VisualWheelAngleRad[Index]);

		// 操舵は前輪のみ。FRotator(Pitch, Yaw, Roll) は Yaw を先に効かせる
		// ので、操舵した向きのまま転がる。
		const bool bFront = (Index == static_cast<int32>(ZN6::EWheel::FL))
		                 || (Index == static_cast<int32>(ZN6::EWheel::FR));

		WheelMeshes[Index]->SetRelativeRotation(
			FRotator(SpinDeg, bFront ? SteerDeg : 0.0, 0.0));
	}
}

void AZN6VehicleActor::Tick(float DeltaSeconds)
{
	Super::Tick(DeltaSeconds);

	// **入力 -> 物理 -> 描画 の順。** 逆にすると1フレーム遅れる。
	ApplyDriverInput(DeltaSeconds);
	AdvancePhysics(static_cast<double>(DeltaSeconds));
	SyncVisualToPhysics();

	if (bShowTelemetry)
	{
		DrawTelemetry();
	}
}

// ---------------------------------------------------------------------------
// 入力
// ---------------------------------------------------------------------------

void AZN6VehicleActor::SetupPlayerInputComponent(UInputComponent* PlayerInputComponent)
{
	Super::SetupPlayerInputComponent(PlayerInputComponent);
	if (PlayerInputComponent == nullptr)
	{
		return;
	}

	PlayerInputComponent->BindAxis(TEXT("ZN6_Throttle"), this, &AZN6VehicleActor::InputThrottle);
	PlayerInputComponent->BindAxis(TEXT("ZN6_Brake"), this, &AZN6VehicleActor::InputBrake);
	PlayerInputComponent->BindAxis(TEXT("ZN6_Steer"), this, &AZN6VehicleActor::InputSteer);
	PlayerInputComponent->BindAxis(TEXT("ZN6_Clutch"), this, &AZN6VehicleActor::InputClutch);
	PlayerInputComponent->BindAxis(TEXT("ZN6_Handbrake"), this, &AZN6VehicleActor::InputHandbrake);

	PlayerInputComponent->BindAction(TEXT("ZN6_ShiftUp"), IE_Pressed,
	                                 this, &AZN6VehicleActor::ShiftUp);
	PlayerInputComponent->BindAction(TEXT("ZN6_ShiftDown"), IE_Pressed,
	                                 this, &AZN6VehicleActor::ShiftDown);
	PlayerInputComponent->BindAction(TEXT("ZN6_Reset"), IE_Pressed,
	                                 this, &AZN6VehicleActor::ResetToStart);
}

void AZN6VehicleActor::InputThrottle(float Value) { RawThrottle = Value; }
void AZN6VehicleActor::InputBrake(float Value) { RawBrake = Value; }
void AZN6VehicleActor::InputSteer(float Value) { RawSteer = Value; }
void AZN6VehicleActor::InputClutch(float Value) { RawClutch = Value; }
void AZN6VehicleActor::InputHandbrake(float Value) { RawHandbrake = Value; }

void AZN6VehicleActor::ShiftUp()
{
	// **上限を超えない。** 存在しないギアを入れると TotalRatio が check で落ちる。
	Control.GearIndex = FMath::Min(Control.GearIndex + 1, ZN6::ForwardGearCount - 1);
}

void AZN6VehicleActor::ShiftDown()
{
	Control.GearIndex = FMath::Max(Control.GearIndex - 1, 0);
}

void AZN6VehicleActor::ApplyDriverInput(float DeltaSeconds)
{
	// **キーボードの 0/1 をそのまま物理へ入れない。**
	// 踏み込み量が無い入力を生で渡すと、アクセルもブレーキも常に全開全閉に
	// なり、FR では即スピンする。時間をかけて目標値へ寄せる。
	auto Approach = [DeltaSeconds](float Current, float Target, float Rate)
	{
		const float Step = Rate * DeltaSeconds;
		return FMath::Abs(Target - Current) <= Step
			? Target
			: Current + FMath::Sign(Target - Current) * Step;
	};

	const float PedalRate = DriverFeel.PedalRatePerS;
	Control.Throttle = Approach(static_cast<float>(Control.Throttle),
	                            FMath::Clamp(RawThrottle, 0.0f, 1.0f), PedalRate);
	Control.Brake = Approach(static_cast<float>(Control.Brake),
	                         FMath::Clamp(RawBrake, 0.0f, 1.0f), PedalRate);

	// クラッチは踏むと切れる（0 = 切、1 = 繋）。**入力の意味を反転させる。**
	Control.Clutch = 1.0 - FMath::Clamp(RawClutch, 0.0f, 1.0f);
	Control.Handbrake = FMath::Clamp(RawHandbrake, 0.0f, 1.0f);

	// --- 操舵 -------------------------------------------------------------
	//
	// 速度が上がるほど最大舵角を絞る。**操作系の補助であって車の特性では
	// ない**（憲法ルール18）。キーボードには「少しだけ切る」が無いため、
	// これが無いと直線で軽く当てただけでスピンする。
	const double SpeedMps = FMath::Abs(PhysicsState.VxMps);
	const double Falloff = 1.0 / (1.0 + DriverFeel.SteerSpeedFalloffPerMps * SpeedMps);
	const double SteerLimit = MaxSteerRad * Falloff;

	const float Target = FMath::Clamp(RawSteer, -1.0f, 1.0f);
	const bool bReturning = FMath::IsNearlyZero(Target);
	const float Rate = bReturning
		? DriverFeel.SteerReturnRateRadPerS
		: DriverFeel.SteerRateRadPerS;

	Control.SteerRad = Approach(static_cast<float>(Control.SteerRad),
	                            Target * static_cast<float>(SteerLimit), Rate);
	Control.SteerRad = FMath::Clamp(Control.SteerRad, -SteerLimit, SteerLimit);
}

void AZN6VehicleActor::DrawTelemetry() const
{
	if (GEngine == nullptr || !bPhysicsReady)
	{
		return;
	}

	const double SpeedKmh = PhysicsState.SpeedMps() * ZN6::KmhPerMps;
	const double Rpm = ZN6::RadsToRpm(PhysicsState.EngineOmegaRads);

	// **後輪のすべりを出す。** FR なのでここがスピンの前触れになる。
	const double RearSlip = FMath::Max(
		FMath::Abs(PhysicsOutputs.SlipRatio[static_cast<int32>(ZN6::EWheel::RL)]),
		FMath::Abs(PhysicsOutputs.SlipRatio[static_cast<int32>(ZN6::EWheel::RR)]));
	const double SideslipDeg = FMath::RadiansToDegrees(PhysicsState.SideslipRad());

	GEngine->AddOnScreenDebugMessage(
		1, 0.0f, FColor::White,
		FString::Printf(TEXT("%5.1f km/h   %5.0f rpm   %d速"),
		                SpeedKmh, Rpm, Control.GearIndex + 1));
	GEngine->AddOnScreenDebugMessage(
		2, 0.0f, RearSlip > 0.20 ? FColor::Orange : FColor::Silver,
		FString::Printf(TEXT("後輪すべり率 %.3f   車体すべり角 %+.1f deg   "
		                     "ロール %+.1f / ピッチ %+.1f deg（演出）"),
		                RearSlip, SideslipDeg,
		                FMath::RadiansToDegrees(VisualRollRad),
		                FMath::RadiansToDegrees(VisualPitchRad)));
	GEngine->AddOnScreenDebugMessage(
		3, 0.0f, FColor::Silver,
		FString::Printf(TEXT("舵角 %+.1f deg / 最大 %.1f   ｱｸｾﾙ %.2f  ﾌﾞﾚｰｷ %.2f  ｸﾗｯﾁ %.2f"),
		                FMath::RadiansToDegrees(Control.SteerRad),
		                FMath::RadiansToDegrees(MaxSteerRad),
		                Control.Throttle, Control.Brake, Control.Clutch));
	GEngine->AddOnScreenDebugMessage(
		4, 0.0f, FColor::Silver,
		TEXT("W/S ｱｸｾﾙ･ﾌﾞﾚｰｷ   A/D 操舵   Space ｻｲﾄﾞ   LShift ｸﾗｯﾁ   E/Q 変速   R 戻す"));
}
