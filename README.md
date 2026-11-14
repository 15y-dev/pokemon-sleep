# pokemon-sleep

Pokémon Sleep のポケモン詳細画面を、adb で自動スクロール撮影して縦長の1枚画像(パノラマ)にするツールです。
横スワイプで次のポケモンへ切り替えながら、全匹を連続で撮影できます。

## 仕組み

1. `adb exec-out screencap` で画面を撮影する
2. `adb shell input swipe` で縦にスクロールして、また撮影する
3. 隣り合う2枚をテンプレートマッチングで照合し、何px動いたかを求めて重複を除きながら縦に連結する
4. 最後まで到達したら保存する
5. 連続撮影では、横スワイプで次のポケモンへ切り替えて、一番上までスクロールを戻してから 1〜4 を繰り返す

上下に固定表示されている部分(ヘッダー・フッター)は、最初の2枚の差分から自動検出して、毎回同じ値で切り落とします。

## 必要なもの

- Windows(動作確認環境)
- Python 3(3.15.2 で確認)
- `opencv-python`, `numpy`
- adb(Android SDK Platform-Tools)
- USBデバッグまたはワイヤレスデバッグを有効にした Android 端末(`adb devices` で `device` と表示されること)

## セットアップ

```powershell
cd d:\work\pokemon-sleep
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install opencv-python numpy
```

adb が PATH に無い場合は、実行時に `--adb` でパスを渡します(この環境では `D:\android\platform-tools\adb.exe`)。

端末の接続確認:

```powershell
D:\android\platform-tools\adb.exe devices
```

## 使い方

### run.ps1(この環境の設定をまとめたもの)

adb のパスやスワイプ距離などの引数は `run.ps1` にまとめてあります。通常はこれを使います。

```powershell
.\run.ps1                          # 1匹目から637匹を撮影(前面で実行)
.\run.ps1 -Start 70                # 70匹目の画面を表示した状態から再開
.\run.ps1 -Start 70 -Background    # バックグラウンドで実行(ログは capture_日時.log)
.\run.ps1 -Count 3 -OutDir panoramas_test   # 3匹だけ試す
.\run.ps1 -DryRun                  # 実行せず、組み立てたコマンドだけ表示する
```

| パラメータ | 既定値 | 内容 |
|---|---|---|
| `-Start` | `1` | N匹目の画面を表示した状態から撮影する |
| `-Count` | `637` | 撮影する総数(ポケモンの数) |
| `-OutDir` | `panoramas` | 保存先フォルダ |
| `-Serial` | 自動選択 | 端末のシリアル(USB を優先して選ぶ) |
| `-Background` | オフ | バックグラウンドで実行し、ログをファイルに出す |
| `-DryRun` | オフ | コマンドを表示するだけで実行しない |

- 設定値(adb のパス、`--swipe 0.3 --wait 1.0 --thresh 0.8 --margin 16`)は、`run.ps1` の上部にあります。
- 既に `adb_panorama.py` が動いている間は、多重起動しないようにエラーで止まります。止めるには `Stop-Process -Id <PID>` を使います。
- 実行できないと言われたときは、`powershell -ExecutionPolicy Bypass -File .\run.ps1` で実行してください。

以降の例は、`run.ps1` を使わずに直接コマンドを書く場合です。

### 1匹だけ撮影する

スマホで、撮りたいポケモンの詳細画面を**一番上までスクロールした状態**で表示してから実行します。

```powershell
$env:PYTHONIOENCODING='utf-8'
.\.venv\Scripts\python.exe adb_panorama.py --adb 'D:\android\platform-tools\adb.exe' --swipe 0.3 --wait 1.0 --thresh 0.8 --margin 16 -o panorama.png
```

### 全匹を連続で撮影する

スマホで**1匹目のポケモンの詳細画面**を表示してから実行します。

```powershell
$env:PYTHONIOENCODING='utf-8'
.\.venv\Scripts\python.exe adb_panorama.py --adb 'D:\android\platform-tools\adb.exe' --swipe 0.3 --wait 1.0 --thresh 0.8 --margin 16 --count 637 --out-dir panoramas --skip-existing
```

1匹ごとに次の処理を行い、`panoramas\0001.png`, `0002.png` … と連番で保存します。

1. 横スワイプで次のポケモンに切り替える
2. 画面が動かなくなるまで下方向にフリックして、一番上まで戻す
3. 前のポケモンの画面と比べて、切り替わったかを確認する(切り替わっていなければ1回だけやり直し、それでも同じなら最後のポケモンとみなして終了)
4. 縦スクロールで撮影して保存する

### 途中から再開する

撮影が止まったときは、スマホを**1匹目の画面に戻して**、同じコマンドをもう一度実行します。
`--skip-existing` により、保存済みの番号は撮影せずに横スワイプだけで送り、続きから撮影します。
撮り直したい番号があるときは、その画像ファイルを削除してから実行してください。

### 途中のポケモンから再開する(`--start`)

スマホが途中のポケモン(N匹目)の画面のままなら、1匹目に戻さずに `--start N` で続きから撮影できます。
`--start` を付けると、N匹目の画面をそのまま撮影し、それ以前の番号は横スワイプで送りません。

```powershell
.\.venv\Scripts\python.exe adb_panorama.py --adb 'D:\android\platform-tools\adb.exe' --swipe 0.3 --wait 1.0 --thresh 0.8 --margin 16 --count 637 --start 21 --out-dir panoramas --skip-existing
```

`N` は、保存済みの最後の番号 + 1 にしてください(`0020.png` まであるなら `21`)。
画面に出ているポケモンが、`N-1` 番の次の個体であることを確認してから実行してください。

### お知らせポップアップ

切り替えた先で「おやすみリボン」などの青いお知らせポップアップが出ることがあります。
スクリプトはこれを自動で検出して、OK ボタンをタップして閉じます。
閉じずに放置すると、画面が切り替わらず「最後のポケモン」と誤判定して終了したり、ポップアップの画像を保存してしまうためです。

### 接続が切れたとき

adb が失敗した場合は、5秒おきに最大60回(約5分)、`adb connect` で再接続しながら再試行します。
それでも復旧しない場合はエラーで止まるので、端末を接続し直して `--start` で再開してください。
端末が複数見える場合(USBとワイヤレスの二重認識など)は、USB接続の端末を自動で選びます。
`--serial` で端末を指定することもできます。

## オプション

| オプション | 既定値 | 内容 |
|---|---|---|
| `--adb` | `adb` | adb 実行ファイルのパス |
| `--serial` | 自動選択 | 使う端末のシリアル(`adb devices` の1列目)。省略時は USB を優先して選ぶ |
| `--start` | `1` | N匹目から撮影を始める。N匹目の画面を表示した状態で実行する |
| `--swipe` | `0.4` | 1回の縦スワイプ距離(画面高さ比)。小さいほど照合に成功しやすい |
| `--duration` | `1000` | 縦スワイプの時間(ms)。長いほど慣性が出にくい |
| `--wait` | `0.6` | スワイプ後の待ち時間(秒) |
| `--max-steps` | `50` | 1匹あたりの最大スクロール回数 |
| `--thresh` | `0.9` | 照合の一致度のしきい値。一致が見つからないときは下げる |
| `--top` / `--bottom` | 自動検出 | 上下の固定部分の高さ(px)。自動検出がうまくいかないときに指定 |
| `--margin` | `12` | 自動検出した固定部分に足す余白(px)。継ぎ目の線を防ぐ |
| `-o`, `--out` | `panorama.png` | `--count 1` のときの保存先 |
| `--count` | `1` | 撮影するポケモンの数。2以上で連続撮影 |
| `--out-dir` | `panoramas` | 連続撮影の保存先フォルダ |
| `--skip-existing` | オフ | 保存先に既にあるファイルは撮影せずに飛ばす |
| `--direction` | `left` | 次のポケモンへ切り替える横スワイプの向き(`left` / `right`) |
| `--hswipe-y` | `0.5` | 横スワイプを行う高さ(画面高さ比) |
| `--hswipe-duration` | `200` | 横スワイプの時間(ms) |
| `--switch-wait` | `1.2` | 切り替え後の待ち時間(秒) |
| `--top-duration` | `200` | 一番上に戻すフリックの時間(ms) |
| `--same-thresh` | `0.003` | 前のポケモンとの変化率がこの値未満なら、切り替わっていないとみなす |

## 注意

- 撮影中は、スマホの画面を消さず、他の操作もしないでください。
- 1匹あたり約15秒かかります。637匹で約2.7時間が目安です。
- 実行中に横スワイプやスクロールが想定どおりに動かなくなると、画像がずれることがあります。撮影後に枚数と内容を確認してください。
- 画面サイズや端末が変わると、固定部分の検出値などが変わります。最初の1〜3匹を `--count 3` で試して確認することをおすすめします。

## トラブルシューティング

- **「一致が見つからず停止」と出る**: `--swipe` を小さくする(例: `0.3`)、`--wait` を長くする、`--thresh` を下げる(例: `0.8`)。
- **継ぎ目に線が入る**: `--margin` を大きくするか、`--top` / `--bottom` を手動で指定する。
- **文字化けする**: 実行前に `$env:PYTHONIOENCODING='utf-8'` を設定する。
- **`adb` が見つからない**: `--adb` にフルパスを指定する。
- **画面のスワイプが効かない**: 端末側の「USBデバッグ(セキュリティ設定)」で、入力のシミュレーションを許可する(機種による)。

## OCR(Google Cloud Vision API)

撮影した画像を1枚ずつ Google Cloud Vision API で OCR して、結果を画像1枚につき1ファイル(`ocr\0001.json` など)で保存します。
保存するのは API の応答そのまま(文字ごとの位置情報つき)です。あとから取りたい項目が増えても、OCR をやり直さずに済みます。

### 準備

1. Google Cloud のプロジェクトで **Cloud Vision API を有効にする**(キーのサービスアカウントと同じプロジェクト)
2. **サービスアカウントのキー(JSON)** を、`ocr_vision.py` と同じフォルダに置く

`"type": "service_account"` の JSON を、同じフォルダから自動で探して使います(ファイル名は問いません)。
別の場所に置く場合は `--key-file パス` で指定します。
キーが無いときは、`--api-key` または環境変数 `GOOGLE_API_KEY`(API キー)にもフォールバックします。

追加のインストールは不要です。Python の標準ライブラリだけで、サービスアカウントの認証(JWT の署名とトークン取得)と REST API の呼び出しを行います。
アクセストークンは1時間ほど有効で、期限が近づくと自動で取り直します。

> **キーの JSON は秘密情報です。** git にコミットしたり、他人に渡したりしないでください。
> 画面や通信で内容を出すことは、このスクリプトでは一切しません(認証に使うメールアドレスとファイル名だけ表示します)。

### 使い方

```powershell
$env:PYTHONIOENCODING='utf-8'
.\.venv\Scripts\python.exe ocr_vision.py --dry-run          # API を呼ばずに、処理対象だけ表示する
.\.venv\Scripts\python.exe ocr_vision.py --limit 3          # 最初の3枚だけ試す
.\.venv\Scripts\python.exe ocr_vision.py                    # panoramas の全画像を処理する
.\.venv\Scripts\python.exe ocr_vision.py --force --limit 1  # 保存済みでも取り直して上書きする
```

- 保存済み(`ocr\NNNN.json` がある)の画像は飛ばします。途中で止まっても、同じコマンドで続きから再開できます。
- 保存は一時ファイル経由なので、途中で止めても壊れたファイルは残りません。
- 回数制限(429)やサーバーエラー(5xx)、通信エラーは、待ちながら自動で再試行します。
- 認証エラー(401)は、トークンの期限切れかもしれないので、1回だけ取り直して再試行します。
- キーの権限不足、API が未有効、リクエスト不正(HTTP 400/401/403)のときは、続けても無駄なのですぐに中断します。
- `--dry-run` でも、使う認証(メールアドレスとキーのファイル名)を表示します。通信はしません。
- 1枚だけ失敗した場合は、その画像を飛ばして続けます。最後に失敗した画像の一覧を表示し、終了コード 1 で終わります。もう一度実行すれば、失敗した分だけ取り直します。

| オプション | 既定値 | 内容 |
|---|---|---|
| `--in-dir` | `panoramas` | OCR する画像のフォルダ |
| `--out-dir` | `ocr` | 結果(JSON)の保存先 |
| `--pattern` | `*.png` | 対象のファイル名パターン |
| `--key-file` | 同じフォルダを自動検索 | サービスアカウントの JSON キーのパス |
| `--api-key` | 環境変数 `GOOGLE_API_KEY` | API キー(キー JSON が無いとき用。履歴に残るので環境変数を推奨) |
| `--feature` | `DOCUMENT_TEXT_DETECTION` | OCR の種類(`TEXT_DETECTION` も選べる) |
| `--lang` | `ja` | 言語のヒント(カンマ区切りで複数可) |
| `--limit` | なし | 今回処理する枚数の上限 |
| `--force` | オフ | 保存済みでも取り直す |
| `--dry-run` | オフ | API を呼ばずに、対象だけ表示する |
| `--sleep` | `0.1` | 1枚ごとの待ち時間(秒) |
| `--timeout` | `60` | 1回の通信のタイムアウト(秒) |
| `--retries` | `5` | 一時的なエラーの再試行回数 |

### JSON の中身

```json
{
  "source": "0001.png",
  "feature": "DOCUMENT_TEXT_DETECTION",
  "languageHints": ["ja"],
  "response": { "fullTextAnnotation": { "text": "...", "pages": [ ... ] }, "textAnnotations": [ ... ] }
}
```

文字列全体は `response.fullTextAnnotation.text`、単語ごとの位置は `response.textAnnotations` にあります。

### 料金と注意

- 文字検出は、月1000枚まで無料、以降は1000枚あたり約1.5ドルが目安です(最新の料金は Google Cloud の料金表で確認してください)。629枚なら、無料枠の範囲に収まります。
- 画像は Google のサーバーに送信されます。
- 同じ画像を何度も処理しないよう、まず `--limit 3` で確認してから全件を実行してください。

## 項目の抽出(CSV / JSON)

OCR 済みの JSON(`ocr` フォルダ)から、画面の項目を取り出して表にします。通信はしません(ローカルで完結)。
文字の位置(座標)を使って、ラベルの右隣や枠の中の値だけを読みます。追加インストールは不要です。

```powershell
$env:PYTHONIOENCODING='utf-8'
.\.venv\Scripts\python.exe extract_fields.py                  # 全件
.\.venv\Scripts\python.exe extract_fields.py --ids 1,2,10-12  # 指定した番号だけ(ファイルは書かず、画面に表示)
```

| 出力 | 内容 |
|---|---|
| `pokemon.csv` | 1匹1行。Excel で開ける(UTF-8 BOM つき) |
| `pokemon.json` | 同じ内容(JSON) |
| `review.csv` | 要確認の値。どの画像の、どの項目が、なぜ怪しいか(重要度つき) |

### 取り出せる項目
- **基本**: `sp`、`level`、`name_text`(名前の欄。ニックネームのこともある)、`candy_species`(「◯◯のアメ」の◯◯。進化前の系統名)、`name_is_candy_species`、`exp_to_next`、`candy_count`、`mood_percent`、`specialty`(とくいなもの)、`ribbon_present`、`has_evolve_button`
- **おてつだい能力**: `berry_count`、`ingredient1〜3_count`、`ingredient2_locked`、`ingredient3_locked`、`help_interval_sec`、`max_carry`
- **スキル**: `main_skill`、`main_skill_level`、`main_skill_desc`、`sub_skill1〜5`、`sub_skill1〜5_unlocked`
- **詳細ステータス**: `nature`、`nature_up`、`nature_down`、`nature_neutral`、`met_date`、`field`、`field_ex`、`sleep_minutes`

### 取り出せないもの(文字ではなくアイコンのため)
きのみ・食材の種類、タイプ、性別、リボンの種類、お気に入りの星、色違いなど。見本画像との照合が別途必要です。

### 補正と要確認
- 名前の候補が決まっている項目(せいかく、サブスキル、フィールドなど)は、一覧と照合して、OCR の誤読を直します。補正したものは `review.csv` に `low` で残ります。
- 値が取れない、明らかにおかしい、矛盾する場合は `high` / `mid` で `review.csv` に出ます。
- 解放済みか(食材2・3、サブスキル2〜5)は、レベルと鍵バッジから判断します。
- 一緒に眠った時間は、手前の「Zz」のアイコンを位置で切り離し、「分」の誤読(例: 053)を補正し、出会ってからの日数より長い値を検出します(`--as-of` で基準日を指定可)。

## ファイル

- `adb_panorama.py`: 撮影スクリプト本体
- `ocr_vision.py`: Google Cloud Vision API で OCR して、結果を1枚ずつ JSON で保存するスクリプト
- `ocr\`: OCR 結果の保存先(自動作成)
- `extract_fields.py`: OCR 結果から項目を取り出して `pokemon.csv` / `pokemon.json` / `review.csv` を作るスクリプト
- `panoramas\`: 連続撮影の出力先(自動作成)
- `.venv\`: Python の仮想環境
