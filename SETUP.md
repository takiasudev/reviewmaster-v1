# ReviewMaster セットアップガイド

## 概要

ReviewMasterは、AWS環境上にCloudFormationを使用してデプロイするセルフホスティング型のドキュメントレビューシステムです。本ガイドでは、システムの構築から運用開始までの手順を説明します。

## システム構成

- **フロントエンド**: React + TypeScript (CloudFront + S3)
- **バックエンド**: AWS Lambda (Python)
- **データベース**: Amazon DynamoDB
- **AI**: Amazon Bedrock
- **API**: Amazon API Gateway
- **デプロイ**: CloudFormation ネストテンプレート

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
- 使用するBedrockモデルの選定（動作確認済モデル: Claude 3.5 Sonnet v2, Claude 4 Sonnet）

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

## デプロイ手順

### 1. 資材の準備
1. ReviewMasterの配布資材を取得
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
2. デプロイスクリプト（`reviewmaster-deploy.sh`）をアップロード
3. 実行権限を付与：
   ```bash
   chmod +x reviewmaster-deploy.sh
   ```
4. デプロイを実行：
   ```bash
   ./reviewmaster-deploy.sh -p s3://your-bucket/reviewmaster -r region_name

    ※ [-p]の指定値は資材を配置したS3バケット、[-r]の指定値は構築対象のリージョン名
   ```

### 3. デプロイ完了確認（スクリプト実行から10～15分ぐらいかかります）
デプロイが正常に完了すると、以下の情報が表示されます：
- フロントエンドURL（CloudFront）
- API Gateway URL
- 各種リソースの詳細情報

## 運用開始

### 1. システムアクセス
デプロイ完了後に表示されたフロントエンドURLにアクセスしてシステムを利用開始

例）
```
フロントエンドURL: https://aaaaaaa.cloudfront.net   ←　これ
API URL: https://aaaaaaaa.execute-api.ap-northeast-1.amazonaws.com/prod

スタック名: ReviewMaster-Infrastructure
```


### 2. 初期設定
設定管理タブを開き、以下の情報を設定しましょう
1. 共通カテゴリ管理
   1. プロジェクトに紐づくカテゴリ名を設定しましょう　例：01_要件定義,02_基本設計・・・（通番をつけると表示順をソートできます）
2. プロジェクト管理
   1. プロジェクト名を登録しましょう
3. レビュー観点管理
   1. レビュー指摘の手動追加時やAIがレビューする際の観点を登録しましょう
4. RAG管理
   1. 既にAmazon Bedrock Knowledge Basesがあれば登録しましょう
      1. Knowledge Basesは構築対象とおなじリージョンに存在している必要があります
      2. 用途に記載する文章はBedrockがRAGを利用する材料になるのできちんと記載しましょう

### 3. 初期動作確認
1. テスト用ドキュメントをアップロード　
2. AIレビュー機能の動作確認
3. レビュー結果のダウンロード確認


### 4. デプロイ後のBedrockのモデル変更

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
- **設定管理テーブル**: システム設定情報の管理
  - リソース名: `{ResourcePrefix}reviewmaster-config-dynamo{ResourceSuffix}`
  - 用途: プロジェクト、カテゴリ、レビュー観点、RAG設定の管理
  - キー構成: id (HASH), type (RANGE)
  - GSI: type-index
  - 暗号化: 有効

- **レビュー結果テーブル**: レビューデータとファイル情報の管理
  - リソース名: `{ResourcePrefix}reviewmaster-result-dynamo{ResourceSuffix}`
  - 用途: レビュー結果、ファイル情報、指摘事項の保存
  - キー構成: PK (HASH), SK (RANGE)
  - GSI: GSI1 (projectId, uploadDate)
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

- **設定管理関数**: システム設定管理処理
  - リソース名: `{ResourcePrefix}reviewmaster-config-manager-lambda{ResourceSuffix}`
  - 用途: プロジェクト、カテゴリ等の設定情報CRUD操作

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