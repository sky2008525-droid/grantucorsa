// 車のセッティング（Physics/setup.py の移植）。
//
// **スライダーは全部、物理に効くものだけを置く。**
// 動かしても何も変わらない項目を画面に出さない。出すなら「効かない」と
// 書く。効いているふりをするのは、数値を捏造するのと同じ性質の嘘である。
//
// だから `UnsupportedItems()` に「今のモデルでは効かないもの」を理由つきで
// 並べてある。セッティング画面はこれを読んで、灰色で理由を出す。
//
// **既定は「何も変えない」。** そのときの結果は、セッティング機能を
// 入れる前とビット単位で一致する。

#pragma once

#include "CoreMinimal.h"
#include "ZN6VehicleData.h"

namespace ZN6
{
	/** 調整項目の識別子。**並び順が画面の並び順。** */
	enum class ESetupItem : uint8
	{
		RideHeight,
		CamberFront,
		CamberRear,
		ToeFront,
		ToeRear,
		SpringFront,
		SpringRear,
		DampingFront,
		DampingRear,
		BrakeBias,
		Count,
	};

	/** 調整できる範囲。**画面はここから目盛りを作る。** */
	struct FSetupRange
	{
		double Low = 0.0;
		double High = 0.0;
		double Default = 0.0;
		FString Unit;
		FString Label;
		FString Note;
		/** 画面に出すときの倍率と桁（rad -> deg、m -> mm など）。 */
		double DisplayScale = 1.0;
		FString DisplayUnit;
		int32 DisplayDigits = 2;

		double Clamp(double Value) const { return FMath::Clamp(Value, Low, High); }
		bool Contains(double Value) const
		{
			return Value >= Low - 1e-12 && Value <= High + 1e-12;
		}
		bool IsAdjustable() const { return High > Low + 1e-12; }
	};

	/** 1台ぶんのセッティング。 */
	struct FCarSetup
	{
		double RideHeightM = 0.0;
		double CamberFrontRad = 0.0;
		double CamberRearRad = 0.0;
		double ToeFrontRad = 0.0;
		double ToeRearRad = 0.0;
		double SpringScaleFront = 1.0;
		double SpringScaleRear = 1.0;
		double DampingScaleFront = 1.0;
		double DampingScaleRear = 1.0;
		/** 負なら「vehicle.json の値をそのまま使う」。 */
		double BrakeBias = -1.0;

		bool IsDefault() const;

		double Get(ESetupItem Item) const;
		void Set(ESetupItem Item, double Value);

		/** その車輪の静的な向き [rad]。**車体座標系での符号に直す。** */
		double WheelToeRad(int32 Wheel) const;

		/**
		 * キャンバーを**車体座標系の傾き**に直す [rad]。正が左へ倒れる。
		 *
		 * 自動車の慣習では負が内側倒し。左輪の内側は -y（右）、右輪の
		 * 内側は +y（左）なので、**同じ負のキャンバーでも倒れる向きは
		 * 左右で逆になる。** ここを揃えないと直進で横に走り出す。
		 */
		double WheelCamberLeanRad(int32 Wheel) const;

		/** セッティング後の重心高 [m]。**基準値そのものは書き換えない。** */
		double CgHeightM(double BaselineM) const { return BaselineM + RideHeightM; }

		bool UsesCamber() const
		{
			return CamberFrontRad != 0.0 || CamberRearRad != 0.0;
		}

		/** 人が読む要約。 */
		FString Describe() const;
	};

	/** 調整範囲。`vehicle.json` の min/max を超えない。 */
	class FSetupLimits
	{
	public:
		bool Init(FVehicleData& Data, FString& OutError);

		const FSetupRange& Range(ESetupItem Item) const
		{
			return Ranges[static_cast<int32>(Item)];
		}

		/** 範囲外の項目を並べて返す。**空なら妥当。** */
		void Validate(const FCarSetup& Setup, TArray<FString>& OutProblems) const;

		/** 範囲に収めた複製。 */
		FCarSetup Clamped(const FCarSetup& Setup) const;

	private:
		FSetupRange Ranges[static_cast<int32>(ESetupItem::Count)];
	};

	/**
	 * 今のモデルでは効かない項目と、その理由。
	 *
	 * **項目を黙って消さない。** 消すと「このゲームにこの調整が無い」のか
	 * 「実装が抜けている」のかが区別できない。
	 */
	const TArray<TPair<FString, FString>>& UnsupportedItems();
}
