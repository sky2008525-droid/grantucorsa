#include "ZN6Replay.h"

#include "HAL/PlatformFileManager.h"
#include "Misc/FileHelper.h"
#include "Misc/Paths.h"
#include "Misc/SecureHash.h"
#include "Serialization/BufferArchive.h"
#include "Serialization/MemoryReader.h"

namespace ZN6
{
	void FReplayControl::From(const FControlInput& In)
	{
		Throttle = static_cast<float>(In.Throttle);
		Brake = static_cast<float>(In.Brake);
		SteerRad = static_cast<float>(In.SteerRad);
		Clutch = static_cast<float>(In.Clutch);
		Handbrake = static_cast<float>(In.Handbrake);
		GearIndex = In.GearIndex;
	}

	void FReplayControl::To(FControlInput& Out) const
	{
		Out.Throttle = Throttle;
		Out.Brake = Brake;
		Out.SteerRad = SteerRad;
		Out.Clutch = Clutch;
		Out.Handbrake = Handbrake;
		Out.GearIndex = GearIndex;
	}

	// -----------------------------------------------------------------------

	void FReplay::Reset()
	{
		Header = FReplayHeader();
		Controls.Reset();
		Ghost.Reset();
	}

	double FReplay::DurationS() const
	{
		return static_cast<double>(Controls.Num()) * Header.FixedStepS;
	}

	bool FReplay::SampleGhost(double TimeS, FGhostSample& Out) const
	{
		if (Ghost.Num() == 0)
		{
			return false;
		}
		if (TimeS < Ghost[0].TimeS || TimeS > Ghost.Last().TimeS)
		{
			// **端で丸めない。** 丸めると、記録が尽きたあともゴーストが
			// 最後の場所に立ち続け「そこで止まった車」に見える。
			return false;
		}

		// 等間隔で記録しているので、位置は割り算で当たりを付けられる。
		// ただし**割り算の結果を信用しきらない**（浮動小数の端で1つずれる）。
		const double Span = (Ghost.Num() > 1)
			? (Ghost[1].TimeS - Ghost[0].TimeS) : 1.0;
		int32 Index = (Span > 0.0)
			? FMath::Clamp(static_cast<int32>((TimeS - Ghost[0].TimeS) / Span),
			               0, Ghost.Num() - 1)
			: 0;
		while (Index > 0 && Ghost[Index].TimeS > TimeS) { --Index; }
		while (Index + 1 < Ghost.Num() && Ghost[Index + 1].TimeS < TimeS) { ++Index; }

		if (Index + 1 >= Ghost.Num())
		{
			Out = Ghost.Last();
			return true;
		}

		const FGhostSample& A = Ghost[Index];
		const FGhostSample& B = Ghost[Index + 1];
		const double Width = B.TimeS - A.TimeS;
		const double T = (Width > 1e-12) ? FMath::Clamp((TimeS - A.TimeS) / Width, 0.0, 1.0) : 0.0;

		Out.TimeS = TimeS;
		Out.XM = FMath::Lerp(A.XM, B.XM, T);
		Out.YM = FMath::Lerp(A.YM, B.YM, T);
		Out.ZM = FMath::Lerp(A.ZM, B.ZM, T);
		// **方位は素の線形補間をしない。** ±180 度をまたぐ点で
		// ゴーストが1回転する。
		Out.YawRad = A.YawRad + FMath::UnwindRadians(B.YawRad - A.YawRad) * T;
		Out.PitchRad = FMath::Lerp(A.PitchRad, B.PitchRad, T);
		Out.RollRad = FMath::Lerp(A.RollRad, B.RollRad, T);
		Out.SpeedMps = FMath::Lerp(A.SpeedMps, B.SpeedMps, T);
		return true;
	}

	bool FReplay::MatchesConditions(const FReplayHeader& Other, FString& OutReason) const
	{
		TArray<FString> Problems;

		// **記録の側に値があるなら、必ず一致を要求する。**
		//
		// 「どちらかが空なら飛ばす」にしていたときは、**今の条件が
		// 分からない場合に必ず一致したことになっていた。** 分からない
		// ことを一致として扱うのは、いちばん静かな形の捏造である
		// （憲法ルール1）。記録の側が空のときだけ飛ばす（その項目を
		// 持たない古い記録のため）。
		if (!Header.TrackKey.IsEmpty() && Header.TrackKey != Other.TrackKey)
		{
			Problems.Add(FString::Printf(
				TEXT("コースが違う（記録: %s / 今: %s）"),
				*Header.TrackKey,
				Other.TrackKey.IsEmpty() ? TEXT("不明") : *Other.TrackKey));
		}
		if (!Header.VehicleHash.IsEmpty() && Header.VehicleHash != Other.VehicleHash)
		{
			Problems.Add(TEXT("vehicle.json が違う（車両データが変わっている）"));
		}
		if (!Header.SetupHash.IsEmpty() && Header.SetupHash != Other.SetupHash)
		{
			Problems.Add(TEXT("セッティングが違う"));
		}
		if (Other.FixedStepS > 0.0
		    && !FMath::IsNearlyEqual(Header.FixedStepS, Other.FixedStepS, 1e-12))
		{
			Problems.Add(FString::Printf(
				TEXT("固定刻みが違う（記録: %.6f s / 今: %.6f s）"),
				Header.FixedStepS, Other.FixedStepS));
		}

		if (Problems.Num() == 0)
		{
			OutReason.Empty();
			return true;
		}
		OutReason = FString::Join(Problems, TEXT(" / "));
		return false;
	}

	// -----------------------------------------------------------------------

	namespace
	{
		void SerialiseHeader(FArchive& Ar, FReplayHeader& Header)
		{
			Ar << Header.TrackKey;
			Ar << Header.VehicleHash;
			Ar << Header.SetupHash;
			Ar << Header.FixedStepS;
			Ar << Header.GhostStride;
			Ar << Header.LapTimeS;
			Ar << Header.bInvalidated;
		}

		void SerialiseControl(FArchive& Ar, FReplayControl& C)
		{
			Ar << C.Throttle;
			Ar << C.Brake;
			Ar << C.SteerRad;
			Ar << C.Clutch;
			Ar << C.Handbrake;
			Ar << C.GearIndex;
		}

		void SerialiseGhost(FArchive& Ar, FGhostSample& G)
		{
			Ar << G.TimeS;
			Ar << G.XM;
			Ar << G.YM;
			Ar << G.ZM;
			Ar << G.YawRad;
			Ar << G.PitchRad;
			Ar << G.RollRad;
			Ar << G.SpeedMps;
		}
	}

	bool FReplay::Save(const FString& Path, FString& OutError) const
	{
		if (Controls.Num() == 0)
		{
			OutError = TEXT("空の記録は保存しない");
			return false;
		}

		FBufferArchive Buffer;
		uint32 Magic = ReplayMagic;
		int32 Version = ReplayVersion;
		Buffer << Magic;
		Buffer << Version;

		FReplayHeader Copy = Header;
		SerialiseHeader(Buffer, Copy);

		int32 ControlCount = Controls.Num();
		Buffer << ControlCount;
		for (const FReplayControl& C : Controls)
		{
			FReplayControl Item = C;
			SerialiseControl(Buffer, Item);
		}

		int32 GhostCount = Ghost.Num();
		Buffer << GhostCount;
		for (const FGhostSample& G : Ghost)
		{
			FGhostSample Item = G;
			SerialiseGhost(Buffer, Item);
		}

		IPlatformFile& File = FPlatformFileManager::Get().GetPlatformFile();
		File.CreateDirectoryTree(*FPaths::GetPath(Path));

		if (!FFileHelper::SaveArrayToFile(Buffer, *Path))
		{
			OutError = FString::Printf(TEXT("書けない: %s"), *Path);
			return false;
		}
		return true;
	}

	bool FReplay::Load(const FString& Path, FString& OutError)
	{
		Reset();

		TArray<uint8> Bytes;
		if (!FFileHelper::LoadFileToArray(Bytes, *Path))
		{
			OutError = FString::Printf(TEXT("読めない: %s"), *Path);
			return false;
		}

		FMemoryReader Reader(Bytes, /*bIsPersistent=*/true);

		uint32 Magic = 0;
		int32 Version = 0;
		if (Bytes.Num() < static_cast<int32>(sizeof(Magic) + sizeof(Version)))
		{
			OutError = TEXT("ファイルが短すぎる（壊れている）");
			return false;
		}
		Reader << Magic;
		Reader << Version;

		if (Magic != ReplayMagic)
		{
			OutError = TEXT("リプレイのファイルではない");
			return false;
		}
		if (Version != ReplayVersion)
		{
			// **黙って読み替えない**（憲法ルール6）。意味の違う数値を
			// 同じ形で受け取ることになる。
			OutError = FString::Printf(
				TEXT("形式の版が違う（ファイル: %d / 今: %d）。録り直すこと"),
				Version, ReplayVersion);
			return false;
		}

		SerialiseHeader(Reader, Header);

		int32 ControlCount = 0;
		Reader << ControlCount;
		if (ControlCount < 0 || Reader.IsError())
		{
			OutError = TEXT("操作の数がおかしい（壊れている）");
			return false;
		}
		Controls.SetNum(ControlCount);
		for (int32 Index = 0; Index < ControlCount; ++Index)
		{
			SerialiseControl(Reader, Controls[Index]);
		}

		int32 GhostCount = 0;
		Reader << GhostCount;
		if (GhostCount < 0 || Reader.IsError())
		{
			OutError = TEXT("軌跡の数がおかしい（壊れている）");
			return false;
		}
		Ghost.SetNum(GhostCount);
		for (int32 Index = 0; Index < GhostCount; ++Index)
		{
			SerialiseGhost(Reader, Ghost[Index]);
		}

		if (Reader.IsError())
		{
			OutError = TEXT("途中で終わっている（壊れている）");
			Reset();
			return false;
		}
		return true;
	}

	// -----------------------------------------------------------------------

	void FReplayRecorder::Begin(const FReplayHeader& InHeader)
	{
		Data.Reset();
		Data.Header = InHeader;
		if (Data.Header.GhostStride <= 0)
		{
			Data.Header.GhostStride = DefaultGhostStride;
		}
		StepsSinceGhost = Data.Header.GhostStride;   // 最初の1点を必ず残す
		bActive = true;
	}

	void FReplayRecorder::Record(const FControlInput& Control, double TimeS,
	                             double XM, double YM, double ZM,
	                             double YawRad, double PitchRad, double RollRad,
	                             double SpeedMps)
	{
		if (!bActive)
		{
			return;
		}

		FReplayControl Item;
		Item.From(Control);
		Data.Controls.Add(Item);

		if (++StepsSinceGhost >= Data.Header.GhostStride)
		{
			StepsSinceGhost = 0;
			FGhostSample Sample;
			Sample.TimeS = TimeS;
			Sample.XM = XM;
			Sample.YM = YM;
			Sample.ZM = ZM;
			Sample.YawRad = YawRad;
			Sample.PitchRad = PitchRad;
			Sample.RollRad = RollRad;
			Sample.SpeedMps = SpeedMps;
			Data.Ghost.Add(Sample);
		}
	}

	FReplay& FReplayRecorder::Take(double LapTimeS, bool bInvalidated)
	{
		Data.Header.LapTimeS = LapTimeS;
		Data.Header.bInvalidated = bInvalidated;
		return Data;
	}

	// -----------------------------------------------------------------------

	void FReplayPlayer::Begin(const FReplay& InData)
	{
		Data = InData;
		Index = 0;
		bActive = Data.StepCount() > 0;
	}

	bool FReplayPlayer::Next(FControlInput& Out)
	{
		if (!bActive || Index >= Data.StepCount())
		{
			return false;
		}
		Data.Controls[Index].To(Out);
		++Index;
		return true;
	}

	// -----------------------------------------------------------------------

	FString ReplayDirectory()
	{
		return FPaths::ProjectSavedDir() / TEXT("ZN6Replays");
	}

	FString BestLapReplayPath(const FString& TrackKey)
	{
		const FString Key = TrackKey.IsEmpty() ? TEXT("unknown") : TrackKey;
		return ReplayDirectory() / (Key + TEXT("_best.zn6replay"));
	}

	FString HashFileContents(const FString& Path)
	{
		// **空のパスで読みに行かない。** 行くと LogStreaming が
		// 「Failed to read file ''」を毎回吐き、テストの警告に紛れて
		// 本物の警告が見えなくなる。
		if (Path.IsEmpty())
		{
			return FString();
		}

		TArray<uint8> Bytes;
		if (!FFileHelper::LoadFileToArray(Bytes, *Path))
		{
			// **空を返す。** でたらめな値を返すと、照合が「一致した」に
			// なってしまう（憲法ルール1）。空は照合を飛ばす合図として扱う。
			return FString();
		}
		FMD5 Md5;
		Md5.Update(Bytes.GetData(), Bytes.Num());
		uint8 Digest[16] = {};
		Md5.Final(Digest);
		return BytesToHex(Digest, 16);
	}
}
