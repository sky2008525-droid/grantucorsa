// 4輪モデル（Physics/vehicle.py）の C++ 移植と Python 実装の突き合わせ。
//
// **縦断モデルだけでは 4輪モデルの検査にならない。** 横力・ヨー・LSD・
// 左右の荷重移動・複合スリップは直進では一切効かないため、参照シナリオに
// 旋回と制動を含めてある（Tools/export_reference.py の VEHICLE_SCENARIOS）。
//
// 期待値はここに書かない。Python 実装が唯一の基準
// （理由は ZN6LongitudinalTest.cpp の先頭コメントを参照）。

#include "CoreMinimal.h"
#include "Misc/AutomationTest.h"
#include "Misc/FileHelper.h"
#include "Misc/Paths.h"
#include "Dom/JsonObject.h"
#include "Serialization/JsonReader.h"
#include "Serialization/JsonSerializer.h"

#include "Physics/ZN6Vehicle.h"
#include "Physics/ZN6VehicleData.h"

#if WITH_DEV_AUTOMATION_TESTS

namespace
{
	FString VehicleJsonPathForVehicleTest()
	{
		return FPaths::ConvertRelativePathToFull(FPaths::ProjectDir() / TEXT("../..")) /
		       TEXT("Vehicles/ZN6/vehicle.json");
	}

	FString ReferenceJsonPathForVehicleTest()
	{
		return FPaths::ConvertRelativePathToFull(
			FPaths::ProjectDir() / TEXT("Reference/longitudinal_reference.json"));
	}

	bool LoadReference(TSharedPtr<FJsonObject>& OutObject, FString& OutError)
	{
		FString Text;
		const FString Path = ReferenceJsonPathForVehicleTest();
		if (!FFileHelper::LoadFileToString(Text, *Path))
		{
			OutError = FString::Printf(
				TEXT("参照値が無い: %s。先に `python3 Tools/export_reference.py` を実行すること。"), *Path);
			return false;
		}
		const TSharedRef<TJsonReader<TCHAR>> Reader = TJsonReaderFactory<TCHAR>::Create(Text);
		if (!FJsonSerializer::Deserialize(Reader, OutObject) || !OutObject.IsValid())
		{
			OutError = FString::Printf(TEXT("参照値を JSON として解釈できない: %s"), *Path);
			return false;
		}
		return true;
	}

	/**
	 * ギア名（"1"-"6" / "N" / "R"）を添字へ。
	 *
	 * Python 側は段を文字列で持ち、C++ は添字で持つ。**この対応表が
	 * 両者の唯一の接点**なので、片方に段を足したらここも足すこと。
	 */
	bool GearNameToIndex(const FString& GearName, int32& OutIndex)
	{
		if (GearName == TEXT("N")) { OutIndex = ZN6::GearNeutral; return true; }
		if (GearName == TEXT("R")) { OutIndex = ZN6::GearReverse; return true; }
		for (int32 Index = 0; Index < ZN6::ForwardGearCount; ++Index)
		{
			if (GearName == ZN6::ForwardGears[Index])
			{
				OutIndex = Index;
				return true;
			}
		}
		return false;
	}
}

IMPLEMENT_SIMPLE_AUTOMATION_TEST(
	FZN6VehicleMatchesPython,
	"ZN6.Physics.4輪モデルがPython実装と一致する",
	EAutomationTestFlags::EditorContext | EAutomationTestFlags::EngineFilter)

bool FZN6VehicleMatchesPython::RunTest(const FString& Parameters)
{
	FString Error;

	TSharedPtr<FJsonObject> Reference;
	if (!LoadReference(Reference, Error))
	{
		AddError(Error);
		return false;
	}

	const TSharedPtr<FJsonObject>* Scenarios = nullptr;
	if (!Reference->TryGetObjectField(TEXT("vehicle_scenarios"), Scenarios) || Scenarios == nullptr)
	{
		AddError(TEXT("参照 JSON に vehicle_scenarios が無い。export_reference.py が古い可能性がある。"));
		return false;
	}

	// 許容差は**相対 1e-7 + 絶対の下限**でとる。
	//
	// 数千ステップぶんの丸め差が積み上がるため固定の絶対値では判定できない
	// （engine_omega は 640 rad/s あり、相対 1e-8 でも絶対 6e-6 になる）。
	// 一方、**物理が違えば差は相対 1e-3 以上**に出るので、この幅なら
	// 「同じ計算をしている」と「違う計算をしている」を区別できる。
	//
	// double の相対精度は約 1e-16。1e-7 はその 10^9 倍で、数千ステップの
	// 誤差増幅を見込んでも十分に狭い。
	constexpr double RelativeTolerance = 1e-7;
	constexpr double AbsoluteFloor = 1e-9;

	bool bAllPassed = true;

	for (const TPair<FString, TSharedPtr<FJsonValue>>& Entry : (*Scenarios)->Values)
	{
		const FString ScenarioName = Entry.Key;
		const TSharedPtr<FJsonObject> Scenario = Entry.Value->AsObject();
		if (!Scenario.IsValid())
		{
			AddError(FString::Printf(TEXT("シナリオ %s が壊れている"), *ScenarioName));
			bAllPassed = false;
			continue;
		}

		// --- 参照 JSON からシナリオの条件を読む（C++ 側に書かない）---
		const double InitialSpeedMps = Scenario->GetNumberField(TEXT("initial_speed_mps"));
		const int32 Steps = static_cast<int32>(Scenario->GetNumberField(TEXT("steps")));
		const double DtS = Scenario->GetNumberField(TEXT("dt_s"));
		const int32 MidpointStep = static_cast<int32>(Scenario->GetNumberField(TEXT("midpoint_step")));
		const FString GearName = Scenario->GetStringField(TEXT("gear"));

		int32 GearIndex = 0;
		if (!GearNameToIndex(GearName, GearIndex))
		{
			AddError(FString::Printf(TEXT("シナリオ %s: ギア '%s' が解釈できない"), *ScenarioName, *GearName));
			bAllPassed = false;
			continue;
		}

		const TSharedPtr<FJsonObject>* ControlNode = nullptr;
		if (!Scenario->TryGetObjectField(TEXT("control"), ControlNode) || ControlNode == nullptr)
		{
			AddError(FString::Printf(TEXT("シナリオ %s に control が無い"), *ScenarioName));
			bAllPassed = false;
			continue;
		}

		ZN6::FControlInput Control;
		Control.GearIndex = GearIndex;
		Control.Throttle  = (*ControlNode)->GetNumberField(TEXT("throttle"));
		Control.Brake     = (*ControlNode)->GetNumberField(TEXT("brake"));
		Control.SteerRad  = (*ControlNode)->GetNumberField(TEXT("steer_rad"));
		Control.Clutch    = (*ControlNode)->GetNumberField(TEXT("clutch"));
		Control.Handbrake = (*ControlNode)->GetNumberField(TEXT("handbrake"));

		// --- C++ 側で同じシナリオを回す ---
		// **シナリオごとに VehicleData を作り直す。** Python 側も
		// run_vehicle_scenario で毎回 VehicleData() を作っており、
		// Vehicle が持つ前ステップ加速度も初期化されるため。
		ZN6::FVehicleData Data;
		if (!Data.LoadFromFile(VehicleJsonPathForVehicleTest(), Error))
		{
			AddError(Error);
			return false;
		}

		ZN6::FVehicle Vehicle;
		if (!Vehicle.Init(Data, /*bUseLsd=*/true, Error))
		{
			AddError(FString::Printf(TEXT("4輪モデルを初期化できない: %s"), *Error));
			return false;
		}

		ZN6::FVehicleState State = Vehicle.InitialState(InitialSpeedMps, GearIndex);
		ZN6::FVehicleOutputs Outputs;

		ZN6::FVehicleState MidpointState;
		ZN6::FVehicleOutputs MidpointOutputs;

		for (int32 Step = 0; Step < Steps; ++Step)
		{
			ZN6::FVehicleState NextState;
			Vehicle.Step(State, Control, DtS, NextState, Outputs);
			State = NextState;

			if (Step + 1 == MidpointStep)
			{
				MidpointState = State;
				MidpointOutputs = Outputs;
			}
		}

		// --- 参照値と比較する ---
		const TSharedPtr<FJsonObject>* SnapshotsNode = nullptr;
		if (!Scenario->TryGetObjectField(TEXT("snapshots"), SnapshotsNode) || SnapshotsNode == nullptr)
		{
			AddError(FString::Printf(TEXT("シナリオ %s に snapshots が無い"), *ScenarioName));
			bAllPassed = false;
			continue;
		}

		auto CompareSnapshot =
			[this, &ScenarioName, &bAllPassed](const TSharedPtr<FJsonObject>& Expected,
			                                   const ZN6::FVehicleState& Actual,
			                                   const ZN6::FVehicleOutputs& ActualOutputs,
			                                   const TCHAR* Label)
		{
			auto Check = [this, &ScenarioName, &bAllPassed, Label](
				const TCHAR* Field, double ExpectedValue, double ActualValue)
			{
				const double Tolerance = FMath::Max(
					FMath::Abs(ExpectedValue) * RelativeTolerance, AbsoluteFloor);

				if (FMath::Abs(ActualValue - ExpectedValue) > Tolerance)
				{
					AddError(FString::Printf(
						TEXT("%s [%s] %s: Python %.12g / C++ %.12g（差 %.3e、許容 %.3e）"),
						*ScenarioName, Label, Field, ExpectedValue, ActualValue,
						ActualValue - ExpectedValue, Tolerance));
					bAllPassed = false;
				}
			};

			Check(TEXT("vx_mps"),        Expected->GetNumberField(TEXT("vx_mps")),        Actual.VxMps);
			Check(TEXT("vy_mps"),        Expected->GetNumberField(TEXT("vy_mps")),        Actual.VyMps);
			Check(TEXT("yaw_rate_rads"), Expected->GetNumberField(TEXT("yaw_rate_rads")), Actual.YawRateRads);
			Check(TEXT("x_m"),           Expected->GetNumberField(TEXT("x_m")),           Actual.XM);
			Check(TEXT("y_m"),           Expected->GetNumberField(TEXT("y_m")),           Actual.YM);
			Check(TEXT("heading_rad"),   Expected->GetNumberField(TEXT("heading_rad")),   Actual.HeadingRad);
			Check(TEXT("engine_omega_rads"), Expected->GetNumberField(TEXT("engine_omega_rads")),
			      Actual.EngineOmegaRads);
			Check(TEXT("ax_mps2"),       Expected->GetNumberField(TEXT("ax_mps2")),       ActualOutputs.AxMps2);
			Check(TEXT("ay_mps2"),       Expected->GetNumberField(TEXT("ay_mps2")),       ActualOutputs.AyMps2);

			// 各輪の量。**4輪別々に見ることが重要。** 合計だけ見ていると
			// 左右の荷重移動や LSD のトルク移動が打ち消し合って隠れる。
			const TCHAR* const PerWheelFields[] = {
				TEXT("wheel_omega_rads"), TEXT("tire_fz_n"), TEXT("tire_fx_n"),
				TEXT("tire_fy_n"), TEXT("slip_ratio"), TEXT("slip_angle_rad"),
			};

			for (const TCHAR* FieldName : PerWheelFields)
			{
				const TSharedPtr<FJsonObject>* PerWheel = nullptr;
				if (!Expected->TryGetObjectField(FieldName, PerWheel) || PerWheel == nullptr)
				{
					continue;
				}

				for (int32 Wheel = 0; Wheel < ZN6::WheelCount; ++Wheel)
				{
					const FString WheelName = ZN6::WheelNames[Wheel];
					const double ExpectedValue = (*PerWheel)->GetNumberField(WheelName);

					double ActualValue = 0.0;
					const FString Name(FieldName);
					if (Name == TEXT("wheel_omega_rads")) { ActualValue = Actual.WheelOmegaRads[Wheel]; }
					else if (Name == TEXT("tire_fz_n"))   { ActualValue = ActualOutputs.TireFzN[Wheel]; }
					else if (Name == TEXT("tire_fx_n"))   { ActualValue = ActualOutputs.TireFxN[Wheel]; }
					else if (Name == TEXT("tire_fy_n"))   { ActualValue = ActualOutputs.TireFyN[Wheel]; }
					else if (Name == TEXT("slip_ratio"))  { ActualValue = ActualOutputs.SlipRatio[Wheel]; }
					else if (Name == TEXT("slip_angle_rad")) { ActualValue = ActualOutputs.SlipAngleRad[Wheel]; }

					Check(*FString::Printf(TEXT("%s.%s"), FieldName, *WheelName),
					      ExpectedValue, ActualValue);
				}
			}
		};

		const TSharedPtr<FJsonObject>* MidpointNode = nullptr;
		if ((*SnapshotsNode)->TryGetObjectField(TEXT("midpoint"), MidpointNode) && MidpointNode != nullptr)
		{
			CompareSnapshot(*MidpointNode, MidpointState, MidpointOutputs, TEXT("midpoint"));
		}

		const TSharedPtr<FJsonObject>* FinalNode = nullptr;
		if ((*SnapshotsNode)->TryGetObjectField(TEXT("final"), FinalNode) && FinalNode != nullptr)
		{
			CompareSnapshot(*FinalNode, State, Outputs, TEXT("final"));
		}
	}

	return bAllPassed;
}

// ---------------------------------------------------------------------------
// FR であることの帰結
// ---------------------------------------------------------------------------
//
// Python 側 Tests/test_vehicle_and_lap.py と同じ趣旨。
// **参照値との一致とは別に、向きが正しいことを独立に検査する。**
// 参照値がもし間違っていても、こちらは物理の向きで判定できる。

IMPLEMENT_SIMPLE_AUTOMATION_TEST(
	FZN6RearWheelDriveBehaviour,
	"ZN6.Physics.FRとして荷重移動の向きが正しい",
	EAutomationTestFlags::EditorContext | EAutomationTestFlags::EngineFilter)

bool FZN6RearWheelDriveBehaviour::RunTest(const FString& Parameters)
{
	FString Error;

	ZN6::FVehicleData Data;
	if (!Data.LoadFromFile(VehicleJsonPathForVehicleTest(), Error))
	{
		AddError(Error);
		return false;
	}

	ZN6::FVehicle Vehicle;
	if (!Vehicle.Init(Data, /*bUseLsd=*/true, Error))
	{
		AddError(FString::Printf(TEXT("4輪モデルを初期化できない: %s"), *Error));
		return false;
	}

	double Static[ZN6::WheelCount];
	double Accelerating[ZN6::WheelCount];
	double Braking[ZN6::WheelCount];
	Vehicle.WheelLoadsN(0.0, 0.0, Static);
	Vehicle.WheelLoadsN(4.0, 0.0, Accelerating);
	Vehicle.WheelLoadsN(-4.0, 0.0, Braking);

	const int32 RL = static_cast<int32>(ZN6::EWheel::RL);
	const int32 FL = static_cast<int32>(ZN6::EWheel::FL);

	// **FR なので加速時の荷重は駆動輪（後輪）に乗る。FF とは逆。**
	TestTrue(TEXT("加速時に後輪荷重が静止時より増える"), Accelerating[RL] > Static[RL]);
	TestTrue(TEXT("加速時に前輪荷重が静止時より減る"), Accelerating[FL] < Static[FL]);
	TestTrue(TEXT("制動時に後輪荷重が静止時より減る"), Braking[RL] < Static[RL]);
	TestTrue(TEXT("制動時に前輪荷重が静止時より増える"), Braking[FL] > Static[FL]);

	// 左右: 左旋回（ay 正）では荷重が右へ移る
	double LeftTurn[ZN6::WheelCount];
	Vehicle.WheelLoadsN(0.0, 8.0, LeftTurn);
	const int32 FR = static_cast<int32>(ZN6::EWheel::FR);
	TestTrue(TEXT("左旋回では外輪(右)の荷重が増える"), LeftTurn[FR] > LeftTurn[FL]);

	// 荷重の合計は車重に一致する（片輪も浮いていない条件で）
	double Sum = 0.0;
	for (int32 Wheel = 0; Wheel < ZN6::WheelCount; ++Wheel)
	{
		Sum += Static[Wheel];
	}
	TestTrue(TEXT("静止時の4輪荷重の合計が車重と一致する"), FMath::Abs(Sum - 1230.0 * 9.80665) < 1.0);

	return true;
}

#endif // WITH_DEV_AUTOMATION_TESTS
