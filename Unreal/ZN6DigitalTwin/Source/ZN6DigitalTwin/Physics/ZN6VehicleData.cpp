#include "ZN6VehicleData.h"

#include "ZN6Units.h"
#include "Misc/FileHelper.h"
#include "Serialization/JsonReader.h"
#include "Serialization/JsonSerializer.h"

namespace ZN6
{
	static const TCHAR* UnknownMarker = TEXT("unknown");

	bool FVehicleData::LoadFromFile(const FString& JsonPath, FString& OutError)
	{
		FString Text;
		if (!FFileHelper::LoadFileToString(Text, *JsonPath))
		{
			OutError = FString::Printf(TEXT("vehicle.json を読めない: %s"), *JsonPath);
			return false;
		}

		const TSharedRef<TJsonReader<TCHAR>> Reader = TJsonReaderFactory<TCHAR>::Create(Text);
		if (!FJsonSerializer::Deserialize(Reader, Root) || !Root.IsValid())
		{
			OutError = FString::Printf(TEXT("vehicle.json を JSON として解釈できない: %s"), *JsonPath);
			return false;
		}

		Accessed.Empty();
		return true;
	}

	bool FVehicleData::ResolveNode(const FString& DottedPath, TSharedPtr<FJsonValue>& OutNode, FString& OutError) const
	{
		if (!Root.IsValid())
		{
			OutError = TEXT("vehicle.json が読み込まれていない");
			return false;
		}

		TArray<FString> Keys;
		DottedPath.ParseIntoArray(Keys, TEXT("."), true);

		TSharedPtr<FJsonObject> Current = Root;
		TSharedPtr<FJsonValue> Node;

		for (int32 Index = 0; Index < Keys.Num(); ++Index)
		{
			if (!Current.IsValid())
			{
				OutError = FString::Printf(TEXT("%s は vehicle.json に存在しない"), *DottedPath);
				return false;
			}

			// UE5.8 で FJsonObject::Values のキー型が FString から
			// UE::FSharedString に変わったため、内部の Values を直接引かず
			// 公開 API を通す（バージョン差の影響を受けにくい）。
			TSharedPtr<FJsonValue> Found = Current->TryGetField(Keys[Index]);
			if (!Found.IsValid())
			{
				OutError = FString::Printf(TEXT("%s は vehicle.json に存在しない"), *DottedPath);
				return false;
			}

			Node = Found;
			Current = (Node->Type == EJson::Object) ? Node->AsObject() : nullptr;
		}

		OutNode = Node;
		return true;
	}

	bool FVehicleData::ReadParam(const FString& DottedPath, TSharedPtr<FJsonObject>& OutObject,
	                             FParam& OutParam, FString& OutError)
	{
		TSharedPtr<FJsonValue> Node;
		if (!ResolveNode(DottedPath, Node, OutError))
		{
			return false;
		}

		// **"unknown" を握りつぶしてデフォルト値を入れないこと。** 値が無いなら
		// そのモデルはまだ動かせない、というのが正しい状態（憲法ルール14）。
		if (Node->Type == EJson::String && Node->AsString() == UnknownMarker)
		{
			OutError = FString::Printf(
				TEXT("%s は unknown。値が取れるまでこのモデルは動かせない。")
				TEXT(" 推測で埋めないこと（憲法ルール14）。出典を取るか、")
				TEXT(" source='assumed' + method + confidence<=0.39 で明示的に置くこと。"),
				*DottedPath);
			return false;
		}

		if (Node->Type != EJson::Object)
		{
			OutError = FString::Printf(TEXT("%s は測定ノードではない（value を持たない）"), *DottedPath);
			return false;
		}

		OutObject = Node->AsObject();
		if (!OutObject->HasField(TEXT("value")))
		{
			OutError = FString::Printf(TEXT("%s は測定ノードではない（value を持たない）"), *DottedPath);
			return false;
		}

		OutParam.Path = DottedPath;
		OutParam.Unit = OutObject->HasField(TEXT("unit")) ? OutObject->GetStringField(TEXT("unit")) : FString();
		OutParam.Source = OutObject->HasField(TEXT("source")) ? OutObject->GetStringField(TEXT("source")) : FString(UnknownMarker);
		OutParam.Confidence = OutObject->HasField(TEXT("confidence")) ? OutObject->GetNumberField(TEXT("confidence")) : 0.0;

		Accessed.Add(DottedPath, OutParam);
		return true;
	}

	bool FVehicleData::GetValue(const FString& DottedPath, const FString& Unit, double& OutValue, FString& OutError)
	{
		TSharedPtr<FJsonObject> Object;
		FParam Param;
		if (!ReadParam(DottedPath, Object, Param, OutError))
		{
			return false;
		}

		if (Param.Unit.IsEmpty())
		{
			OutError = FString::Printf(TEXT("%s は unit を持たない。単位付きで読めない。"), *DottedPath);
			return false;
		}

		const TSharedPtr<FJsonValue> ValueField = Object->TryGetField(TEXT("value"));
		if (!ValueField.IsValid() || ValueField->Type != EJson::Number)
		{
			OutError = FString::Printf(TEXT("%s の value は数値でない"), *DottedPath);
			return false;
		}

		if (!TryConvert(ValueField->AsNumber(), Param.Unit, Unit, OutValue))
		{
			OutError = FString::Printf(
				TEXT("%s: 保存単位 '%s' を要求単位 '%s' にできない。")
				TEXT(" 暗黙の変換を増やす前に、本当に同じ物理量か確認すること。"),
				*DottedPath, *Param.Unit, *Unit);
			return false;
		}
		return true;
	}

	bool FVehicleData::GetCurve(const FString& DottedPath, const FString& XUnit, const FString& YUnit,
	                            TArray<double>& OutX, TArray<double>& OutY, FString& OutError)
	{
		TSharedPtr<FJsonObject> Object;
		FParam Param;
		if (!ReadParam(DottedPath, Object, Param, OutError))
		{
			return false;
		}

		const TArray<TSharedPtr<FJsonValue>>* Pairs = nullptr;
		if (!Object->TryGetArrayField(TEXT("value"), Pairs) || Pairs == nullptr)
		{
			OutError = FString::Printf(TEXT("%s の value が [[x, y], ...] 形式でない"), *DottedPath);
			return false;
		}

		// x の単位は rpm_unit フィールドから読む（Python 側 curve() と同じ）。
		const FString StoredXUnit = Object->HasField(TEXT("rpm_unit"))
			? Object->GetStringField(TEXT("rpm_unit"))
			: XUnit;

		OutX.Empty(Pairs->Num());
		OutY.Empty(Pairs->Num());

		for (const TSharedPtr<FJsonValue>& Entry : *Pairs)
		{
			const TArray<TSharedPtr<FJsonValue>>* Pair = nullptr;
			if (!Entry.IsValid() || !Entry->TryGetArray(Pair) || Pair == nullptr || Pair->Num() != 2)
			{
				OutError = FString::Printf(TEXT("%s の要素が [x, y] の2要素でない"), *DottedPath);
				return false;
			}

			double X = 0.0;
			double Y = 0.0;
			if (!TryConvert((*Pair)[0]->AsNumber(), StoredXUnit, XUnit, X) ||
			    !TryConvert((*Pair)[1]->AsNumber(), Param.Unit, YUnit, Y))
			{
				OutError = FString::Printf(
					TEXT("%s: 単位を変換できない（x: '%s'->'%s' / y: '%s'->'%s'）"),
					*DottedPath, *StoredXUnit, *XUnit, *Param.Unit, *YUnit);
				return false;
			}
			OutX.Add(X);
			OutY.Add(Y);
		}
		return true;
	}

	bool FVehicleData::GetPlainString(const FString& DottedPath, FString& OutValue, FString& OutError)
	{
		TSharedPtr<FJsonValue> Node;
		if (!ResolveNode(DottedPath, Node, OutError))
		{
			return false;
		}

		if (Node->Type == EJson::Object && Node->AsObject()->HasField(TEXT("value")))
		{
			OutError = FString::Printf(
				TEXT("%s は測定ノード。GetPlainString ではなく GetValue を使うこと"), *DottedPath);
			return false;
		}
		if (Node->Type != EJson::String)
		{
			OutError = FString::Printf(TEXT("%s は文字列でない"), *DottedPath);
			return false;
		}
		if (Node->AsString() == UnknownMarker)
		{
			OutError = FString::Printf(TEXT("%s は unknown"), *DottedPath);
			return false;
		}

		OutValue = Node->AsString();
		return true;
	}

	const FParam* FVehicleData::Weakest() const
	{
		const FParam* Lowest = nullptr;
		for (const TPair<FString, FParam>& Entry : Accessed)
		{
			if (Lowest == nullptr || Entry.Value.Confidence < Lowest->Confidence)
			{
				Lowest = &Entry.Value;
			}
		}
		return Lowest;
	}

	double FVehicleData::ResultConfidence() const
	{
		const FParam* Lowest = Weakest();
		return Lowest == nullptr ? 0.0 : Lowest->Confidence;
	}

	bool FVehicleData::IsValidatable(double Threshold) const
	{
		return ResultConfidence() >= Threshold;
	}
}
