#include "ZN6GameMode.h"

AZN6GameMode::AZN6GameMode()
{
	// **既定の Pawn を spawn させない。**
	//
	// レベルに置いた AZN6VehicleActor を `bAutoPossessPlayer` で掴む設計
	// なので、ここで別の Pawn が湧くと、操作対象が2台になったうえ
	// 描画メッシュを持たないほうを操作してしまう。
	DefaultPawnClass = nullptr;
}
