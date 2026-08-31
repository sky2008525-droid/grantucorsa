#include "ZN6VehicleActor.h"

#include "Components/StaticMeshComponent.h"
#include "Misc/Paths.h"

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

	VisualMesh = CreateDefaultSubobject<UStaticMeshComponent>(TEXT("VisualMesh"));
	VisualMesh->SetupAttachment(Root);

	// **描画メッシュのコリジョンを切る。** UE の物理エンジンがこのメッシュに
	// 干渉すると、憲法ルール4（物理計算と表示用3Dモデルの完全分離）が壊れる。
	// 物理は Physics/ZN6Vehicle だけが担当する。
	VisualMesh->SetCollisionEnabled(ECollisionEnabled::NoCollision);
	VisualMesh->SetSimulatePhysics(false);
	VisualMesh->SetGenerateOverlapEvents(false);
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
	const FString JsonPath = FPaths::ConvertRelativePathToFull(
		FPaths::ProjectDir() / TEXT("../..")) / TEXT("Vehicles/ZN6/vehicle.json");

	FString Error;
	if (!InitialisePhysics(JsonPath, Error))
	{
		// **握りつぶさない。** 値が無いならこのモデルは動かせない、が正しい状態。
		UE_LOG(LogTemp, Error, TEXT("ZN6: 物理モデルを初期化できない: %s"), *Error);
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

	PhysicsState = Vehicle.InitialState(0.0, 0);
	SimulatedTimeS = 0.0;
	TotalStepCount = 0;
	Accumulator.AccumulatedS = 0.0;
	Accumulator.DroppedS = 0.0;
	bPhysicsReady = true;
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
		ZN6::FVehicleState NextState;
		Vehicle.Step(PhysicsState, Control, FixedStep, NextState, PhysicsOutputs);
		PhysicsState = NextState;
		SimulatedTimeS += FixedStep;
		++TotalStepCount;
	}
}

void AZN6VehicleActor::SyncVisualToPhysics()
{
	if (!bPhysicsReady || VisualMesh == nullptr)
	{
		return;
	}

	// **物理 -> 描画の一方向のみ。** ここで VisualMesh から値を読み戻さないこと。
	//
	// 物理は右手系 y 左方 [m]、UE は左手系 y 右方 [cm]。
	// y の符号反転と 100 倍の単位変換をここで行う（**物理側に UE の都合を
	// 持ち込まない**）。
	constexpr double MetresToCentimetres = 100.0;

	const FVector Location(
		PhysicsState.XM * MetresToCentimetres,
		-PhysicsState.YM * MetresToCentimetres,
		0.0);

	const FRotator Rotation(0.0, -FMath::RadiansToDegrees(PhysicsState.HeadingRad), 0.0);

	SetActorLocationAndRotation(Location, Rotation);
}

void AZN6VehicleActor::Tick(float DeltaSeconds)
{
	Super::Tick(DeltaSeconds);

	AdvancePhysics(static_cast<double>(DeltaSeconds));
	SyncVisualToPhysics();
}
