using UnrealBuildTool;

public class ZN6DigitalTwinEditorTarget : TargetRules
{
	public ZN6DigitalTwinEditorTarget(TargetInfo Target) : base(Target)
	{
		Type = TargetType.Editor;
		DefaultBuildSettings = BuildSettingsVersion.V7;
		IncludeOrderVersion = EngineIncludeOrderVersion.Latest;

		ExtraModuleNames.Add("ZN6DigitalTwin");
	}
}
