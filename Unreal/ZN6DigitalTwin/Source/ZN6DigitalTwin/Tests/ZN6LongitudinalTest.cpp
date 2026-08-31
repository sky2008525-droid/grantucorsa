// UE5(C++) 実装と Python 実装の突き合わせ。
//
// Docs/SPEC_ZN6.md §10.3「Python 版と UE5 版の 0-100km/h が一致する」を
// 機械的に判定する。
//
// **期待値をこのファイルに書かないこと。**
// Tools/export_reference.py が Python 実装から生成した
// Reference/longitudinal_reference.json を読んで比較する。ベタ書きすると、
// Python 側を変えたときに C++ 側が古い値のまま通り続け、どちらが正しいのか
// 分からなくなる。**Python 実装が唯一の基準。**
//
// ここで検証していないこと: 実車との一致。参照値の confidence は 0.20 で、
// 入力に assumed が混ざっている（Docs/AGENT_TOPOLOGY.md §3）。
// このテストが通っても「実車に近い」ことの証拠にはならない。
// 通るのは「2つの実装が同じ計算をしている」ことだけ。

#include "CoreMinimal.h"
#include "Misc/AutomationTest.h"
#include "Misc/FileHelper.h"
#include "Misc/Paths.h"
#include "Dom/JsonObject.h"
#include "Serialization/JsonReader.h"
#include "Serialization/JsonSerializer.h"

#include "Physics/ZN6LongitudinalModel.h"
#include "Physics/ZN6VehicleData.h"

#if WITH_DEV_AUTOMATION_TESTS

namespace
{
	/** リポジトリルート。プロジェクトは <repo>/Unreal/ZN6DigitalTwin/ にある。 */
	FString RepoRoot()
	{
		return FPaths::ConvertRelativePathToFull(FPaths::ProjectDir() / TEXT("../.."));
	}

	FString VehicleJsonPath()
	{
		return RepoRoot() / TEXT("Vehicles/ZN6/vehicle.json");
	}

	FString ReferenceJsonPath()
	{
		return FPaths::ConvertRelativePathToFull(
			FPaths::ProjectDir() / TEXT("Reference/longitudinal_reference.json"));
	}

	bool LoadJsonObject(const FString& Path, TSharedPtr<FJsonObject>& OutObject, FString& OutError)
	{
		FString Text;
		if (!FFileHelper::LoadFileToString(Text, *Path))
		{
			OutError = FString::Printf(TEXT("読めない: %s"), *Path);
			return false;
		}
		const TSharedRef<TJsonReader<TCHAR>> Reader = TJsonReaderFactory<TCHAR>::Create(Text);
		if (!FJsonSerializer::Deserialize(Reader, OutObject) || !OutObject.IsValid())
		{
			OutError = FString::Printf(TEXT("JSON として解釈できない: %s"), *Path);
			return false;
		}
		return true;
	}

	/** 参照 JSON の test_conditions を、C++ 側の設定へそのまま写す。 */
	bool ReadSettings(const TSharedPtr<FJsonObject>& Reference, ZN6::FAccelerationSettings& OutSettings, FString& OutError)
	{
		const TSharedPtr<FJsonObject>* Conditions = nullptr;
		if (!Reference->TryGetObjectField(TEXT("test_conditions"), Conditions) || Conditions == nullptr)
		{
			OutError = TEXT("参照 JSON に test_conditions が無い");
			return false;
		}
		OutSettings.ShiftTimeS = (*Conditions)->GetNumberField(TEXT("shift_time_s"));
		OutSettings.LaunchRpm  = (*Conditions)->GetNumberField(TEXT("launch_rpm"));
		OutSettings.DtS        = (*Conditions)->GetNumberField(TEXT("dt_s"));
		OutSettings.TargetKmh  = (*Conditions)->GetNumberField(TEXT("target_kmh"));
		OutSettings.Throttle   = (*Conditions)->GetNumberField(TEXT("throttle"));
		return true;
	}
}

// ---------------------------------------------------------------------------
// トルクカーブ（PCHIP 補間）が Python と一致するか
// ---------------------------------------------------------------------------
//
// **物理モデルより先にここを検査する。** 補間が違っていると 0-100km/h も
// ずれるが、その場合に原因が補間なのか物理なのか切り分けられない。

IMPLEMENT_SIMPLE_AUTOMATION_TEST(
	FZN6TorqueCurveMatchesPython,
	"ZN6.Physics.トルクカーブがPython実装と一致する",
	EAutomationTestFlags::EditorContext | EAutomationTestFlags::EngineFilter)

bool FZN6TorqueCurveMatchesPython::RunTest(const FString& Parameters)
{
	FString Error;

	TSharedPtr<FJsonObject> Reference;
	if (!LoadJsonObject(ReferenceJsonPath(), Reference, Error))
	{
		AddError(FString::Printf(
			TEXT("参照値が無い。先に `python3 Tools/export_reference.py` を実行すること。%s"), *Error));
		return false;
	}

	ZN6::FVehicleData Data;
	if (!Data.LoadFromFile(VehicleJsonPath(), Error))
	{
		AddError(Error);
		return false;
	}

	ZN6::FEngine Engine;
	if (!Engine.Init(Data, Error))
	{
		AddError(FString::Printf(TEXT("エンジンを初期化できない: %s"), *Error));
		return false;
	}

	const TSharedPtr<FJsonObject>* CurveNode = nullptr;
	if (!Reference->TryGetObjectField(TEXT("torque_curve"), CurveNode) || CurveNode == nullptr)
	{
		AddError(TEXT("参照 JSON に torque_curve が無い"));
		return false;
	}

	const TArray<TSharedPtr<FJsonValue>>* Rpms = nullptr;
	const TArray<TSharedPtr<FJsonValue>>* Torques = nullptr;
	if (!(*CurveNode)->TryGetArrayField(TEXT("rpm"), Rpms) ||
	    !(*CurveNode)->TryGetArrayField(TEXT("wot_torque_nm"), Torques) ||
	    Rpms == nullptr || Torques == nullptr || Rpms->Num() != Torques->Num())
	{
		AddError(TEXT("参照 JSON の torque_curve が壊れている"));
		return false;
	}

	// 許容差 1e-9 N*m。**これは「だいたい合っている」ための値ではなく、
	// 浮動小数の丸め差だけを許すための値。** 補間方式が違えばここは
	// 0.1 N*m 単位でずれるので確実に落ちる。
	constexpr double ToleranceNm = 1e-9;

	int32 Mismatches = 0;
	for (int32 Index = 0; Index < Rpms->Num(); ++Index)
	{
		const double Rpm = (*Rpms)[Index]->AsNumber();
		const double Expected = (*Torques)[Index]->AsNumber();
		const double Actual = Engine.WotTorqueNm(Rpm);

		if (FMath::Abs(Actual - Expected) > ToleranceNm)
		{
			++Mismatches;
			if (Mismatches <= 5)
			{
				AddError(FString::Printf(
					TEXT("%.0f rpm: Python %.9f N*m に対し C++ %.9f N*m（差 %.3e）"),
					Rpm, Expected, Actual, Actual - Expected));
			}
		}
	}

	if (Mismatches > 0)
	{
		AddError(FString::Printf(
			TEXT("トルクカーブが %d/%d 点で一致しない。PCHIP 補間の実装を疑うこと")
			TEXT("（4,000rpm 付近の谷が最も差が出る）。"), Mismatches, Rpms->Num()));
		return false;
	}
	return true;
}

// ---------------------------------------------------------------------------
// 0-100km/h が Python と一致するか（Phase 8 の判定基準）
// ---------------------------------------------------------------------------

IMPLEMENT_SIMPLE_AUTOMATION_TEST(
	FZN6Acceleration0100MatchesPython,
	"ZN6.Physics.0-100km-hがPython実装と一致する",
	EAutomationTestFlags::EditorContext | EAutomationTestFlags::EngineFilter)

bool FZN6Acceleration0100MatchesPython::RunTest(const FString& Parameters)
{
	FString Error;

	TSharedPtr<FJsonObject> Reference;
	if (!LoadJsonObject(ReferenceJsonPath(), Reference, Error))
	{
		AddError(FString::Printf(
			TEXT("参照値が無い。先に `python3 Tools/export_reference.py` を実行すること。%s"), *Error));
		return false;
	}

	ZN6::FAccelerationSettings Settings;
	if (!ReadSettings(Reference, Settings, Error))
	{
		AddError(Error);
		return false;
	}

	ZN6::FVehicleData Data;
	if (!Data.LoadFromFile(VehicleJsonPath(), Error))
	{
		AddError(Error);
		return false;
	}

	ZN6::FLongitudinalModel Model;
	if (!Model.Init(Data, Error))
	{
		AddError(FString::Printf(TEXT("縦断モデルを初期化できない: %s"), *Error));
		return false;
	}

	const ZN6::FAccelerationResult Result = Model.Accelerate(Settings);

	if (!Result.bReachedTarget)
	{
		AddError(TEXT("100km/h に到達しなかった"));
		return false;
	}

	const TSharedPtr<FJsonObject>* Expected = nullptr;
	if (!Reference->TryGetObjectField(TEXT("acceleration_0_100_kmh"), Expected) || Expected == nullptr)
	{
		AddError(TEXT("参照 JSON に acceleration_0_100_kmh が無い"));
		return false;
	}

	const double ExpectedTimeS = (*Expected)->GetNumberField(TEXT("time_s"));
	const double ExpectedDistanceM = (*Expected)->GetNumberField(TEXT("distance_m"));
	const int32 ExpectedShiftCount = static_cast<int32>((*Expected)->GetNumberField(TEXT("shift_count")));

	// 時間の許容差は dt の半分。**「実測に近いか」ではなく「同じ計算をしたか」の
	// 判定なので、dt 未満の一致を要求してよい。**
	const double TimeToleranceS = Settings.DtS * 0.5;

	bool bPassed = true;

	if (FMath::Abs(Result.TimeToTargetS - ExpectedTimeS) > TimeToleranceS)
	{
		AddError(FString::Printf(
			TEXT("0-100km/h が一致しない: Python %.4f s / C++ %.4f s（差 %.4f s、許容 %.4f s）"),
			ExpectedTimeS, Result.TimeToTargetS, Result.TimeToTargetS - ExpectedTimeS, TimeToleranceS));
		bPassed = false;
	}

	// 距離は速度の積分なので、時間より緩い許容差を取る（1cm）
	if (FMath::Abs(Result.DistanceAtTargetM - ExpectedDistanceM) > 0.01)
	{
		AddError(FString::Printf(
			TEXT("到達距離が一致しない: Python %.4f m / C++ %.4f m"),
			ExpectedDistanceM, Result.DistanceAtTargetM));
		bPassed = false;
	}

	// **変速回数は独立した指標。** 0-100km/h だけを見ているとファイナルの
	// 取り違えを見落とす（Tests/test_longitudinal.py の
	// test_0_100だけを見るとファイナルの取り違えを見落とす と同じ理由。憲法ルール10）。
	if (Result.ShiftPoints.Num() != ExpectedShiftCount)
	{
		AddError(FString::Printf(
			TEXT("変速回数が一致しない: Python %d 回 / C++ %d 回"),
			ExpectedShiftCount, Result.ShiftPoints.Num()));
		bPassed = false;
	}

	return bPassed;
}

// ---------------------------------------------------------------------------
// 保存則と拘束条件（Physics Validity / SPEC_ZN6.md §8.4）
// ---------------------------------------------------------------------------

IMPLEMENT_SIMPLE_AUTOMATION_TEST(
	FZN6PhysicsValidity,
	"ZN6.Physics.保存則と拘束条件を破らない",
	EAutomationTestFlags::EditorContext | EAutomationTestFlags::EngineFilter)

bool FZN6PhysicsValidity::RunTest(const FString& Parameters)
{
	FString Error;

	ZN6::FVehicleData Data;
	if (!Data.LoadFromFile(VehicleJsonPath(), Error))
	{
		AddError(Error);
		return false;
	}

	ZN6::FLongitudinalModel Model;
	if (!Model.Init(Data, Error))
	{
		AddError(FString::Printf(TEXT("縦断モデルを初期化できない: %s"), *Error));
		return false;
	}

	ZN6::FAccelerationSettings Settings;
	for (const FString& Problem : Model.CheckPhysicsValidity(Settings))
	{
		AddError(Problem);
	}
	return true;
}

// ---------------------------------------------------------------------------
// この結果を実測比較に使ってはいけないこと
// ---------------------------------------------------------------------------
//
// Tests/test_longitudinal.py::test_この結果は検証対象にできない と同じ趣旨。
// **issue #3 が閉じるまでこのテストは通り続けるべき。**

IMPLEMENT_SIMPLE_AUTOMATION_TEST(
	FZN6ResultIsNotValidatable,
	"ZN6.Physics.この結果は実測比較に使えない",
	EAutomationTestFlags::EditorContext | EAutomationTestFlags::EngineFilter)

bool FZN6ResultIsNotValidatable::RunTest(const FString& Parameters)
{
	FString Error;

	ZN6::FVehicleData Data;
	if (!Data.LoadFromFile(VehicleJsonPath(), Error))
	{
		AddError(Error);
		return false;
	}

	ZN6::FLongitudinalModel Model;
	if (!Model.Init(Data, Error))
	{
		AddError(FString::Printf(TEXT("縦断モデルを初期化できない: %s"), *Error));
		return false;
	}

	ZN6::FAccelerationSettings Settings;
	const ZN6::FAccelerationResult Result = Model.Accelerate(Settings);

	TestFalse(TEXT("assumed が混ざっている限り validatable であってはいけない"), Result.bValidatable);
	TestTrue(TEXT("confidence は 0.40 未満のはず"), Result.Confidence < 0.40);

	// **信頼度が入力の最小値を超えていないこと。** ここが崩れると、
	// 弱いデータから強い結論を出せてしまう。
	double Weakest = 1.0;
	for (const TPair<FString, ZN6::FParam>& Entry : Data.GetAccessed())
	{
		Weakest = FMath::Min(Weakest, Entry.Value.Confidence);
	}
	TestEqual(TEXT("結果の信頼度が入力の最小値と一致する"), Result.Confidence, Weakest);

	return true;
}

// ---------------------------------------------------------------------------
// unknown を読んだら止まること
// ---------------------------------------------------------------------------
//
// **デフォルト値で代用しないことの検査**（憲法ルール14）。
// C++ は Python と違って「うっかり 0.0 が入る」事故が起きやすいので、
// ここを明示的に固定しておく。

IMPLEMENT_SIMPLE_AUTOMATION_TEST(
	FZN6UnknownParameterStops,
	"ZN6.Physics.unknownを読んだら止まる",
	EAutomationTestFlags::EditorContext | EAutomationTestFlags::EngineFilter)

bool FZN6UnknownParameterStops::RunTest(const FString& Parameters)
{
	FString Error;

	ZN6::FVehicleData Data;
	if (!Data.LoadFromFile(VehicleJsonPath(), Error))
	{
		AddError(Error);
		return false;
	}

	// suspension.damper_front は "unknown"（2026-08-31 時点）
	double Value = -12345.0;
	const bool bRead = Data.GetValue(TEXT("suspension.damper_front"), TEXT("N*s/m"), Value, Error);

	TestFalse(TEXT("unknown の項目は読めてはいけない"), bRead);
	TestTrue(TEXT("エラーメッセージに unknown と書かれている"), Error.Contains(TEXT("unknown")));
	TestEqual(TEXT("失敗時に出力変数を書き換えていない"), Value, -12345.0);

	return true;
}

#endif // WITH_DEV_AUTOMATION_TESTS
