// 車両の音を鳴らすコンポーネント（Phase 14）。
//
// **音は物理へ戻らない。** このコンポーネントは物理の出力を読むだけで、
// `FVehicleState` にも `FControlInput` にも書かない。音を切っても走りは
// 1ビットも変わらない（`ZN6.Audio.音は物理に影響しない` で検査）。
//
// 鳴らす音は `Audio/synth.py` が手続き的に合成したループ。
// **実車の録音ではない。** FA20 の音でもない（`Audio/audio.json`）。
//
// ## 鳴らし方
//
//   エンジン: 等比に並べた N 段のループのうち隣り合う2段を混ぜ、
//             それぞれを「今の回転数 / その段の回転数」でピッチ変更する。
//             各段は負荷あり(load)と負荷なし(overrun)の2本を持ち、
//             スロットルで混ぜる
//   タイヤ  : 利用率が閾値を超えたら鳴らし、速度でピッチを変える
//   路面    : アスファルトと草をコース端からの距離でクロスフェード
//
// **ピッチ倍率は SetPitchMultiplier の範囲（既定 0.4〜2.0）に収まること。**
// 段を等比に並べているのはそのため。等間隔だと低回転側で 2.37 倍になる。

#pragma once

#include "CoreMinimal.h"
#include "Components/SceneComponent.h"
#include "Audio/ZN6AudioModel.h"
#include "ZN6VehicleAudioComponent.generated.h"

class UAudioComponent;
class USoundBase;

UCLASS(ClassGroup = (ZN6), meta = (BlueprintSpawnableComponent))
class ZN6DIGITALTWIN_API UZN6VehicleAudioComponent : public USceneComponent
{
	GENERATED_BODY()

public:
	UZN6VehicleAudioComponent();

	/**
	 * 音響モデルとループを読む。
	 *
	 * **読めなくても物理は動く。** 音が出ないだけ。警告に留める。
	 *
	 * @param RepoRoot  リポジトリのルート（Audio/audio.json を探す）
	 */
	bool Initialise(const FString& RepoRoot, ZN6::FVehicleData& VehicleData,
	                FString& OutError);

	/**
	 * 1フレーム分の音を更新する。**物理の後に呼ぶ。**
	 *
	 * @param EngineRpm         物理が出したエンジン回転数
	 * @param Throttle          0..1
	 * @param Utilisation       4輪の摩擦円利用率の最大
	 * @param SpeedMps          車速
	 * @param DistanceToEdgeM   路面の内側を正とする符号つき距離
	 * @param TimeS             シミュレーション時刻（リミッタの断続に使う）
	 */
	void UpdateAudio(double EngineRpm, double Throttle, double Utilisation,
	                 double SpeedMps, double DistanceToEdgeM, double TimeS);

	/** **音を止める。** 検証は必ずアシスト・演出を切った状態で行う（ルール18）。 */
	UFUNCTION(BlueprintCallable, Category = "ZN6|Audio")
	void SetAudioEnabled(bool bEnabled);

	UFUNCTION(BlueprintPure, Category = "ZN6|Audio")
	bool IsAudioEnabled() const { return bAudioEnabled; }

	/** ループを読めて再生の準備ができているか。 */
	bool IsReady() const { return bReady; }

	const ZN6::FAudioModel& GetModel() const { return Model; }

	/** 検査用: 直近に設定した音量（エンジン全段の合計）。 */
	double GetEngineVolumeSum() const { return EngineVolumeSum; }
	double GetSkidVolume() const { return SkidVolume; }

protected:
	virtual void BeginPlay() override;

private:
	/** `/Game/ZN6/Audio/<Name>` を読む。**無ければ nullptr を返して警告する。** */
	USoundBase* LoadLoop(const FString& Name) const;

	/** ループ1本ぶんの再生器を作る。音量 0 で鳴らし始める。 */
	UAudioComponent* MakeVoice(USoundBase* Sound);

	ZN6::FAudioModel Model;
	bool bReady = false;
	bool bAudioEnabled = true;

	/** 段ごとの [負荷あり, 負荷なし]。 */
	UPROPERTY(Transient)
	TArray<UAudioComponent*> EngineVoices;

	UPROPERTY(Transient)
	TObjectPtr<UAudioComponent> SkidVoice;

	/** 路面。0 = アスファルト（内側） / 1 = 草（外側）。 */
	UPROPERTY(Transient)
	TArray<UAudioComponent*> RoadVoices;

	double EngineVolumeSum = 0.0;
	double SkidVolume = 0.0;
};
