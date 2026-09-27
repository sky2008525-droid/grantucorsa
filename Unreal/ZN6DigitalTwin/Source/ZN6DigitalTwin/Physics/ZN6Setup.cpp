#include "ZN6Setup.h"

#include "ZN6Vehicle.h"

namespace ZN6
{
	namespace
	{
		bool IsFront(int32 Wheel)
		{
			return Wheel == static_cast<int32>(EWheel::FL)
			    || Wheel == static_cast<int32>(EWheel::FR);
		}

		bool IsLeft(int32 Wheel)
		{
			return Wheel == static_cast<int32>(EWheel::FL)
			    || Wheel == static_cast<int32>(EWheel::RL);
		}
	}

	// -----------------------------------------------------------------------

	bool FCarSetup::IsDefault() const
	{
		return RideHeightM == 0.0
		    && CamberFrontRad == 0.0 && CamberRearRad == 0.0
		    && ToeFrontRad == 0.0 && ToeRearRad == 0.0
		    && SpringScaleFront == 1.0 && SpringScaleRear == 1.0
		    && DampingScaleFront == 1.0 && DampingScaleRear == 1.0
		    && BrakeBias < 0.0;
	}

	double FCarSetup::Get(ESetupItem Item) const
	{
		switch (Item)
		{
		case ESetupItem::RideHeight:   return RideHeightM;
		case ESetupItem::CamberFront:  return CamberFrontRad;
		case ESetupItem::CamberRear:   return CamberRearRad;
		case ESetupItem::ToeFront:     return ToeFrontRad;
		case ESetupItem::ToeRear:      return ToeRearRad;
		case ESetupItem::SpringFront:  return SpringScaleFront;
		case ESetupItem::SpringRear:   return SpringScaleRear;
		case ESetupItem::DampingFront: return DampingScaleFront;
		case ESetupItem::DampingRear:  return DampingScaleRear;
		case ESetupItem::BrakeBias:    return BrakeBias;
		default:                       return 0.0;
		}
	}

	void FCarSetup::Set(ESetupItem Item, double Value)
	{
		switch (Item)
		{
		case ESetupItem::RideHeight:   RideHeightM = Value; break;
		case ESetupItem::CamberFront:  CamberFrontRad = Value; break;
		case ESetupItem::CamberRear:   CamberRearRad = Value; break;
		case ESetupItem::ToeFront:     ToeFrontRad = Value; break;
		case ESetupItem::ToeRear:      ToeRearRad = Value; break;
		case ESetupItem::SpringFront:  SpringScaleFront = Value; break;
		case ESetupItem::SpringRear:   SpringScaleRear = Value; break;
		case ESetupItem::DampingFront: DampingScaleFront = Value; break;
		case ESetupItem::DampingRear:  DampingScaleRear = Value; break;
		case ESetupItem::BrakeBias:    BrakeBias = Value; break;
		default: break;
		}
	}

	double FCarSetup::WheelToeRad(int32 Wheel) const
	{
		// トーインは「前が内側を向く」こと。左輪では右（負）、右輪では左（正）。
		// **符号を逆にすると、直進で車が片側へ引っ張られる。**
		const double Toe = IsFront(Wheel) ? ToeFrontRad : ToeRearRad;
		return IsLeft(Wheel) ? -Toe : Toe;
	}

	double FCarSetup::WheelCamberLeanRad(int32 Wheel) const
	{
		const double Camber = IsFront(Wheel) ? CamberFrontRad : CamberRearRad;
		return IsLeft(Wheel) ? Camber : -Camber;
	}

	FString FCarSetup::Describe() const
	{
		if (IsDefault())
		{
			return TEXT("純正（何も変更していない）");
		}

		TArray<FString> Parts;
		if (RideHeightM != 0.0)
		{
			Parts.Add(FString::Printf(TEXT("車高 %+.0fmm"), RideHeightM * 1000.0));
		}
		if (CamberFrontRad != 0.0)
		{
			Parts.Add(FString::Printf(TEXT("前キャンバー %+.2fdeg"),
			                          FMath::RadiansToDegrees(CamberFrontRad)));
		}
		if (CamberRearRad != 0.0)
		{
			Parts.Add(FString::Printf(TEXT("後キャンバー %+.2fdeg"),
			                          FMath::RadiansToDegrees(CamberRearRad)));
		}
		if (ToeFrontRad != 0.0)
		{
			Parts.Add(FString::Printf(TEXT("前トー %+.2fdeg"),
			                          FMath::RadiansToDegrees(ToeFrontRad)));
		}
		if (ToeRearRad != 0.0)
		{
			Parts.Add(FString::Printf(TEXT("後トー %+.2fdeg"),
			                          FMath::RadiansToDegrees(ToeRearRad)));
		}
		if (SpringScaleFront != 1.0)
		{
			Parts.Add(FString::Printf(TEXT("前ばね %.0f%%"), SpringScaleFront * 100.0));
		}
		if (SpringScaleRear != 1.0)
		{
			Parts.Add(FString::Printf(TEXT("後ばね %.0f%%"), SpringScaleRear * 100.0));
		}
		if (DampingScaleFront != 1.0)
		{
			Parts.Add(FString::Printf(TEXT("前減衰 %.0f%%"), DampingScaleFront * 100.0));
		}
		if (DampingScaleRear != 1.0)
		{
			Parts.Add(FString::Printf(TEXT("後減衰 %.0f%%"), DampingScaleRear * 100.0));
		}
		if (BrakeBias >= 0.0)
		{
			Parts.Add(FString::Printf(TEXT("ブレーキ前 %.0f%%"), BrakeBias * 100.0));
		}
		return FString::Join(Parts, TEXT(" / "));
	}

	// -----------------------------------------------------------------------

	bool FSetupLimits::Init(FVehicleData& Data, FString& OutError)
	{
		auto Set = [&](ESetupItem Item, const FSetupRange& Range)
		{
			Ranges[static_cast<int32>(Item)] = Range;
		};

		// --- 車高 ---
		//
		// 下げ側は最低地上高で頭打ちにする。**地面に擦る車高を選べない。**
		double ClearanceM = 0.0;
		if (!Data.GetValue(TEXT("dimensions.ground_clearance"), TEXT("m"),
		                   ClearanceM, OutError))
		{
			return false;
		}

		FSetupRange RideHeight;
		RideHeight.Low = -FMath::Min(0.060, ClearanceM - 0.060);
		RideHeight.High = 0.020;
		RideHeight.Default = 0.0;
		RideHeight.Unit = TEXT("m");
		RideHeight.Label = TEXT("車高");
		RideHeight.DisplayScale = 1000.0;
		RideHeight.DisplayUnit = TEXT("mm");
		RideHeight.DisplayDigits = 0;
		RideHeight.Note = FString::Printf(
			TEXT("下げると重心が下がる。下限は最低地上高 %.0fmm から 60mm を"
			     "残した位置。ロールセンタの変化は計算できないので含まない。"),
			ClearanceM * 1000.0);
		Set(ESetupItem::RideHeight, RideHeight);

		// --- アライメント ---
		//
		// **範囲の根拠は vehicle.json に無い。** 実車の調整範囲を測った資料が
		// 無いので、ここで決めて理由を残す。ZN6 の純正値の出典も取れて
		// いないため、既定は 0（＝何も足さない）。
		FSetupRange Camber;
		Camber.Low = FMath::DegreesToRadians(-4.0);
		Camber.High = FMath::DegreesToRadians(1.0);
		Camber.Default = 0.0;
		Camber.Unit = TEXT("rad");
		Camber.DisplayScale = 180.0 / PI;
		Camber.DisplayUnit = TEXT("deg");
		Camber.DisplayDigits = 2;
		Camber.Note = TEXT("負が内側倒し。既定 0 は「純正値」ではなく"
		                   "「何も足さない」。範囲は実車で測ったものではない。");
		Camber.Label = TEXT("キャンバー（前）");
		Set(ESetupItem::CamberFront, Camber);
		Camber.Label = TEXT("キャンバー（後）");
		Set(ESetupItem::CamberRear, Camber);

		FSetupRange Toe = Camber;
		Toe.Low = FMath::DegreesToRadians(-0.5);
		Toe.High = FMath::DegreesToRadians(0.5);
		Toe.Note = TEXT("正がトーイン（前が内向き）。片輪あたりの角度。"
		                "既定 0 は「純正値」ではなく「何も足さない」。");
		Toe.Label = TEXT("トー（前）");
		Set(ESetupItem::ToeFront, Toe);
		Toe.Label = TEXT("トー（後）");
		Set(ESetupItem::ToeRear, Toe);

		// --- ばねと減衰 ---
		//
		// **vehicle.json の min/max を超えない。**
		auto ScaleRange = [&](const TCHAR* Path, const TCHAR* Unit,
		                      const TCHAR* Label, FSetupRange& Out) -> bool
		{
			double Base = 0.0;
			if (!Data.GetValue(Path, Unit, Base, OutError))
			{
				return false;
			}
			const FParam* Param = Data.ReadParamInfo(Path, Unit, OutError);
			if (Param == nullptr)
			{
				return false;
			}

			Out = FSetupRange();
			Out.Label = Label;
			Out.Unit = TEXT("-");
			Out.DisplayScale = 100.0;
			Out.DisplayUnit = TEXT("%");
			Out.DisplayDigits = 0;
			Out.Default = 1.0;

			const bool bHasRange = Param != nullptr && Param->bHasMin && Param->bHasMax
			                    && Param->Maximum > Param->Minimum;
			if (!bHasRange || Base <= 0.0)
			{
				// **範囲が書かれていないなら動かさない。勝手に広げない。**
				Out.Low = Out.High = 1.0;
				Out.Note = FString::Printf(
					TEXT("%s に min/max が無いので調整できない。"), Path);
				return true;
			}

			Out.Low = Param->Minimum / Base;
			Out.High = Param->Maximum / Base;
			Out.Note = FString::Printf(TEXT("%s の min/max を倍率にしたもの。"), Path);
			return true;
		};

		FSetupRange Range;
		if (!ScaleRange(TEXT("suspension.spring_rate_front"), TEXT("N/m"),
		                TEXT("ばねレート（前）"), Range)) { return false; }
		Set(ESetupItem::SpringFront, Range);
		if (!ScaleRange(TEXT("suspension.spring_rate_rear"), TEXT("N/m"),
		                TEXT("ばねレート（後）"), Range)) { return false; }
		Set(ESetupItem::SpringRear, Range);
		if (!ScaleRange(TEXT("suspension.damping_ratio_front"), TEXT("-"),
		                TEXT("減衰比（前）"), Range)) { return false; }
		Set(ESetupItem::DampingFront, Range);
		if (!ScaleRange(TEXT("suspension.damping_ratio_rear"), TEXT("-"),
		                TEXT("減衰比（後）"), Range)) { return false; }
		Set(ESetupItem::DampingRear, Range);

		// --- ブレーキバイアス ---
		double BiasBase = 0.0;
		if (!Data.GetValue(TEXT("brakes.brake_bias"), TEXT("-"), BiasBase, OutError))
		{
			return false;
		}
		FSetupRange Bias;
		Bias.Low = 0.50;
		Bias.High = 0.90;
		Bias.Default = BiasBase;
		Bias.Unit = TEXT("-");
		Bias.Label = TEXT("ブレーキバイアス（前）");
		Bias.DisplayScale = 100.0;
		Bias.DisplayUnit = TEXT("%");
		Bias.DisplayDigits = 0;
		Bias.Note = TEXT("brakes.brake_bias は assumed で min/max が無い。"
		                 "前 50〜90% は物理的にあり得る範囲としてここで決めた。");
		Set(ESetupItem::BrakeBias, Bias);

		return true;
	}

	void FSetupLimits::Validate(const FCarSetup& Setup, TArray<FString>& OutProblems) const
	{
		OutProblems.Reset();
		for (int32 Index = 0; Index < static_cast<int32>(ESetupItem::Count); ++Index)
		{
			const ESetupItem Item = static_cast<ESetupItem>(Index);
			const double Value = Setup.Get(Item);
			// ブレーキバイアスの負は「既定を使う」の意味。範囲外ではない。
			if (Item == ESetupItem::BrakeBias && Value < 0.0)
			{
				continue;
			}
			if (!Ranges[Index].Contains(Value))
			{
				OutProblems.Add(FString::Printf(
					TEXT("%s が範囲外: %.4g は %.4g〜%.4g に入らない"),
					*Ranges[Index].Label, Value, Ranges[Index].Low, Ranges[Index].High));
			}
		}
	}

	FCarSetup FSetupLimits::Clamped(const FCarSetup& Setup) const
	{
		FCarSetup Out = Setup;
		for (int32 Index = 0; Index < static_cast<int32>(ESetupItem::Count); ++Index)
		{
			const ESetupItem Item = static_cast<ESetupItem>(Index);
			const double Value = Setup.Get(Item);
			if (Item == ESetupItem::BrakeBias && Value < 0.0)
			{
				continue;
			}
			Out.Set(Item, Ranges[Index].Clamp(Value));
		}
		return Out;
	}

	const TArray<TPair<FString, FString>>& UnsupportedItems()
	{
		static const TArray<TPair<FString, FString>> Items = {
			{ TEXT("タイヤ空気圧"),
			  TEXT("タイヤモデルに空気圧の入力が無い。圧力による剛性・μの変化を"
			       "測った資料も無く、入れれば数値の捏造になる。") },
			{ TEXT("ダウンフォース"),
			  TEXT("aerodynamics.lift_coefficient_front / _rear が unknown。"
			       "計算できないので、ウイング角を置いても効かない。") },
			{ TEXT("キャスター"),
			  TEXT("suspension.geometry が unknown。キャンバー変化や"
			       "セルフアライニングトルクを出せない。") },
			{ TEXT("ロールセンタ"),
			  TEXT("同じく geometry が unknown。車高を変えるとロールセンタも"
			       "動くが、その量が計算できない。車高の効果は重心高の変化だけ。") },
			{ TEXT("スタビライザー"),
			  TEXT("径（前 18mm / 後 14mm）は分かっているが、アーム長と"
			       "レバー比が unknown なのでロール剛性に直せない。") },
			{ TEXT("デフ"),
			  TEXT("preload / accel_lock_ratio / decel_lock_ratio がすべて"
			       "assumed。実車のトルセンの特性ではないので出さない。") },
			{ TEXT("ギア比"),
			  TEXT("公式ギア比は Level 0（変更禁止）。ZN6 の諸元そのもので、"
			       "セッティングで動かすものではない。") },
		};
		return Items;
	}
}
