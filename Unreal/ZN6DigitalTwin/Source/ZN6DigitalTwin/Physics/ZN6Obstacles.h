// 樹木と世界境界の当たり判定（Physics/obstacles.py の移植）。
//
// **描画メッシュから形を読まないこと。** 憲法ルール4。
// 読むのは Tracks/Export/placement.json の**配置データ**だけで、
// 樹木の StaticMesh やそのコリジョンではない。
// ZN6Terrain が heightfield.json を読むのと同じ考え方。
//
// **Python 版と同じ結果になるように書くこと。** 接触を解く順序も含めて
// 一致させる（placement.json の並び順で逐次に解く）。

#pragma once

#include "CoreMinimal.h"
#include "ZN6Vehicle.h"
#include "ZN6VehicleData.h"

namespace ZN6
{
	/**
	 * 当たり判定のゲーム側設定。
	 *
	 * **ここに書く値は車両仕様ではない**（憲法ルール18）。実車が樹木に
	 * 衝突したときの反発係数を測った資料は無く、樹木も景観であって
	 * 計測対象ではない。**vehicle.json に混ぜないこと。**
	 */
	struct FObstacleFeel
	{
		/** 反発係数 [-]。**出典は無い。** 0 = 跳ね返らない / 1 = 完全弾性。 */
		double Restitution = 0.15;

		/** 幹の当たり半径 [m]（placement.json の scale 1 あたり）。**出典は無い。** */
		double TrunkRadiusPerScaleM = 0.15;
	};

	/**
	 * 当たり判定に使う車体の外形（重心を原点とする車体固定系、x 前方 / y 左方）。
	 *
	 * **描画メッシュからは読まない。** 全長・全幅は vehicle.json の official。
	 * ただし**前後オーバーハングの配分は vehicle.json に無い**ので、
	 * Init は等分と仮定する。実測値ではない。
	 */
	struct FCollisionBody
	{
		double FrontM = 0.0;        /**< 重心から前端まで [m]。 */
		double RearM = 0.0;         /**< 重心から後端まで [m]（正の距離）。 */
		double HalfWidthM = 0.0;    /**< 中心線から側面まで [m]。 */

		/** vehicle.json から作る。**数値をここに書かない。** */
		bool Init(FVehicleData& Data, FString& OutError);

		/** 重心から最も遠い角までの距離 [m]。粗い判定に使う。 */
		double BoundingRadiusM() const;

		/** 4隅（車体固定系）。前左・前右・後左・後右。 */
		void Corners(double OutX[4], double OutY[4]) const;
	};

	/** 1回の接触の記録。テレメトリと検査用。 */
	struct FContact
	{
		bool bTree = true;           /**< true=樹木 / false=世界境界。 */
		int32 Index = 0;
		double DepthM = 0.0;
		double ClosingSpeedMps = 0.0;
		double ImpulseNs = 0.0;
		/** 幹の中心が車体の内側にあった。**dt が大き過ぎるとこうなる。** */
		bool bEngulfed = false;
	};

	/**
	 * 車体長方形と円（幹の断面）の接触。**すべて車体固定系。**
	 *
	 * 法線は**「障害物 -> 車」向き**。逆にすると車が木へ吸い込まれる。
	 *
	 * @return 接触していれば true
	 */
	bool CircleContact(const FCollisionBody& Body, double BxM, double ByM, double RadiusM,
	                   double& OutPxM, double& OutPyM, double& OutNx, double& OutNy,
	                   double& OutDepthM, bool& bOutEngulfed);

	/**
	 * 接触点に加える法線撃力 [N*s] と接近速度 [m/s]。**車体固定系。**
	 *
	 * **離れつつあるなら 0 を返す。** これが無いと、一度触れた物体に
	 * 何ステップも撃力が入って車が弾き飛ばされる。
	 */
	void ContactImpulse(double VxMps, double VyMps, double YawRateRads,
	                    double PxM, double PyM, double Nx, double Ny,
	                    double MassKg, double IzzKgm2, double Restitution,
	                    double& OutImpulseNs, double& OutClosingMps);

	/** 樹木（鉛直な円柱）の集合と世界境界。 */
	class FObstacleField
	{
	public:
		/** placement.json を読む。**メッシュではなく配置データ。** */
		bool LoadFromPlacement(const FString& Path, FString& OutError);

		bool IsValid() const { return bLoaded; }
		int32 TreeCount() const { return Trees.Num(); }

		/**
		 * i 番目の樹木の位置と当たり半径 [m]。**検査用。**
		 * テストが座標を書き写さずに、実際の配置へ車を向けられるようにする。
		 */
		bool GetTree(int32 Index, double& OutXM, double& OutYM, double& OutRadiusM) const;

		/** 世界境界 [m]（placement.json の extent_m）。 */
		void Bounds(double& OutX0M, double& OutX1M, double& OutY0M, double& OutY1M) const
		{
			OutX0M = X0M; OutX1M = X1M; OutY0M = Y0M; OutY1M = Y1M;
		}
		const FObstacleFeel& GetFeel() const { return Feel; }
		void SetFeel(const FObstacleFeel& InFeel) { Feel = InFeel; }

		/**
		 * 1ステップ分の接触を解く。**FVehicle::Step の後**に呼ぶ。
		 *
		 * **どこにも触れていなければ State を書き換えない。** 障害物が無い
		 * 走行では、結果が当たり判定を入れる前とビット単位で一致する。
		 *
		 * @return 接触した数
		 */
		int32 Resolve(FVehicleState& State, const FCollisionBody& Body,
		              double MassKg, double IzzKgm2,
		              TArray<FContact>* OutContacts = nullptr) const;

	private:
		struct FTree
		{
			double XM = 0.0;
			double YM = 0.0;
			double RadiusM = 0.0;
		};

		bool bLoaded = false;
		TArray<FTree> Trees;
		double X0M = 0.0;
		double X1M = 0.0;
		double Y0M = 0.0;
		double Y1M = 0.0;
		FObstacleFeel Feel;
	};
}
