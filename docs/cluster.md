# Mac mini＋MacBook Pro／Airでの協調AI

Mac miniだけがDiscordに接続します。質問が1件なら最大3台で「主回答・別案・注意点」を並列に生成し、最後に1台で統合します。質問が重なれば、まず質問ごとに1台ずつ割り当て、余った端末だけを追加検討に使います。有料の外部AI・検索APIは使用しません。

## 動作

| 状況 | 処理 |
| --- | --- |
| 1問・3台空き | 主回答＋別案＋点検 → 統合 |
| 2問・3台空き | 主回答を各1台、残り1台で片方の別案 → 必要な質問のみ統合 |
| 3問以上 | 各端末が主回答を担当、残りは待機 |
| 処理中に次の質問 | 進行中の生成は継続。次に空いた端末で待機質問を優先 |
| MacBook停止・スリープ | 稼働する端末だけで処理。Mac miniのみでも利用可能 |
| 統合に失敗 | 成功済みの主回答、なければ独立した別案を返す |

統合も端末を1枠使います。主回答がまだ始まっていない質問を先に割り当て、その後、統合、追加検討の順に開始します。同時受付を約50msまとめて判断します。既存の生成は途中で奪いません。各端末は1プロセス・1生成です。Mac miniのBotとは別に、同じMac miniでWorkerを起動しないでください。

同じ会話の質問は順に実行します。Lite／Flash／Plusは最終回答のスタイルで、どのモードでも余力があれば協調処理を行います。複数台の同じAFMが同じ誤りを返すこともあるため、精度向上は保証されません。単独実行との実測比較が必要です。

## 1. 各Macの準備

各端末でApple IntelligenceとAFMが利用できる状態にし、このリポジトリをクローンまたは更新します。画像入力には画像対応のOS・SDKも必要です。仮想環境は端末ごとに作成します。

```sh
cd ~/Developer/TrainLocationBot
/opt/homebrew/bin/python3.11 -m venv .venv
.venv/bin/python -m pip install -e '.[ai]'
```

Python 3.11がまだない端末では、先にインストールしてください。既存環境に更新する場合は、仮想環境の再作成は不要です。再現可能な依存関係は `requirements-macos.lock.txt` にもあります。

## 2. Worker用のキー

Mac miniのターミナルで次のコマンドを一度実行し、ランダムな専用キーを作ります。

```sh
openssl rand -hex 32
```

生成した値を、Mac miniの `.env` と、2台のMacBookの `.env.worker` の `AI_WORKER_TOKEN` に同じ値で設定してください。GitやDiscordに貼り付けないでください。DiscordトークンやODPTキーを流用しません。

## 3. MacBook Pro／Air側

各MacBookで、まだ `.env.worker` がない場合にコピーします。

```sh
cd ~/Developer/TrainLocationBot
cp -n .env.worker.example .env.worker
chmod 600 .env.worker
```

`.env.worker` を編集します。以下のIPは例です。Mac miniの実際の接続元IPに置き換えてください。

```dotenv
AI_ENABLED=true
AI_IMAGES_ENABLED=false
AI_WORKER_TOKEN=生成した同じ専用キー
AI_WORKER_HOST=0.0.0.0
AI_WORKER_PORT=8000
AI_WORKER_ALLOWED_IPS=192.168.1.10/32
```

`AI_WORKER_ALLOWED_IPS` は接続元のMac miniだけを許可します。IPが変わる環境ではDHCP予約や固定アドレスのプライベートVPNを使用してください。アクセス制御はプロキシのヘッダーを信用せず、TCPの接続元を使います。

Workerを手動で起動します。

```sh
.venv/bin/python -m trainbot.worker
```

macOSのファイアウォールで求められた場合は、利用するネットワークでの接続を許可します。ルーターのポート転送は不要です。インターネットに公開しないでください。

通常の `http://` は認証されますが暗号化されません。会話・画像・認証キーもネットワークを通るため、信頼できるLANまたは暗号化されたプライベートVPN内で使ってください。`https://` のWorker URLも利用できます（証明書検証は有効）。

Workerは `.env.worker` だけを読み込みます。Mac miniの `.env` はMacBookへ配布しません。WorkerでDiscord Botを起動する必要もありません。

`AI_IMAGES_ENABLED=false` のWorkerには、画像を含む会話を割り当てません。APIの存在だけでは画像対応モデルか判断できないため、画像入力が実機で確認できたMacBookだけ `true` に変更します。PDF・DOCXなど抽出済みの文書テキストは `false` でも処理できます。

## 4. Mac mini側

既存の `.env` に次の設定を追記・変更します。既存トークンなどは残します。

```dotenv
AI_CLUSTER_ENABLED=true
AI_WORKER_URLS=http://MacBook-Pro.local:8000,http://MacBook-Air.local:8000
AI_WORKER_TOKEN=生成した同じ専用キー
AI_TIMEOUT_SECONDS=90
AI_CLUSTER_TIMEOUT_SECONDS=240
AI_MAX_PENDING=4
AI_IMAGES_ENABLED=true
```

URLは各MacBookの実際のホスト名またはIPに置き換えます。ホスト名は各MacBookで `scutil --get LocalHostName` を実行して確認できます。Mac mini自身のURLは追加しません。

```sh
chmod 600 .env
.venv/bin/python -m trainbot.bot
```

既存のBotが動作中の場合は停止してから起動します。すでにlaunchd管理の場合は手動起動を重ねず、既存Agentを再起動します。

```sh
launchctl kickstart -k gui/$(id -u)/jp.trainlocationbot.discord
```

設定を戻す場合は `AI_CLUSTER_ENABLED=false` にして再起動すると従来の単独AIに戻ります。両方式の履歴はメモリ内にあり、切り替えを伴う再起動で引き継がれません。

## 5. 自動起動

Mac miniは既存のBot用LaunchAgentを使用します。各MacBookではWorker用Agentを生成して確認できます。

```sh
.venv/bin/python scripts/install_launchd.py --worker
```

配置・読み込みする場合は各MacBookで実行します。

```sh
.venv/bin/python scripts/install_launchd.py --worker --install
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/jp.trainlocationbot.worker.plist
```

設定変更後の再起動と停止は次のとおりです。

```sh
launchctl kickstart -k gui/$(id -u)/jp.trainlocationbot.worker
launchctl bootout gui/$(id -u) ~/Library/LaunchAgents/jp.trainlocationbot.worker.plist
```

LaunchAgentはログイン後に起動します。スリープは自動解除しません。常に3台で処理したい場合は、電源接続とスリープ設定を各端末で確認してください。Agentを複数プロセス構成にしないでください。

## 履歴・添付・障害時の扱い

- 会話はユーザー・サーバー・チャンネル単位でMac miniが管理し、質問と最終回答だけを確定します。各生成は新しいSDKセッションを使い、確定した会話全文を構造化データとして渡します。過去のモードや内部の回答案は履歴に追加しません。
- モード変更で履歴を削除しません。`AIリセット` で該当会話だけ削除します。リセット前の回答が後から完成しても履歴への追加・回答の投稿はしません。
- ネイティブSDKセッションを端末間で移す方式ではありません。画像も番号付きで再送するため、SDKの非互換なセッション復元に依存しません。
- 画像は既存の縮小・検証処理後に送信します。保持画像は1会話合計8MiB・24枚まで、会話テキストと統合用回答案は合計60,000文字まで、履歴は200ターンまでです。モデルのコンテキスト上限はこれらより先に到達する場合があります。
- 画像を含む履歴は画像対応端末だけで処理します。画像対応がMac miniだけなら、その会話は単独処理になります。画像非対応端末へ画像の内容を省略した履歴を送ることはしません。
- 履歴を自動で切り捨てません。容量・コンテキスト上限時は説明を返し、利用者の明示的なリセットを待ちます。複数台のメモリやコンテキストは合算できません。
- 保持する画像はMac miniのプロセスメモリ内に残り、明示的なリセットかBot終了で解放されます。Workerは生成中だけ一時ファイルを持ちます。会話・画像を通常ログや履歴ファイルに保存しません。再起動時に会話は失われます。
- リセット時も、すでに実行中の処理は完了まで続く場合があります。その処理分の画像は終了まで保持されますが、結果を会話に戻すことはありません。
- 認証付きの稼働確認を5秒間隔で行います。切断などで使えなくなった端末は新規割り当てから外し、稼働確認で復帰します。
- 1回の生成は `AI_TIMEOUT_SECONDS`、同じ会話の順番待ちを含む全体は `AI_CLUSTER_TIMEOUT_SECONDS` で期限を設けます。タイムアウトしてもWorkerの生成が続いていれば、その端末を空き扱いしません。Workerは質問IDで直近32件の重複実行を防ぎます。
- 鉄道コマンドはMac miniで従来どおり処理します。AI生成の待ち行列には入りません。汎用AIが鉄道APIを自動で呼び出す機能は、この改修では追加していません。

## 確認

```sh
.venv/bin/python -m unittest discover -s tests -q
```

テストは本物のDiscordや鉄道APIに接続せず、3台の振り分け・追加検討・統合・途中受付・失敗・リセット・画像引き継ぎ・Worker認証を確認します。実際の3台では、応答時間、単独回答との差、スリープからの復帰、各端末の画像対応を別途確認してください。

Discordの `AI状態` で、稼働確認で利用可能と判定された端末数と画像対応の端末数を確認できます。
