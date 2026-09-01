// リプレイとゴースト。
//
// **遊びの機能である以上に、検証の道具である**（`Docs/SPEC_GT7_GAP.md` §9）。
//
// 物理は 1 ms の固定刻みで決定的に回っている（`FZN6FixedStepAccumulator`）。
// **その性質は既に手に入っているのに、まだ何にも使われていなかった。**
// 操作を記録して差し戻せば、
//
//   - **同じ操作で、セッティングだけを変えた比較ができる。**
//     今は「車高を下げたから速いのか、運転が良かったのか」を分離できない
//   - 接地力をタイヤへ繋いだ影響（`bRideDrivesTyreLoads`）を同一入力で測れる
//   - 積分が1次であることによる刻み依存（issue #28）を再現可能な形で示せる
//
// 憲法ルール3 が禁じるのは「タイムだけを合わせる最適化」であって、
// **入力を固定した比較はその逆——原因を切り分ける手段**である。
//
// ---
//
// **記録するのは操作だけ**（1 物理ステップにつき1つ）。
// 軌跡は疎に（既定 20 ステップ = 20 ms ごと）別に持つ。これは2つの理由による:
//
//   1. ゴーストの描画に、毎ステップの位置は要らない
//   2. **決定性の検査に使える。** 記録した操作を流し直したとき、
//      疎な軌跡と一致しなければ**物理が決定的でない。**
//      それはリプレイの不具合ではなく物理の不具合なので、
//      黙って許容せず issue にすること（憲法ルール6）
//
// 記録した操作を再生すると、車は**同じ道を通るはず**である。
// 通らなければ、上のとおり物理側を疑う。

#pragma once

#include "CoreMinimal.h"
#include "Physics/ZN6Vehicle.h"

namespace ZN6
{
	/** ファイルの目印。壊れたファイルを読んで落ちないため。 */
	inline constexpr uint32 ReplayMagic = 0x5A4E3652u;   // 'ZN6R'

	/**
	 * 形式の版。**上げたら古いファイルは読まない。**
	 * 黙って読み替えると、意味の違う数値を同じ形で受け取ることになる。
	 */
	inline constexpr int32 ReplayVersion = 1;

	/** 既定の軌跡の間隔 [ステップ]。1 ms 刻みなら 20 ms ごと。 */
	inline constexpr int32 DefaultGhostStride = 20;

	/**
	 * 1ステップぶんの操作。**物理の固定刻みと1対1で対応する。**
	 *
	 * フレーム単位ではなくステップ単位で持つのが要点。フレームは
	 * 描画の都合で長さが変わるので、フレーム単位で記録すると
	 * **再生の fps が録画時と違うだけで結果が変わる。**
	 */
	struct FReplayControl
	{
		float Throttle = 0.0f;
		float Brake = 0.0f;
		float SteerRad = 0.0f;
		float Clutch = 1.0f;
		float Handbrake = 0.0f;
		int32 GearIndex = 0;

		void From(const FControlInput& In);
		void To(FControlInput& Out) const;
	};

	/** 疎な軌跡の1点。ゴーストの描画と決定性の検査に使う。 */
	struct FGhostSample
	{
		double TimeS = 0.0;
		double XM = 0.0;
		double YM = 0.0;
		double ZM = 0.0;
		double YawRad = 0.0;
		double PitchRad = 0.0;
		double RollRad = 0.0;
		double SpeedMps = 0.0;
	};

	/**
	 * 記録の見出し。
	 *
	 * **何と一緒に録ったかを必ず持つ。** 別の車・別のコース・別の
	 * セッティングの記録を黙って再生すると、出てくる軌跡は
	 * 「同じ操作をしたときの姿」ではなくなる。それを気づかずに
	 * 比較に使うのが、いちばん静かな形の捏造である。
	 */
	struct FReplayHeader
	{
		FString TrackKey;

		/** `vehicle.json` の中身のハッシュ。 */
		FString VehicleHash;

		/** セッティング（車高・アライメント・ばね等）のハッシュ。 */
		FString SetupHash;

		/** 物理の固定刻み [s]。**違えば再生は一致しない。** */
		double FixedStepS = 0.0;

		/** 軌跡を残す間隔 [ステップ]。 */
		int32 GhostStride = DefaultGhostStride;

		/** この記録のラップタイム [s]。0 なら未計測（周回していない）。 */
		double LapTimeS = 0.0;

		/** コース外に出た周か。**参考記録である**ことを持ち歩く。 */
		bool bInvalidated = false;
	};

	/** 記録そのもの。 */
	class FReplay
	{
	public:
		FReplayHeader Header;
		TArray<FReplayControl> Controls;
		TArray<FGhostSample> Ghost;

		void Reset();
		bool IsEmpty() const { return Controls.Num() == 0; }
		int32 StepCount() const { return Controls.Num(); }

		/** 記録の長さ [s]。 */
		double DurationS() const;

		bool Save(const FString& Path, FString& OutError) const;
		bool Load(const FString& Path, FString& OutError);

		/**
		 * 時刻 t の姿を返す（点と点の間は線形補間）。
		 *
		 * **範囲外では false。** 端の値で埋めない。埋めると、記録が
		 * 尽きたあともゴーストが最後の場所に立ち続け、「そこで
		 * 止まった車」に見える。
		 */
		bool SampleGhost(double TimeS, FGhostSample& Out) const;

		/**
		 * 別の記録と、同じ条件で録られたかを照合する。
		 *
		 * 一致しない項目を `OutReason` に日本語で並べて false を返す。
		 * **呼ぶ側は false を無視しないこと。**
		 */
		bool MatchesConditions(const FReplayHeader& Other, FString& OutReason) const;
	};

	/** 記録係。**物理ステップの中から呼ぶ。** */
	class FReplayRecorder
	{
	public:
		void Begin(const FReplayHeader& InHeader);
		void Stop() { bActive = false; }
		bool IsActive() const { return bActive; }

		/** 1ステップぶん。**Vehicle.Step の直前に呼ぶ**（渡した操作を記録する）。 */
		void Record(const FControlInput& Control, double TimeS,
		            double XM, double YM, double ZM,
		            double YawRad, double PitchRad, double RollRad,
		            double SpeedMps);

		/** 記録を取り出す。ラップタイムはここで入れる。 */
		FReplay& Take(double LapTimeS, bool bInvalidated);

		const FReplay& Peek() const { return Data; }

	private:
		FReplay Data;
		bool bActive = false;
		int32 StepsSinceGhost = 0;
	};

	/** 再生係。**記録した操作を1ステップずつ差し戻す。** */
	class FReplayPlayer
	{
	public:
		void Begin(const FReplay& InData);
		void Stop() { bActive = false; }
		bool IsActive() const { return bActive; }
		bool IsFinished() const { return Index >= Data.StepCount(); }
		int32 Position() const { return Index; }

		/**
		 * 次の1ステップぶんの操作を取り出す。
		 *
		 * 記録が尽きたら false を返す。**そのとき Out は触らない**
		 * （最後の操作を踏み続けることにならないよう、呼ぶ側で止める）。
		 */
		bool Next(FControlInput& Out);

		const FReplay& GetData() const { return Data; }

	private:
		FReplay Data;
		int32 Index = 0;
		bool bActive = false;
	};

	/** `Saved/ZN6Replays/` の下のパスを作る。 */
	FString ReplayDirectory();
	FString BestLapReplayPath(const FString& TrackKey);

	/** `vehicle.json` の中身からハッシュを作る。読めなければ空。 */
	FString HashFileContents(const FString& Path);
}
