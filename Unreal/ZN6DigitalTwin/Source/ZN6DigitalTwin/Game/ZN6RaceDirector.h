// セッションの進行（スタート・カウントダウン・周回計測）。
//
// **ここは物理ではない。** 時間を測り、状態を切り替えるだけで、
// 車の運動には一切触らない。触ると、検証済みの結果がゲームの都合で
// 変わってしまう（憲法ルール18）。
//
// 唯一の例外が**カウントダウン中の入力を止めること**だが、これは
// 「操作を受け付けない」であって「物理を変える」ではない。
// アクセル入力そのものを 0 にして車へ渡すので、物理から見れば
// 単に踏んでいないのと同じ。

#pragma once

#include "CoreMinimal.h"
#include "Physics/ZN6Track.h"

namespace ZN6
{
	/** セッションの状態。 */
	enum class ERacePhase : uint8
	{
		/** メニュー。車は止まっている。 */
		Menu,
		/** カウントダウン中。**操作は受け付けない。** */
		Countdown,
		/** 走行中。計測している。 */
		Racing,
		/** 一時停止。**時計も止める。** */
		Paused,
		/** 規定周回を終えた。 */
		Finished,
	};

	/** 1周の記録。 */
	struct FLapRecord
	{
		int32 LapNumber = 0;
		double TimeS = 0.0;
		/** 区間タイム [s]。3分割。 */
		double SectorS[3] = {};
		/** その時点での自己ベストだったか。 */
		bool bBest = false;
		/**
		 * **コース外に出た周か。**
		 *
		 * 出た周は参考記録で、ベストにはならない（`bBest` が付かない）。
		 * ただし「ベストでない」と「参考記録である」は別のことなので、
		 * 記録そのものに印を残す。**後から周の一覧を見て判断できないと、
		 * ショートカットした周と単に遅かった周が同じ顔をする。**
		 */
		bool bInvalidated = false;
	};

	/** セッションの設定。**演出であって車両仕様ではない。** */
	struct FRaceRules
	{
		/** カウントダウンの秒数。3 なら「3・2・1・GO」。 */
		int32 CountdownSeconds = 3;
		/** 走る周回数。0 なら無制限（フリー走行）。 */
		int32 TotalLaps = 3;
		/** 区間の数。ミニマップと画面表示に使う。 */
		static constexpr int32 SectorCount = 3;
	};

	/**
	 * セッションの進行役。
	 *
	 * **時間は物理の積算時間で測る。** フレーム時間で測ると、
	 * 描画が重い日にラップタイムが変わる。物理は固定刻みなので、
	 * そちらを使えば再現する。
	 */
	class FRaceDirector
	{
	public:
		void Init(const FTrackEdge* InTrack, const FRaceRules& InRules);

		/** メニューへ戻す。**記録も消える。** */
		void Reset();

		/** カウントダウンを始める。メニューからのみ。 */
		bool StartCountdown();

		/**
		 * カウントダウン無しで走り出す（フリー走行）。
		 *
		 * 練習走行では 3 秒待たされる意味が無い。計測はするが、
		 * 周回数の上限は掛けない。
		 */
		bool StartFreeRun();

		/** 一時停止 / 再開。**走行中と一時停止の間だけ。** */
		bool Pause();
		bool Resume();

		/**
		 * 1ステップ進める。**物理を進めた後に呼ぶ。**
		 *
		 * @param DtS      物理の固定刻み [s]
		 * @param XM, YM   車の位置。周回判定に使う
		 * @param SpeedMps 表示用（フライングの判定はしない）
		 */
		void Advance(double DtS, double XM, double YM, double SpeedMps);

		/** 今の入力倍率。**カウントダウン中は 0。** */
		double InputScale() const;

		ERacePhase Phase() const { return CurrentPhase; }
		bool IsRacing() const { return CurrentPhase == ERacePhase::Racing; }

		/** カウントダウンの残り [s]。 */
		double CountdownRemainingS() const { return CountdownLeftS; }
		/** 画面に出す数字。0 は「GO」。カウントダウン中以外は -1。 */
		int32 CountdownNumber() const;

		/** 現在の周回（1 始まり）。走り出す前は 0。 */
		int32 CurrentLap() const { return LapsCompleted + 1; }
		int32 LapsDone() const { return LapsCompleted; }
		/** 今の周回の経過時間 [s]。 */
		double CurrentLapTimeS() const { return LapTimeS; }
		/** セッション全体の経過時間 [s]。 */
		double SessionTimeS() const { return SessionElapsedS; }

		/** 自己ベスト [s]。まだ無ければ 0。 */
		double BestLapS() const { return BestLapTimeS; }
		const TArray<FLapRecord>& Laps() const { return CompletedLaps; }

		/** 今いる区間（0 始まり）。 */
		int32 CurrentSector() const { return SectorIndex; }
		/** コース上の道のり [m]。ミニマップに使う。 */
		double TrackSM() const { return LastSM; }
		/** 中心線からの横ずれ [m]。左が正。 */
		double LateralM() const { return LastLateralM; }
		/** 1周の進捗 0..1。 */
		double LapProgress() const;

		/** **コース外に出ているか。** 記録の扱いを変えるために持つ。 */
		bool IsOffTrack() const { return bOffTrack; }
		/** 今の周回でコース外に出たか。**出た周は参考記録。** */
		bool CurrentLapInvalidated() const { return bLapInvalidated; }

	private:
		void CompleteLap();

		const FTrackEdge* Track = nullptr;
		FRaceRules Rules;

		ERacePhase CurrentPhase = ERacePhase::Menu;
		double CountdownLeftS = 0.0;
		double SessionElapsedS = 0.0;
		double LapTimeS = 0.0;

		int32 LapsCompleted = 0;
		double BestLapTimeS = 0.0;
		TArray<FLapRecord> CompletedLaps;

		int32 SectorIndex = 0;
		double SectorStartS[FRaceRules::SectorCount] = {};
		double SectorTimes[FRaceRules::SectorCount] = {};

		double LastSM = 0.0;
		double LastLateralM = 0.0;
		bool bHasLastS = false;
		bool bOffTrack = false;
		bool bLapInvalidated = false;
	};
}
