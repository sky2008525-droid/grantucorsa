using UnrealBuildTool;

public class ZN6DigitalTwinTarget : TargetRules
{
	public ZN6DigitalTwinTarget(TargetInfo Target) : base(Target)
	{
		Type = TargetType.Game;
		DefaultBuildSettings = BuildSettingsVersion.V7;
		IncludeOrderVersion = EngineIncludeOrderVersion.Latest;

		ExtraModuleNames.Add("ZN6DigitalTwin");
	}
}
