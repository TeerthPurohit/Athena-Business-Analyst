"""Durable content-addressed object-storage boundary."""

from typing import Protocol


class ObjectStore(Protocol):
    def put_if_absent(self, key: str, content: bytes, media_type: str) -> str:
        """Persist content under a deterministic key and return its durable reference."""

    def get(self, reference: str) -> bytes:
        """Read content from a reference returned by put_if_absent."""

    def delete(self, reference: str) -> bool:
        """Delete content from a reference. Return True if deleted, False if not found."""

    def delete_prefix(self, prefix: str) -> int:
        """Delete all objects matching key prefix. Return count of deleted objects."""

class S3ObjectStore:
    """S3-compatible store configured only when the ingestion capability is invoked."""

    def __init__(
        self,
        *,
        endpoint: str,
        access_key: str,
        secret_key: str,
        bucket: str,
        region: str,
        force_path_style: bool,
    ):
        try:
            import boto3
            from botocore.config import Config
            from botocore.exceptions import ClientError
        except ImportError as error:
            raise RuntimeError("boto3 is required for Athena object storage.") from error

        self._bucket = bucket
        self._client_error = ClientError
        self._client = boto3.client(
            "s3",
            endpoint_url=endpoint,
            region_name=region,
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
            config=Config(
                s3={"addressing_style": "path" if force_path_style else "auto"}
            ),
        )

    def put_if_absent(self, key: str, content: bytes, media_type: str) -> str:
        try:
            self._client.head_object(Bucket=self._bucket, Key=key)
        except self._client_error as error:
            status = error.response.get("ResponseMetadata", {}).get("HTTPStatusCode")
            if status != 404:
                raise
            self._client.put_object(
                Bucket=self._bucket,
                Key=key,
                Body=content,
                ContentType=media_type,
            )
        return f"s3://{self._bucket}/{key}"

    def get(self, reference: str) -> bytes:
        prefix = f"s3://{self._bucket}/"
        if not reference.startswith(prefix) or len(reference) == len(prefix):
            raise ValueError("Invalid object storage reference")
        key = reference[len(prefix):]
        return self._client.get_object(Bucket=self._bucket, Key=key)["Body"].read()

    def delete(self, reference: str) -> bool:
        prefix = f"s3://{self._bucket}/"
        if reference.startswith(prefix):
            key = reference[len(prefix):]
        elif not reference.startswith("s3://"):
            key = reference.lstrip("/")
        else:
            raise ValueError(f"Foreign object storage reference cannot be deleted: {reference}")
        if not key:
            raise ValueError("Empty key in object storage reference")
        try:
            self._client.delete_object(Bucket=self._bucket, Key=key)
            return True
        except self._client_error as error:
            status = error.response.get("ResponseMetadata", {}).get("HTTPStatusCode")
            if status == 404:
                return False
            raise

    def delete_prefix(self, prefix: str) -> int:
        bucket_prefix = f"s3://{self._bucket}/"
        if prefix.startswith(bucket_prefix):
            prefix = prefix[len(bucket_prefix):]
        elif prefix.startswith("s3://"):
            raise ValueError(f"Foreign bucket prefix cannot be deleted: {prefix}")
        else:
            prefix = prefix.lstrip("/")

        paginator = self._client.get_paginator("list_objects_v2")
        deleted_count = 0
        for page in paginator.paginate(Bucket=self._bucket, Prefix=prefix):
            objects = [{"Key": obj["Key"]} for obj in page.get("Contents", [])]
            if objects:
                self._client.delete_objects(Bucket=self._bucket, Delete={"Objects": objects})
                deleted_count += len(objects)
        return deleted_count
