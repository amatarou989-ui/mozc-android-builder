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

## Futatsumugi順位付け用 Sudachi word cost

このBuilderは補正済みSudachi追加辞書から、IME候補順位付け専用の追加成果物も生成します。

- `sudachi_word_cost_v1.bin`: Android向け固定長バイナリ
- `sudachi_word_cost_v1.tsv`: 確認用TSV
- `SUDACHI_WORD_COST_MANIFEST.json`: 生成条件・ハッシュ・統計

キーは `NFKC(reading) + U+001F + NFKC(surface)` をUTF-16 code unit単位で
FNV-1a 64bit化したものです。同じ読み・表記が複数行ある場合は最小costを採用します。
costは「頻度そのもの」ではなくSudachi/Mozc辞書側の選好を示す補助値として使い、
Futatsumugi本体ではTanaka/TUBELEXの実頻度と併用する想定です。

## Futatsumugi向け Mozc候補ランキングメタデータ

この版では、Mozc本体の候補一覧 `CandidateWord` にFutatsumugi専用の
ランキングEvidenceを追加してから `libmozc.so` をビルドします。
新しいJNIメソッドは増やさず、既存の `MozcJNI.evalCommand()` のprotobuf応答から
そのまま読み出せる設計です。

追加フィールドはproto field number `200-211` を使います。既存のMozcクライアントは
未知フィールドとして無視でき、標準フィールド `id/index/key/value/attributes/num_segments_in_candidate`
は変更しません。

| field | 内容 | 主な用途 |
|---|---|---|
| `futatsumugi_lid` / `rid` | 候補左右端の文脈・品詞ID | 接続・品詞Evidence |
| `futatsumugi_cost` | Mozc候補総コスト | Mozc順位より細かい相対評価 |
| `futatsumugi_wcost` | 語・経路側コスト | 語彙的な不自然さ |
| `futatsumugi_structure_cost` | 候補内部の遷移構造コスト | 不自然な語接続の減点 |
| `futatsumugi_content_key/value` | 機能語部分を除いた内容語 | `打ち + ました` 等の構造把握 |
| `futatsumugi_raw_attributes` | `Segment::Candidate` 生bit mask | 公開enumにない内部属性の保持 |
| `futatsumugi_source_info` | 候補生成元の生bit mask | 辞書・予測系Evidenceの診断 |
| `futatsumugi_consumed_key_size` | 消費した読みサイズ | 部分候補の判定 |
| `futatsumugi_inner_segment_boundary` | 内部文節境界の符号化値 | リアルタイム変換の内部構造 |
| `futatsumugi_cost_before_rescoring` | 再スコア前cost | Mozc再ランキングの影響比較 |

詳細なfield numberは `data/futatsumugi_candidate_metadata_fields.tsv` に固定しています。
GitHub ActionsではMozc checkout後に `tools/patch_mozc_candidate_metadata.py` を実行し、
想定しているCandidate構造が見つからない場合は**推測で継続せずビルドを停止**します。
そのためMozcのrefを更新したときも、構造変更を見落としたまま誤ったバイナリを作りません。

### なぜこの項目を出すか

MozcのN-best候補では `structure_cost` が候補内部のノード間遷移を含み、
`content_key/content_value` は内容語と後続の機能語を区別するために使われています。
Futatsumugi側では特定の文字列や「漢字2文字+ました」のようなルールではなく、
これらを弱い減点Evidenceとして利用できます。

`inner_segment_boundary` は4つのUTF-8 byte長
`key/value/content_key/content_value` を各8bitに詰めたMozc内部値です。
通常の候補数だけ必要なら標準field 7の `num_segments_in_candidate` を使用し、
詳細な内部境界が必要な場合だけfield 210を解析してください。

### ビルド成果物の追加

従来の成果物に加えて次をArtifactsへ保存します。

- `MOZC_CANDIDATE_METADATA_PATCH.txt`: パッチ適用結果
- `futatsumugi_candidate_metadata_fields.tsv`: Kotlin側と共有するfield map
- `mozc_pos_id.def`: このMozc refのLID/RID→品詞定義 (`data/dictionary_oss/id.def`)

LID/RIDは数値だけでは意味が分かりにくいため、`mozc_pos_id.def`も同時に保存します。
例えば「助動詞」「特殊・デス」「連用形」のような品詞・活用情報を後から対応付けできます。

`mozc.data` の辞書フォーマットはこの変更では変えません。主な変更対象は
`libmozc.so` に含まれるsession出力とprotobuf定義です。
