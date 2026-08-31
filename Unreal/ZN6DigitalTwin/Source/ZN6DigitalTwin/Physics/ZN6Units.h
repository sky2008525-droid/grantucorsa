// 単位変換を1箇所に集める（Physics/units.py の移植）。
//
// 憲法ルール5「SI単位系を内部計算の標準とする」/ ルール13「単位を明示する」。
//
// **式の中に /3.6 や *9.80665 を散らさないこと。** 単位の取り違えは、
// 物理的にあり得ない結果としてではなく「もっともらしい間違った結果」として
// 現れる。これが最も見つけにくい。
//
// vehicle.json は一次資料の表記に近い形で保存してある（rpm、L）。
// SI への変換はここでのみ行う。

#pragma once

#include "CoreMinimal.h"

namespace ZN6
{
	/** 標準重力加速度 [m/s^2]。 */
	inline constexpr double GravityMps2 = 9.80665;

	/**
	 * 海面上・15degC の空気密度 [kg/m^3]。ISA 標準大気。
	 *
	 * **測定条件を変えるなら明示すること。** 気温が変われば空力抗力が変わり、
	 * 0-100km/h の実測値がばらつく原因の1つになる。
	 */
	inline constexpr double AirDensityKgPm3 = 1.225;

	inline constexpr double KmhPerMps = 3.6;
	inline constexpr double M3PerLitre = 1.0e-3;

	/**
	 * 円周率。**UE の `PI` を使わないこと。**
	 *
	 * UE の `PI` は float 定数（`3.1415926535897932f`）で、double に昇格させても
	 * 3.14159274101257324... となり、Python の `math.pi` と相対 2.8e-8 ずれる。
	 * 単位変換は全ての rpm <-> rad/s が通る1点なので、ここでずれると
	 * エンジン回転数を経由する量すべてに波及する。
	 *
	 * **単位の取り違えと同じで、物理的にあり得ない結果ではなく
	 * 「もっともらしい間違った結果」として現れる。** double 版を使いたい
	 * 場合は UE_DOUBLE_PI もあるが、Python 側と同じ値であることを明示したいので
	 * ここで定義する。
	 */
	inline constexpr double Pi = 3.14159265358979323846;

	inline constexpr double RadsPerRpm()
	{
		return 2.0 * Pi / 60.0;
	}

	inline double KmhToMps(double Kmh) { return Kmh / KmhPerMps; }
	inline double MpsToKmh(double Mps) { return Mps * KmhPerMps; }

	/** エンジン回転数 [1/min] を角速度 [rad/s] へ。 */
	inline double RpmToRads(double Rpm) { return Rpm * RadsPerRpm(); }
	inline double RadsToRpm(double Rads) { return Rads / RadsPerRpm(); }

	inline double LitreToM3(double Litre) { return Litre * M3PerLitre; }

	/**
	 * vehicle.json の保存単位から要求単位へ変換する。
	 *
	 * **暗黙の変換を増やさないこと。** 変換できてしまうと取り違えに気づけなくなる。
	 * Physics/units.py の CONVERSIONS と同じ組み合わせだけを通す。
	 *
	 * @return 変換できたら true。定義の無い組み合わせは false（呼び出し側で停止すること）
	 */
	bool TryConvert(double Value, const FString& FromUnit, const FString& ToUnit, double& OutValue);
}
