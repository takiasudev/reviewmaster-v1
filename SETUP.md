# ReviewMaster セットアップガイド

## 概要

ReviewMasterは、AWS環境上にCloudFormationを使用してデプロイするセルフホスティング型のドキュメントレビューシステムです。本ガイドでは、システムの構築から運用開始までの手順を説明します。

## システム構成

- **フロントエンド**: React + TypeScript (CloudFront + S3)
- **バックエンド**: AWS Lambda (Python)
- **データベース**: Amazon DynamoDB
- **ユーザ管理**: Amazon Cognito
- **AI**: Amazon Bedrock
- **API**: Amazon API Gateway
- **デプロイ**: CloudFormation ネストテンプレート (Python スクリプト)

## 事前準備

### 1. AWSアカウント準備
- AWSアカウントの作成

### 2. デプロイ用IAMユーザー作成
以下の権限を持つIAMユーザーまたはロールを作成：
- CloudFormation: フルアクセス
- S3: 指定バケットへの読み書き権限
- IAM: ロール・ポリシー作成権限
- Lambda: 関数作成・管理権限
- API Gateway: リソース作成・管理権限
- CloudFront: 配信作成・管理権限
- DynamoDB: テーブル作成・管理権限
- Bedrock: モデル実行権限

### 3. リージョン選定・Bedrockモデル選定
- デプロイ対象リージョンの決定（動作確認済リージョン：ap-northeast-1,ap-northeast-3）
- 使用するBedrockモデルの選定（サポート対象のLLMモデルはClaudeシリーズのみです。動作確認済モデル: Claude Sonnet 3.5v2, Claude Sonnet 4, Claude Sonnet 4.5 ）

### 4. Bedrockモデル利用申請
- 対象リージョンにおけるBedrockモデルの利用申請
  - AWSコンソール-[Amazon Bedrock]-[モデルアクセス]　※リージョン間違いに注意

### 5. Service Quotas確認
- 対象リージョンにおけるBedrockモデルのService Quotas確認
  - AWSコンソール-[Service Quotas]-[AWS のサービス]-[Amazon Bedrock]　※リージョン間違いに注意
- 利用するモデルの「requests per minute」の『適用されたアカウントレベルのクォータ値』を確認
- 設定値が低い場合、レビュー実行時にエラーが発生する可能性があります
  - Claudeモデルの場合、クォーター値の制限緩和申請はAWSサポートに問い合わせして実行する必要があります

### 6. 資材配置用S3バケット作成
- CloudFormationテンプレートとアプリケーション資材を配置するS3バケットを作成
- バケット名は一意である必要があります
- バケットを作成するリージョンはシステムを構築するリージョンと同じである必要があります

## パラメータ設定

### 必須パラメータ

システム構築前に以下のパラメータを調査、検討しておく必要があります：

#### BedrockModelArn（必須）
レビュー用AIモデルのARN（推論プロファイルARN）
```
例: arn:aws:bedrock:★リージョン★:★AWSアカウント★:inference-profile/apac.anthropic.claude-3-5-sonnet-20241022-v2:0
```

参考）構築対象リージョンでCloudshellを起動し以下のコマンドを実行してください
```
aws bedrock list-inference-profiles --type-equals SYSTEM_DEFINED | \
  jq -r '.inferenceProfileSummaries[] | [
    .inferenceProfileArn,
    .status
  ] | @tsv' | column -t -s $'\t'
```



#### RagModelArn（必須）
RAG検索用AIモデルのARN
```
例: apac.anthropic.claude-3-5-sonnet-20241022-v2:0
```

参考）構築対象リージョンでCloudshellを起動し以下のコマンドを実行してください
```
aws bedrock list-inference-profiles --type-equals SYSTEM_DEFINED | \
  jq -r '.inferenceProfileSummaries[] | [
    .inferenceProfileId,
    .status
  ] | @tsv' | column -t -s $'\t'
```

#### RootUserEmail（必須）
ユーザー管理機能のシステム管理者ユーザーのメールアドレス
```
例: admin@your-company.com
```

**重要事項：**
- 実際に受信可能なメールアドレスを指定してください
- デプロイ時に仮パスワードがこのメールアドレスに送信されます
- システム管理者ユーザーはシステム全体の管理者権限を持ちます

### オプションパラメータ

#### リソース命名
- **ResourcePrefix**: リソース名の接頭辞（例: "MyCompany-"）
- **ResourceSuffix**: リソース名の接尾辞（例: "-Prod"）

#### セキュリティ設定
- **AllowedIpRanges**: API Gatewayへのアクセス許可IP範囲
  - 制限しない場合: "0.0.0.0/0"
  - 特定IP範囲のみ: "192.168.1.0/24,10.0.0.0/8"

#### カスタムタグ
- **CustomTags**: AWSリソースに付与するカスタムタグ
  - 形式: "Key1=Value1,Key2=Value2"

#### AIコスト料金初期値
AIレビュー結果に表示されるAI総コストを計算するため、Bedrockモデルの料金情報を設定します。

- **AiCostInferenceProfileId**: 料金設定の対象となるInference Profile ID
- **AiCostPricePer1MInputTokens**: 100万input tokensあたりの料金（USD）
- **AiCostPricePer1MOutputTokens**: 100万output tokensあたりの料金（USD）
- **AiCostPricePer1MCacheWriteInputTokens**: 100万input tokens（5分キャッシュ書き込み）あたりの料金（USD）
- **AiCostPricePer1MCacheReadInputTokens**: 100万input tokens（キャッシュ読み込み）あたりの料金（USD）

```
例:
AiCostInferenceProfileId=apac.anthropic.claude-3-5-sonnet-20241022-v2:0
AiCostPricePer1MInputTokens=3.00
AiCostPricePer1MOutputTokens=15.00
AiCostPricePer1MCacheWriteInputTokens=3.75
AiCostPricePer1MCacheReadInputTokens=0.30
```

**重要事項：**
- 料金はAWS公式の最新情報を確認して設定してください。
  - 参考: https://aws.amazon.com/jp/bedrock/pricing/
- `AiCostInferenceProfileId` は、Lambda環境変数で設定されるモデル値に合わせる必要があります。
  - `review-request` 関数、`revision-checker` 関数: `BEDROCK_MODEL`
  - `rag-search` 関数: `RAG_MODEL_ARN`
- `BEDROCK_MODEL` がARN形式の場合は、`inference-profile/` 以降のIDを `AiCostInferenceProfileId` に設定してください。
- 一致するAIコスト料金設定がない場合、レビュー結果のAI総コストは表示されない、または一部のみ計算される場合があります。

## デプロイ手順

### 1. 資材の準備
1. ReviewMasterの配布資材を取得（[<>Code]ボタンにZipダウンロードがあります）
2. 事前準備で整理したパラメータをもとに`nested-parameters.txt`ファイルを編集
3. S3バケットに以下の構成でアップロード


```
s3://your-bucket/reviewmaster/
├── templates/                  # CloudFormationテンプレート
├── nested-parameters.txt       # パラメータ設定ファイル
├── dist/                      # フロントエンド資材
└── lambda/                    # バックエンド資材
```
※既に作成されているスタックに新しいVerをデプロイする場合は`nested-parameters.txt`以外の資材を一度削除したのちに再配備しスクリプトを実行してください。


### 2. CloudShellでのデプロイ実行
1. AWSコンソールからCloudShellを起動　※構築対象のリージョンでCloudshellを起動すること
2. デプロイスクリプト（`reviewmaster_deploy.py`）をアップロード
3. デプロイを実行：
   ```bash
   python3 reviewmaster_deploy.py -p s3://your-bucket/reviewmaster -r region_name

    ※ [-p]の指定値は資材を配置したS3バケット、[-r]の指定値は構築対象のリージョン名
   ```

**主なオプション:**
- `-p, --s3-path`: S3資材配置パス（必須）
- `-r, --region`: AWSリージョン（必須）
- `-s, --stack-name`: スタック名（省略可、デフォルト: ReviewMaster-Infrastructure）
- `-f, --parameters-file`: パラメータファイル名（省略可、デフォルト: nested-parameters.txt）
- `-y, --yes`: 確認プロンプトをスキップ
- `-d, --debug`: デバッグモード（詳細ログを標準出力にも表示）
- `--log-file PATH`: ログファイルパス指定（省略時は自動生成）
- `--no-log-file`: ログファイル出力を無効化

**ログ出力について:**
- デフォルトでスクリプトと同じディレクトリに詳細ログファイル（DEBUG）が自動作成されます
- 標準出力には通常ログ（INFO）のみ表示されます
- デバッグモード（`--debug`）を指定すると標準出力にも詳細ログが表示されます

### 3. デプロイ完了確認
**所要時間の目安:**
- 初回構築: 約60分
- 既存スタック更新: 約3～5分

デプロイが正常に完了すると、以下の情報が表示されます：
- フロントエンドURL（CloudFront）
- API Gateway URL
- rootユーザー情報（メールアドレス、仮パスワード送信先）
- 各種リソースの詳細情報

## 運用開始

### 1. システム管理者ユーザー初回ログイン

デプロイ完了後、以下の手順でシステム管理者ユーザーの初期設定を行います：

1. **メール確認**
   - `RootUserEmail` で指定したメールアドレスに仮パスワードが送信されます
   - メールの件名: "[ReviewMaster] アカウント作成のお知らせ - 仮パスワード"
   - 送信元: no-reply@verificationemail.com

2. **初回ログイン**
   - フロントエンドURL（CloudFront）にアクセス
   - ログイン画面でメールアドレスと仮パスワードを入力
   - 初回ログイン時にパスワード変更画面が表示されます

3. **パスワード変更**
   - 新しいパスワードを設定してください
   - パスワード要件:
     - 最小8文字
     - 大文字、小文字、数字を含む

4. **ダッシュボードアクセス**
   - パスワード変更後、ダッシュボード画面が表示されます
   - rootユーザーとしてすべての機能にアクセスできます

**注意事項：**
- システム管理者権限をもつユーザは削除できません
- システム管理者権限をもつユーザのみがユーザー管理機能を利用できます
- パスワードを忘れた場合は、ログイン画面の「パスワードを忘れた」から再設定できます

### 2. システムアクセス
デプロイ完了後に表示されたフロントエンドURLにアクセスしてシステムを利用開始

例）
```
フロントエンドURL: https://aaaaaaa.cloudfront.net   ←　これ
API URL: https://aaaaaaaa.execute-api.ap-northeast-1.amazonaws.com/prod

スタック名: ReviewMaster-Infrastructure


```


### 3. 初期設定
設定管理タブを開き、以下の情報を設定しましょう

#### ユーザー管理
1. ユーザー管理画面を開く
2. 必要に応じて一般ユーザーやゲストユーザーを作成
   - メールアドレス、ユーザー名、ロールを選択して入力
   - 作成されたユーザーに仮パスワードがメールで送信されます
3. ユーザーロールについて
   - **管理ユーザー**: 全機能にアクセス可能、ユーザー管理・設定管理が可能
   - **一般ユーザー**: レビュー機能のみ利用可能、設定管理は不可
   - **ゲストユーザー**: レビュー依頼と自分のレビュー閲覧のみ可能
   - 管理ユーザが1名いればロール数の制限はありません（全ユーザが管理ユーザも可能です）

#### システム設定
1. 共通カテゴリ管理
   - プロジェクトに紐づくカテゴリ名を設定しましょう　例：01_要件定義,02_基本設計・・・（通番をつけると表示順をソートできます）
2. プロジェクト管理
   - プロジェクト名を登録しましょう
3. レビュー観点管理
   - レビュー指摘の手動追加時やAIがレビューする際の観点を登録しましょう
4. RAG管理
   - 既にAmazon Bedrock Knowledge Basesがあれば登録しましょう
     - Knowledge Basesは構築対象とおなじリージョンに存在している必要があります
     - 用途に記載する文章はBedrockがRAGを利用する材料になるのできちんと記載しましょう
5. AIコスト管理
   - AI総コストを表示するため、使用するBedrockモデルの料金情報を登録しましょう
   - 料金は概算表示用です。正式な請求金額を保証するものではありません
   - 料金はAWS公式のBedrock料金ページを確認して入力してください
     - https://aws.amazon.com/jp/bedrock/pricing/
   - Inference Profile IDは、Lambda環境変数で設定される値に合わせて登録してください
     - `BEDROCK_MODEL` がARN形式の場合は、`inference-profile/` 以降のIDを登録します
     - 例: `arn:aws:bedrock:ap-northeast-1:123456789012:inference-profile/apac.anthropic.claude-3-5-sonnet-20241022-v2:0` の場合、`apac.anthropic.claude-3-5-sonnet-20241022-v2:0` を登録します

### 4. 初期動作確認
1. テスト用ドキュメントをアップロード　
2. AIレビュー機能の動作確認
3. レビュー結果のダウンロード確認
4. ダッシュボードで自分が依頼したレビューが表示されることを確認


### 5. デプロイ後のBedrockのモデル変更

利用するBedrockのモデルを変更したい場合は以下の作業を実施してください。

- review-request関数の環境変数[BEDROCK_MODEL]の値を変更する
- revision-checker関数の環境変数[BEDROCK_MODEL]の値を変更する
- rag-search関数の環境変数[RAG_MODEL_ARN]の値を変更する


## システム要件

### AWS環境
- 対応リージョン: Amazon Bedrockが利用可能なリージョン
- 推奨リージョン: ap-northeast-1（東京）

### ブラウザ要件
- Chrome（推奨）
- Firefox
- Safari
- Microsoft Edge

### ネットワーク要件
- HTTPS通信（443ポート）
- CloudFrontへのアクセス許可

## 注意事項

### セキュリティ
- IP制限設定を適切に行ってください
- 必要に応じてAWS WAFの追加設定を検討してください

### コスト管理
- 使用量に応じてAWS利用料金が発生します
- 特にBedrock APIの使用量にご注意ください
- AI総コスト表示は、設定管理タブのAIコスト料金設定とBedrock APIのusage情報をもとにした概算です
- Bedrockの料金改定や利用モデル変更を行った場合は、AIコスト料金設定も見直してください
- 不要なリソースは適切に削除してください

### データ管理
- DynamoDBのレビューデータの保存期間は365日間です
- LambdaのCloudwatchlogsの保存期間は7日間です
- S3に一時的に格納されるExcelダウンロードデータの保存期間は7日間です

## 付録:作成されるAWSリソース一覧

ReviewMasterシステムをデプロイすると、以下のAWSリソースが作成されます。

### ストレージ（S3）
- **フロントエンドバケット**: React アプリケーションの静的ファイル格納
  - リソース名: `{ResourcePrefix}reviewmaster-frontend-s3-{AccountId}{ResourceSuffix}`
  - 用途: CloudFront経由でのWebアプリケーション配信
  - 暗号化: AES256
  - バージョニング: 無効

- **バックエンドバケット**: アップロードファイルとExcelダウンロードファイル格納
  - リソース名: `{ResourcePrefix}reviewmaster-backend-s3-{AccountId}{ResourceSuffix}`
  - 用途: ドキュメントファイルの一時保存、レビュー結果のExcelファイル保存
  - 暗号化: AES256
  - バージョニング: 無効
  - ライフサイクル: Excelファイルは1日後に自動削除

### データベース（DynamoDB）
- **設定管理テーブル**: システム設定情報とユーザー情報の管理
  - リソース名: `{ResourcePrefix}reviewmaster-config-dynamo{ResourceSuffix}`
  - 用途: プロジェクト、カテゴリ、レビュー観点、RAG設定、ユーザー情報の管理
  - キー構成: id (HASH), type (RANGE)
  - GSI: 
    - type-index: タイプ別検索用（既存設定データ検索）
    - type-created-at-index: タイプ＋作成日時でソート検索用（ユーザー一覧取得）
  - 暗号化: 有効

- **レビュー結果テーブル**: レビューデータとファイル情報の管理
  - リソース名: `{ResourcePrefix}reviewmaster-result-dynamo{ResourceSuffix}`
  - 用途: レビュー結果、ファイル情報、指摘事項の保存
  - キー構成: PK (HASH), SK (RANGE)
  - GSI: 
    - GSI_ProjectId: プロジェクト別レビュー検索 (project_id, PK)
    - GSI_Currentstatus: ステータス別レビュー検索 (current_status, PK)
    - GSI_Categoryname: カテゴリ別レビュー検索 (category_name, PK)
    - GSI_UploadedBy: ユーザー別レビュー検索（ダッシュボード用） (uploaded_by, uploaded_at)
  - 暗号化: KMS
  - ストリーム: 有効

### コンピューティング（Lambda）
- **ファイルストレージ関数**: ファイルアップロード処理
  - リソース名: `{ResourcePrefix}reviewmaster-file-storage-lambda{ResourceSuffix}`
  - 用途: S3へのファイルアップロード、メタデータ管理

- **レビューリクエスト関数**: AIレビュー実行処理
  - リソース名: `{ResourcePrefix}reviewmaster-review-request-lambda{ResourceSuffix}`
  - 用途: Bedrock APIを使用したドキュメントレビュー実行

- **RAG検索関数**: Knowledge Base検索処理
  - リソース名: `{ResourcePrefix}reviewmaster-rag-search-lambda{ResourceSuffix}`
  - 用途: Bedrock Knowledge Basesを使用した関連情報検索

- **結果チェッカー関数**: レビュー結果確認処理
  - リソース名: `{ResourcePrefix}reviewmaster-result-checker-lambda{ResourceSuffix}`
  - 用途: レビュー処理状況の確認、結果取得

- **設定管理関数**: システム設定管理・ユーザー管理処理
  - リソース名: `{ResourcePrefix}reviewmaster-config-manager-lambda{ResourceSuffix}`
  - 用途: プロジェクト、カテゴリ等の設定情報CRUD操作、ユーザー管理API

- **リビジョンチェッカー関数**: 修正版ドキュメント処理
  - リソース名: `{ResourcePrefix}reviewmaster-revision-checker-lambda{ResourceSuffix}`
  - 用途: 修正版ドキュメントのレビュー処理

### API（API Gateway）
- **REST API**: システムのメインAPI
  - リソース名: `{ResourcePrefix}reviewmaster-api-apigateway{ResourceSuffix}`
  - エンドポイント: Regional
  - IP制限: AllowedIpRangesパラメータで設定
  - 主要リソース:
    - `/upload` - ファイルアップロード
    - `/manage` - 設定管理
    - `/reviews` - レビュー一覧
    - `/result` - レビュー結果取得
    - `/status` - 処理状況確認
    - `/download` - Excelダウンロード
    - `/manage/users` - ユーザー管理（CRUD操作）
    - `/my-reviews` - 自分が依頼したレビュー一覧（ダッシュボード用）
    - `/auth/check-user-limit` - ユーザー制限チェック
    - その他多数のAPIエンドポイント

### コンテンツ配信（CloudFront）
- **ディストリビューション**: フロントエンドアプリケーション配信
  - 用途: React SPAの高速配信
  - オリジン: S3フロントエンドバケット
  - OAC（Origin Access Control）: 有効
  - SPA対応: CloudFront Functionでルーティング処理
  - HTTPS: 強制リダイレクト
  - 圧縮: 有効

### イベント処理（EventBridge）
- **S3イベントルール（レビューリクエスト）**: 新規ファイルアップロード時のトリガー
  - 対象パス: `projects/*/reviews/*/original/*`
  - ターゲット: レビューリクエスト関数

- **S3イベントルール（リビジョンチェッカー）**: 修正版ファイルアップロード時のトリガー
  - 対象パス: `projects/*/reviews/*/revisions/*/documents/*`
  - ターゲット: リビジョンチェッカー関数

### セキュリティ（IAM）
- **Lambda実行ロール**: 各Lambda関数用の実行ロール（6個）
  - ファイルストレージロール
  - レビューリクエストロール
  - RAG検索ロール
  - 結果チェッカーロール
  - 設定管理ロール
  - リビジョンチェッカーロール
  - EventBridge実行ロール

- **マネージドポリシー**: 各機能に必要な最小権限ポリシー
  - S3アクセス権限
  - DynamoDBアクセス権限
  - Bedrockモデル実行権限
  - Lambda関数間呼び出し権限

### ログ管理（CloudWatch Logs）
- **Lambda関数ログ**: 各Lambda関数のログ（6個）
  - 保存期間: LogRetentionDaysパラメータで設定（デフォルト7日）
  - 自動作成・管理

### 認証・認可（Cognito）
- **ユーザープール**: ユーザー管理・認証基盤
  - リソース名: `{ResourcePrefix}reviewmaster-users{ResourceSuffix}`
  - 用途: ユーザー認証、パスワード管理、MFA設定
  - パスワードポリシー: 最小8文字、大文字・小文字・数字必須
  - MFA: オフ（オプションで有効化可能）
  - メール検証: 有効

- **ユーザープールクライアント**: Webアプリケーション用クライアント
  - リソース名: `{ResourcePrefix}reviewmaster-web-client{ResourceSuffix}`
  - 認証フロー: USER_PASSWORD_AUTH、REFRESH_TOKEN_AUTH
  - トークン有効期間: 
    - アクセストークン: 1時間
    - IDトークン: 1時間
    - リフレッシュトークン: 1日

### その他
- **CloudFront Function**: SPA用ルーティング関数
  - 用途: React Routerとの連携、静的ファイル判定
  - ランタイム: cloudfront-js-1.0

### リソース命名規則
全てのリソースは以下の命名規則に従います：
```
{ResourcePrefix}reviewmaster-{service-name}-{resource-type}{ResourceSuffix}
```

例：
- ResourcePrefix: "MyCompany-"
- ResourceSuffix: "-Prod"
- 結果: "MyCompany-reviewmaster-frontend-s3-123456789012-Prod"

### タグ付け
全リソースに以下の共通タグが付与されます：
- Project: ReviewMaster
- Environment: prod
- Module: 各モジュール名
- カスタムタグ: CustomTagパラメータで指定 