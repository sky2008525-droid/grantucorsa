#include "ZN6AudioModel.h"

#include "Dom/JsonObject.h"
#include "Misc/FileHelper.h"
#include "Serialization/JsonReader.h"
#include "Serialization/JsonSerializer.h"

#include <cmath>

namespace ZN6
{
	namespace
	{
		/** ドット区切りのパスを辿って測定ノードを取り出す。 */
		bool FindNode(const TSharedPtr<FJsonObject>& Root, const FString& DottedPath,
		              TSharedPtr<FJsonObject>& OutNode, FString& OutError)
		{
			TArray<FString> Parts;
			DottedPath.ParseIntoArray(Parts, TEXT("."));

			TSharedPtr<FJsonObject> Node = Root;
			for (const FString& Part : Parts)
			{
				const TSharedPtr<FJsonObject>* Child = nullptr;
				if (!Node->TryGetObjectField(Part, Child))
				{
					OutError = FString::Printf(TEXT("audio.json に %s が無い"), *DottedPath);
					return false;
				}
				Node = *Child;
			}

			if (!Node->HasField(TEXT("value")))
			{
				OutError = FString::Printf(TEXT("%s は測定ノードでない"), *DottedPath);
				return false;
			}
			OutNode = Node;
			return true;
		}

		/** **単位を必ず確かめる。** 取り違えはもっともらしい間違いとして現れる。 */
		bool CheckUnit(const TSharedPtr<FJsonObject>& Node, const FString& DottedPath,
		               const FString& Unit, FString& OutError)
		{
			FString Stored;
			if (!Node->TryGetStringField(TEXT("unit"), Stored) || Stored != Unit)
			{
				OutError = FString::Printf(TEXT("%s の単位が %s でなく %s"),
				                           *DottedPath, *Unit, *Stored);
				return false;
			}
			return true;
		}

		bool ReadNumber(const TSharedPtr<FJsonObject>& Root, const FString& DottedPath,
		                const FString& Unit, double& OutValue, FString& OutError)
		{
			TSharedPtr<FJsonObject> Node;
			if (!FindNode(Root, DottedPath, Node, OutError)) { return false; }
			if (!CheckUnit(Node, DottedPath, Unit, OutError)) { return false; }

			// "unknown" を既定値で代用しない（憲法ルール14）。
			//
			// **TryGetStringField で判定しないこと。** UE の JSON は数値を
			// 文字列へ暗黙に変換するので、2.0 が "2" として取れてしまい、
			// 正しい値まで「数値でない」と弾かれる（実際にそうなった）。
			// 型そのものを見る。
			const TSharedPtr<FJsonValue> Value = Node->TryGetField(TEXT("value"));
			if (!Value.IsValid() || Value->Type != EJson::Number)
			{
				OutError = FString::Printf(TEXT("%s が数値でない: %s"), *DottedPath,
				                           Value.IsValid() ? *Value->AsString()
				                                           : TEXT("(無し)"));
				return false;
			}
			OutValue = Value->AsNumber();
			return true;
		}
	}

	bool FAudioModel::Init(const FString& AudioJsonPath, FVehicleData& VehicleData,
	                       FString& OutError)
	{
		bLoaded = false;

		FString Text;
		if (!FFileHelper::LoadFileToString(Text, *AudioJsonPath))
		{
			OutError = FString::Printf(TEXT("audio.json を読めない: %s"), *AudioJsonPath);
			return false;
		}

		TSharedPtr<FJsonObject> Root;
		const TSharedRef<TJsonReader<>> Reader = TJsonReaderFactory<>::Create(Text);
		if (!FJsonSerializer::Deserialize(Reader, Root) || !Root.IsValid())
		{
			OutError = TEXT("audio.json を JSON として解釈できない");
			return false;
		}

		// **回転数の範囲は vehicle.json から。** 音側で決め打ちすると、
		// エンジンの範囲とずれても誰も気づかない。
		if (!VehicleData.GetValue(TEXT("engine.idle_rpm"), TEXT("1/min"),
		                          IdleRpmValue, OutError)) { return false; }
		if (!VehicleData.GetValue(TEXT("engine.redline"), TEXT("1/min"),
		                          RedlineRpmValue, OutError)) { return false; }
		if (RedlineRpmValue <= IdleRpmValue)
		{
			OutError = FString::Printf(TEXT("レッドラインがアイドル以下: %f <= %f"),
			                           RedlineRpmValue, IdleRpmValue);
			return false;
		}

		struct FEntry { const TCHAR* Path; const TCHAR* Unit; double* Target; };
		const FEntry Entries[] = {
			{ TEXT("engine.firing_order_per_rev"), TEXT("-"),  &FiringOrderPerRev },
			{ TEXT("engine.idle_gain"),            TEXT("-"),  &IdleGain },
			{ TEXT("engine.redline_gain"),         TEXT("-"),  &RedlineGain },
			{ TEXT("engine.gain_curve_exponent"),  TEXT("-"),  &GainCurveExponent },
			{ TEXT("engine.overrun_gain"),         TEXT("-"),  &OverrunGain },
			{ TEXT("engine.load_brightness"),      TEXT("-"),  &LoadBrightnessValue },
			{ TEXT("engine.limiter_flutter_hz"),   TEXT("Hz"), &LimiterFlutterHz },
			{ TEXT("tire.skid_slip_threshold"),    TEXT("-"),  &SkidThresholdValue },
			{ TEXT("tire.skid_full_slip"),         TEXT("-"),  &SkidFull },
			{ TEXT("tire.skid_gain"),              TEXT("-"),  &SkidGainValue },
			{ TEXT("tire.skid_base_hz"),           TEXT("Hz"), &SkidBaseHz },
			{ TEXT("tire.skid_speed_ref_mps"),     TEXT("m/s"),&SkidSpeedRefMps },
			{ TEXT("road.crossfade_m"),            TEXT("m"),  &CrossfadeM },
			{ TEXT("road.rolling_gain"),           TEXT("-"),  &RollingGainValue },
			{ TEXT("road.rolling_ref_mps"),        TEXT("m/s"),&RollingRefMps },
			{ TEXT("mix.master_gain"),             TEXT("-"),  &MasterGainValue },
		};

		for (const FEntry& Entry : Entries)
		{
			if (!ReadNumber(Root, Entry.Path, Entry.Unit, *Entry.Target, OutError))
			{
				return false;
			}
		}

		double LoopStepsValue = 0.0;
		double SampleRateValue = 0.0;
		if (!ReadNumber(Root, TEXT("engine.loop_steps"), TEXT("-"),
		                LoopStepsValue, OutError)) { return false; }
		if (!ReadNumber(Root, TEXT("mix.sample_rate_hz"), TEXT("Hz"),
		                SampleRateValue, OutError)) { return false; }
		EngineLoopSteps = static_cast<int32>(LoopStepsValue);
		SampleRate = static_cast<int32>(SampleRateValue);

		if (EngineLoopSteps < 2)
		{
			OutError = FString::Printf(TEXT("engine.loop_steps が少なすぎる: %d"),
			                           EngineLoopSteps);
			return false;
		}
		if (SkidFull <= SkidThresholdValue)
		{
			OutError = TEXT("スキールの飽和点が閾値以下");
			return false;
		}

		// 倍音
		TSharedPtr<FJsonObject> HarmonicNode;
		if (!FindNode(Root, TEXT("engine.harmonics"), HarmonicNode, OutError)) { return false; }
		if (!CheckUnit(HarmonicNode, TEXT("engine.harmonics"), TEXT("-"), OutError))
		{
			return false;
		}

		const TArray<TSharedPtr<FJsonValue>>* Pairs = nullptr;
		if (!HarmonicNode->TryGetArrayField(TEXT("value"), Pairs) || Pairs->Num() == 0)
		{
			OutError = TEXT("engine.harmonics が [[次数, 振幅], ...] でない");
			return false;
		}

		HarmonicOrders.Reset();
		HarmonicWeights.Reset();
		for (const TSharedPtr<FJsonValue>& Pair : *Pairs)
		{
			const TArray<TSharedPtr<FJsonValue>>* Values = nullptr;
			if (!Pair->TryGetArray(Values) || Values->Num() != 2)
			{
				OutError = TEXT("engine.harmonics の要素が2個組でない");
				return false;
			}
			HarmonicOrders.Add((*Values)[0]->AsNumber());
			HarmonicWeights.Add((*Values)[1]->AsNumber());
		}

		bLoaded = true;
		return true;
	}

	FEngineVoice FAudioModel::EngineVoice(double EngineRpm, double Throttle,
	                                      double TimeS) const
	{
		FEngineVoice Voice;

		// **NaN を黙って通さない**（憲法ルール6）。
		// 例外が使えないので、鳴らないことで気づけるようにする。
		if (!FMath::IsFinite(EngineRpm))
		{
			UE_LOG(LogTemp, Error, TEXT("ZN6 audio: 回転数が有限でない"));
			Voice.LimiterGate = 0.0;
			return Voice;
		}

		const double Rpm = FMath::Max(EngineRpm, 0.0);
		Voice.FundamentalHz = Rpm * FiringOrderPerRev / 60.0;

		double Span = (Rpm - IdleRpmValue) / (RedlineRpmValue - IdleRpmValue);
		Span = FMath::Clamp(Span, 0.0, 1.0);
		const double Curve = std::pow(Span, GainCurveExponent);
		double Gain = IdleGain + (RedlineGain - IdleGain) * Curve;

		const double Load = FMath::Clamp(Throttle, 0.0, 1.0);
		Gain *= OverrunGain + (1.0 - OverrunGain) * Load;

		Voice.Gain = Gain;
		Voice.Brightness = LoadBrightnessValue * Load;

		// **回転を制限するのは物理側の仕事。** ここは音を切るだけ。
		Voice.LimiterGate = 1.0;
		if (Rpm >= RedlineRpmValue)
		{
			const double Phase = std::sin(2.0 * PI * LimiterFlutterHz * TimeS);
			Voice.LimiterGate = (Phase >= 0.0) ? 1.0 : 0.0;
		}
		return Voice;
	}

	void FAudioModel::HarmonicAmplitudes(double Brightness, TArray<double>& OutOrders,
	                                     TArray<double>& OutAmplitudes) const
	{
		OutOrders = HarmonicOrders;
		OutAmplitudes.SetNum(HarmonicWeights.Num());

		double Total = 0.0;
		for (int32 Index = 0; Index < HarmonicWeights.Num(); ++Index)
		{
			// 次数が高いほど brightness の効きを強くする
			const double Lift = 1.0 + Brightness * (HarmonicOrders[Index] - 1.0)
			                  / FMath::Max(HarmonicWeights.Num(), 1);
			OutAmplitudes[Index] = HarmonicWeights[Index] * Lift;
			Total += OutAmplitudes[Index];
		}

		if (Total <= 0.0)
		{
			UE_LOG(LogTemp, Error, TEXT("ZN6 audio: 倍音の振幅が全てゼロ"));
			return;
		}
		for (double& Amplitude : OutAmplitudes)
		{
			Amplitude /= Total;
		}
	}

	FTireVoice FAudioModel::TireVoice(double Utilisation, double SpeedMps) const
	{
		FTireVoice Voice;
		Voice.Hz = SkidBaseHz;

		const double Used = FMath::Clamp(Utilisation, 0.0, 1.5);
		if (Used <= SkidThresholdValue)
		{
			// **閾値以下では無音。** 常時鳴らすと限界が近いことが分からない。
			Voice.Gain = 0.0;
			return Voice;
		}

		const double Span = (Used - SkidThresholdValue) / (SkidFull - SkidThresholdValue);
		Voice.Gain = SkidGainValue * FMath::Min(Span, 1.0);

		const double SpeedFactor = std::sqrt(
			FMath::Max(SpeedMps, 0.0) / FMath::Max(SkidSpeedRefMps, 1e-6));
		Voice.Hz = SkidBaseHz * FMath::Max(SpeedFactor, 0.25);
		return Voice;
	}

	FRoadVoice FAudioModel::RoadVoice(double SpeedMps, double DistanceToEdgeM) const
	{
		FRoadVoice Voice;

		const double Half = CrossfadeM / 2.0;
		if (Half <= 0.0)
		{
			Voice.InsideRatio = (DistanceToEdgeM >= 0.0) ? 1.0 : 0.0;
		}
		else
		{
			Voice.InsideRatio = FMath::Clamp(
				(DistanceToEdgeM + Half) / (2.0 * Half), 0.0, 1.0);
		}
		Voice.OutsideRatio = 1.0 - Voice.InsideRatio;

		Voice.Gain = RollingGainValue * FMath::Min(
			FMath::Max(SpeedMps, 0.0) / FMath::Max(RollingRefMps, 1e-6), 1.0);
		return Voice;
	}

	void FAudioModel::EngineLoopRpms(TArray<double>& OutRpms) const
	{
		// **等比に並べる。** 等間隔にすると低回転側で隣り合う段の比が
		// 2.37 倍になり、SetPitchMultiplier の範囲（既定 0.4〜2.0）を超える。
		OutRpms.Reset(EngineLoopSteps);
		const double Ratio = std::pow(RedlineRpmValue / IdleRpmValue,
		                              1.0 / (EngineLoopSteps - 1));
		for (int32 Index = 0; Index < EngineLoopSteps; ++Index)
		{
			OutRpms.Add(IdleRpmValue * std::pow(Ratio, static_cast<double>(Index)));
		}
	}

	void FAudioModel::EngineLoopBlend(double EngineRpm,
	                                  TArray<FEngineLoopVoice>& OutVoices) const
	{
		OutVoices.Reset();

		TArray<double> Rpms;
		EngineLoopRpms(Rpms);
		const double Rpm = FMath::Max(EngineRpm, 1.0);

		if (Rpm <= Rpms[0])
		{
			OutVoices.Add({ 0, 1.0, Rpm / Rpms[0] });
			return;
		}
		if (Rpm >= Rpms.Last())
		{
			const int32 Last = Rpms.Num() - 1;
			OutVoices.Add({ Last, 1.0, Rpm / Rpms[Last] });
			return;
		}

		int32 Upper = 0;
		while (Upper < Rpms.Num() && Rpms[Upper] < Rpm)
		{
			++Upper;
		}
		const int32 Lower = Upper - 1;

		// **対数で混ぜる。** 線形だと段の中央でピッチが偏る。
		const double SpanLog = std::log(Rpms[Upper] / Rpms[Lower]);
		const double Ratio = std::log(Rpm / Rpms[Lower]) / SpanLog;

		OutVoices.Add({ Lower, 1.0 - Ratio, Rpm / Rpms[Lower] });
		OutVoices.Add({ Upper, Ratio, Rpm / Rpms[Upper] });
	}
}
