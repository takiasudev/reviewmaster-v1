#!/usr/bin/env python3
"""
ReviewMaster CloudFormation ネストテンプレート デプロイスクリプト (CloudShell専用)

このスクリプトは、ReviewMasterシステムをAWS CloudFormationを使用してデプロイします。
二段階デプロイ、GSI整合処理、フロントエンド/バックエンドデプロイを自動化します。
"""

import argparse
import boto3
import json
import logging
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional, Any
from urllib.parse import urlparse


# ============================================================
# ロガー設定
# ============================================================


class Logger:
    """ログ出力管理クラス"""

    def __init__(
        self,
        debug_mode: bool = False,
        log_file: Optional[str] = None,
        disable_file_log: bool = False,
    ):
        """
        ロガーを初期化

        Args:
            debug_mode: デバッグモードの有効化（標準出力のみに影響）
            log_file: ログファイルパス（Noneの場合はデフォルトパス使用）
            disable_file_log: ログファイル出力を無効化
        """
        self.logger = logging.getLogger("ReviewMasterDeploy")
        # ロガー自体は常にDEBUGレベル（各ハンドラーでフィルタリング）
        self.logger.setLevel(logging.DEBUG)

        # 既存のハンドラーをクリア（再初期化対策）
        self.logger.handlers.clear()

        # コンソールハンドラー（標準出力）
        # デバッグモードならDEBUG、通常はINFO
        console_handler = logging.StreamHandler(sys.stdout)
        console_level = logging.DEBUG if debug_mode else logging.INFO
        console_handler.setLevel(console_level)
        console_formatter = logging.Formatter(
            "[%(asctime)s] %(message)s", datefmt="%H:%M:%S"
        )
        console_handler.setFormatter(console_formatter)
        self.logger.addHandler(console_handler)

        # ファイルハンドラー（常にDEBUGレベル）
        self.log_file = None
        if not disable_file_log:
            # デフォルトログファイルパス（スクリプトと同じディレクトリ）
            if log_file is None:
                script_dir = sys.path[0] if sys.path[0] else "."
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                log_file = f"{script_dir}/reviewmaster_deploy_{timestamp}.log"

            try:
                file_handler = logging.FileHandler(log_file, mode="a", encoding="utf-8")
                # ファイルは常にDEBUGレベル
                file_handler.setLevel(logging.DEBUG)
                file_formatter = logging.Formatter(
                    "[%(asctime)s] [%(levelname)s] %(message)s",
                    datefmt="%Y-%m-%d %H:%M:%S",
                )
                file_handler.setFormatter(file_formatter)
                self.logger.addHandler(file_handler)
                self.log_file = log_file
            except Exception as e:
                # ファイル作成失敗時は標準出力のみ
                print(f"警告: ログファイルの作成に失敗しました: {e}", file=sys.stderr)
                self.log_file = None

    def info(self, message: str):
        """情報ログ"""
        self.logger.info(message)

    def success(self, message: str):
        """成功ログ"""
        self.logger.info(message)

    def warning(self, message: str):
        """警告ログ"""
        self.logger.warning(f"警告: {message}")

    def error(self, message: str):
        """エラーログ"""
        self.logger.error(f"エラー: {message}")

    def debug(self, message: str):
        """デバッグログ"""
        self.logger.debug(f"デバッグ: {message}")


# ============================================================
# 設定管理
# ============================================================


class ConfigManager:
    """設定・パラメータ管理クラス"""

    def __init__(self, args: argparse.Namespace, logger: Logger):
        """
        設定マネージャーを初期化

        Args:
            args: コマンドライン引数
            logger: ロガーインスタンス
        """
        self.logger = logger
        self.stack_name = args.stack_name
        self.region = args.region
        self.s3_base_path = args.s3_path.rstrip("/")
        self.parameters_file = args.parameters_file
        self.main_template = "00-main-template.yaml"
        self.skip_confirmation = args.yes

        # S3パスをパース
        parsed = urlparse(self.s3_base_path)
        self.s3_bucket = parsed.netloc
        self.s3_prefix = parsed.path.lstrip("/")

        # テンプレートファイルリスト
        self.core_templates = [
            "01-s3.yaml",
            "02-dynamo.yaml",
            "03-iam.yaml",
            "04-lambda.yaml",
            "05-api-gateway.yaml",
            "06-cloudfront.yaml",
            "07-eventbridge.yaml",
            "08-cognito.yaml",
        ]

        self.api_templates = [
            "05-01-api-upload.yaml",
            "05-02-api-manage.yaml",
            "05-03-api-reviews.yaml",
            "05-04-api-review-point-add.yaml",
            "05-05-api-review-point-update.yaml",
            "05-06-api-review-delete.yaml",
            "05-07-api-result.yaml",
            "05-08-api-status.yaml",
            "05-09-api-download.yaml",
            "05-10-api-project-overview.yaml",
            "05-11-api-review-history.yaml",
            "05-12-api-revision-upload.yaml",
            "05-13-api-revision-status.yaml",
            "05-14-api-revision-result.yaml",
            "05-15-api-file-download.yaml",
            "05-16-api-license.yaml",
            "05-17-api-auth.yaml",
            "05-18-api-my-reviews.yaml",
        ]

        self.lambda_files = [
            "config_manager.zip",
            "file_storage.zip",
            "rag_search.zip",
            "result_checker.zip",
            "review_request.zip",
            "revision_checker.zip",
        ]

        # パラメータ（後でS3からロード）
        self.parameters: Dict[str, str] = {}

    def get_template_url(self, template_name: str) -> str:
        """テンプレートのHTTPS URLを取得"""
        return f"https://s3.amazonaws.com/{self.s3_bucket}/{self.s3_prefix}/templates/{template_name}"

    def load_parameters_from_s3(self, s3_client) -> None:
        """S3からパラメータファイルをロード"""
        self.logger.info("パラメータファイルをダウンロード中...")

        try:
            response = s3_client.get_object(
                Bucket=self.s3_bucket, Key=f"{self.s3_prefix}/{self.parameters_file}"
            )
            content = response["Body"].read().decode("utf-8")

            # パラメータを解析
            for line in content.split("\n"):
                line = line.strip()
                # 空行とコメント行をスキップ
                if not line or line.startswith("#"):
                    continue

                # Key=Value形式を解析
                if "=" in line:
                    key, value = line.split("=", 1)
                    key = key.strip()
                    value = value.strip().strip('"').strip("'")
                    self.parameters[key] = value

            self.logger.success(
                f"パラメータファイルダウンロード完了: {len(self.parameters)}個のパラメータ"
            )

        except Exception as e:
            self.logger.error(f"パラメータファイルのダウンロードに失敗: {e}")
            raise

    def convert_to_cf_parameters(self) -> List[Dict[str, str]]:
        """パラメータをCloudFormation形式に変換"""
        cf_params = []
        for key, value in self.parameters.items():
            if key.startswith("AiCost"):
                continue
            cf_params.append({"ParameterKey": key, "ParameterValue": value})
        return cf_params


# ============================================================
# AWSリソース管理
# ============================================================


class AWSResourceManager:
    """AWS リソース操作管理クラス"""

    def __init__(self, config: ConfigManager, logger: Logger):
        """
        AWSリソースマネージャーを初期化

        Args:
            config: 設定マネージャー
            logger: ロガーインスタンス
        """
        self.config = config
        self.logger = logger

        # AWSクライアント
        self.cf_client = boto3.client("cloudformation", region_name=config.region)
        self.s3_client = boto3.client("s3", region_name=config.region)
        self.lambda_client = boto3.client("lambda", region_name=config.region)
        self.apigateway_client = boto3.client("apigateway", region_name=config.region)
        self.cloudfront_client = boto3.client("cloudfront", region_name=config.region)
        self.dynamodb_client = boto3.client("dynamodb", region_name=config.region)
        self.cognito_client = boto3.client("cognito-idp", region_name=config.region)
        self.sts_client = boto3.client("sts", region_name=config.region)

    def check_prerequisites(self) -> None:
        """前提条件をチェック"""
        self.logger.info("前提条件をチェック中...")

        # AWS認証チェック
        try:
            self.sts_client.get_caller_identity()
            self.logger.info("AWS認証確認完了")
        except Exception as e:
            self.logger.error(f"AWS認証が設定されていません: {e}")
            raise

        self.logger.info(f"使用リージョン: {self.config.region}")
        self.logger.success("前提条件チェック完了")

    def check_s3_resources(self) -> None:
        """S3資材の存在確認"""
        self.logger.info("S3資材の存在確認中...")

        # パラメータファイル確認
        if not self._s3_object_exists(
            f"{self.config.s3_prefix}/{self.config.parameters_file}"
        ):
            raise FileNotFoundError(
                f"パラメータファイルが見つかりません: {self.config.s3_base_path}/{self.config.parameters_file}"
            )

        # メインテンプレート確認
        if not self._s3_object_exists(
            f"{self.config.s3_prefix}/templates/{self.config.main_template}"
        ):
            raise FileNotFoundError(
                f"メインテンプレートが見つかりません: {self.config.s3_base_path}/templates/{self.config.main_template}"
            )

        # コアテンプレート確認
        for template in self.config.core_templates:
            if not self._s3_object_exists(
                f"{self.config.s3_prefix}/templates/{template}"
            ):
                raise FileNotFoundError(
                    f"コアテンプレートが見つかりません: {self.config.s3_base_path}/templates/{template}"
                )

        # APIリソーステンプレート確認
        for template in self.config.api_templates:
            if not self._s3_object_exists(
                f"{self.config.s3_prefix}/templates/api-resource/{template}"
            ):
                raise FileNotFoundError(
                    f"APIリソーステンプレートが見つかりません: {self.config.s3_base_path}/templates/api-resource/{template}"
                )

        # フロントエンド資材確認（警告のみ）
        if not self._s3_prefix_exists(f"{self.config.s3_prefix}/dist/"):
            self.logger.warning(
                f"フロントエンド資材が見つかりません: {self.config.s3_base_path}/dist/"
            )
            self.logger.warning("フロントエンドデプロイはスキップされます")

        # Lambda資材確認（警告のみ）
        missing_lambda_files = []
        for lambda_file in self.config.lambda_files:
            if not self._s3_object_exists(
                f"{self.config.s3_prefix}/lambda/{lambda_file}"
            ):
                missing_lambda_files.append(lambda_file)

        if missing_lambda_files:
            self.logger.warning("以下のLambda資材が見つかりません:")
            for file in missing_lambda_files:
                self.logger.warning(f"  - {self.config.s3_base_path}/lambda/{file}")
            self.logger.warning("該当するLambda関数のデプロイはスキップされます")

        self.logger.success(
            f"S3資材の存在確認完了（{len(self.config.core_templates) + len(self.config.api_templates)}個のテンプレートファイル確認済み）"
        )

    def _s3_object_exists(self, key: str) -> bool:
        """S3オブジェクトの存在確認"""
        try:
            self.s3_client.head_object(Bucket=self.config.s3_bucket, Key=key)
            return True
        except:
            return False

    def _s3_prefix_exists(self, prefix: str) -> bool:
        """S3プレフィックスの存在確認"""
        try:
            response = self.s3_client.list_objects_v2(
                Bucket=self.config.s3_bucket, Prefix=prefix, MaxKeys=1
            )
            return response.get("KeyCount", 0) > 0
        except:
            return False

    def validate_template(self) -> None:
        """テンプレート構文チェック"""
        self.logger.info("メインテンプレート構文をチェック中...")

        template_url = self.config.get_template_url(self.config.main_template)

        try:
            self.cf_client.validate_template(TemplateURL=template_url)
            self.logger.success("メインテンプレート構文チェック完了")
        except Exception as e:
            self.logger.error(f"メインテンプレート構文エラーが検出されました: {e}")
            raise

    def stack_exists(self) -> bool:
        """スタックの存在確認"""
        try:
            self.cf_client.describe_stacks(StackName=self.config.stack_name)
            return True
        except self.cf_client.exceptions.ClientError:
            return False

    def get_stack_output(self, output_key: str) -> Optional[str]:
        """スタック出力値を取得"""
        try:
            response = self.cf_client.describe_stacks(StackName=self.config.stack_name)
            outputs = response["Stacks"][0].get("Outputs", [])

            for output in outputs:
                if output["OutputKey"] == output_key:
                    return output["OutputValue"]

            return None
        except Exception as e:
            self.logger.debug(f"スタック出力取得エラー ({output_key}): {e}")
            return None

    def create_stack(self, parameters: List[Dict[str, str]]) -> None:
        """CloudFormationスタックを作成"""
        self.logger.info("CloudFormationスタックをデプロイ中（Phase 1）...")
        self.logger.info(f"スタック名: {self.config.stack_name}")
        self.logger.info(f"リージョン: {self.config.region}")
        self.logger.info(f"S3ベースパス: {self.config.s3_base_path}")

        template_url = self.config.get_template_url(self.config.main_template)

        try:
            self.cf_client.create_stack(
                StackName=self.config.stack_name,
                TemplateURL=template_url,
                Parameters=parameters,
                Capabilities=["CAPABILITY_IAM", "CAPABILITY_NAMED_IAM"],
            )

            # スタック作成完了まで待機
            self.logger.info("スタック作成完了を待機中...")
            waiter = self.cf_client.get_waiter("stack_create_complete")
            waiter.wait(StackName=self.config.stack_name)

            self.logger.success("CloudFormationデプロイ（Phase 1）完了")

        except Exception as e:
            self.logger.error(f"CloudFormationデプロイ（Phase 1）に失敗しました: {e}")
            raise

    def update_stack(self, parameters: List[Dict[str, str]]) -> bool:
        """
        CloudFormationスタックを更新

        Returns:
            bool: 更新が実行された場合True、変更がない場合False
        """
        self.logger.info("CloudFormationスタックを更新中（Phase 2）...")

        template_url = self.config.get_template_url(self.config.main_template)

        try:
            self.cf_client.update_stack(
                StackName=self.config.stack_name,
                TemplateURL=template_url,
                Parameters=parameters,
                Capabilities=["CAPABILITY_IAM", "CAPABILITY_NAMED_IAM"],
            )

            # スタック更新完了まで待機
            self.logger.info("スタック更新完了を待機中...")
            waiter = self.cf_client.get_waiter("stack_update_complete")
            waiter.wait(StackName=self.config.stack_name)

            self.logger.success("CloudFormationデプロイ（Phase 2）完了")
            return True

        except self.cf_client.exceptions.ClientError as e:
            error_message = str(e)
            if "No updates are to be performed" in error_message:
                self.logger.info("変更がないため、スタックの更新をスキップしました")
                return False
            else:
                self.logger.error(f"CloudFormationスタック更新に失敗しました: {e}")
                raise

    def deploy_api_gateway(self) -> None:
        """API Gateway デプロイメント"""
        self.logger.info("")
        self.logger.info("============================================================")
        self.logger.info(" Phase 2-3: API Gateway デプロイメント")
        self.logger.info("============================================================")
        self.logger.info("")

        # API Gateway REST API ID取得
        self.logger.info("API Gateway REST API IDを取得中...")
        api_id = self.get_stack_output("RestApiId")

        if not api_id:
            self.logger.error("API Gateway REST API IDの取得に失敗しました")
            self.logger.error(
                "CloudFormationの出力でRestApiIdが定義されているか確認してください"
            )
            return

        self.logger.success(f"API Gateway REST API ID取得完了: {api_id}")

        # デプロイメント作成
        self.logger.info("API Gateway デプロイメントを作成中（ステージ: prod）...")

        try:
            response = self.apigateway_client.create_deployment(
                restApiId=api_id,
                stageName="prod",
                description=f"Automated deployment from Python script {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
            )

            deployment_id = response["id"]
            self.logger.success("API Gateway デプロイメント完了")
            self.logger.info(f"デプロイメントID: {deployment_id}")
            self.logger.info(
                f"API エンドポイント: https://{api_id}.execute-api.{self.config.region}.amazonaws.com/prod"
            )

        except Exception as e:
            self.logger.error(f"API Gateway デプロイメントに失敗しました: {e}")
            self.logger.error(f"API ID: {api_id}")

        self.logger.success("API Gateway デプロイメント処理完了")

    def update_frontend_config(self) -> None:
        """フロントエンド設定ファイル更新"""
        self.logger.info("")
        self.logger.info("============================================================")
        self.logger.info(" Phase 2-2: フロントエンド設定ファイル更新")
        self.logger.info("============================================================")
        self.logger.info("")

        # API Gateway URL取得
        self.logger.info("API Gateway URLを取得中...")
        api_url = self.get_stack_output("ApiGatewayUrl")

        if not api_url:
            self.logger.error("API Gateway URLの取得に失敗しました")
            return

        self.logger.success(f"API Gateway URL取得完了: {api_url}")

        # Cognito User Pool Client ID取得
        self.logger.info("Cognito User Pool Client IDを取得中...")
        user_pool_client_id = self.get_stack_output("UserPoolClientId")

        if user_pool_client_id:
            self.logger.success(f"User Pool Client ID取得完了: {user_pool_client_id}")
        else:
            self.logger.warning(
                "User Pool Client IDが取得できませんでした（ユーザー管理機能未デプロイの可能性）"
            )

        # config.jsファイルの存在確認
        config_key = f"{self.config.s3_prefix}/dist/config.js"
        if not self._s3_object_exists(config_key):
            self.logger.warning(
                f"config.jsファイルが見つかりません: {self.config.s3_base_path}/dist/config.js"
            )
            self.logger.warning("フロントエンド設定ファイル更新をスキップします")
            return

        # config.jsファイルをダウンロード
        self.logger.info("config.jsファイルをダウンロード中...")

        try:
            response = self.s3_client.get_object(
                Bucket=self.config.s3_bucket, Key=config_key
            )
            config_content = response["Body"].read().decode("utf-8")

            # APIエンドポイントURLを更新
            self.logger.info("config.jsファイルを更新中...")
            self.logger.info(f"  - API Gateway URL: {api_url}")

            config_content = re.sub(
                r"API_BASE_URL:\s*'[^']*'", f"API_BASE_URL: '{api_url}'", config_content
            )

            # Cognito設定を更新
            if user_pool_client_id:
                self.logger.info(f"  - User Pool Client ID: {user_pool_client_id}")
                self.logger.info(f"  - AWS Region: {self.config.region}")

                config_content = re.sub(
                    r"USER_POOL_CLIENT_ID:\s*'[^']*'",
                    f"USER_POOL_CLIENT_ID: '{user_pool_client_id}'",
                    config_content,
                )
                config_content = re.sub(
                    r"AWS_REGION:\s*'[^']*'",
                    f"AWS_REGION: '{self.config.region}'",
                    config_content,
                )

            # 更新されたconfig.jsファイルをS3にアップロード
            self.logger.info("更新されたconfig.jsファイルをS3にアップロード中...")
            self.s3_client.put_object(
                Bucket=self.config.s3_bucket,
                Key=config_key,
                Body=config_content.encode("utf-8"),
                ContentType="application/javascript",
            )

            self.logger.success("config.jsファイルのS3アップロード完了")
            self.logger.success("フロントエンド設定ファイル更新完了")

        except Exception as e:
            self.logger.error(f"config.jsファイルの更新に失敗しました: {e}")


# ============================================================
# GSI整合処理
# ============================================================


class GSIReconciler:
    """DynamoDB GSI整合処理クラス"""

    def __init__(
        self, config: ConfigManager, aws_manager: AWSResourceManager, logger: Logger
    ):
        """
        GSI整合処理を初期化

        Args:
            config: 設定マネージャー
            aws_manager: AWSリソースマネージャー
            logger: ロガーインスタンス
        """
        self.config = config
        self.aws_manager = aws_manager
        self.logger = logger
        self.dynamodb = aws_manager.dynamodb_client

    def reconcile_if_needed(self) -> None:
        """必要に応じてGSI整合を実行"""
        # GSI Desired定義の存在確認
        gsi_json_key = f"{self.config.s3_prefix}/templates/02-01-gsi-desired.json"

        if not self.aws_manager._s3_object_exists(gsi_json_key):
            self.logger.info("GSI Desired定義がないため整合はスキップします")
            return

        try:
            # Desired定義をダウンロード
            response = self.aws_manager.s3_client.get_object(
                Bucket=self.config.s3_bucket, Key=gsi_json_key
            )
            desired_config = json.loads(response["Body"].read().decode("utf-8"))

            tables = desired_config.get("tables", [])

            if not tables:
                self.logger.info("GSI定義がありません")
                return

            self.logger.info(f"GSI関連設定を開始します（テーブル数: {len(tables)}）")

            # 並行処理でGSI整合を実行
            with ThreadPoolExecutor(max_workers=2) as executor:
                futures = []
                for table_config in tables:
                    future = executor.submit(self._reconcile_table, table_config)
                    futures.append(future)

                # 全テーブルの処理完了を待機
                for future in as_completed(futures):
                    try:
                        future.result()
                    except Exception as e:
                        self.logger.error(f"GSI設定処理の一部が失敗しました: {e}")
                        raise

            self.logger.info("GSI関連設定を完了しました")

        except Exception as e:
            self.logger.error(f"GSI設定に失敗しました: {e}")
            raise

    def _reconcile_table(self, table_config: Dict[str, Any]) -> None:
        """単一テーブルのGSI整合"""
        # テーブル名を解決
        table_name = self._resolve_table_name(table_config)
        if not table_name:
            return

        billing_mode = table_config.get("billingMode", "PROVISIONED")
        desired_indexes = table_config.get("indexes", [])
        attribute_types = table_config.get("attributeTypes", {})

        if not desired_indexes:
            self.logger.info(f"Desired indexesが空のため処理なし: {table_name}")
            return

        self.logger.debug(
            f"[GSI設定] テーブル: {table_name} (BillingMode: {billing_mode})"
        )

        # 現在のテーブル情報を取得
        try:
            table_info = self.dynamodb.describe_table(TableName=table_name)["Table"]
            current_indexes = table_info.get("GlobalSecondaryIndexes", [])
            current_index_names = {idx["IndexName"] for idx in current_indexes}
            desired_index_names = {idx["IndexName"] for idx in desired_indexes}

            # スループット更新処理（既存GSI）
            for desired_idx in desired_indexes:
                idx_name = desired_idx["IndexName"]
                if idx_name not in current_index_names:
                    continue

                # PROVISIONED モードの場合のみスループット更新
                if (
                    billing_mode == "PROVISIONED"
                    and "ProvisionedThroughput" in desired_idx
                ):
                    desired_rcu = desired_idx["ProvisionedThroughput"][
                        "ReadCapacityUnits"
                    ]
                    desired_wcu = desired_idx["ProvisionedThroughput"][
                        "WriteCapacityUnits"
                    ]

                    current_idx = next(
                        (
                            idx
                            for idx in current_indexes
                            if idx["IndexName"] == idx_name
                        ),
                        None,
                    )
                    if current_idx and "ProvisionedThroughput" in current_idx:
                        current_rcu = current_idx["ProvisionedThroughput"][
                            "ReadCapacityUnits"
                        ]
                        current_wcu = current_idx["ProvisionedThroughput"][
                            "WriteCapacityUnits"
                        ]

                        if desired_rcu != current_rcu or desired_wcu != current_wcu:
                            self.logger.debug(
                                f"[スループット更新] {table_name}/{idx_name}: {current_rcu},{current_wcu} -> {desired_rcu},{desired_wcu}"
                            )
                            self._update_gsi_throughput(
                                table_name, idx_name, desired_rcu, desired_wcu
                            )

            # 削除処理（Desiredにない）
            for idx_name in current_index_names - desired_index_names:
                self.logger.info(
                    f"[Delete] {table_name}/{idx_name} (Desired定義に不在)"
                )
                self._delete_gsi(table_name, idx_name)

            # 作成処理（現在にない）
            for desired_idx in desired_indexes:
                idx_name = desired_idx["IndexName"]
                if idx_name not in current_index_names:
                    self.logger.info(f"[GSI作成] {table_name}/{idx_name}")
                    self._create_gsi(
                        table_name,
                        desired_idx,
                        billing_mode,
                        attribute_types,
                        table_info,
                    )

        except Exception as e:
            self.logger.error(f"テーブル {table_name} のGSI整合に失敗: {e}")
            raise

    def _resolve_table_name(self, table_config: Dict[str, Any]) -> Optional[str]:
        """テーブル名を解決"""
        resolve = table_config.get("resolve", {})
        cfn_output_key = resolve.get("cfnOutputKey")
        explicit_table = resolve.get("tableName")

        if cfn_output_key:
            table_name = self.aws_manager.get_stack_output(cfn_output_key)
            if table_name:
                return table_name

        if explicit_table:
            return explicit_table

        self.logger.warning("テーブル名を解決できませんでした。スキップします")
        return None

    def _update_gsi_throughput(
        self, table_name: str, index_name: str, rcu: int, wcu: int
    ) -> None:
        """GSIスループット更新"""
        self.dynamodb.update_table(
            TableName=table_name,
            GlobalSecondaryIndexUpdates=[
                {
                    "Update": {
                        "IndexName": index_name,
                        "ProvisionedThroughput": {
                            "ReadCapacityUnits": rcu,
                            "WriteCapacityUnits": wcu,
                        },
                    }
                }
            ],
        )
        self._wait_gsi_active(table_name, index_name)

    def _delete_gsi(self, table_name: str, index_name: str) -> None:
        """GSI削除"""
        self.dynamodb.update_table(
            TableName=table_name,
            GlobalSecondaryIndexUpdates=[{"Delete": {"IndexName": index_name}}],
        )
        self._wait_gsi_absent(table_name, index_name)

    def _create_gsi(
        self,
        table_name: str,
        index_config: Dict[str, Any],
        billing_mode: str,
        attribute_types: Dict[str, str],
        table_info: Dict[str, Any],
    ) -> None:
        """GSI作成"""
        index_name = index_config["IndexName"]
        key_schema = index_config["KeySchema"]
        projection = index_config["Projection"]

        # 高速作成用の初期スループット
        create_rcu = 10
        create_wcu = 10

        # AttributeDefinitionsを準備
        existing_attrs = {
            attr["AttributeName"]: attr["AttributeType"]
            for attr in table_info.get("AttributeDefinitions", [])
        }

        attribute_defs = []
        for key in key_schema:
            attr_name = key["AttributeName"]
            if attr_name in existing_attrs:
                attr_type = existing_attrs[attr_name]
            elif attr_name in attribute_types:
                attr_type = attribute_types[attr_name]
            else:
                self.logger.error(
                    f"AttributeType不明のため作成不可: {table_name}/{index_name} key={attr_name}"
                )
                raise ValueError(f"Unknown attribute type for {attr_name}")

            attribute_defs.append(
                {"AttributeName": attr_name, "AttributeType": attr_type}
            )

        # GSI作成パラメータ
        gsi_update = {
            "Create": {
                "IndexName": index_name,
                "KeySchema": key_schema,
                "Projection": projection,
            }
        }

        if billing_mode == "PROVISIONED":
            gsi_update["Create"]["ProvisionedThroughput"] = {
                "ReadCapacityUnits": create_rcu,
                "WriteCapacityUnits": create_wcu,
            }

        # GSI作成実行
        self.dynamodb.update_table(
            TableName=table_name,
            AttributeDefinitions=attribute_defs,
            GlobalSecondaryIndexUpdates=[gsi_update],
        )

        self._wait_gsi_active(table_name, index_name)

        # Desiredスループットに調整
        if billing_mode == "PROVISIONED" and "ProvisionedThroughput" in index_config:
            desired_rcu = index_config["ProvisionedThroughput"]["ReadCapacityUnits"]
            desired_wcu = index_config["ProvisionedThroughput"]["WriteCapacityUnits"]

            if desired_rcu != create_rcu or desired_wcu != create_wcu:
                self.logger.debug(
                    f"[スループット調整] {table_name}/{index_name}: {create_rcu},{create_wcu} -> {desired_rcu},{desired_wcu}"
                )
                self._update_gsi_throughput(
                    table_name, index_name, desired_rcu, desired_wcu
                )

    def _wait_gsi_active(
        self, table_name: str, index_name: str, max_wait: int = 600
    ) -> None:
        """GSIがACTIVEになるまで待機"""
        delay = 2
        max_delay = 30
        start_time = time.time()

        while time.time() - start_time < max_wait:
            try:
                table = self.dynamodb.describe_table(TableName=table_name)["Table"]
                indexes = table.get("GlobalSecondaryIndexes", [])

                for idx in indexes:
                    if idx["IndexName"] == index_name:
                        if idx["IndexStatus"] == "ACTIVE":
                            self.logger.info(
                                f"[処理完了待ち] ACTIVE 確認: {table_name}/{index_name}"
                            )
                            return

                time.sleep(delay)
                delay = min(delay * 2, max_delay)

            except Exception as e:
                self.logger.debug(f"Wait error: {e}")
                time.sleep(delay)

    def _wait_gsi_absent(
        self, table_name: str, index_name: str, max_wait: int = 600
    ) -> None:
        """GSIが削除されるまで待機"""
        delay = 2
        max_delay = 30
        start_time = time.time()

        while time.time() - start_time < max_wait:
            try:
                table = self.dynamodb.describe_table(TableName=table_name)["Table"]
                indexes = table.get("GlobalSecondaryIndexes", [])

                if not any(idx["IndexName"] == index_name for idx in indexes):
                    self.logger.info(
                        f"[処理完了待ち] 削除確認: {table_name}/{index_name}"
                    )
                    return

                time.sleep(delay)
                delay = min(delay * 2, max_delay)

            except Exception as e:
                self.logger.debug(f"Wait error: {e}")
                time.sleep(delay)


# ============================================================
# ヘルパー関数
# ============================================================


def confirm_execution(config: ConfigManager, logger: Logger) -> None:
    """実行確認プロンプト"""
    logger.info("")
    logger.info("============================================================")
    logger.info(" デプロイ実行確認")
    logger.info("============================================================")
    logger.info("")
    logger.info("以下の設定でデプロイを実行します：")
    logger.info(f"  スタック名: {config.stack_name}")
    logger.info(f"  リージョン: {config.region}")
    logger.info(f"  S3ベースパス: {config.s3_base_path}")
    logger.info(f"  パラメータファイル: {config.parameters_file}")
    logger.info("")
    logger.info("デプロイステップ:")
    logger.info("  1. インフラストラクチャデプロイ（暫定CORS設定）")
    logger.info("  2. CORS設定更新（実際のCloudFrontドメイン使用）")
    logger.info("  2-2. フロントエンド設定ファイル更新（API Gateway URL）")
    logger.info("  2-3. API Gateway デプロイメント（prodステージ）")
    logger.info("  3. フロントエンド・バックエンドデプロイ")
    logger.info("")

    response = input("処理を続行しますか？ (y/N): ").strip().lower()
    if response not in ("y", "yes"):
        logger.info("デプロイをキャンセルしました。")
        sys.exit(0)

    logger.info("デプロイを開始します...")


def deploy_two_stage(
    config: ConfigManager, aws_manager: AWSResourceManager, logger: Logger
) -> None:
    """二段階デプロイ実行"""
    # TemplateBaseUrlパラメータを設定
    template_base_url = (
        f"https://s3.amazonaws.com/{config.s3_bucket}/{config.s3_prefix}/templates"
    )
    config.parameters["TemplateBaseUrl"] = template_base_url

    # パラメータをCloudFormation形式に変換
    cf_parameters = config.convert_to_cf_parameters()

    # 既存スタックの存在確認
    if aws_manager.stack_exists():
        logger.info("")
        logger.info("============================================================")
        logger.info(" Phase 1: インフラストラクチャデプロイ（スキップ - 既存スタック）")
        logger.info("============================================================")
        logger.info("")
        logger.info(
            "既存スタックを検出しました。Phase 1をスキップしてCORS設定を更新します..."
        )

        # CloudFrontドメイン取得
        logger.info("CloudFrontドメインを取得中...")
        cloudfront_domain = aws_manager.get_stack_output(
            "CloudFrontDistributionDomainName"
        )

        if not cloudfront_domain:
            logger.error("CloudFrontドメインの取得に失敗しました")
            raise ValueError("CloudFront domain not found")

        logger.success(f"CloudFrontドメイン取得完了: {cloudfront_domain}")

        # Phase 2: CORS設定更新
        logger.info("")
        logger.info("============================================================")
        logger.info(" Phase 2: 構築後パラメータ更新")
        logger.info("============================================================")
        logger.info("")

        config.parameters["CloudFrontDomain"] = cloudfront_domain
        updated_cf_parameters = config.convert_to_cf_parameters()

        logger.info("CloudFrontドメインを使用してCORS設定とLambda環境変数を更新中...")
        aws_manager.update_stack(updated_cf_parameters)

        logger.success("CORS設定更新完了")

        # GSI整合処理
        gsi_reconciler = GSIReconciler(config, aws_manager, logger)
        gsi_reconciler.reconcile_if_needed()

        # Phase 2-2: フロントエンド設定ファイル更新
        aws_manager.update_frontend_config()

        # Phase 2-3: API Gateway デプロイメント
        aws_manager.deploy_api_gateway()

    else:
        # 新規スタック作成
        logger.info("")
        logger.info("============================================================")
        logger.info(" Phase 1: インフラストラクチャデプロイ")
        logger.info("============================================================")
        logger.info("")

        aws_manager.create_stack(cf_parameters)

        # CloudFrontドメイン取得
        logger.info("CloudFrontドメインを取得中...")
        cloudfront_domain = aws_manager.get_stack_output(
            "CloudFrontDistributionDomainName"
        )

        if not cloudfront_domain:
            logger.error("CloudFrontドメインの取得に失敗しました")
            raise ValueError("CloudFront domain not found")

        logger.success(f"CloudFrontドメイン取得完了: {cloudfront_domain}")

        # Phase 2: CORS設定更新
        logger.info("")
        logger.info("============================================================")
        logger.info(" Phase 2: 構築後パラメータ更新")
        logger.info("============================================================")
        logger.info("")

        config.parameters["CloudFrontDomain"] = cloudfront_domain
        updated_cf_parameters = config.convert_to_cf_parameters()

        logger.info("CloudFrontドメインを使用してCORS設定とLambda環境変数を更新中...")
        aws_manager.update_stack(updated_cf_parameters)

        logger.success("CORS設定更新完了")

        # GSI整合処理
        gsi_reconciler = GSIReconciler(config, aws_manager, logger)
        gsi_reconciler.reconcile_if_needed()

        # Phase 2-2: フロントエンド設定ファイル更新
        aws_manager.update_frontend_config()

        # Phase 2-3: API Gateway デプロイメント
        aws_manager.deploy_api_gateway()

    # Phase 3: フロントエンド・バックエンドデプロイ（常に実行）
    logger.info("")
    logger.info("============================================================")
    logger.info(" Phase 3: フロントエンド・バックエンドデプロイ")
    logger.info("============================================================")
    logger.info("")

    deploy_frontend(config, aws_manager, logger)
    deploy_backend(config, aws_manager, logger)


def deploy_frontend(
    config: ConfigManager, aws_manager: AWSResourceManager, logger: Logger
) -> None:
    """フロントエンドデプロイ"""
    logger.info("============================================================")
    logger.info(" Phase 3-1: フロントエンドデプロイ")
    logger.info("============================================================")
    logger.info("")

    # フロントエンド資材の存在確認
    if not aws_manager._s3_prefix_exists(f"{config.s3_prefix}/dist/"):
        logger.warning(
            f"フロントエンド資材が見つかりません: {config.s3_base_path}/dist/"
        )
        logger.warning("フロントエンドデプロイをスキップします")
        return

    # フロントエンドS3バケット名取得
    logger.info("フロントエンドS3バケット名を取得中...")
    frontend_bucket = aws_manager.get_stack_output("FrontendBucketName")

    if not frontend_bucket:
        logger.error("フロントエンドS3バケット名の取得に失敗しました")
        logger.error(
            "CloudFormationの出力でFrontendBucketNameが定義されているか確認してください"
        )
        return

    logger.success(f"フロントエンドS3バケット名取得完了: {frontend_bucket}")

    # フロントエンド資材をS3にsync
    logger.info("フロントエンド資材をS3バケットにデプロイ中...")

    try:
        # S3のsync機能を使用（boto3ではs3 transferを使用）
        s3_resource = boto3.resource("s3", region_name=config.region)
        source_bucket = s3_resource.Bucket(config.s3_bucket)
        dest_bucket = s3_resource.Bucket(frontend_bucket)

        # ソースからファイル一覧を取得
        source_prefix = f"{config.s3_prefix}/dist/"
        copied_count = 0

        for obj in source_bucket.objects.filter(Prefix=source_prefix):
            # ファイル名からプレフィックスを除去
            relative_key = obj.key[len(source_prefix) :]
            if not relative_key:  # ディレクトリのみの場合はスキップ
                continue

            # コピー実行
            copy_source = {"Bucket": config.s3_bucket, "Key": obj.key}
            dest_bucket.copy(copy_source, relative_key)
            copied_count += 1

            if copied_count % 10 == 0:
                logger.debug(f"コピー済み: {copied_count}ファイル")

        logger.success(f"フロントエンドデプロイ完了（{copied_count}ファイル）")

        # CloudFrontキャッシュクリア
        logger.info("CloudFrontキャッシュをクリア中...")
        cloudfront_domain = aws_manager.get_stack_output(
            "CloudFrontDistributionDomainName"
        )

        if cloudfront_domain:
            # Distribution IDを取得
            distributions = aws_manager.cloudfront_client.list_distributions()
            dist_list = distributions.get("DistributionList", {}).get("Items", [])

            cloudfront_id = None
            for dist in dist_list:
                if dist["DomainName"] == cloudfront_domain:
                    cloudfront_id = dist["Id"]
                    break

            if cloudfront_id:
                logger.info(f"CloudFront Distribution ID: {cloudfront_id}")
                invalidation = aws_manager.cloudfront_client.create_invalidation(
                    DistributionId=cloudfront_id,
                    InvalidationBatch={
                        "Paths": {"Quantity": 1, "Items": ["/*"]},
                        "CallerReference": str(int(time.time())),
                    },
                )
                invalidation_id = invalidation["Invalidation"]["Id"]
                logger.success(f"CloudFrontキャッシュクリア開始: {invalidation_id}")
            else:
                logger.warning("CloudFront Distribution IDが取得できませんでした")
        else:
            logger.warning("CloudFrontドメインが取得できませんでした")

    except Exception as e:
        logger.error(f"フロントエンドデプロイに失敗しました: {e}")
        return


def deploy_backend(
    config: ConfigManager, aws_manager: AWSResourceManager, logger: Logger
) -> None:
    """バックエンドデプロイ"""
    logger.info("")
    logger.info("============================================================")
    logger.info(" Phase 3-2: バックエンドデプロイ")
    logger.info("============================================================")
    logger.info("")

    # Lambda関数名取得
    logger.info("CloudFormationスタック内のLambda関数を検索中...")

    try:
        # LambdaStackを取得
        resources = aws_manager.cf_client.list_stack_resources(
            StackName=config.stack_name
        )

        lambda_stack_arn = None
        for resource in resources["StackResourceSummaries"]:
            if resource["LogicalResourceId"] == "LambdaStack":
                lambda_stack_arn = resource["PhysicalResourceId"]
                break

        if not lambda_stack_arn:
            logger.error("LambdaStackが見つかりませんでした")
            return

        # ARNからスタック名を抽出
        lambda_stack_name = lambda_stack_arn.split("/")[1]
        logger.debug(f"Lambda Stack Name: {lambda_stack_name}")

        # LambdaStack内のLambda関数を取得
        lambda_resources = aws_manager.cf_client.describe_stack_resources(
            StackName=lambda_stack_name
        )

        lambda_functions = {}
        for resource in lambda_resources["StackResources"]:
            if resource["ResourceType"] == "AWS::Lambda::Function":
                logical_id = resource["LogicalResourceId"].lower()
                physical_id = resource["PhysicalResourceId"]

                # 関数名のマッピング
                if "configmanager" in logical_id or "config" in logical_id:
                    lambda_functions["config_manager"] = physical_id
                elif "filestorage" in logical_id or "storage" in logical_id:
                    lambda_functions["file_storage"] = physical_id
                elif "ragsearch" in logical_id or "rag" in logical_id:
                    lambda_functions["rag_search"] = physical_id
                elif "resultchecker" in logical_id or "result" in logical_id:
                    lambda_functions["result_checker"] = physical_id
                elif "reviewrequest" in logical_id:
                    lambda_functions["review_request"] = physical_id
                elif "revisionchecker" in logical_id or "revision" in logical_id:
                    lambda_functions["revision_checker"] = physical_id

        if not lambda_functions:
            logger.error("Lambda関数が見つかりませんでした")
            return

        logger.success(f"Lambda関数名取得完了: {len(lambda_functions)}個の関数を検出")

        # 検出された関数の一覧を表示
        logger.info("検出されたLambda関数 (CloudFormationスタック内):")
        for zip_name, function_name in lambda_functions.items():
            logger.info(f"  - {zip_name} → {function_name}")

        # 各Lambda関数にzipファイルをデプロイ
        deployed_count = 0
        total_count = len(lambda_functions)

        for zip_name, function_name in lambda_functions.items():
            # zipファイルの存在確認
            zip_key = f"{config.s3_prefix}/lambda/{zip_name}.zip"
            if not aws_manager._s3_object_exists(zip_key):
                logger.warning(f"Lambda資材が見つかりません: {zip_key}")
                logger.warning(f"{function_name}のデプロイをスキップします")
                continue

            logger.info(f"Lambda関数を更新中: {function_name} ({zip_name}.zip)")

            # Lambda関数のコードを更新
            try:
                aws_manager.lambda_client.update_function_code(
                    FunctionName=function_name,
                    S3Bucket=config.s3_bucket,
                    S3Key=zip_key,
                )
                logger.success(f"Lambda関数更新完了: {function_name}")
                deployed_count += 1
            except Exception as e:
                logger.error(f"Lambda関数更新に失敗しました: {function_name} - {e}")

        logger.info(
            f"バックエンドデプロイ結果: {deployed_count}/{total_count} 個のLambda関数が更新されました"
        )

        if deployed_count > 0:
            logger.success("バックエンドデプロイ完了")
        else:
            logger.warning("バックエンドデプロイで更新されたLambda関数がありません")

    except Exception as e:
        logger.error(f"バックエンドデプロイに失敗しました: {e}")
        return


def setup_root_user(
    config: ConfigManager, aws_manager: AWSResourceManager, logger: Logger
) -> None:
    """rootユーザー作成・確認"""
    logger.debug("")
    logger.debug("============================================================")
    logger.debug(" rootユーザー作成・確認")
    logger.debug("============================================================")
    logger.debug("")

    # パラメータからRootUserEmailを取得
    root_email = config.parameters.get("RootUserEmail", "")

    if not root_email:
        logger.error("RootUserEmail がパラメータファイルに設定されていません")
        return

    # メールアドレスバリデーション
    denied_emails = [
        "",
        "your-email@your-domain.com",
        "admin@example.com",
        "root@example.com",
    ]

    if root_email in denied_emails:
        logger.error("RootUserEmail に実際のメールアドレスを設定してください")
        logger.info(f"現在の値: {root_email}")
        return

    # User Pool ID取得
    user_pool_id = aws_manager.get_stack_output("UserPoolId")

    if not user_pool_id:
        logger.debug(
            "User Pool ID が取得できませんでした。ユーザー管理機能がデプロイされていない可能性があります"
        )
        return

    # Config Table名取得
    config_table = aws_manager.get_stack_output("ManageTableName")

    if not config_table:
        logger.error("Config Table 名の取得に失敗しました")
        return

    # rootユーザー存在チェック
    try:
        response = aws_manager.dynamodb_client.scan(
            TableName=config_table,
            FilterExpression="#type = :type AND #role = :role",
            ExpressionAttributeNames={"#type": "type", "#role": "role"},
            ExpressionAttributeValues={
                ":type": {"S": "user_info"},
                ":role": {"S": "root"},
            },
        )

        if response["Count"] > 0:
            logger.info("既存のrootユーザーが検出されました。スキップします。")
            return

        # rootユーザー新規作成
        logger.debug("rootユーザーを作成中...")

        # Cognitoにユーザー作成
        cognito_response = aws_manager.cognito_client.admin_create_user(
            UserPoolId=user_pool_id,
            Username=root_email,
            UserAttributes=[
                {"Name": "email", "Value": root_email},
                {"Name": "email_verified", "Value": "true"},
            ],
            DesiredDeliveryMediums=["EMAIL"],
        )

        cognito_sub = cognito_response["User"]["Username"]
        logger.debug(f"Cognito ユーザー作成完了: {cognito_sub}")

        # DynamoDBに保存（JST対応）
        jst = timezone(timedelta(hours=9))
        created_at = datetime.now(jst).strftime("%Y-%m-%dT%H:%M:%S+09:00")

        aws_manager.dynamodb_client.put_item(
            TableName=config_table,
            Item={
                "id": {"S": f"USER#{cognito_sub}"},
                "type": {"S": "user_info"},
                "email": {"S": root_email},
                "username": {"S": "システム管理者"},
                "role": {"S": "root"},
                "created_at": {"S": created_at},
                "updated_at": {"S": created_at},
            },
        )

        logger.debug("rootユーザーの作成が完了しました")
        logger.debug(f"メールアドレス: {root_email}")
        logger.debug("仮パスワードがメールアドレスに送信されています")

    except Exception as e:
        logger.error(f"rootユーザー作成に失敗しました: {e}")


def setup_ai_cost_seed(
    config: ConfigManager, aws_manager: AWSResourceManager, logger: Logger
) -> None:
    """パラメータファイルのAIコスト初期値をconfigテーブルに投入する。"""
    profile_id = config.parameters.get("AiCostInferenceProfileId", "").strip()
    if not profile_id:
        logger.info("AIコスト料金マスタ初期投入はスキップします（AiCostInferenceProfileId未設定）")
        return

    required_keys = [
        "AiCostPricePer1MInputTokens",
        "AiCostPricePer1MOutputTokens",
        "AiCostPricePer1MCacheWriteInputTokens",
        "AiCostPricePer1MCacheReadInputTokens",
    ]
    missing_keys = [key for key in required_keys if not config.parameters.get(key)]
    if missing_keys:
        logger.warning(
            "AIコスト料金マスタ初期投入をスキップします。不足パラメータ: "
            + ", ".join(missing_keys)
        )
        return

    config_table = aws_manager.get_stack_output("ManageTableName")
    if not config_table:
        logger.error("Config Table 名の取得に失敗しました")
        return

    jst = timezone(timedelta(hours=9))
    created_at = datetime.now(jst).strftime("%Y-%m-%dT%H:%M:%S+09:00")

    try:
        aws_manager.dynamodb_client.put_item(
            TableName=config_table,
            Item={
                "id": {"S": profile_id},
                "type": {"S": "ai_cost"},
                "price_per_1m_input_tokens": {
                    "N": config.parameters["AiCostPricePer1MInputTokens"]
                },
                "price_per_1m_output_tokens": {
                    "N": config.parameters["AiCostPricePer1MOutputTokens"]
                },
                "price_per_1m_cache_write_input_tokens": {
                    "N": config.parameters["AiCostPricePer1MCacheWriteInputTokens"]
                },
                "price_per_1m_cache_read_input_tokens": {
                    "N": config.parameters["AiCostPricePer1MCacheReadInputTokens"]
                },
                "created_at": {"S": created_at},
                "updated_at": {"S": created_at},
            },
            ConditionExpression="attribute_not_exists(id) AND attribute_not_exists(#type)",
            ExpressionAttributeNames={"#type": "type"},
        )
        logger.success(f"AIコスト料金マスタを初期投入しました: {profile_id}")
    except aws_manager.dynamodb_client.exceptions.ConditionalCheckFailedException:
        logger.info(
            f"AIコスト料金マスタは既に存在するためスキップします: {profile_id}"
        )
    except Exception as e:
        logger.error(f"AIコスト料金マスタ初期投入に失敗しました: {e}")


# ============================================================
# メイン処理
# ============================================================


def show_banner():
    """バナー表示"""
    print("")
    print("============================================================")
    print(" ReviewMaster デプロイスクリプト")
    print("============================================================")
    print("")


def main():
    """メイン処理"""
    # コマンドライン引数パーサー
    parser = argparse.ArgumentParser(
        description="ReviewMaster CloudFormation ネストテンプレート デプロイスクリプト (CloudShell専用)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
使用例:
  # 基本実行（自動的にログファイルに詳細ログを記録）
  %(prog)s -p s3://my-bucket/reviewmaster -r ap-northeast-1

  # デバッグモード実行（標準出力にも詳細ログを表示）
  %(prog)s -p s3://my-bucket/reviewmaster -r ap-northeast-1 --debug

  # カスタムログファイルパス
  %(prog)s -p s3://my-bucket/reviewmaster -r ap-northeast-1 --log-file /path/to/deploy.log

  # ログファイル出力を無効化（標準出力のみ）
  %(prog)s -p s3://my-bucket/reviewmaster -r ap-northeast-1 --no-log-file

  # 確認プロンプトなしで自動実行
  %(prog)s -p s3://my-bucket/reviewmaster -r ap-northeast-1 --yes

  # カスタムスタック名
  %(prog)s -p s3://my-bucket/reviewmaster -r ap-northeast-1 --stack-name "MyCompany-reviewmaster"

ログ出力について:
  - デフォルトでログファイルに詳細ログ（DEBUG）を記録
  - 標準出力は通常ログ（INFO）のみ表示
  - --debugオプションで標準出力にも詳細ログを表示
  - --no-log-fileで標準出力のみに変更可能
        """,
    )

    # 必須パラメータ
    parser.add_argument(
        "-p",
        "--s3-path",
        required=True,
        help="S3資材配置パス (例: s3://my-bucket/reviewmaster)",
    )
    parser.add_argument(
        "-r", "--region", required=True, help="AWSリージョン (例: ap-northeast-1)"
    )

    # オプションパラメータ
    parser.add_argument(
        "-s",
        "--stack-name",
        default="ReviewMaster-Infrastructure",
        help="CloudFormationスタック名 (デフォルト: ReviewMaster-Infrastructure)",
    )
    parser.add_argument(
        "-f",
        "--parameters-file",
        default="nested-parameters.txt",
        help="パラメータファイル名 (デフォルト: nested-parameters.txt)",
    )
    parser.add_argument(
        "-d",
        "--debug",
        action="store_true",
        help="デバッグモード（詳細な実行ログを表示）",
    )
    parser.add_argument(
        "-y", "--yes", action="store_true", help="確認プロンプトをスキップして自動実行"
    )
    parser.add_argument(
        "-l",
        "--log-file",
        default=None,
        help="ログファイルパス（デフォルト: ./reviewmaster_deploy_YYYYMMDD_HHMMSS.log）",
    )
    parser.add_argument(
        "--no-log-file",
        action="store_true",
        help="ログファイル出力を無効化（標準出力のみ）",
    )

    args = parser.parse_args()

    # ロガー初期化
    logger = Logger(
        debug_mode=args.debug, log_file=args.log_file, disable_file_log=args.no_log_file
    )

    try:
        # バナー表示
        show_banner()

        # ログ設定の表示
        if not args.no_log_file:
            if logger.log_file:
                logger.info(f"ログファイル: {logger.log_file} (DEBUGレベル)")
            else:
                logger.warning("ログファイルの作成に失敗しました")
        else:
            logger.info("ログファイル出力: 無効")

        if args.debug:
            logger.info("デバッグモード: 有効（標準出力にDEBUGレベルを表示）")
        else:
            logger.info("標準出力: INFOレベル")

        # 設定マネージャー初期化
        config = ConfigManager(args, logger)

        # AWSリソースマネージャー初期化
        aws_manager = AWSResourceManager(config, logger)

        # 前提条件チェック
        aws_manager.check_prerequisites()

        # S3資材存在確認
        aws_manager.check_s3_resources()

        # パラメータファイルダウンロード
        config.load_parameters_from_s3(aws_manager.s3_client)

        # テンプレート構文チェック
        aws_manager.validate_template()

        logger.info("デプロイパラメータ:")
        for key, value in config.parameters.items():
            logger.info(f"  {key}={value}")

        # 実行確認
        if not config.skip_confirmation:
            confirm_execution(config, logger)
        else:
            logger.info("確認プロンプトをスキップしてデプロイを開始します...")

        # 二段階デプロイ実行
        deploy_two_stage(config, aws_manager, logger)

        # rootユーザー作成・確認
        setup_root_user(config, aws_manager, logger)

        # AIコスト料金マスタ初期投入
        setup_ai_cost_seed(config, aws_manager, logger)

        # デプロイ完了メッセージ
        logger.info("")
        logger.info("============================================================")
        logger.info(" デプロイ完了")
        logger.info("============================================================")
        logger.info("")

        # 主要URL表示
        cloudfront_url = aws_manager.get_stack_output("CloudFrontDistributionUrl")
        api_url = aws_manager.get_stack_output("ApiGatewayUrl")

        if cloudfront_url:
            logger.info(f"フロントエンドURL: {cloudfront_url}")
        if api_url:
            logger.info(f"API URL: {api_url}")

        root_email = config.parameters.get("RootUserEmail", "")
        if root_email and root_email not in [
            "your-email@your-domain.com",
            "admin@example.com",
        ]:
            logger.info(f"rootユーザーアドレス: {root_email}")

        logger.info("")
        logger.info(f"スタック名: {config.stack_name}")
        logger.info(f"リージョン: {config.region}")
        logger.info(f"S3ベースパス: {config.s3_base_path}")
        logger.info("総スタック数: 22 (1 Main + 7 Core + 15 API Resources)")
        logger.info("")
        logger.success("デプロイが正常に完了しました！")

    except KeyboardInterrupt:
        logger.info("\nデプロイがキャンセルされました")
        sys.exit(1)
    except Exception as e:
        logger.error(f"デプロイに失敗しました: {e}")
        if args.debug:
            import traceback

            traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
