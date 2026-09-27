#include "ZN6VehicleAudioComponent.h"

#include "Components/AudioComponent.h"
#include "Kismet/GameplayStatics.h"
#include "Misc/Paths.h"
#include "Sound/SoundBase.h"

namespace
{
	/** ループの置き場。`Scripts/import_audio.py` がここへ取り込む。 */
	const TCHAR* AudioPackage = TEXT("/Game/ZN6/Audio/");

	/** ピッチ倍率の範囲。**UE の既定に合わせる。** 外れると勝手に丸められ、
	 *  「音程が回転数と合っていない」ことに気づけない。 */
	constexpr float MinPitch = 0.4f;
	constexpr float MaxPitch = 2.0f;
}

UZN6VehicleAudioComponent::UZN6VehicleAudioComponent()
{
	PrimaryComponentTick.bCanEverTick = false;   // 車両アクタが呼ぶ
}

void UZN6VehicleAudioComponent::BeginPlay()
{
	Super::BeginPlay();
}

USoundBase* UZN6VehicleAudioComponent::LoadLoop(const FString& Name) const
{
	const FString Path = FString(AudioPackage) + Name + TEXT(".") + Name;
	USoundBase* Sound = LoadObject<USoundBase>(nullptr, *Path);
	if (Sound == nullptr)
	{
		// **黙って無音にしない**（憲法ルール6）。
		// 取り込みを忘れているのか、名前がずれているのかを区別できるようにする。
		UE_LOG(LogTemp, Warning, TEXT("ZN6 audio: ループが無い: %s"), *Path);
	}
	return Sound;
}

UAudioComponent* UZN6VehicleAudioComponent::MakeVoice(USoundBase* Sound)
{
	if (Sound == nullptr)
	{
		return nullptr;
	}

	UAudioComponent* Voice = UGameplayStatics::SpawnSoundAttached(
		Sound, this, NAME_None, FVector::ZeroVector, EAttachLocation::KeepRelativeOffset,
		/*bStopWhenAttachedToDestroyed=*/true, /*VolumeMultiplier=*/0.0f);
	if (Voice == nullptr)
	{
		UE_LOG(LogTemp, Warning, TEXT("ZN6 audio: 再生器を作れない: %s"),
		       *Sound->GetName());
		return nullptr;
	}

	// **止めない。** 全ループを鳴らしっぱなしにして音量だけで混ぜる。
	// 都度 Play すると位相が飛んで「プツッ」と鳴る。
	Voice->bAutoDestroy = false;
	Voice->SetVolumeMultiplier(0.0f);
	return Voice;
}

bool UZN6VehicleAudioComponent::Initialise(const FString& RepoRoot,
                                           ZN6::FVehicleData& VehicleData,
                                           FString& OutError)
{
	bReady = false;

	if (!Model.Init(RepoRoot / TEXT("Audio/audio.json"), VehicleData, OutError))
	{
		return false;
	}

	EngineVoices.Reset();
	RoadVoices.Reset();

	// 段ごとに [負荷あり, 負荷なし] の2本。**並びを固定する。**
	int32 Missing = 0;
	for (int32 Step = 0; Step < Model.LoopSteps(); ++Step)
	{
		for (const TCHAR* Tag : { TEXT("load"), TEXT("overrun") })
		{
			const FString Name = FString::Printf(TEXT("engine_%02d_%s"), Step, Tag);
			UAudioComponent* Voice = MakeVoice(LoadLoop(Name));
			EngineVoices.Add(Voice);
			if (Voice == nullptr) { ++Missing; }
		}
	}

	SkidVoice = MakeVoice(LoadLoop(TEXT("tire_skid")));
	if (SkidVoice == nullptr) { ++Missing; }

	for (const TCHAR* Surface : { TEXT("asphalt"), TEXT("grass") })
	{
		UAudioComponent* Voice = MakeVoice(
			LoadLoop(FString(TEXT("road_")) + Surface));
		RoadVoices.Add(Voice);
		if (Voice == nullptr) { ++Missing; }
	}

	if (Missing > 0)
	{
		// **「音が出ない」を成功として返さない**（ルール16）。
		OutError = FString::Printf(
			TEXT("ループが %d 本読めない。python Audio/synth.py で生成し、"
			     "Scripts/import_audio.py で取り込むこと"), Missing);
		return false;
	}

	bReady = true;
	return true;
}

void UZN6VehicleAudioComponent::SetAudioEnabled(bool bEnabled)
{
	bAudioEnabled = bEnabled;
	if (!bEnabled)
	{
		for (UAudioComponent* Voice : EngineVoices)
		{
			if (Voice != nullptr) { Voice->SetVolumeMultiplier(0.0f); }
		}
		if (SkidVoice != nullptr) { SkidVoice->SetVolumeMultiplier(0.0f); }
		for (UAudioComponent* Voice : RoadVoices)
		{
			if (Voice != nullptr) { Voice->SetVolumeMultiplier(0.0f); }
		}
		EngineVolumeSum = 0.0;
		SkidVolume = 0.0;
	}
}

void UZN6VehicleAudioComponent::UpdateAudio(double EngineRpm, double Throttle,
                                            double Utilisation, double SpeedMps,
                                            double DistanceToEdgeM, double TimeS)
{
	if (!bReady || !bAudioEnabled)
	{
		return;
	}

	const double Master = Model.MasterGain();

	// --- エンジン ---
	const ZN6::FEngineVoice Voice = Model.EngineVoice(EngineRpm, Throttle, TimeS);
	const double Load = FMath::Clamp(Throttle, 0.0, 1.0);
	const double Level = Master * Voice.Gain * Voice.LimiterGate;

	TArray<ZN6::FEngineLoopVoice> Blend;
	Model.EngineLoopBlend(EngineRpm, Blend);

	// **まず全部を 0 にする。** 前フレームで鳴っていた段が鳴り続けると、
	// 回転が下がっても高い段が残る。
	for (UAudioComponent* Component : EngineVoices)
	{
		if (Component != nullptr) { Component->SetVolumeMultiplier(0.0f); }
	}

	EngineVolumeSum = 0.0;
	for (const ZN6::FEngineLoopVoice& Loop : Blend)
	{
		const int32 Base = Loop.LoopIndex * 2;
		if (!EngineVoices.IsValidIndex(Base + 1))
		{
			continue;
		}

		// 負荷ありと負荷なしをスロットルで混ぜる。**合計は Loop.Gain。**
		const double Volumes[2] = { Level * Loop.Gain * Load,
		                            Level * Loop.Gain * (1.0 - Load) };
		for (int32 Which = 0; Which < 2; ++Which)
		{
			UAudioComponent* Component = EngineVoices[Base + Which];
			if (Component == nullptr) { continue; }
			Component->SetVolumeMultiplier(static_cast<float>(Volumes[Which]));
			Component->SetPitchMultiplier(FMath::Clamp(
				static_cast<float>(Loop.PitchMultiplier), MinPitch, MaxPitch));
			EngineVolumeSum += Volumes[Which];
		}
	}

	// --- タイヤ ---
	const ZN6::FTireVoice Tire = Model.TireVoice(Utilisation, SpeedMps);
	SkidVolume = Master * Tire.Gain;
	if (SkidVoice != nullptr)
	{
		SkidVoice->SetVolumeMultiplier(static_cast<float>(SkidVolume));
		// スキール音は基準周波数のループを持っているので、比でピッチを変える
		const double Pitch = Tire.Hz / FMath::Max(Model.GetSkidBaseHzForPitch(), 1e-6);
		SkidVoice->SetPitchMultiplier(FMath::Clamp(
			static_cast<float>(Pitch), MinPitch, MaxPitch));
	}

	// --- 路面 ---
	const ZN6::FRoadVoice Road = Model.RoadVoice(SpeedMps, DistanceToEdgeM);
	const double Ratios[2] = { Road.InsideRatio, Road.OutsideRatio };
	for (int32 Index = 0; Index < RoadVoices.Num() && Index < 2; ++Index)
	{
		if (RoadVoices[Index] == nullptr) { continue; }
		RoadVoices[Index]->SetVolumeMultiplier(
			static_cast<float>(Master * Road.Gain * Ratios[Index]));
	}
}
