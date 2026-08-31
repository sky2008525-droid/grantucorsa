// 音響パラメータの計算（Audio/audio_model.py の移植）。
//
// **ここは演出であって物理ではない**（憲法ルール18）。
// 物理の出力を読んで再生パラメータを返すだけで、物理へは何も返さない。
// だから `FVehicle` への参照を持たない。
//
// **Python が唯一の基準。** 数値が食い違ったら C++ 側が間違っている。
//
// この音は FA20 の音ではない。実車を録音していない（Audio/audio.json）。

#pragma once

#include "CoreMinimal.h"
#include "Physics/ZN6VehicleData.h"

namespace ZN6
{
	/** エンジン音1フレーム分。 */
	struct FEngineVoice
	{
		double FundamentalHz = 0.0;
		double Gain = 0.0;
		double Brightness = 0.0;
		/** レブリミッタによる断続。1 = 鳴っている / 0 = 切れている。 */
		double LimiterGate = 1.0;
	};

	/** タイヤのスキール音1フレーム分。 */
	struct FTireVoice
	{
		double Hz = 0.0;
		double Gain = 0.0;
	};

	/** ロードノイズ1フレーム分。**内側と外側の比の合計は必ず 1。** */
	struct FRoadVoice
	{
		double Gain = 0.0;
		double InsideRatio = 1.0;
		double OutsideRatio = 0.0;
	};

	/** 再生するエンジンループ1つぶんの指示。 */
	struct FEngineLoopVoice
	{
		int32 LoopIndex = 0;
		double Gain = 0.0;
		/** 今の回転数 / その段の回転数。 */
		double PitchMultiplier = 1.0;
	};

	/**
	 * `Audio/audio.json` の読み取り。**数値をコードに書かないための入口。**
	 *
	 * 例外を使わない（UE は例外を無効にしてビルドされる）。
	 * 失敗は戻り値で返す。
	 */
	class FAudioModel
	{
	public:
		/**
		 * @param AudioJsonPath  Audio/audio.json への絶対パス
		 * @param VehicleData    回転数の範囲を読む先。**音側で決め打ちしない**
		 */
		bool Init(const FString& AudioJsonPath, FVehicleData& VehicleData, FString& OutError);

		bool IsValid() const { return bLoaded; }

		FEngineVoice EngineVoice(double EngineRpm, double Throttle, double TimeS) const;
		FTireVoice TireVoice(double Utilisation, double SpeedMps) const;

		/**
		 * @param DistanceToEdgeM  路面の内側を正とする符号つき距離 [m]
		 */
		FRoadVoice RoadVoice(double SpeedMps, double DistanceToEdgeM) const;

		/**
		 * 再生するループの選択。隣り合う2段を混ぜる。
		 * **音量比の合計は必ず 1。**
		 */
		void EngineLoopBlend(double EngineRpm, TArray<FEngineLoopVoice>& OutVoices) const;

		/** ループを作る回転数。**等比に並べる**（ピッチ倍率は比で効くため）。 */
		void EngineLoopRpms(TArray<double>& OutRpms) const;

		/** 倍音の [次数, 振幅]。振幅の合計は 1 に正規化される。 */
		void HarmonicAmplitudes(double Brightness, TArray<double>& OutOrders,
		                        TArray<double>& OutAmplitudes) const;

		double IdleRpm() const { return IdleRpmValue; }
		double RedlineRpm() const { return RedlineRpmValue; }
		int32 LoopSteps() const { return EngineLoopSteps; }
		double MasterGain() const { return MasterGainValue; }
		int32 SampleRateHz() const { return SampleRate; }
		double SkidThreshold() const { return SkidThresholdValue; }
		double SkidGain() const { return SkidGainValue; }
		double RollingGain() const { return RollingGainValue; }
		double LoadBrightness() const { return LoadBrightnessValue; }
		/** スキールループを合成したときの基準周波数 [Hz]。ピッチ比の分母。 */
		double GetSkidBaseHzForPitch() const { return SkidBaseHz; }

	private:
		bool bLoaded = false;

		double IdleRpmValue = 0.0;
		double RedlineRpmValue = 0.0;

		double FiringOrderPerRev = 0.0;
		TArray<double> HarmonicOrders;
		TArray<double> HarmonicWeights;
		double IdleGain = 0.0;
		double RedlineGain = 0.0;
		double GainCurveExponent = 1.0;
		double OverrunGain = 0.0;
		double LoadBrightnessValue = 0.0;
		double LimiterFlutterHz = 0.0;
		int32 EngineLoopSteps = 0;

		double SkidThresholdValue = 0.0;
		double SkidFull = 0.0;
		double SkidGainValue = 0.0;
		double SkidBaseHz = 0.0;
		double SkidSpeedRefMps = 0.0;

		double CrossfadeM = 0.0;
		double RollingGainValue = 0.0;
		double RollingRefMps = 0.0;

		double MasterGainValue = 0.0;
		int32 SampleRate = 0;
	};
}
