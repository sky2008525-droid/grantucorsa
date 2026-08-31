// コース中心線からの位置（Tracks/physics_test_track.json の読み取り）。
//
// **読むのはコースの定義データであって、路面メッシュの頂点ではない**
// （憲法ルール4）。ZN6Terrain が heightfield.json を、ZN6Obstacles が
// placement.json を読むのと同じ考え方。
//
// 今のところ使い道は音のクロスフェード（路面が変わる境界）だけだが、
// 「コースの内か外か」は音以外にも要るので Physics/ に置いてある。
// **ここから車の運動へは何も返さない。**

#pragma once

#include "CoreMinimal.h"

namespace ZN6
{
	/** コース中心線と幅。 */
	class FTrackEdge
	{
	public:
		bool LoadFromFile(const FString& Path, FString& OutError);

		bool IsValid() const { return bLoaded; }
		int32 PointCount() const { return PointsX.Num(); }
		double WidthM() const { return TrackWidthM; }

		/**
		 * 路面の端までの符号つき距離 [m]。**内側が正。**
		 *
		 * 中心線までの距離を測り、`幅/2 - その距離` を返す。中心線上なら
		 * `幅/2`、路肩へ出ると負。
		 *
		 * **中心線の点は 1 m 間隔**（physics_test_track.json の spacing_m）
		 * なので、最近点は総当たりで求める。点数は千個程度で、
		 * 呼ぶのはフレームに1回。
		 */
		double DistanceToEdgeM(double XM, double YM) const;

	private:
		bool bLoaded = false;
		TArray<double> PointsX;
		TArray<double> PointsY;
		double TrackWidthM = 0.0;
	};
}
