import sys
from types import ModuleType
from unittest.mock import Mock


def test_s3_store_uses_neon_region_and_path_style(monkeypatch):
    from athena.storage import S3ObjectStore

    client = Mock()
    boto3 = ModuleType("boto3")
    boto3.client = client
    botocore = ModuleType("botocore")
    botocore_config = ModuleType("botocore.config")
    botocore_config.Config = lambda **kwargs: kwargs
    monkeypatch.setitem(sys.modules, "boto3", boto3)
    botocore_exceptions = ModuleType("botocore.exceptions")
    botocore_exceptions.ClientError = RuntimeError
    monkeypatch.setitem(sys.modules, "botocore.exceptions", botocore_exceptions)
    monkeypatch.setitem(sys.modules, "botocore", botocore)
    monkeypatch.setitem(sys.modules, "botocore.config", botocore_config)

    S3ObjectStore(
        endpoint="https://storage.example.neon.tech",
        access_key="token",
        secret_key="secret",
        bucket="athena-sources",
        region="ap-southeast-1",
        force_path_style=True,
    )

    client.assert_called_once_with(
        "s3",
        endpoint_url="https://storage.example.neon.tech",
        region_name="ap-southeast-1",
        aws_access_key_id="token",
        aws_secret_access_key="secret",
        config={"s3": {"addressing_style": "path"}},
    )
