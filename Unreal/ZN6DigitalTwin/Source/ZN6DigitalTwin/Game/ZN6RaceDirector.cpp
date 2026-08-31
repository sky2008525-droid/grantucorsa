#include "ZN6RaceDirector.h"

namespace ZN6
{
	void FRaceDirector::Init(const FTrackEdge* InTrack, const FRaceRules& InRules)
	{
		Track = InTrack;
		Rules = InRules;
		Reset();
	}

	void FRaceDirector::Reset()
	{
		CurrentPhase = ERacePhase::Menu;
		CountdownLeftS = 0.0;
		SessionElapsedS = 0.0;
		LapTimeS = 0.0;
		LapsCompleted = 0;
		BestLapTimeS = 0.0;
		CompletedLaps.Reset();
		SectorIndex = 0;
		for (int32 Index = 0; Index < FRaceRules::SectorCount; ++Index)
		{
			SectorStartS[Index] = 0.0;
			SectorTimes[Index] = 0.0;
		}
		LastSM = 0.0;
		LastLateralM = 0.0;
		bHasLastS = false;
		bOffTrack = false;
		bLapInvalidated = false;
	}

	bool FRaceDirector::StartCountdown()
	{
		// **メニューからだけ。** 走行中に押されても無視する。
		if (CurrentPhase != ERacePhase::Menu && CurrentPhase != ERacePhase::Finished)
		{
			return false;
		}
		Reset();
		CurrentPhase = ERacePhase::Countdown;
		CountdownLeftS = static_cast<double>(FMath::Max(Rules.CountdownSeconds, 0));
		return true;
	}

	bool FRaceDirector::StartFreeRun()
	{
		if (CurrentPhase != ERacePhase::Menu && CurrentPhase != ERacePhase::Finished)
		{
			return false;
		}
		Reset();
		// **周回数の上限を外す。** 練習走行は好きなだけ走れること。
		Rules.TotalLaps = 0;
		CurrentPhase = ERacePhase::Racing;
		CountdownLeftS = 0.0;
		return true;
	}

	bool FRaceDirector::Pause()
	{
		if (CurrentPhase != ERacePhase::Racing && CurrentPhase != ERacePhase::Countdown)
		{
			return false;
		}
		CurrentPhase = ERacePhase::Paused;
		return true;
	}

	bool FRaceDirector::Resume()
	{
		if (CurrentPhase != ERacePhase::Paused)
		{
			return false;
		}
		// カウントダウンの途中で止めた場合も、残り時間から再開する。
		CurrentPhase = (CountdownLeftS > 0.0) ? ERacePhase::Countdown : ERacePhase::Racing;
		return true;
	}

	double FRaceDirector::InputScale() const
	{
		// **カウントダウン中と一時停止中は操作を渡さない。**
		// 物理を止めるのではなく、入力を 0 にする。物理から見れば
		// 「踏んでいない」のと同じで、モデルには何も足していない。
		// **走行中以外はすべて 0。** ここを default: 1.0 にしていたせいで、
		// チェッカー後（Finished）も操作を受け付けていた。
		// 「受け付ける状態」を列挙する側に書けば、状態を足したときに
		// 黙って通ってしまうことがない。
		return (CurrentPhase == ERacePhase::Racing) ? 1.0 : 0.0;
	}

	int32 FRaceDirector::CountdownNumber() const
	{
		if (CurrentPhase != ERacePhase::Countdown)
		{
			return -1;
		}
		// 残り 2.4 秒なら「3」。切り上げる。
		return FMath::Max(FMath::CeilToInt(static_cast<float>(CountdownLeftS)), 0);
	}

	double FRaceDirector::LapProgress() const
	{
		if (Track == nullptr || !Track->IsValid() || Track->LengthM() <= 0.0)
		{
			return 0.0;
		}
		return FMath::Clamp(LastSM / Track->LengthM(), 0.0, 1.0);
	}

	void FRaceDirector::Advance(double DtS, double XM, double YM, double SpeedMps)
	{
		if (CurrentPhase == ERacePhase::Menu
		    || CurrentPhase == ERacePhase::Paused
		    || CurrentPhase == ERacePhase::Finished)
		{
			// **止まっているときは時計も止める。**
			return;
		}

		if (CurrentPhase == ERacePhase::Countdown)
		{
			CountdownLeftS -= DtS;
			if (CountdownLeftS <= 0.0)
			{
				CountdownLeftS = 0.0;
				CurrentPhase = ERacePhase::Racing;
				// **スタートの瞬間から計測する。** カウントダウンぶんは入れない。
				SessionElapsedS = 0.0;
				LapTimeS = 0.0;
				bLapInvalidated = false;
			}
			return;
		}

		SessionElapsedS += DtS;
		LapTimeS += DtS;

		if (Track == nullptr || !Track->IsValid())
		{
			return;
		}

		double SM = 0.0;
		double LateralM = 0.0;
		Track->NearestPoint(XM, YM, SM, LateralM);
		LastLateralM = LateralM;

		// コース外に出たか。**出た周は参考記録にする。**
		// 記録そのものは消さない。消すと「なぜ残っていないか」が分からない。
		bOffTrack = Track->DistanceToEdgeM(XM, YM) < 0.0;
		if (bOffTrack)
		{
			bLapInvalidated = true;
		}

		const double LengthM = Track->LengthM();

		// --- 区間 ---
		//
		// 道のりを等分する。**時間ではなく距離で切る**ので、
		// 遅く走っても区間の境目は動かない。
		const double SectorLengthM = LengthM / FRaceRules::SectorCount;
		const int32 NewSector = FMath::Clamp(
			FMath::FloorToInt(static_cast<float>(SM / SectorLengthM)),
			0, FRaceRules::SectorCount - 1);

		if (bHasLastS && NewSector != SectorIndex)
		{
			// **前へ進んだときだけ区間を閉じる。** 逆走やコース脇での
			// 行ったり来たりで区間タイムが刻まれるのを防ぐ。
			const bool bForward = (NewSector == SectorIndex + 1);
			if (bForward)
			{
				SectorTimes[SectorIndex] = LapTimeS - SectorStartS[SectorIndex];
				SectorStartS[NewSector] = LapTimeS;
			}
			SectorIndex = NewSector;
		}

		// --- 周回 ---
		//
		// **ゴール線の通過を「s の巻き戻り」で見る。**
		// 位置で見ると、線の近くで止まっているだけで何周も加算される。
		if (bHasLastS)
		{
			const bool bWrapped = (LastSM > LengthM * 0.9) && (SM < LengthM * 0.1);
			if (bWrapped)
			{
				CompleteLap();
			}
		}

		LastSM = SM;
		bHasLastS = true;
	}

	void FRaceDirector::CompleteLap()
	{
		// 最終区間を閉じる
		SectorTimes[FRaceRules::SectorCount - 1] =
			LapTimeS - SectorStartS[FRaceRules::SectorCount - 1];

		FLapRecord Record;
		Record.LapNumber = LapsCompleted + 1;
		Record.TimeS = LapTimeS;
		for (int32 Index = 0; Index < FRaceRules::SectorCount; ++Index)
		{
			Record.SectorS[Index] = SectorTimes[Index];
		}

		// **コース外に出た周はベストにしない。** 記録は残すが印を付ける。
		if (!bLapInvalidated && (BestLapTimeS <= 0.0 || LapTimeS < BestLapTimeS))
		{
			BestLapTimeS = LapTimeS;
			Record.bBest = true;
		}

		CompletedLaps.Add(Record);
		++LapsCompleted;

		LapTimeS = 0.0;
		SectorIndex = 0;
		bLapInvalidated = false;
		for (int32 Index = 0; Index < FRaceRules::SectorCount; ++Index)
		{
			SectorStartS[Index] = 0.0;
		}

		if (Rules.TotalLaps > 0 && LapsCompleted >= Rules.TotalLaps)
		{
			CurrentPhase = ERacePhase::Finished;
		}
	}
}
