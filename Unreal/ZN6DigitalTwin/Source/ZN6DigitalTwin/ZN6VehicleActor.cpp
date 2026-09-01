#include "ZN6VehicleActor.h"

#include "Camera/CameraComponent.h"
#include "Components/InputComponent.h"
#include "Components/StaticMeshComponent.h"
#include "Dom/JsonObject.h"
#include "Engine/Engine.h"
#include "GameFramework/SpringArmComponent.h"
#include "Misc/FileHelper.h"
#include "Misc/Paths.h"
#include "Engine/GameViewportClient.h"
#include "Physics/ZN6Units.h"
#include "Framework/Application/SlateApplication.h"
#include "Materials/MaterialInstanceDynamic.h"
#include "Misc/CommandLine.h"
#include "Misc/Parse.h"
#include "UnrealClient.h"
#include "GameFramework/PlayerController.h"
#include "UI/SZN6Hud.h"
#include "UI/SZN6Menu.h"
#include "Widgets/SWeakWidget.h"
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
	// タイヤ痕。**絵であって物理ではない。**
	TyreMarks = CreateDefaultSubobject<UZN6TyreMarkComponent>(TEXT("TyreMarks"));
	TyreMarks->SetupAttachment(Root);

	// **音は最後。** 物理にも描画にも関わらない（憲法ルール18）。
	Audio = CreateDefaultSubobject<UZN6VehicleAudioComponent>(TEXT("VehicleAudio"));
	Audio->SetupAttachment(Root);

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

	// コース定義。**周回計測・ミニマップ・音の混合が読む。**
	if (!LoadTrack(RepoRoot / TEXT("Tracks/physics_test_track.json"), Error))
	{
		UE_LOG(LogTemp, Warning,
		       TEXT("ZN6: コース定義を読めない: %s。計測とミニマップが働かない。"),
		       *Error);
	}

	// タイヤ痕。**出なくても走りは変わらない。**
	if (TyreMarks != nullptr && !TyreMarks->Initialise(Error))
	{
		UE_LOG(LogTemp, Warning,
		       TEXT("ZN6: タイヤ痕を用意できない: %s。痕なしで走る。"), *Error);
	}

	// 音。**鳴らなくても走りは変わらない。**
	if (!InitialiseAudio(RepoRoot, Error))
	{
		UE_LOG(LogTemp, Warning,
		       TEXT("ZN6: 音を用意できない: %s。無音で走る。"), *Error);
	}

	// 障害物。**読めなければ当たり判定なしで走る。**
	// 木をすり抜けるのは明らかに分かるが、でっち上げた境界に阻まれるのは
	// 原因が分からない。
	if (!LoadObstacles(RepoRoot / TEXT("Tracks/Export/placement.json"), Error))
	{
		UE_LOG(LogTemp, Warning,
		       TEXT("ZN6: 障害物を読めない: %s。当たり判定なしで走る。"), *Error);
	}

	// **最後に、今いる地面の上で釣り合わせる。**
	// 地形を読んだ後でないと、平地の姿勢のまま坂に置かれて跳ねる。
	SettleRide();

	// 塗装スロットを探しておく。**色を変えるより前に。**
	PreparePaintMaterials();

	CreateHud();
}

void AZN6VehicleActor::EndPlay(const EEndPlayReason::Type Reason)
{
	// **片付ける。** 残すと、PIE を止めた後もビューポートに計器が出たままになる。
	DestroyHud();
	Super::EndPlay(Reason);
}

void AZN6VehicleActor::CreateHud()
{
	if (Hud.IsValid() || GEngine == nullptr || GEngine->GameViewport == nullptr)
	{
		return;
	}

	Hud = SNew(SZN6Hud);

	// ミニマップの中心線は**1回だけ**渡す。毎フレーム渡すと千点の配列を
	// 毎回コピーすることになる。
	if (bTrackEdgeLoaded)
	{
		TArray<FVector2D> Points;
		const int32 Count = TrackEdge.CentrelineCount();
		// 全点は要らない。**間引いて渡す。** 1 m 間隔の千点を線で結んでも
		// 210px の枠では違いが出ない。
		const int32 Stride = FMath::Max(Count / 240, 1);
		Points.Reserve(Count / Stride + 1);
		for (int32 Index = 0; Index < Count; Index += Stride)
		{
			double XM = 0.0;
			double YM = 0.0;
			TrackEdge.CentrelinePoint(Index, XM, YM);
			Points.Add(FVector2D(XM, YM));
		}
		Hud->SetCentreline(MoveTemp(Points));
	}

	GEngine->GameViewport->AddViewportWidgetContent(
		SNew(SWeakWidget).PossiblyNullContent(Hud.ToSharedRef()), /*ZOrder=*/10);

	// --- メニュー ---
	//
	// **HUD より手前**に出す。メニューを開いたら計器はその下に隠れる。
	Menu = SNew(SZN6Menu)
		.OnStartRace_Lambda([this]()
		{
			ReturnToMenu();
			StartCountdown();
		})
		.OnFreeRun_Lambda([this]()
		{
			ReturnToMenu();
			StartFreeRun();
		})
		.OnResume_Lambda([this]() { Race.Resume(); })
		.OnQuit_Lambda([this]()
		{
			// **確認せずに閉じる**のは避けたいが、ここは QUIT を選んだ後。
			if (APlayerController* PlayerPC = Cast<APlayerController>(GetController()))
			{
				PlayerPC->ConsoleCommand(TEXT("quit"));
			}
		})
		.OnSetupChanged_Lambda([this](const ZN6::FCarSetup& NewSetup)
		{
			ApplySetup(NewSetup);
		})
		.OnPaintChanged_Lambda([this](int32 Index)
		{
			SetPaintIndex(Index);
		});

	if (bSetupLimitsReady)
	{
		Menu->SetLimits(SetupLimits);
	}
	Menu->SetSetup(Setup);
	Menu->SetPaintIndex(PaintIndex);

	GEngine->GameViewport->AddViewportWidgetContent(
		SNew(SWeakWidget).PossiblyNullContent(Menu.ToSharedRef()), /*ZOrder=*/20);

	// --- 確認用の起動オプション ---
	//
	// **画面を人手なしで撮れるようにしておく。** これが無いと、
	// 「メニューの奥の画面」を誰も見ないまま完成と呼ぶことになる。
	int32 StartPaint = 0;
	if (FParse::Value(FCommandLine::Get(), TEXT("ZN6Paint="), StartPaint))
	{
		SetPaintIndex(StartPaint);
		Menu->SetPaintIndex(PaintIndex);
	}

	// **最初はメニューから始める。** いきなり走り出さない。
	//
	// ここでは入力モードを触らない。BeginPlay の時点では
	// PlayerController がまだこの Pawn を所有していないことがあり、
	// GetController() が null を返す。**その場合フォーカスが渡らず、
	// メニューが操作できないまま起動する。**
	// 最初の Tick で追いつかせる（bInputModeDirty）。
	int32 StartPage = 0;
	FParse::Value(FCommandLine::Get(), TEXT("ZN6MenuPage="), StartPage);
	Menu->Open(static_cast<SZN6Menu::EPage>(
		FMath::Clamp(StartPage, 0, static_cast<int32>(SZN6Menu::EPage::Result))));
	bInputModeDirty = true;
}

void AZN6VehicleActor::DestroyHud()
{
	if (GEngine != nullptr && GEngine->GameViewport != nullptr)
	{
		if (Hud.IsValid())
		{
			GEngine->GameViewport->RemoveViewportWidgetContent(Hud.ToSharedRef());
		}
		if (Menu.IsValid())
		{
			GEngine->GameViewport->RemoveViewportWidgetContent(Menu.ToSharedRef());
		}
	}
	Hud.Reset();
	Menu.Reset();
}

void AZN6VehicleActor::ApplySetup(const ZN6::FCarSetup& InSetup)
{
	// **範囲に収めてから使う。** 画面が範囲外を渡してきても物理へは通さない。
	Setup = bSetupLimitsReady ? SetupLimits.Clamped(InSetup) : InSetup;

	FString Error;
	if (!Vehicle.Init(VehicleData, /*bUseLsd=*/true, Error, Setup))
	{
		// **黙って古い設定のまま走らせない。**
		UE_LOG(LogTemp, Error,
		       TEXT("ZN6: セッティングを適用できない: %s"), *Error);
		return;
	}

	FString RideError;
	bRideReady = Ride.Init(VehicleData, RideError, Setup);
	if (!bRideReady)
	{
		UE_LOG(LogTemp, Warning,
		       TEXT("ZN6: 接地モデルを作り直せない: %s"), *RideError);
	}

	// ばねが変われば釣り合う高さも変わる。**置き直す。**
	SettleRide();
}

void AZN6VehicleActor::TickAutoScreenshot(float DeltaSeconds)
{
	if (bAutoShotTaken)
	{
		return;
	}

	if (AutoShotDelayS < 0.0f)
	{
		// 一度だけコマンドラインを見る。**指定が無ければ何もしない。**
		float Seconds = 0.0f;
		if (!FParse::Value(FCommandLine::Get(), TEXT("ZN6Shot="), Seconds))
		{
			bAutoShotTaken = true;      // 二度と見ない
			return;
		}
		AutoShotDelayS = FMath::Max(Seconds, 0.1f);
		UE_LOG(LogTemp, Display,
		       TEXT("ZN6: %.1f 秒後に画面を撮って終了する"), AutoShotDelayS);
	}

	AutoShotElapsedS += DeltaSeconds;
	if (AutoShotElapsedS < AutoShotDelayS)
	{
		return;
	}
	bAutoShotTaken = true;

	APlayerController* PlayerPC = Cast<APlayerController>(GetController());
	if (PlayerPC == nullptr)
	{
		// **撮れなかったことを黙って通さない**（憲法ルール6）。
		UE_LOG(LogTemp, Error,
		       TEXT("ZN6: PlayerController が無く画面を撮れない"));
		return;
	}

	// **`Shot showui` を使う。**
	//
	// FScreenshotRequest::RequestScreenshot は要求を出すだけで、
	// -game では拾われずファイルが出なかった（実際に空振りした）。
	// コンソールコマンドは UGameViewportClient を通るので確実に書かれる。
	// `showui` を付けないと **Slate の HUD とメニューが写らない**。
	PlayerPC->ConsoleCommand(TEXT("Shot showui"));
	UE_LOG(LogTemp, Display,
	       TEXT("ZN6: 画面を撮った（Saved/Screenshots/ 以下）"));

	// **書き終わるのを待ってから終了する。** 同じフレームで quit すると
	// ファイルが出ない。
	FTimerHandle Handle;
	GetWorldTimerManager().SetTimer(Handle, [this]()
	{
		if (APlayerController* Controller = Cast<APlayerController>(GetController()))
		{
			Controller->ConsoleCommand(TEXT("quit"));
		}
		else
		{
			FPlatformMisc::RequestExit(false);
		}
	}, 2.0f, /*bLoop=*/false);
}

void AZN6VehicleActor::TickAutoShift(float DeltaSeconds)
{
	SinceShiftS += DeltaSeconds;
	if (!Assists.bAutoShift || !bPhysicsReady)
	{
		return;
	}

	// **間隔を空ける。** 空けないと境目で上下に振動して延々と変速し続ける。
	if (SinceShiftS < Assists.ShiftIntervalS)
	{
		return;
	}

	const double Rpm = ZN6::RadsToRpm(PhysicsState.EngineOmegaRads);

	if (Rpm > Assists.UpshiftRpm && Control.GearIndex < ZN6::ForwardGearCount - 1)
	{
		ShiftUp();
		SinceShiftS = 0.0f;
	}
	else if (Rpm < Assists.DownshiftRpm && Control.GearIndex > 0)
	{
		ShiftDown();
		SinceShiftS = 0.0f;
	}
}

void AZN6VehicleActor::PreparePaintMaterials()
{
	if (PaintMaterials.Num() > 0 || BodyMesh == nullptr)
	{
		return;
	}

	const int32 SlotCount = BodyMesh->GetNumMaterials();
	for (int32 Slot = 0; Slot < SlotCount; ++Slot)
	{
		UMaterialInterface* Source = BodyMesh->GetMaterial(Slot);
		if (Source == nullptr)
		{
			continue;
		}

		// **今の色を見て、塗装かどうかを決める。**
		// スロット番号を直接書くと、モデルを差し替えた瞬間に
		// 「別の場所が塗られる」という気づきにくい壊れ方をする。
		FLinearColor Base = FLinearColor::White;
		if (!Source->GetVectorParameterValue(
				FMaterialParameterInfo(TEXT("BaseColorFactor")), Base))
		{
			continue;
		}
		if (!ZN6::IsPaintSlot(Base))
		{
			continue;
		}

		if (UMaterialInstanceDynamic* Dynamic =
				BodyMesh->CreateAndSetMaterialInstanceDynamicFromMaterial(Slot, Source))
		{
			PaintMaterials.Add(Dynamic);
		}
	}

	if (PaintMaterials.Num() == 0)
	{
		// **黙って何もしない、で終わらせない**（憲法ルール6）。
		// 塗る場所が分からない状態を、成功として通さない。
		UE_LOG(LogTemp, Warning,
		       TEXT("ZN6: 塗装スロットが見つからない（%d スロット中）。"
		            "ボディカラーは変わらない。"), SlotCount);
	}
	else
	{
		UE_LOG(LogTemp, Display, TEXT("ZN6: 塗装スロット %d 個"),
		       PaintMaterials.Num());
	}
}

void AZN6VehicleActor::SetBodyColour(const FLinearColor& Colour)
{
	PreparePaintMaterials();
	for (UMaterialInstanceDynamic* Dynamic : PaintMaterials)
	{
		if (Dynamic != nullptr)
		{
			Dynamic->SetVectorParameterValue(TEXT("BaseColorFactor"), Colour);
		}
	}
}

void AZN6VehicleActor::SetPaintIndex(int32 Index)
{
	const TArrayView<const ZN6::FPaintColour> Palette = ZN6::PaintPalette();
	if (Palette.Num() == 0)
	{
		return;
	}
	PaintIndex = ((Index % Palette.Num()) + Palette.Num()) % Palette.Num();
	SetBodyColour(Palette[PaintIndex].Colour);
}

void AZN6VehicleActor::SyncInputModeToMenu()
{
	// **これが無いとメニューが一切操作できない。**
	//
	// -game で走らせると PlayerController が入力を全部持っていく。
	// ビューポートに足しただけの Slate ウィジェットにはキーが届かない。
	// AddViewportWidgetContent は「描く」だけで、入力の経路は作らない。
	//
	// UI に渡すには入力モードを切り替え、**フォーカスするウィジェットを
	// 明示する**必要がある。SetKeyboardFocus だけでは足りない
	// （次のフレームでビューポートが取り返す）。
	APlayerController* PlayerPC = Cast<APlayerController>(GetController());
	if (PlayerPC == nullptr || !Menu.IsValid())
	{
		return;
	}

	if (Menu->IsOpen())
	{
		FInputModeUIOnly Mode;
		Mode.SetWidgetToFocus(Menu);
		// カーソルを閉じ込めない。**閉じ込めると窓から出られなくなる。**
		Mode.SetLockMouseToViewportBehavior(EMouseLockMode::DoNotLock);
		PlayerPC->SetInputMode(Mode);
		PlayerPC->SetShowMouseCursor(true);

		// 念のためこちらでも focus を渡す（モードだけだと取りこぼす環境がある）
		if (FSlateApplication::IsInitialized())
		{
			FSlateApplication::Get().SetKeyboardFocus(Menu, EFocusCause::SetDirectly);
		}
	}
	else
	{
		PlayerPC->SetInputMode(FInputModeGameOnly());
		PlayerPC->SetShowMouseCursor(false);
	}
}

void AZN6VehicleActor::ToggleMenu()
{
	if (!Menu.IsValid())
	{
		return;
	}

	if (Menu->IsOpen())
	{
		Menu->Close();
		Race.Resume();
	}
	else
	{
		// 走行中に開いたら止める。**時計も止まる。**
		Race.Pause();
		Menu->SetSetup(Setup);
		Menu->SetSnapshot(MakeHudSnapshot());
		Menu->Open(Race.Phase() == ZN6::ERacePhase::Finished
			? SZN6Menu::EPage::Result : SZN6Menu::EPage::Main);
	}

	SyncInputModeToMenu();
}

ZN6::FHudSnapshot AZN6VehicleActor::MakeHudSnapshot() const
{
	ZN6::FHudSnapshot Snapshot;

	Snapshot.SpeedKmh = PhysicsState.SpeedMps() * ZN6::KmhPerMps;
	Snapshot.EngineRpm = ZN6::RadsToRpm(PhysicsState.EngineOmegaRads);
	Snapshot.Gear = Control.GearIndex + 1;
	Snapshot.Throttle = Control.Throttle;
	Snapshot.Brake = Control.Brake;
	Snapshot.ClutchEngagement = Control.Clutch;
	Snapshot.Handbrake = Control.Handbrake;
	Snapshot.SteerRad = Control.SteerRad;
	Snapshot.MaxSteerRad = MaxSteerRad;

	for (int32 Wheel = 0; Wheel < ZN6::WheelCount; ++Wheel)
	{
		Snapshot.Utilisation[Wheel] = PhysicsOutputs.Utilisation[Wheel];
		// 接地の有無は**接地モデルから**。無いときは接地しているものとして扱う
		// （地面に置いているだけの状態では「浮く」が存在しない）。
		Snapshot.bContact[Wheel] = IsUsingRideModel()
			? RideOutputs.bContact[Wheel] : true;
	}

	Snapshot.SlipAngleDeg = FMath::RadiansToDegrees(PhysicsState.SideslipRad());
	Snapshot.LateralG = PhysicsOutputs.AyMps2 / ZN6::GravityMps2;
	Snapshot.LongitudinalG = PhysicsOutputs.AxMps2 / ZN6::GravityMps2;

	Snapshot.Phase = Race.Phase();
	Snapshot.CountdownNumber = Race.CountdownNumber();
	Snapshot.CountdownRemainingS = Race.CountdownRemainingS();
	Snapshot.CurrentLap = Race.CurrentLap();
	Snapshot.LapTimeS = Race.CurrentLapTimeS();
	Snapshot.BestLapS = Race.BestLapS();
	Snapshot.SessionTimeS = Race.SessionTimeS();
	Snapshot.Sector = Race.CurrentSector();
	Snapshot.bOffTrack = Race.IsOffTrack();
	Snapshot.bLapInvalidated = Race.CurrentLapInvalidated();
	Snapshot.LapProgress = Race.LapProgress();
	Snapshot.Laps = Race.Laps();

	Snapshot.CarXM = PhysicsState.XM;
	Snapshot.CarYM = PhysicsState.YM;
	Snapshot.CarHeadingRad = PhysicsState.HeadingRad;

	// **信頼度も画面へ。** 出典のある値と仮定値を同じ顔で並べない。
	Snapshot.Confidence = Vehicle.GetConfidence();
	Snapshot.bValidatable = Vehicle.IsValidatable();

	return Snapshot;
}

bool AZN6VehicleActor::InitialisePhysics(const FString& VehicleJsonPath, FString& OutError)
{
	if (!VehicleData.LoadFromFile(VehicleJsonPath, OutError))
	{
		return false;
	}
	// 接地モデル。**読めなくても平面3自由度は動く**ので、失敗しても
	// 物理そのものは止めない。ただし黙って無効にはしない。
	FString RideError;
	bRideReady = Ride.Init(VehicleData, RideError, Setup);
	if (!bRideReady)
	{
		UE_LOG(LogTemp, Warning,
		       TEXT("ZN6: 接地モデルを初期化できない: %s。車体は地面の高さに"
		            "置かれるだけになる（重力で支えられない）。"), *RideError);
	}

	if (!Vehicle.Init(VehicleData, /*bUseLsd=*/true, OutError, Setup))
	{
		return false;
	}

	// 調整範囲。**読めなければセッティング画面は「調整不可」と出す。**
	// 勝手な範囲を作らない（憲法ルール1）。
	FString LimitsError;
	bSetupLimitsReady = SetupLimits.Init(VehicleData, LimitsError);
	if (!bSetupLimitsReady)
	{
		UE_LOG(LogTemp, Warning,
		       TEXT("ZN6: セッティングの調整範囲を読めない: %s"), *LimitsError);
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

bool AZN6VehicleActor::LoadTrack(const FString& TrackJsonPath, FString& OutError)
{
	// **音だけのものではなくなった。** 周回計測もミニマップもここを読む。
	// 読めなければ、音の混合も計測も働かない（でっち上げない）。
	bTrackEdgeLoaded = TrackEdge.LoadFromFile(TrackJsonPath, OutError);

	ZN6::FRaceRules Rules;
	Race.Init(bTrackEdgeLoaded ? &TrackEdge : nullptr, Rules);
	return bTrackEdgeLoaded;
}

void AZN6VehicleActor::ReturnToMenu()
{
	Race.Reset();
	ResetToStart();
	// **痕も消す。** 残したまま位置を戻すと、走っていない場所に痕が残る。
	if (TyreMarks != nullptr)
	{
		TyreMarks->ClearMarks();
	}
}

bool AZN6VehicleActor::InitialiseAudio(const FString& RepoRoot, FString& OutError)
{
	if (Audio == nullptr)
	{
		OutError = TEXT("音のコンポーネントが無い");
		return false;
	}
	return Audio->Initialise(RepoRoot, VehicleData, OutError);
}

double AZN6VehicleActor::GetDistanceToTrackEdgeM() const
{
	return TrackEdge.DistanceToEdgeM(PhysicsState.XM, PhysicsState.YM);
}

void AZN6VehicleActor::SettleRide()
{
	if (!bRideReady)
	{
		return;
	}

	// 今いる場所の地面を先に調べる。**姿勢を決める前に地面を知る。**
	SampleGround();

	ZN6::FRideState Settled;
	ZN6::FRideOutputs Outputs;
	if (Ride.Settle(WheelGroundM, Settled, Outputs))
	{
		RideState = Settled;
		RideOutputs = Outputs;
		// **釣り合った荷重から始める。** 0 のまま走り出すと、最初の
		// 1ステップだけ全輪が浮いた扱いになる。
		for (int32 Wheel = 0; Wheel < ZN6::WheelCount; ++Wheel)
		{
			PreviousContactLoadsN[Wheel] = Outputs.LoadsN[Wheel];
		}
		bHasPreviousContactLoads = true;
		return;
	}

	// **収束しなかったことを黙って通さない**（憲法ルール6）。
	// 落ち着かない場所（穴の上など）に置かれた可能性がある。
	UE_LOG(LogTemp, Warning,
	       TEXT("ZN6: 接地の釣り合いに収束しない（(%.1f, %.1f) 付近）。"
	            "そのまま動的に落ち着かせる。"),
	       PhysicsState.XM, PhysicsState.YM);
	RideState = Settled;
	RideOutputs = Outputs;
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
		for (int32 Index = 0; Index < ZN6::WheelCount; ++Index)
		{
			WheelGroundM[Index] = 0.0;
		}
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
		// **接地モデルは4点別々に使う。** 平均だと片輪だけの段差が消える。
		WheelGroundM[Index] = Height[Index];
	}

	const int32 FL = static_cast<int32>(ZN6::EWheel::FL);
	const int32 FR = static_cast<int32>(ZN6::EWheel::FR);
	const int32 RL = static_cast<int32>(ZN6::EWheel::RL);
	const int32 RR = static_cast<int32>(ZN6::EWheel::RR);

	GroundHeightM = (Height[FL] + Height[FR] + Height[RL] + Height[RR]) / 4.0;

	// 前後・左右の高さ差から車体の傾きを出す。
	//
	// **UE の正のピッチは機首上げ**（FRotator(90,0,0).Vector() == (0,0,1)）。
	// 上り坂では前輪の下の地面が高いので FrontZ > RearZ、そのとき機首は
	// 上がる。したがって FrontZ - RearZ をそのまま使う。
	//
	// ここは以前 RearZ - FrontZ になっていて、**上り坂で機首が下がっていた。**
	// テストは「傾きがゼロでない」しか見ておらず、符号を検査していなかった
	// ため通っていた。ロールで同じ間違いをした直後に、ピッチで繰り返している。
	// **向きのあるものは、大きさではなく向きを検査すること。**
	const double FrontZ = (Height[FL] + Height[FR]) / 2.0;
	const double RearZ = (Height[RL] + Height[RR]) / 2.0;
	const double LeftZ = (Height[FL] + Height[RL]) / 2.0;
	const double RightZ = (Height[FR] + Height[RR]) / 2.0;

	const double WheelbaseM = FMath::Max(
		WheelAttachM[FL].X - WheelAttachM[RL].X, 0.1);
	const double TrackM = FMath::Max(
		WheelAttachM[FL].Y - WheelAttachM[FR].Y, 0.1);

	TerrainPitchRad = FMath::Atan2(FrontZ - RearZ, WheelbaseM);
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

		// **接地モデルの力をタイヤへ渡す。**
		//
		// ride は vehicle の加速度を要るので同時には解けない。前ステップの
		// 接地力を渡す（1ステップ遅れ）。荷重を1ステップ遅らせるのは
		// 準静的モデルでも同じことをしている。
		const double* ContactLoads =
			(bRideDrivesTyreLoads && IsUsingRideModel() && bHasPreviousContactLoads)
				? PreviousContactLoadsN : nullptr;

		ZN6::FVehicleState NextState;
		Vehicle.Step(PhysicsState, Control, FixedStep, NextState, PhysicsOutputs,
		             SlopeGxMps2, SlopeGyMps2, NormalScale, ContactLoads);
		PhysicsState = NextState;

		// **当たり判定は Step の後。** 触れていなければ状態は変わらないので、
		// 障害物の無い走行の結果は当たり判定を入れる前と一致する。
		if (bObstaclesLoaded)
		{
			ContactCount = Obstacles.Resolve(PhysicsState, CollisionBody,
			                                 Vehicle.GetMassKg(), Vehicle.GetIzzKgm2());
		}

		// **接地を解く。** ここで初めて、車体が「地面に置かれている」
		// のではなく「車輪に支えられている」状態になる。
		//
		// 車輪の下の地面は4点別々（SampleGround が入れた）。片輪だけ
		// 段差に乗れば、その輪だけ縮む。落ちれば接地が切れて力が 0 になる。
		if (bRideReady && bUseRideModel)
		{
			ZN6::FRideState NextRide;
			Ride.Step(RideState, FixedStep, WheelGroundM,
			          PhysicsOutputs.AxMps2, PhysicsOutputs.AyMps2,
			          NextRide, RideOutputs);
			RideState = NextRide;

			// 次のステップでタイヤへ渡す
			for (int32 Wheel = 0; Wheel < ZN6::WheelCount; ++Wheel)
			{
				PreviousContactLoadsN[Wheel] = RideOutputs.LoadsN[Wheel];
			}
			bHasPreviousContactLoads = true;
		}

		// **セッションの進行は物理の後。** 物理の固定刻みで時間を測るので、
		// 描画が重い日でもラップタイムが変わらない。
		Race.Advance(FixedStep, PhysicsState.XM, PhysicsState.YM,
		             PhysicsState.SpeedMps());

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
		// **接地モデルが入っていれば、姿勢は演出ではなく物理そのもの。**
		//
		// 接地モデルは地面の傾きも荷重移動もまとめて解いている。
		// 坂に置けば坂なりに、加速すれば機首上げに落ち着く。だから
		// TerrainPitchRad（幾何から出した地面の傾き）も
		// VisualPitchRad（荷重移動の可視化）も足さない。**足すと二重になる。**
		//
		// 接地モデルを切ったときだけ、以前の見せ方に戻す。
		double PitchDeg = 0.0;
		double RollDeg = 0.0;
		double BodyRiseCm = 0.0;

		if (IsUsingRideModel())
		{
			PitchDeg = FMath::RadiansToDegrees(RideState.PitchRad);
			RollDeg = FMath::RadiansToDegrees(RideState.RollRad);
			// Actor は接地面の平均高さに置いてあるので、車体はそこからの
			// 差だけ動かす。**沈み込みが見えるのはここ。**
			BodyRiseCm = (RideState.HeaveM - GroundHeightM) * MetresToCentimetres;
		}
		else
		{
			// 地形の傾き（**物理の一部**）に、荷重移動の可視化（演出）を足す。
			// 前者は地面そのもの、後者は見せ方。**性質が違うので分けて持つ。**
			PitchDeg = FMath::RadiansToDegrees(VisualPitchRad + TerrainPitchRad);
			RollDeg = FMath::RadiansToDegrees(VisualRollRad + TerrainRollRad);
		}

		const FRotator BodyTilt(PitchDeg, 0.0, RollDeg);

		const FVector Pivot(0.0, 0.0, AttitudeFeel.PivotHeightM * MetresToCentimetres);
		BodyMesh->SetRelativeLocation(
			Pivot - BodyTilt.RotateVector(Pivot) + FVector(0.0, 0.0, BodyRiseCm));
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
		// **基準位置は最初の1回だけ決める。**
		// 毎フレーム manifest から書き直すと、読めなかったときに
		// 車輪が原点へ飛ぶ（レベル側で設定済みの位置を潰す）。
		if (!bWheelBaseCaptured)
		{
			if (bVisualManifestLoaded)
			{
				const FVector& Attach = WheelAttachM[Index];
				WheelBaseLocationCm[Index] = FVector(
					Attach.X * MetresToCentimetres,
					-Attach.Y * MetresToCentimetres,
					Attach.Z * MetresToCentimetres);
			}
			else
			{
				WheelBaseLocationCm[Index] = WheelMeshes[Index]->GetRelativeLocation();
			}
		}

		// **車輪はそれぞれの下の地面に付いている。**
		// 車体だけが上下するので、段差では車輪が車体に対して動いて見える。
		// これがサスペンションのストローク。
		double WheelRiseCm = 0.0;
		if (IsUsingRideModel())
		{
			WheelRiseCm = (WheelGroundM[Index] - GroundHeightM) * MetresToCentimetres;
		}
		WheelMeshes[Index]->SetRelativeLocation(
			WheelBaseLocationCm[Index] + FVector(0.0, 0.0, WheelRiseCm));

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

	// **4輪ぶん読み終えてから立てる。** ループの中で立てると、
	// 2輪目以降が「もう取得済み」と判断されて基準位置を持たない。
	bWheelBaseCaptured = true;
}

void AZN6VehicleActor::Tick(float DeltaSeconds)
{
	Super::Tick(DeltaSeconds);

	// **確認用の自動走行。** `-ZN6AutoDrive` を付けたときだけ。
	//
	// 走行中の HUD は走らせないと出ない（メニューでは描かない）。人が
	// 操作せずに撮れるようにしておかないと、**HUD を誰も見ないまま完成と
	// 呼ぶことになる。** 実際そうなっていた。
	//
	// **必ず ApplyDriverInput より前に書くこと。** 入力軸のコールバックは
	// フレームの頭で毎回 RawThrottle を 0 に戻す。後ろに置くと、書いた値が
	// 使われる前に上書きされて**車が1ミリも動かない**（実際そうなった）。
	if (!bAutoDriveChecked)
	{
		bAutoDriveChecked = true;
		bAutoDrive = FParse::Param(FCommandLine::Get(), TEXT("ZN6AutoDrive"));
		if (bAutoDrive)
		{
			// **メニューを閉じる。** 開いたままだと HUD が覆われて見えない。
			if (Menu.IsValid())
			{
				Menu->Close();
			}
			StartFreeRun();
			SyncInputModeToMenu();
			// **確認用の走行では自動変速を入れる。** 入れないと1速で
			// 吹け切ったままになり、撮った画面がいつも同じになる。
			Assists.bAutoShift = true;
			UE_LOG(LogTemp, Display, TEXT("ZN6: 自動走行で起動した（確認用）"));
		}
	}
	if (bAutoDrive)
	{
		// **これは撮影用の素朴な運転であって、AIドライバーではない。**
		// `Physics/driver.py` の PID + スピン検出とは別物。ここは
		// 「コースに乗ったまま画面を撮れる」ことだけを狙っている。
		//
		// 速度を抑える。速いとコーナーで曲がりきれず、撮れる画面が
		// 毎回「草の上で回っている車」になる（実際そうなった）。
		// 速度は上書きできる。**滑らせた画面を撮りたいときに使う**
		// （タイヤ痕は滑らないと出ない）。
		float TargetKmh = 55.0f;
		FParse::Value(FCommandLine::Get(), TEXT("ZN6DriveKmh="), TargetKmh);
		const double SpeedKmh = PhysicsState.SpeedMps() * ZN6::KmhPerMps;
		RawThrottle = (SpeedKmh < TargetKmh) ? 0.55f : 0.0f;
		RawBrake = (SpeedKmh > TargetKmh * 1.25) ? 0.35f : 0.0f;

		// **滑り出したらアクセルを抜く。** FR は踏んだままだと戻らない。
		const double SlipDeg =
			FMath::Abs(FMath::RadiansToDegrees(PhysicsState.SideslipRad()));
		if (SlipDeg > 6.0)
		{
			RawThrottle = 0.0f;
		}

		// **コースを追従する。** 直進のままだと最初のコーナーで
		// コースアウトし、撮れる画面が毎回「草の上で回っている車」になる。
		//
		// 前方 12 m の点がコース中心からどれだけ外れるかを見て、
		// それを減らす向きへ切る。**今いる位置ではなく前を見る**ことで
		// 減衰が入り、蛇行しない（純粋な位置制御は必ず振動する）。
		RawSteer = 0.0f;
		if (bTrackEdgeLoaded)
		{
			constexpr double LookaheadM = 12.0;
			const double AheadX = PhysicsState.XM
			                    + FMath::Cos(PhysicsState.HeadingRad) * LookaheadM;
			const double AheadY = PhysicsState.YM
			                    + FMath::Sin(PhysicsState.HeadingRad) * LookaheadM;

			double SM = 0.0;
			double LateralM = 0.0;
			TrackEdge.NearestPoint(AheadX, AheadY, SM, LateralM);

			// LateralM は左が正。左へ外れていたら右（負）へ切る。
			// **ここは運転の補助であって車両の特性ではない**（ルール18）。
			constexpr double GainPerMetre = 0.22;
			RawSteer = static_cast<float>(
				FMath::Clamp(-LateralM * GainPerMetre, -1.0, 1.0));
		}
	}

	// **入力 -> 物理 -> 描画 の順。** 逆にすると1フレーム遅れる。
	TickAutoShift(DeltaSeconds);
	ApplyDriverInput(DeltaSeconds);
	AdvancePhysics(static_cast<double>(DeltaSeconds));
	SyncVisualToPhysics();

	// **音は物理の後。** 固定刻みの中ではなくフレームごとに更新する
	// （音は聞こえるものであって、積分するものではない）。
	if (Audio != nullptr && Audio->IsReady())
	{
		double Worst = 0.0;
		for (int32 Wheel = 0; Wheel < ZN6::WheelCount; ++Wheel)
		{
			Worst = FMath::Max(Worst, PhysicsOutputs.Utilisation[Wheel]);
		}
		Audio->UpdateAudio(PhysicsOutputs.EngineRpm, Control.Throttle, Worst,
		                   PhysicsState.SpeedMps(), GetDistanceToTrackEdgeM(),
		                   SimulatedTimeS);
	}

	TickAutoScreenshot(DeltaSeconds);

	// **入力モードをメニューの状態に追従させる。**
	// BeginPlay では PlayerController がまだ居ないことがあるので、
	// ここで確実に合わせる。合っていれば何もしない。
	if (Menu.IsValid())
	{
		const bool bMenuOpen = Menu->IsOpen();
		if (bInputModeDirty || bMenuOpen != bInputModeAppliedForOpen)
		{
			SyncInputModeToMenu();
			if (Cast<APlayerController>(GetController()) != nullptr)
			{
				bInputModeDirty = false;
				bInputModeAppliedForOpen = bMenuOpen;
			}
		}
	}

	// --- タイヤ痕 ---
	//
	// **物理の後。** 滑りと接地の判定を物理から取る。
	// 「アクセルを踏んだら出す」ようにはしない（物理と関係ない飾りになる）。
	if (TyreMarks != nullptr && TyreMarks->IsReady())
	{
		constexpr double MetresToCentimetres = 100.0;
		const double CosH = FMath::Cos(PhysicsState.HeadingRad);
		const double SinH = FMath::Sin(PhysicsState.HeadingRad);

		FVector ContactCm[ZN6::WheelCount];
		double Used[ZN6::WheelCount];
		bool bTouching[ZN6::WheelCount];

		for (int32 Wheel = 0; Wheel < ZN6::WheelCount; ++Wheel)
		{
			const FVector& Attach = WheelAttachM[Wheel];
			const double WorldX = PhysicsState.XM + Attach.X * CosH - Attach.Y * SinH;
			const double WorldY = PhysicsState.YM + Attach.X * SinH + Attach.Y * CosH;

			// **痕は接地点に残る。** 車輪の中心ではない。
			// 物理の y は左が正、UE は右が正なので符号を反転する。
			ContactCm[Wheel] = FVector(
				WorldX * MetresToCentimetres,
				-WorldY * MetresToCentimetres,
				WheelGroundM[Wheel] * MetresToCentimetres);

			Used[Wheel] = PhysicsOutputs.Utilisation[Wheel];
			bTouching[Wheel] = IsUsingRideModel() ? RideOutputs.bContact[Wheel] : true;
		}

		TyreMarks->Update(DeltaSeconds, ContactCm, Used, bTouching,
		                  PhysicsState.HeadingRad);
	}

	// **画面は物理の後。** 表示のために物理を先読みしない。
	if (Hud.IsValid())
	{
		Hud->SetSnapshot(MakeHudSnapshot());
	}

	// 規定周回を終えたら、**放っておいてもリザルトを出す。**
	// 何も起きないと「終わったのか固まったのか」が分からない。
	if (Menu.IsValid() && !Menu->IsOpen()
	    && Race.Phase() == ZN6::ERacePhase::Finished)
	{
		Menu->SetSetup(Setup);
		Menu->SetSnapshot(MakeHudSnapshot());
		Menu->Open(SZN6Menu::EPage::Result);
		SyncInputModeToMenu();
	}

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
	PlayerInputComponent->BindAction(TEXT("ZN6_Menu"), IE_Pressed,
	                                 this, &AZN6VehicleActor::ToggleMenu);
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

	// **カウントダウン中・一時停止中は操作を渡さない。**
	//
	// 物理を止めるのではなく入力を 0 にする。物理から見れば「踏んでいない」
	// のと同じで、モデルには何も足していない（憲法ルール18）。
	// 止めてしまうと、スタート前に車が坂を転がることさえ再現できなくなる。
	const float Gate = static_cast<float>(Race.InputScale());

	const float PedalRate = DriverFeel.PedalRatePerS;
	Control.Throttle = Approach(static_cast<float>(Control.Throttle),
	                            Gate * FMath::Clamp(RawThrottle, 0.0f, 1.0f),
	                            PedalRate);
	Control.Brake = Approach(static_cast<float>(Control.Brake),
	                         Gate * FMath::Clamp(RawBrake, 0.0f, 1.0f),
	                         PedalRate);

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

	const float Target = Gate * FMath::Clamp(RawSteer, -1.0f, 1.0f);
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

	// **メニューが開いているあいだは出さない。**
	//
	// AddOnScreenDebugMessage は必ず左上に描かれ、メニューの1行目
	// （RACE）に重なって読めなくしていた。実際に撮った画面で初めて
	// 気づいた。**画面に出るものは見て確かめること。**
	if (Menu.IsValid() && Menu->IsOpen())
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
