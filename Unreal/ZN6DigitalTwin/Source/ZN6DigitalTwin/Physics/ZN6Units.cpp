#include "ZN6Units.h"

namespace ZN6
{
	bool TryConvert(double Value, const FString& FromUnit, const FString& ToUnit, double& OutValue)
	{
		if (FromUnit == ToUnit)
		{
			OutValue = Value;
			return true;
		}

		// Physics/units.py の CONVERSIONS と1対1で対応させること。
		// **片方だけに変換を足さないこと。** 実装間で結果が変わる。
		if (FromUnit == TEXT("1/min") && ToUnit == TEXT("rad/s")) { OutValue = RpmToRads(Value); return true; }
		if (FromUnit == TEXT("rad/s") && ToUnit == TEXT("1/min")) { OutValue = RadsToRpm(Value); return true; }
		if (FromUnit == TEXT("L")     && ToUnit == TEXT("m^3"))   { OutValue = LitreToM3(Value); return true; }
		if (FromUnit == TEXT("m^3")   && ToUnit == TEXT("L"))     { OutValue = Value / M3PerLitre; return true; }
		if (FromUnit == TEXT("km/h")  && ToUnit == TEXT("m/s"))   { OutValue = KmhToMps(Value); return true; }
		if (FromUnit == TEXT("m/s")   && ToUnit == TEXT("km/h"))  { OutValue = MpsToKmh(Value); return true; }

		return false;
	}
}
