#include "ZN6TyreMarkComponent.h"

#include "Components/DecalComponent.h"
#include "Materials/MaterialInstanceDynamic.h"
#include "Materials/MaterialInterface.h"
#include "Physics/ZN6Units.h"

namespace
{
	/** 物理 [m] から UE [cm] へ。 */
	constexpr double MetresToCentimetres = 100.0;

	/** デカールのマテリアル。`build_level.py` が作る。 */
	const TCHAR* MarkMaterialPath = TEXT("/Game/ZN6/Materials/M_ZN6_TyreMark.M_ZN6_TyreMark");
}

UZN6TyreMarkComponent::UZN6TyreMarkComponent()
{
	PrimaryComponentTick.bCanEverTick = false;   // 車両アクタが呼ぶ
}

bool UZN6TyreMarkComponent::Initialise(FString& OutError)
{
	bReady = false;

	MarkMaterial = LoadObject<UMaterialInterface>(nullptr, MarkMaterialPath);
	if (MarkMaterial == nullptr)
	{
		// **黙って痕なしにしない**（憲法ルール6）。
		OutError = FString::Printf(
			TEXT("タイヤ痕のマテリアルが無い: %s。build_level.py を走らせること"),
			MarkMaterialPath);
		return false;
	}

	Decals.Reset();
	RemainingS.Reset();
	Decals.Reserve(Feel.MaxMarks);
	RemainingS.SetNumZeroed(Feel.MaxMarks);

	bReady = true;
	return true;
}

void UZN6TyreMarkComponent::ClearMarks()
{
	for (UDecalComponent* Decal : Decals)
	{
		if (Decal != nullptr)
		{
			Decal->SetVisibility(false);
		}
	}
	for (float& Remaining : RemainingS)
	{
		Remaining = 0.0f;
	}
	for (int32 Wheel = 0; Wheel < ZN6::WheelCount; ++Wheel)
	{
		bHasLastPlaced[Wheel] = false;
	}
	NextIndex = 0;
	LiveMarks = 0;
}

void UZN6TyreMarkComponent::PlaceMark(const FVector& WorldCm, double HeadingRad,
                                      float Opacity)
{
	if (!bReady || Feel.MaxMarks <= 0)
	{
		return;
	}

	// **足りなければ作る。足りていれば使い回す。**
	// 上限を持たないと、滑り続けるだけでコンポーネントが際限なく増える。
	UDecalComponent* Decal = nullptr;
	if (Decals.Num() < Feel.MaxMarks)
	{
		Decal = NewObject<UDecalComponent>(this);
		Decal->SetupAttachment(this);
		Decal->RegisterComponent();
		Decal->SetDecalMaterial(MarkMaterial);
		// **車に追従させない。** 痕は路面に残るもので、車と一緒に
		// 動いてはいけない。親の変換を無視させる。
		Decal->SetAbsolute(/*bNewAbsoluteLocation=*/true,
		                   /*bNewAbsoluteRotation=*/true,
		                   /*bNewAbsoluteScale=*/true);
		Decals.Add(Decal);
		NextIndex = Decals.Num() - 1;
	}
	else
	{
		NextIndex = (NextIndex + 1) % Decals.Num();
		Decal = Decals[NextIndex];
	}
	if (Decal == nullptr)
	{
		return;
	}

	// **デカールは -X 方向へ投影する。** 上から落とすには
	// ピッチ -90 度にする。ここを間違えると痕が横向きに飛ぶ。
	//
	// 物理のヨーは左が正、UE は右が正なので符号を反転する
	// （`SyncVisualToPhysics` と同じ約束）。
	const FRotator Rotation(-90.0, -FMath::RadiansToDegrees(HeadingRad), 0.0);

	// DecalSize は (投影距離, 幅の半分, 長さの半分)。
	const FVector Size(
		40.0,
		Feel.WidthM * 0.5 * MetresToCentimetres,
		Feel.LengthM * 0.5 * MetresToCentimetres);

	Decal->SetWorldLocationAndRotation(WorldCm, Rotation);
	Decal->DecalSize = Size;
	Decal->SetVisibility(true);

	UMaterialInstanceDynamic* Dynamic = Decal->CreateDynamicMaterialInstance();
	if (Dynamic != nullptr)
	{
		Dynamic->SetScalarParameterValue(TEXT("Opacity"), Opacity);
	}

	if (RemainingS.IsValidIndex(NextIndex))
	{
		RemainingS[NextIndex] = (Feel.FadeSeconds > 0.0f) ? Feel.FadeSeconds : -1.0f;
	}
	LiveMarks = FMath::Min(LiveMarks + 1, Decals.Num());
}

void UZN6TyreMarkComponent::Update(float DeltaSeconds,
                                   const FVector WheelWorldCm[ZN6::WheelCount],
                                   const double Utilisation[ZN6::WheelCount],
                                   const bool bContact[ZN6::WheelCount],
                                   double HeadingRad)
{
	if (!bReady)
	{
		return;
	}

	// --- 薄れさせる ---
	for (int32 Index = 0; Index < Decals.Num(); ++Index)
	{
		if (!RemainingS.IsValidIndex(Index) || RemainingS[Index] <= 0.0f)
		{
			continue;                      // 未使用、または消えない設定
		}
		RemainingS[Index] -= DeltaSeconds;
		UDecalComponent* Decal = Decals[Index];
		if (Decal == nullptr)
		{
			continue;
		}
		if (RemainingS[Index] <= 0.0f)
		{
			Decal->SetVisibility(false);
			LiveMarks = FMath::Max(LiveMarks - 1, 0);
			continue;
		}
		// 最後の 1/3 で薄れる。**最初から薄れさせない**（すぐ見えなくなる）。
		const float Fraction = RemainingS[Index] / FMath::Max(Feel.FadeSeconds, 1e-3f);
		if (Fraction < 0.34f)
		{
			if (UMaterialInstanceDynamic* Dynamic =
					Cast<UMaterialInstanceDynamic>(Decal->GetDecalMaterial()))
			{
				Dynamic->SetScalarParameterValue(
					TEXT("Opacity"), Feel.MaxOpacity * Fraction / 0.34f);
			}
		}
	}

	// --- 置く ---
	const double SpacingCm = Feel.SpacingM * MetresToCentimetres;

	for (int32 Wheel = 0; Wheel < ZN6::WheelCount; ++Wheel)
	{
		// **接地していない輪は痕を残さない。** 地面に触れていないため。
		if (!bContact[Wheel])
		{
			bHasLastPlaced[Wheel] = false;
			continue;
		}

		const double Used = Utilisation[Wheel];
		if (Used < Feel.SlipThreshold)
		{
			// 滑っていない。**次に滑り出したときは間隔を測り直す。**
			bHasLastPlaced[Wheel] = false;
			continue;
		}

		const FVector& Position = WheelWorldCm[Wheel];
		if (bHasLastPlaced[Wheel]
		    && FVector::Dist(Position, LastPlacedCm[Wheel]) < SpacingCm)
		{
			continue;                      // まだ間隔に達していない
		}

		const double Span = FMath::Max(Feel.SlipFull - Feel.SlipThreshold, 1e-6);
		const float Opacity = static_cast<float>(
			Feel.MaxOpacity * FMath::Clamp((Used - Feel.SlipThreshold) / Span, 0.0, 1.0));

		PlaceMark(Position, HeadingRad, Opacity);
		LastPlacedCm[Wheel] = Position;
		bHasLastPlaced[Wheel] = true;
	}
}
