#include "ZN6VehicleActor.h"

#include "Components/StaticMeshComponent.h"
#include "Dom/JsonObject.h"
#include "Misc/FileHelper.h"
#include "Misc/Paths.h"
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
	for (int32 Index = 0; Index < ZN6::WheelCount; ++Index)
	{
		VisualWheelAngleRad[Index] = 0.0;
	}
	bPhysicsReady = true;
	return true;
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
		ZN6::FVehicleState NextState;
		Vehicle.Step(PhysicsState, Control, FixedStep, NextState, PhysicsOutputs);
		PhysicsState = NextState;
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
	}
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

	const FVector Location(
		PhysicsState.XM * MetresToCentimetres,
		-PhysicsState.YM * MetresToCentimetres,
		0.0);

	// 物理のヨーは左が正、UE のヨーは右が正なので符号を反転する
	const FRotator Rotation(0.0, -FMath::RadiansToDegrees(PhysicsState.HeadingRad), 0.0);

	SetActorLocationAndRotation(Location, Rotation);

	if (!bVisualManifestLoaded)
	{
		return;
	}

	const double SteerDeg = -FMath::RadiansToDegrees(Control.SteerRad);

	for (int32 Index = 0; Index < ZN6::WheelCount; ++Index)
	{
		if (!WheelMeshes.IsValidIndex(Index) || WheelMeshes[Index] == nullptr)
		{
			continue;
		}

		const FVector& Attach = WheelAttachM[Index];
		WheelMeshes[Index]->SetRelativeLocation(FVector(
			Attach.X * MetresToCentimetres,
			-Attach.Y * MetresToCentimetres,
			Attach.Z * MetresToCentimetres));

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

	AdvancePhysics(static_cast<double>(DeltaSeconds));
	SyncVisualToPhysics();
}
