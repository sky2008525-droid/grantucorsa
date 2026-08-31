// vehicle.json の読み取り（Physics/vehicle_data.py の移植）。
//
// **物理コードが数値をハードコードしないための唯一の入口。**
//
// このクラスが守っていること（.claude/rules/physics.md）:
//
//   - "unknown" を読んだら失敗として止まる。デフォルト値で代用しない（憲法ルール14）
//   - 要求した単位と保存されている単位が違えば失敗。定義済みの変換のみ通す（ルール5・13）
//   - 読んだ全パラメータの confidence を記録し、**結果の信頼度が入力の最小値を
//     超えないようにする**
//
// 最後の点が重要。トルクカーブが assumed / 0.30 なら、そこから計算した
// 0-100km/h の信頼度も 0.30 を超えない。**結果だけを見て「実測と一致した」と
// 言えなくなる**のが狙い（Docs/AGENT_TOPOLOGY.md §3）。
//
// **例外を使わない代わりに、失敗は必ず戻り値で返す。**
// UE のコードベースは例外を無効化してビルドされるため。呼び出し側が結果を
// 無視できないよう、値は出力引数で受け取る形にしてある。

#pragma once

#include "CoreMinimal.h"
#include "Dom/JsonObject.h"

namespace ZN6
{
	/** vehicle.json の1項目。 */
	struct FParam
	{
		FString Path;
		FString Unit;
		FString Source;
		double Confidence = 0.0;
	};

	class FVehicleData
	{
	public:
		/**
		 * vehicle.json を読み込む。
		 *
		 * @param JsonPath  vehicle.json への絶対パス
		 * @param OutError  失敗した理由（成功時は空）
		 * @return 読めたら true
		 */
		bool LoadFromFile(const FString& JsonPath, FString& OutError);

		/**
		 * 数値を取得する。**単位を必ず指定させる。**
		 *
		 * 保存単位と一致しない場合、TryConvert に定義があれば変換し、
		 * 無ければ失敗する。"unknown" の項目も失敗する。
		 */
		bool GetValue(const FString& DottedPath, const FString& Unit, double& OutValue, FString& OutError);

		/**
		 * [[x, y], ...] 形式の曲線を取得する（トルクカーブ用）。
		 * x の単位は rpm_unit フィールドから読む。
		 */
		bool GetCurve(const FString& DottedPath, const FString& XUnit, const FString& YUnit,
		              TArray<double>& OutX, TArray<double>& OutY, FString& OutError);

		/**
		 * 測定ノードでない素の文字列を取得する（identity.grade など）。
		 * **数値には使わないこと**（単位検証を通らないため）。
		 */
		bool GetPlainString(const FString& DottedPath, FString& OutValue, FString& OutError);

		/** 読んだ中で最も confidence が低い項目。**計算結果の信頼度はこれを超えない。** */
		const FParam* Weakest() const;

		/** 計算結果に付けてよい confidence の上限。 */
		double ResultConfidence() const;

		/**
		 * この結果を Reality Validator の検証対象にしてよいか。
		 * assumed（0.0-0.39）の値が1つでも混ざっていたら false。
		 */
		bool IsValidatable(double Threshold = 0.40) const;

		/** これまでに読んだ全項目（confidence の伝播を検査するため）。 */
		const TMap<FString, FParam>& GetAccessed() const { return Accessed; }

	private:
		/** ドット区切りパスでノードを辿る。 */
		bool ResolveNode(const FString& DottedPath, TSharedPtr<FJsonValue>& OutNode, FString& OutError) const;

		/** 測定ノードを FParam として取り出し、アクセスを記録する。 */
		bool ReadParam(const FString& DottedPath, TSharedPtr<FJsonObject>& OutObject,
		               FParam& OutParam, FString& OutError);

		TSharedPtr<FJsonObject> Root;
		TMap<FString, FParam> Accessed;
	};
}
