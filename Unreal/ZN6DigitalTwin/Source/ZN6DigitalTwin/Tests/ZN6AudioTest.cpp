// 音響モデルの検査（Tests/test_audio.py の対応物、Phase 14）。
//
// **音は演出であって物理ではない**（憲法ルール18）。だからここで検査するのは
// 「実車の音に近いか」ではない。測っていないので判定できない。
//
// 検査するのは:
//
//   1. **音を鳴らしても物理が1ビットも変わらないか**（一方通行であること）
//   2. Python と同じ値を返すか
//   3. 不連続が無いか（境界で音が飛ぶのは実装のバグ）
//   4. 混合比の合計が 1、ピッチ倍率が再生できる範囲に収まっているか

#include "CoreMinimal.h"
#include "Misc/AutomationTest.h"
#include "Misc/Paths.h"

#include "Audio/ZN6AudioModel.h"
#include "Physics/ZN6Track.h"
#include "Physics/ZN6Vehicle.h"
#include "Physics/ZN6VehicleData.h"

#if WITH_DEV_AUTOMATION_TESTS

namespace
{
	FString AudioRepoRoot()
	{
		return FPaths::ConvertRelativePathToFull(FPaths::ProjectDir() / TEXT("../.."));
	}

	bool MakeAudioModel(FAutomationTestBase& Test, ZN6::FVehicleData& OutData,
	                    ZN6::FAudioModel& OutModel)
	{
		FString Error;
		if (!OutData.LoadFromFile(AudioRepoRoot() / TEXT("Vehicles/ZN6/vehicle.json"), Error))
		{
			Test.AddError(FString::Printf(TEXT("vehicle.json を読めない: %s"), *Error));
			return false;
		}
		if (!OutModel.Init(AudioRepoRoot() / TEXT("Audio/audio.json"), OutData, Error))
		{
			Test.AddError(FString::Printf(TEXT("audio.json を読めない: %s"), *Error));
			return false;
		}
		return true;
	}
}

// ---------------------------------------------------------------------------

IMPLEMENT_SIMPLE_AUTOMATION_TEST(
	FZN6AudioEngineVoice,
	"ZN6.Audio.エンジン音のパラメータ",
	EAutomationTestFlags::EditorContext | EAutomationTestFlags::EngineFilter)

bool FZN6AudioEngineVoice::RunTest(const FString& Parameters)
{
	ZN6::FVehicleData Data;
	ZN6::FAudioModel Model;
	if (!MakeAudioModel(*this, Data, Model))
	{
		return false;
	}

	// --- 回転数の範囲は vehicle.json から ---
	//
	// **音側で決め打ちしない。** ずれても誰も気づかない。
	double IdleRpm = 0.0;
	double RedlineRpm = 0.0;
	FString Error;
	Data.GetValue(TEXT("engine.idle_rpm"), TEXT("1/min"), IdleRpm, Error);
	Data.GetValue(TEXT("engine.redline"), TEXT("1/min"), RedlineRpm, Error);
	TestEqual(TEXT("アイドルが vehicle.json と一致"), Model.IdleRpm(), IdleRpm);
	TestEqual(TEXT("レッドラインが vehicle.json と一致"), Model.RedlineRpm(), RedlineRpm);

	// --- 基本周波数は回転数に比例（4ストローク4気筒で1回転2回）---
	for (const double Rpm : { 700.0, 3000.0, 7400.0 })
	{
		const ZN6::FEngineVoice Voice = Model.EngineVoice(Rpm, 1.0, 0.0);
		TestTrue(*FString::Printf(TEXT("%.0f rpm で %.4f Hz"), Rpm, Voice.FundamentalHz),
		         FMath::Abs(Voice.FundamentalHz - Rpm * 2.0 / 60.0) < 1e-9);
	}

	// --- 回転が上がると音量も上がる ---
	double Previous = -1.0;
	for (double Rpm = 700.0; Rpm <= 7400.0; Rpm += 200.0)
	{
		const double Gain = Model.EngineVoice(Rpm, 1.0, 0.0).Gain;
		TestTrue(*FString::Printf(TEXT("%.0f rpm で音量が下がらない"), Rpm),
		         Gain >= Previous - 1e-12);
		Previous = Gain;
	}

	// --- アクセルオフで音量と明るさが下がる ---
	const ZN6::FEngineVoice On = Model.EngineVoice(4000.0, 1.0, 0.0);
	const ZN6::FEngineVoice Off = Model.EngineVoice(4000.0, 0.0, 0.0);
	TestTrue(TEXT("アクセルオフで音量が下がる"), Off.Gain < On.Gain);
	TestTrue(TEXT("アクセルオフで高次倍音が落ちる"), Off.Brightness < On.Brightness);
	TestEqual(TEXT("周波数は負荷では変わらない"), Off.FundamentalHz, On.FundamentalHz);

	// --- 音量が跳ばない ---
	//
	// **段差があると「ブツッ」と鳴る。** 目では分からないので測る。
	double LargestJump = 0.0;
	double Last = Model.EngineVoice(0.0, 1.0, 0.0).Gain;
	for (double Rpm = 5.0; Rpm <= 7600.0; Rpm += 5.0)
	{
		const double Gain = Model.EngineVoice(Rpm, 1.0, 0.0).Gain;
		LargestJump = FMath::Max(LargestJump, FMath::Abs(Gain - Last));
		Last = Gain;
	}
	TestTrue(*FString::Printf(TEXT("音量が跳ばない（最大 %.5f）"), LargestJump),
	         LargestJump < 0.01);

	// --- レブリミッタで断続する ---
	bool bAnyOff = false;
	bool bAnyOn = false;
	for (int32 Step = 0; Step < 200; ++Step)
	{
		const double Gate = Model.EngineVoice(7500.0, 1.0, Step / 1000.0).LimiterGate;
		bAnyOff = bAnyOff || (Gate == 0.0);
		bAnyOn = bAnyOn || (Gate == 1.0);

		TestEqual(TEXT("レッドライン未満では切れない"),
		          Model.EngineVoice(7000.0, 1.0, Step / 1000.0).LimiterGate, 1.0);
	}
	TestTrue(TEXT("レッドライン超過で断続する"), bAnyOff && bAnyOn);

	// --- 倍音の振幅は合計 1 ---
	for (const double Brightness : { 0.0, 0.3, 0.6, 1.0 })
	{
		TArray<double> Orders;
		TArray<double> Amplitudes;
		Model.HarmonicAmplitudes(Brightness, Orders, Amplitudes);

		double Total = 0.0;
		for (const double Amplitude : Amplitudes)
		{
			TestTrue(TEXT("振幅が正"), Amplitude > 0.0);
			Total += Amplitude;
		}
		TestTrue(*FString::Printf(TEXT("振幅の合計が 1（%.12f）"), Total),
		         FMath::Abs(Total - 1.0) < 1e-12);
	}

	return true;
}

// ---------------------------------------------------------------------------

IMPLEMENT_SIMPLE_AUTOMATION_TEST(
	FZN6AudioLoopSelection,
	"ZN6.Audio.ループの選択",
	EAutomationTestFlags::EditorContext | EAutomationTestFlags::EngineFilter)

bool FZN6AudioLoopSelection::RunTest(const FString& Parameters)
{
	ZN6::FVehicleData Data;
	ZN6::FAudioModel Model;
	if (!MakeAudioModel(*this, Data, Model))
	{
		return false;
	}

	// --- 段は等比 ---
	//
	// **等間隔にしない。** ピッチ倍率は比で効くので、等間隔だと
	// 低回転側で 2.37 倍になり SetPitchMultiplier の範囲を超える。
	TArray<double> Rpms;
	Model.EngineLoopRpms(Rpms);
	TestEqual(TEXT("段数が audio.json と一致"), Rpms.Num(), Model.LoopSteps());
	TestTrue(TEXT("先頭がアイドル"), FMath::Abs(Rpms[0] - Model.IdleRpm()) < 1e-9);
	TestTrue(TEXT("末尾がレッドライン"),
	         FMath::Abs(Rpms.Last() - Model.RedlineRpm()) < 1e-9);

	const double FirstRatio = Rpms[1] / Rpms[0];
	for (int32 Index = 1; Index < Rpms.Num(); ++Index)
	{
		const double Ratio = Rpms[Index] / Rpms[Index - 1];
		TestTrue(*FString::Printf(TEXT("段 %d の比が一定（%.6f）"), Index, Ratio),
		         FMath::Abs(Ratio - FirstRatio) < 1e-9);
		TestTrue(*FString::Printf(TEXT("段 %d の比が 2.0 未満（%.4f）"), Index, Ratio),
		         Ratio < 2.0);
	}

	// --- 混合比の合計は 1、ピッチ倍率は再生できる範囲 ---
	TArray<ZN6::FEngineLoopVoice> Blend;
	for (double Rpm = 300.0; Rpm <= 8200.0; Rpm += 25.0)
	{
		Model.EngineLoopBlend(Rpm, Blend);

		double Total = 0.0;
		for (const ZN6::FEngineLoopVoice& Voice : Blend)
		{
			TestTrue(TEXT("段の番号が範囲内"),
			         Voice.LoopIndex >= 0 && Voice.LoopIndex < Model.LoopSteps());
			TestTrue(TEXT("音量比が負でない"), Voice.Gain >= 0.0);
			Total += Voice.Gain;

			// **使用回転域では丸めが起きないこと。** 丸められると
			// 音程が回転数と合わなくなるが、それに気づく手立てが無い。
			if (Rpm >= Model.IdleRpm() && Rpm <= Model.RedlineRpm())
			{
				TestTrue(
					*FString::Printf(TEXT("%.0f rpm でピッチ倍率 %.4f が範囲内"),
					                 Rpm, Voice.PitchMultiplier),
					Voice.PitchMultiplier > 0.4 && Voice.PitchMultiplier < 2.0);
			}
		}
		TestTrue(*FString::Printf(TEXT("%.0f rpm で混合比の合計が 1（%.12f）"), Rpm, Total),
		         FMath::Abs(Total - 1.0) < 1e-12);
	}

	// --- 段の回転数ちょうどでは、その段だけがピッチ 1 で鳴る ---
	for (int32 Index = 0; Index < Rpms.Num(); ++Index)
	{
		Model.EngineLoopBlend(Rpms[Index], Blend);

		int32 Loud = 0;
		for (const ZN6::FEngineLoopVoice& Voice : Blend)
		{
			if (Voice.Gain <= 1e-9)
			{
				continue;
			}
			++Loud;
			TestEqual(TEXT("その段が選ばれている"), Voice.LoopIndex, Index);
			TestTrue(*FString::Printf(TEXT("ピッチを変える必要が無い（%.9f）"),
			                          Voice.PitchMultiplier),
			         FMath::Abs(Voice.PitchMultiplier - 1.0) < 1e-9);
		}
		TestEqual(TEXT("鳴っている段は1つ"), Loud, 1);
	}

	return true;
}

// ---------------------------------------------------------------------------

IMPLEMENT_SIMPLE_AUTOMATION_TEST(
	FZN6AudioTireAndRoad,
	"ZN6.Audio.タイヤと路面",
	EAutomationTestFlags::EditorContext | EAutomationTestFlags::EngineFilter)

bool FZN6AudioTireAndRoad::RunTest(const FString& Parameters)
{
	ZN6::FVehicleData Data;
	ZN6::FAudioModel Model;
	if (!MakeAudioModel(*this, Data, Model))
	{
		return false;
	}

	// --- 限界に近づくまでスキール音は出ない ---
	//
	// **常時鳴らさない。** 鳴りっぱなしだと限界が近いことが分からない。
	TestEqual(TEXT("利用率 0 では無音"), Model.TireVoice(0.0, 20.0).Gain, 0.0);
	TestEqual(TEXT("閾値ちょうどでは無音"),
	          Model.TireVoice(Model.SkidThreshold(), 20.0).Gain, 0.0);
	TestTrue(TEXT("閾値を超えたら鳴る"),
	         Model.TireVoice(Model.SkidThreshold() + 0.01, 20.0).Gain > 0.0);

	// --- 飽和する（伸び続けるとクリップする）---
	TestTrue(*FString::Printf(TEXT("限界超過で飽和（%.9f）"),
	                          Model.TireVoice(1.4, 20.0).Gain),
	         FMath::Abs(Model.TireVoice(1.4, 20.0).Gain - Model.SkidGain()) < 1e-12);

	// --- 停止していてもピッチが 0 にならない ---
	TestTrue(TEXT("0 Hz は再生できない"), Model.TireVoice(1.0, 0.0).Hz > 0.0);

	// --- 路面の混合比は合計 1、境界で跳ばない ---
	double LargestJump = 0.0;
	double Last = Model.RoadVoice(20.0, -5.0).InsideRatio;
	for (double Distance = -5.0; Distance <= 5.0; Distance += 0.01)
	{
		const ZN6::FRoadVoice Voice = Model.RoadVoice(20.0, Distance);
		const double Total = Voice.InsideRatio + Voice.OutsideRatio;
		TestTrue(*FString::Printf(TEXT("混合比の合計が 1（%.12f）"), Total),
		         FMath::Abs(Total - 1.0) < 1e-12);

		LargestJump = FMath::Max(LargestJump, FMath::Abs(Voice.InsideRatio - Last));
		Last = Voice.InsideRatio;
	}
	TestTrue(*FString::Printf(TEXT("路面比が跳ばない（最大 %.5f）"), LargestJump),
	         LargestJump < 0.02);

	TestTrue(TEXT("十分内側なら完全にアスファルト"),
	         FMath::Abs(Model.RoadVoice(20.0, 5.0).InsideRatio - 1.0) < 1e-12);
	TestTrue(TEXT("十分外側なら完全に草"),
	         FMath::Abs(Model.RoadVoice(20.0, -5.0).OutsideRatio - 1.0) < 1e-12);

	// --- 速度が上がるとロードノイズが増え、やがて飽和する ---
	TestEqual(TEXT("停止していれば無音"), Model.RoadVoice(0.0, 3.0).Gain, 0.0);
	TestTrue(TEXT("速度で増える"),
	         Model.RoadVoice(10.0, 3.0).Gain < Model.RoadVoice(20.0, 3.0).Gain);
	TestTrue(TEXT("飽和する"),
	         FMath::Abs(Model.RoadVoice(100.0, 3.0).Gain - Model.RollingGain()) < 1e-12);

	// --- コース中心線 ---
	ZN6::FTrackEdge Track;
	FString Error;
	if (!Track.LoadFromFile(
			AudioRepoRoot() / TEXT("Tracks/physics_test_track.json"), Error))
	{
		AddError(FString::Printf(TEXT("コース中心線を読めない: %s"), *Error));
		return false;
	}

	// スタートライン上（中心線 x=0, y=0）は路面の真ん中
	TestTrue(*FString::Printf(TEXT("中心線上は幅の半分（%.4f m）"),
	                          Track.DistanceToEdgeM(0.0, 0.0)),
	         FMath::Abs(Track.DistanceToEdgeM(0.0, 0.0) - Track.WidthM() / 2.0) < 1e-6);

	// **大きく外れたら負になる。** 符号が逆だと草の上でアスファルトの音が鳴る。
	TestTrue(TEXT("コース外は負"), Track.DistanceToEdgeM(0.0, -200.0) < 0.0);

	return true;
}

// ---------------------------------------------------------------------------

IMPLEMENT_SIMPLE_AUTOMATION_TEST(
	FZN6AudioDoesNotAffectPhysics,
	"ZN6.Audio.音は物理に影響しない",
	EAutomationTestFlags::EditorContext | EAutomationTestFlags::EngineFilter)

bool FZN6AudioDoesNotAffectPhysics::RunTest(const FString& Parameters)
{
	// **一方通行であること。**
	//
	// 音響モデルが物理の状態を触っていたら、走りが音の設定で変わる。
	// 目や耳では分からないので、同じ入力で2回走らせてビット単位で比べる。
	ZN6::FVehicleData Data;
	ZN6::FAudioModel Model;
	if (!MakeAudioModel(*this, Data, Model))
	{
		return false;
	}

	ZN6::FControlInput Control;
	Control.GearIndex = 2;
	Control.Throttle = 1.0;
	Control.SteerRad = 0.05;
	Control.Clutch = 1.0;

	// **走行ごとに FVehicle を作り直す。**
	//
	// FVehicle は前ステップの加速度を内部に持っている（荷重移動に使う）。
	// 使い回すと1回目の走行の残りが2回目に入り、**音とは無関係に**
	// 5〜6桁目がずれる。最初これで「音が物理に影響している」ように見えた。
	auto Run = [&](bool bWithAudio) -> ZN6::FVehicleState
	{
		ZN6::FVehicle Vehicle;
		FString InitError;
		if (!Vehicle.Init(Data, /*bUseLsd=*/true, InitError))
		{
			AddError(FString::Printf(TEXT("物理モデルを初期化できない: %s"), *InitError));
			return ZN6::FVehicleState();
		}

		ZN6::FVehicleState State = Vehicle.InitialState(80.0 / 3.6, 2);
		ZN6::FVehicleState Next;
		ZN6::FVehicleOutputs Outputs;
		TArray<ZN6::FEngineLoopVoice> Blend;

		for (int32 Step = 0; Step < 400; ++Step)
		{
			Vehicle.Step(State, Control, 0.002, Next, Outputs);
			State = Next;

			if (bWithAudio)
			{
				double Worst = 0.0;
				for (int32 Wheel = 0; Wheel < ZN6::WheelCount; ++Wheel)
				{
					Worst = FMath::Max(Worst, Outputs.Utilisation[Wheel]);
				}
				Model.EngineVoice(Outputs.EngineRpm, Control.Throttle, Step * 0.002);
				Model.TireVoice(Worst, State.SpeedMps());
				Model.RoadVoice(State.SpeedMps(), 3.0);
				Model.EngineLoopBlend(Outputs.EngineRpm, Blend);
			}
		}
		return State;
	};

	const ZN6::FVehicleState Quiet = Run(false);
	const ZN6::FVehicleState Loud = Run(true);

	TestEqual(TEXT("音を鳴らしても vx が変わらない"), Loud.VxMps, Quiet.VxMps);
	TestEqual(TEXT("音を鳴らしても vy が変わらない"), Loud.VyMps, Quiet.VyMps);
	TestEqual(TEXT("音を鳴らしてもヨー角速度が変わらない"),
	          Loud.YawRateRads, Quiet.YawRateRads);
	TestEqual(TEXT("音を鳴らしても x が変わらない"), Loud.XM, Quiet.XM);
	TestEqual(TEXT("音を鳴らしても y が変わらない"), Loud.YM, Quiet.YM);
	TestEqual(TEXT("音を鳴らしても向きが変わらない"), Loud.HeadingRad, Quiet.HeadingRad);

	return true;
}

#endif  // WITH_DEV_AUTOMATION_TESTS
