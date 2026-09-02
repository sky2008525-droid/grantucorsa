using UnrealBuildTool;

public class ZN6DigitalTwin : ModuleRules
{
	public ZN6DigitalTwin(ReadOnlyTargetRules Target) : base(Target)
	{
		PCHUsage = PCHUsageMode.UseExplicitOrSharedPCHs;

		// Json / JsonUtilities: vehicle.json を読むため。
		// **物理コードが数値をハードコードしないための唯一の入口**
		// （Physics/ZN6VehicleData）が依存する。
		PublicDependencyModuleNames.AddRange(new string[] { "Core", "CoreUObject", "Engine", "InputCore", "Json" });

		// HUD とメニューは Slate で直接描く。**.uasset を作らない**ので、
		// 画面まわりの変更が全部ソースの差分として残る。
		PrivateDependencyModuleNames.AddRange(new string[] { "Slate", "SlateCore", "ApplicationCore" });

		// Physics/ を "ZN6Units.h" のように書けるようにする
		// （テスト側は "Physics/..." で参照するのでモジュール直下も残す）。
		PublicIncludePaths.Add(ModuleDirectory);
		PublicIncludePaths.Add(System.IO.Path.Combine(ModuleDirectory, "Physics"));
	}
}
