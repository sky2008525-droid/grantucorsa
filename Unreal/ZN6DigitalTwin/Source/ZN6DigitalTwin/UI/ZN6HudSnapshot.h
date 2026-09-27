// 画面へ渡す値をまとめた箱。
//
// **UI が物理オブジェクトを直接読まないための境界。**
//
// これが無いと、ウィジェットが `AZN6VehicleActor` を持ち、そこから
// `FVehicleState` を読むことになる。そうなると
//
//   - UI を単体で試せない（車を用意しないと絵が出ない）
//   - UI から物理を触れてしまう（触らない保証がコメントだけになる）
//
// 値は毎フレーム、車から**一方向に**詰める。UI からは書き戻さない。

#pragma once

#include "CoreMinimal.h"
#include "Game/ZN6RaceDirector.h"

namespace ZN6
{
	/** 1フレーム分の表示内容。 */
	struct FHudSnapshot
	{
		// --- 計器 ---
		double SpeedKmh = 0.0;
		double EngineRpm = 0.0;
		double RedlineRpm = 7400.0;
		double IdleRpm = 700.0;
		/** 表示上のギア。1 始まり。0 はニュートラル、-1 は後退。 */
		int32 Gear = 1;
		double Throttle = 0.0;
		double Brake = 0.0;
		double ClutchEngagement = 1.0;
		double Handbrake = 0.0;
		double SteerRad = 0.0;
		double MaxSteerRad = 0.6;

		// --- 挙動 ---
		/** 4輪の摩擦円利用率 0..1。**限界の近さ。** */
		double Utilisation[4] = {};
		double SlipAngleDeg = 0.0;
		double LateralG = 0.0;
		double LongitudinalG = 0.0;
		/** 接地しているか。**浮いている輪が分かるようにする。** */
		bool bContact[4] = { true, true, true, true };

		// --- セッション ---
		ERacePhase Phase = ERacePhase::Menu;
		int32 CountdownNumber = -1;
		double CountdownRemainingS = 0.0;
		int32 CurrentLap = 1;
		int32 TotalLaps = 0;
		double LapTimeS = 0.0;
		double BestLapS = 0.0;
		double SessionTimeS = 0.0;
		int32 Sector = 0;
		bool bOffTrack = false;
		bool bLapInvalidated = false;
		/** 1周の進捗 0..1。ミニマップの点と進捗バーに使う。 */
		double LapProgress = 0.0;

		// --- コース上の位置（ミニマップ用、世界座標 [m]）---
		double CarXM = 0.0;
		double CarYM = 0.0;
		double CarHeadingRad = 0.0;

		// --- 信頼度 ---
		//
		// **画面にも出す。** 「この数字はどれくらい確かか」を隠さない
		// （Docs/AGENT_TOPOLOGY.md §3）。
		double Confidence = 0.0;
		bool bValidatable = false;

		/** 直近の周回記録。リザルト表示に使う。 */
		TArray<FLapRecord> Laps;
	};
}
