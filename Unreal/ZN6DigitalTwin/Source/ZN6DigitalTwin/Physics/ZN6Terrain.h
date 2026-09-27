// 地形の高さ場（Physics/terrain.py の移植）。
//
// **描画メッシュから高さを読まないこと。** 憲法ルール4。
// Blender/build_track.py が地面メッシュと同じ値から heightfield.json を
// 書き出す。物理も描画もこれを読む。
//
// この地形が物理に与えるのは
//   - 接地面の高さ
//   - 斜面方向の重力成分（上り坂で減速する）
//   - 法線方向の荷重 mg*cos(傾き)
// であって、**サスペンションの伸縮ではない。**
// damper が "unknown" である以上、バネ上の上下振動は組めない（issue #19）。

#pragma once

#include "CoreMinimal.h"

namespace ZN6
{
	/** 等間隔格子の高さ場。双線形補間。 */
	class FHeightfield
	{
	public:
		/** heightfield.json を読む。失敗したら false（**既定値で代用しない**）。 */
		bool LoadFromFile(const FString& Path, FString& OutError);

		bool IsValid() const { return bLoaded; }

		/** (x, y) の地面高さ [m]。**範囲外は端の高さが続くとみなす**（落とさない）。 */
		double HeightAt(double XM, double YM) const;

		/** 勾配 (dz/dx, dz/dy)。中心差分。 */
		void SlopeAt(double XM, double YM, double& OutDzDx, double& OutDzDy) const;

	private:
		int32 ClampedIndex(double Value, double Origin, int32 Count, double& OutFraction) const;

		bool bLoaded = false;
		double X0M = 0.0;
		double Y0M = 0.0;
		double CellM = 1.0;
		int32 Nx = 0;
		int32 Ny = 0;
		TArray<double> Heights;      // [iy * Nx + ix]
	};

	/**
	 * 斜面が車体に与える重力成分と法線荷重の係数。
	 *
	 * **Python 版（terrain.body_gravity）と同じ式にすること。**
	 *
	 * 車体の前後軸・左右軸は接平面の中にあるので、重力 (0,0,-g) を
	 * その軸へ直接射影する。**接平面成分の水平投影を取ってはいけない**
	 * （傾きが大きいと合成して g に戻らない）。
	 */
	void BodyGravity(double DzDx, double DzDy, double HeadingRad,
	                 double& OutForwardMps2, double& OutLeftMps2, double& OutNormalScale);
}
