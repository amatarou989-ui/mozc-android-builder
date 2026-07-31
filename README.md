# Mozc Android Builder — Sudachi辞書補正版 v2

## 固定バージョン

- Mozc: `3f235b4eb6fcff7d14ef5f0fb8ee56de7ee4c732`（2026-07-17）
- mozcdic-ut-sudachidict: `c686771bada1d59e9b105c81b29e6ac1a239cb54`（v20260723相当）

## 補正ルール

`data/sudachi_dictionary_corrections.tsv`に、表記と読みの完全一致で53ルールを記録しています。

基準辞書では次の結果になります。

- 元辞書: 1,284,866行
- 削除: 82行
- 追加・分離: 31行
- 補正後: 1,284,815行

## Sudachi更新時の判定

新しいSudachiで一部だけ公式修正されていても、残っている補正は続行します。

| 状態 | 動作 |
|---|---|
| `APPLIED` | 誤った行が残っているため、その行だけ補正 |
| `ALREADY_FIXED` | 誤った行がなく、必要な正しい行も存在するため続行 |
| `NOT_PRESENT` | 誤った行も必要な正しい行もそろわず、自動判断せず停止 |
| `UNEXPECTED` | 基準時より対象が増えており、新しい変更を確認するため停止 |

`expected_matches`は「必ず同数でなければならない件数」ではなく、
**検証済み基準辞書に存在した最大想定件数**として扱います。
件数が減った場合は公式修正済みまたは部分修正として処理し、
件数が増えた場合だけ停止します。

### ルール別の公式修正判定

- `DELETE`: 削除対象が0件なら`ALREADY_FIXED`
- `MOVE`: 元が0件で移動先が存在すれば`ALREADY_FIXED`
- `SPLIT`: 元が0件で分割後の読みが全部存在すれば`ALREADY_FIXED`
- `KEEP_ONLY`: 許可読みが存在し、非許可読みが0件なら`ALREADY_FIXED`
- 正しい移動先・分割先・許可読みが不足する場合は`NOT_PRESENT`

## 実行方法

1. このフォルダ一式をGitHubのBuilderリポジトリへ上書きします。
2. コミットしてプッシュします。
3. GitHubの`Actions`を開きます。
4. `Build Mozc for Android with corrected SudachiDict`を選択します。
5. `Run workflow`を押します。
6. 入力値は初期値のまま実行します。
7. 完了後、`mozc-android-sudachi-corrected-*`をダウンロードします。

## 診断成果物

- `SUDACHI_CORRECTION_REPORT.tsv`: ルールごとの状態と処理件数
- `SUDACHI_CORRECTION_SUMMARY.txt`: 状態別件数と削除・追加件数
- `sudachi_dictionary_corrections.tsv`: 使用した補正表
- `mozcdic-ut-sudachidict-original.txt.bz2`: 加工前辞書

`NOT_PRESENT`または`UNEXPECTED`で停止した場合も、上記診断ファイルをArtifactsへ残します。

## 主な完成成果物

- `mozc.data`
- `native_libs.zip`
- `mozcdic-ut-sudachidict-corrected.txt.bz2`
- `dictionary00-merged-corrected.txt.bz2`
- `MOZC_BUILD_INFO.txt`
- `SHA256SUMS.txt`
