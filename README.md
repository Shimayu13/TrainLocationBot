# TrainLocationBot

Python製Discord Bot。既存の路線表示とミニゲームを保持し、JR東日本・都営地下鉄・東武（本線系統を含む）・横浜市営地下鉄の列車位置と運行情報、Apple公式Foundation Models SDKによるローカルAI会話に対応します。京急と従来の運行情報11事業者も残しています。

## 構成

| 場所 | 役割 |
| --- | --- |
| `trainbot/bot.py` | Discordコマンド・起動・ログ |
| `trainbot/config.py` | 環境変数・秘密設定 |
| `trainbot/catalog.py` | 事業者・路線・互換コマンドの登録 |
| `trainbot/odpt.py` | API接続先・認証・タイムアウト・再試行・キャッシュ |
| `trainbot/services.py` | 運行情報整形・追加路線の列車位置表示 |
| `trainbot/ai.py` | Apple公式SDK・会話管理 |
| `trainbot/legacy/` | 既存の路線別表示ロジック |
| `scripts/` | 認証取り込み・起動・launchd設定生成 |
| `tests/` | 通信・コマンド・AI・分割送信の回帰テスト |

ルートの旧ファイル名は互換エントリーポイントです。従来の `python Discordbot1-1.py` や `python TrainLocationJY.py` も仮想環境から実行できます。秘密情報・依存環境・ログはGit管理から除外します。

## Mac miniでの準備

開発機はMacBook Pro M2 Pro、本番機はMac mini M4です。両方ともApple Siliconですが、`.venv`・`.env`・生成済みlaunchd plistはコピーせず、本番機のPython・OS・配置パスに合わせて作り直してください。APIキーは本番機の `.env` に設定し、AI推論とAPI接続を本番機でも検証します。Mac miniでの実測・常時稼働試験は別途必要です。

Python 3.11以上のarm64環境を使用してください。Apple SDKの前提はmacOS 26以降、Xcode 26以降とCommand Line Tools、Apple Intelligence有効化です。モデルのダウンロード完了と利用可否は起動時に確認します。

```sh
cd /Users/yuki/Developer/TrainLocationBot
/opt/homebrew/bin/python3.11 -m venv .venv
.venv/bin/python -m pip install -e '.[ai]'
```

AIを使用しない環境では `pip install -e .` と `AI_ENABLED=false` を使用できます。AIが利用できない場合も鉄道コマンドは稼働します。

開発機と同じ検証済み依存バージョンを使う場合は、仮想環境作成後に `pip install -r requirements-macos.lock.txt` と `pip install --no-deps -e .` を実行します。

## 認証設定

`.env.example` を `.env` にコピーして値を設定し、`chmod 600 .env` を実行します。既存の環境変数が `.env` より優先されます。別ファイルを使う場合は `TRAINBOT_ENV_FILE` に絶対パスを指定してください。

| 環境変数 | 用途 |
| --- | --- |
| `DISCORD_BOT_TOKEN` | Discord Developer PortalのBotトークン |
| `ODPT_CHALLENGE_CONSUMER_KEY` | JR東日本・東武・京急等のチャレンジAPI |
| `ODPT_STANDARD_CONSUMER_KEY` | 横浜市営・東京メトロ等の通常API |
| `ODPT_*_BASE_URL` | 提供先変更時の接続先変更 |
| `AI_ENABLED` | ローカルAI有効化 |
| `AI_MAX_PENDING` | 処理中を含むAI受付上限（初期値4） |
| `AI_MAX_SESSIONS` | 会話保持件数（初期値32） |
| `AI_MAX_TURNS` | 旧設定との互換用。往復数による自動リセットは行わない |
| `AI_SESSION_TTL_SECONDS` | 旧設定との互換用。経過時間による自動リセットは行わない |
| `AI_TIMEOUT_SECONDS` | 推論の制限時間（初期値90秒） |
| `AI_TEMPERATURE` | 表現の多様性（初期値0.7、範囲0〜1） |
| `AI_TOP_P` | ランダム生成の候補確率（初期値0.9、範囲0.01〜1） |
| `AI_MAX_RESPONSE_TOKENS` | 出力トークン上限。初期値0は明示的な制限なし |

AIは汎用アシスタントとして質問に合わせた長さで応答します。挨拶や「一文で」「短く」の指定は各モードの長さより優先します。文字数は目安であり保証ではありません。固定の自己紹介や同じ書き出しを毎回付ける指示はありません。

| 応答モード | 説明・相談の長さの目安 | 切り替え |
| --- | --- | --- |
| Menima AI Lite | 80〜180文字、要点中心 | `AIモード Lite` |
| Menima AI Flash | 200〜450文字、理由・具体例 | `AIモード Flash` |
| Menima AI Plus | 500〜1200文字、詳しい説明・Markdown対応 | `AIモード Plus` |

初期モードはPlusです。`AIモード`で現在の選択と使い方を表示します。`AIモード Menima AI Lite`のような正式名称でも変更できます。選択は利用者・サーバー・チャンネルごとに分離し、`AI`コマンドとメンションへの返信に共通して適用します。モードを変更しても同じセッション・会話履歴を維持し、次の質問から新しい応答スタイルを指定します。`AIリセット`、`AI履歴削除`、`AIコンテキストクリア`は自分のこのチャンネルの履歴だけを消去し、選択モードは維持します。`AI 会話履歴を削除して`や`@Bot コンテキストを空にしてください`という明示的な指示でも消去できます。

3つとも同じAppleローカルモデルを使う応答スタイルで、別モデルへの切り替えや外部API利用はありません。モード設定はメモリにのみ保持し、Bot再起動後はPlusに戻ります。設定件数は `AI_MAX_SESSIONS` の範囲に制限し、上限を超えると最も古く使われた設定を破棄します。

生成は固定seedを使わないランダムサンプリングです。上の3設定は `.env` に追加しなくても初期値が使われます。temperatureを高くすると表現の多様性が増える一方、回答の正確性を保証する設定ではありません。出力上限を小さくすると文章が途中で切れる可能性があるため、初期値では制限していません。回答の文字数はDiscord送信時に切り捨てず、長い場合は分割します。

JRE_appのODPTキーを採用する場合:

```sh
.venv/bin/python scripts/import_credentials.py --jre-app /Users/yuki/Developer/JRE_app
```

この処理はJRE_appを読み取るだけで、Bot側の `.env` のチャレンジキーのみを書き換えます。Discordトークンや通常APIキーは別の認証情報です。JRE_appに通常API用 `ODPTStandardConsumerKey` が未設定の場合、別途通常APIで有効なキーを設定してください。起動時にJRE_appへのアクセスは不要です。

旧ソースのDiscordトークンを移行しても、その有効性は保証されません。ソースやGit履歴に残ったトークンはDeveloper Portalで再発行してください。新しい値をチャットやコミットに記載しないでください。

Discord Developer Portalで **Message Content Intent** を有効にし、Botへチャンネル閲覧・メッセージ送信権限を付与します。

## コマンド

プレフィックスは従来どおり空です。

`運行情報 JR` など事業者全体を指定した場合は、平常運転だけの路線を省きます。取得した情報がすべて平常運転なら返信しません。`運行情報 東海道線` または `運行情報 JR 東海道線` のように路線を指定すると、その路線の平常運転も返します。APIエラー・情報未提供は平常運転と区別して案内します。

ほかのBotが送信したコマンドにも応答します。自分自身のメッセージは無視します。相手Botがこちらの返信へ自動返信する場合は、相手側でも対象コマンドやチャンネルを限定してください。

Bot自身をメンションして `@Bot こんにちは` のように送ってもAIが応答します。最初のBotメンションより後の文章が質問になり、`AI <質問>` と同じ会話履歴を使います。メンションだけの場合は質問の入力方法を案内します。他のユーザー・ロール・everyoneへのメンションではAIを起動しません。

```text
対応路線
運行情報対応路線
山手線
都営大江戸線
ブルーライン
東武東上線
東武スカイツリーライン
東武伊勢崎線
東武日光線
東武アーバンパークライン
東武越生線
東武亀戸線
東武大師線
列車位置 東武スカイツリーライン支線
列車位置 Tobu.Nikko
京急本線列車番号
運行情報 JR
運行情報 都営
運行情報 東武
運行情報 横浜市営
AI こんにちは
AIリセット
AIモード
AIモード Lite
AIモード Flash
AIモード Plus
AI状態
サイコロ
おみくじ
```

従来の路線は駅順・種別・方向記号を保持します。追加した東武本線系統はAPIの駅順を使い、列車番号・駅または駅間・方向・種別を一覧表示します。`列車位置 事業者.路線ID` はAPIで公開される路線を指定できます。

東武列車位置は館林以北・新栃木以北が対象外です。亀戸線の小村井〜東あずま〜亀戸水神は在線位置を区別できません。API提供・認証の制約により、すべての路線でリアルタイム情報が取得できるとは限りません。空の運行情報は「平常運転」の保証ではありません。

AIはサーバー・チャンネル・ユーザーごとに会話を分離し、推論を1件ずつ処理します。稼働中は時間・往復数・モード変更・生成失敗を理由にBot側から履歴を削除しません。コンテキスト上限に達した場合は、ユーザーに明示的な `AIリセット` を案内します。会話保持件数の上限では既存の履歴を追い出さず、新しい利用者の会話を受け付けません。不要な履歴の持ち主によるリセットか `AI_MAX_SESSIONS` の調整が必要です。会話はメモリにのみ保持するため、Bot再起動で失われます。ローカル推論はAPIキー不要ですが、入力と回答はDiscordを経由します。生成内容をログに保存しません。

## 検証と手動起動

```sh
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/python -m trainbot.check
.venv/bin/python -m trainbot.check --api --ai-prompt
.venv/bin/python -m trainbot.check --discord
.venv/bin/python -m trainbot.bot
```

`check` はDiscordへログインしません。`--api` は読み取りのみの実API検証です。出力にはキーの値や認証付きURLを含めません。

## launchd

まず設定ファイルを生成し、内容を確認します。

```sh
.venv/bin/python scripts/install_launchd.py
plutil -lint launchd/jp.trainlocationbot.discord.plist
```

次にユーザーのLaunchAgentsへ配置・登録します。

```sh
.venv/bin/python scripts/install_launchd.py --install
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/jp.trainlocationbot.discord.plist
launchctl print gui/$(id -u)/jp.trainlocationbot.discord
```

設定変更後の再起動・登録解除:

```sh
launchctl kickstart -k gui/$(id -u)/jp.trainlocationbot.discord
launchctl bootout gui/$(id -u) ~/Library/LaunchAgents/jp.trainlocationbot.discord.plist
```

ログイン時に起動し、異常終了後は60秒以上の間隔で再起動します。実行ファイルと作業ディレクトリは絶対パスで、シェルのPATH設定に依存しません。plistに秘密情報を保存しません。Botが `.env` を読み込みます。

`logs/bot.log` は5MB×4ファイルに制限します。launchdのstdout/stderrは `/dev/null` とし、容量無制限の二重ログを避けます。起動失敗は手動起動と `launchctl print` で確認してください。SIGTERM/SIGINTではDiscord接続を閉じます。

LaunchAgentはログイン後に稼働し、ログアウト時は停止します。再起動後、FileVault解除・ログインまでの稼働は保証しません。常時稼働にはmacOS設定で電源接続時の自動スリープを無効にし、有線LAN等を使用してください。ログイン前のLaunchDaemonでのApple Intelligence利用は未検証です。

## データ提供元

- [公共交通オープンデータセンター](https://www.odpt.org/)
- [東武列車ロケーション公開範囲](https://ckan.odpt.org/dataset/tobu__r_train_location)
- [Apple公式Foundation Models Python SDK](https://github.com/apple/python-apple-fm-sdk)
- [Apple SDKセットアップ](https://apple.github.io/python-apple-fm-sdk/getting_started.html)

ODPTの各データセットのライセンス・公開期間は提供元を確認してください。チャレンジ限定データの提供期間終了時は、利用可能な接続先・キーへ設定を更新する必要があります。
