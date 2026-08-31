// テストコースを走るためだけの最小の GameMode。
//
// **Pawn を自前で spawn しない。** レベルに置いてある AZN6VehicleActor を
// そのまま操作する（`bAutoPossessPlayer` で PlayerController が掴む）。
//
// 新しく spawn すると、Blender が決めた車輪の取り付け位置や、
// build_level.py が割り当てた描画メッシュを持たない「空の車」が出てくる。

#pragma once

#include "CoreMinimal.h"
#include "GameFramework/GameModeBase.h"
#include "ZN6GameMode.generated.h"

UCLASS()
class ZN6DIGITALTWIN_API AZN6GameMode : public AGameModeBase
{
	GENERATED_BODY()

public:
	AZN6GameMode();
};
