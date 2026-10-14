"""The `s3` backend (store LLD § `s3` Backend).

Credentials, bucket, and region come only from the environment and configuration, passed to
the client explicitly. The session never reads the AWS shared config or credentials files.
"""

from __future__ import annotations

import base64
import hashlib
import os
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import botocore.session
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError, FlexibleChecksumError

from urbanbio.errors import ConfigError, IntegrityError, StorageError
from urbanbio.storage.store import BaseStore, StoredObject, source_size

RETRIES = {"mode": "standard", "total_max_attempts": 5}
CHUNK = 1 << 20
_MISSING = {"404", "NoSuchKey", "NotFound"}
_PRECONDITION = {"PreconditionFailed", "ConditionalRequestConflict"}
_BAD_DIGEST = {"BadDigest", "InvalidDigest", "XAmzContentSHA256Mismatch"}


class _IsolatedSession(botocore.session.Session):
    """A botocore session that never reads the AWS shared config or credentials files."""

    def get_scoped_config(self) -> dict[str, Any]:
        return {}

    @property
    def full_config(self) -> dict[str, Any]:
        return {"profiles": {}}


def _b64(sha256_hex: str) -> str:
    return base64.b64encode(bytes.fromhex(sha256_hex)).decode()


def _hex(sha256_b64: str | None) -> str:
    return base64.b64decode(sha256_b64).hex() if sha256_b64 else ""


def _code(err: ClientError) -> str:
    return str(err.response.get("Error", {}).get("Code", ""))


def _status(err: ClientError) -> int:
    return int(err.response.get("ResponseMetadata", {}).get("HTTPStatusCode", 0) or 0)


class S3Store(BaseStore):
    # @spec STORE-S3-001, STORE-S3-002
    def __init__(self, *, bucket: str, region: str, key_id: str, secret_key: str) -> None:
        self._bucket = bucket
        self._region = region
        self._key_id = key_id
        self._secret_key = secret_key
        session = _IsolatedSession()
        session.set_config_variable("ignore_configured_endpoint_urls", True)
        self._client = session.create_client(
            "s3",
            region_name=region,
            aws_access_key_id=key_id,
            aws_secret_access_key=secret_key,
            config=Config(retries=dict(RETRIES)),
        )

    # @spec STORE-CFG-003, CLI-PRIV-007
    @classmethod
    def from_environment(cls, store_cfg: Any) -> S3Store:
        names = ("URBANBIO_BUCKET", "AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY")
        values = {name: os.environ.get(name, "") for name in names}
        missing = [name for name, value in values.items() if not value]
        if missing:
            raise ConfigError(
                f"the s3 store needs these environment variables: {', '.join(missing)}",
                reason_code="missing_private_var",
            )
        return cls(
            bucket=values["URBANBIO_BUCKET"],
            region=store_cfg.region or "us-east-1",
            key_id=values["AWS_ACCESS_KEY_ID"],
            secret_key=values["AWS_SECRET_ACCESS_KEY"],
        )

    # --- Errors -----------------------------------------------------------------------------

    # @spec STORE-API-016
    def _run(self, op: Any) -> Any:
        try:
            return op()
        except FlexibleChecksumError as err:
            raise IntegrityError(
                "the object's content doesn't match its stored checksum",
                reason_code="store_checksum_mismatch",
            ) from err
        except BotoCoreError as err:
            raise StorageError(
                f"AWS S3 is unreachable: {type(err).__name__}", reason_code="store_unavailable"
            ) from err
        except ClientError as err:
            if _status(err) >= 500 or _code(err) in {"SlowDown", "Throttling", "RequestTimeout"}:
                raise StorageError(
                    f"AWS S3 is unavailable: {_code(err)}", reason_code="store_unavailable"
                ) from err
            raise StorageError(
                f"AWS S3 refused the request: {_code(err)}", reason_code="store_request_failed"
            ) from err

    # --- Operations -------------------------------------------------------------------------

    # @spec STORE-S3-006
    def _head(self, key: str) -> tuple[StoredObject, str] | None:
        try:
            r = self._client.head_object(Bucket=self._bucket, Key=key, ChecksumMode="ENABLED")
        except ClientError as err:
            if _code(err) in _MISSING or _status(err) == 404:
                return None
            raise
        obj = StoredObject(
            key=key,
            size=int(r["ContentLength"]),
            sha256=_hex(r.get("ChecksumSHA256")),
            metadata=dict(r.get("Metadata", {})),
        )
        return obj, r["ETag"]

    # @spec STORE-S3-003, STORE-S3-004, STORE-S3-005, STORE-API-009
    def _write(
        self,
        key: str,
        source: Path | bytes,
        sha256: str,
        metadata: dict[str, str],
        expect: str | None,
    ) -> StoredObject:
        size = source_size(source)
        params: dict[str, Any] = {
            "Bucket": self._bucket,
            "Key": key,
            "ChecksumAlgorithm": "SHA256",
            "ChecksumSHA256": _b64(sha256),
            "Metadata": metadata,
        }
        if expect is None:
            params["IfNoneMatch"] = "*"
        else:
            params["IfMatch"] = expect
        try:
            if isinstance(source, bytes | bytearray):
                self._client.put_object(Body=bytes(source), **params)
            else:
                with open(source, "rb") as body:
                    self._client.put_object(Body=body, **params)
        except ClientError as err:
            if _code(err) in _PRECONDITION or _status(err) == 412:
                if expect is None:
                    raise StorageError(
                        f"an object already exists at {key}", reason_code="store_object_exists"
                    ) from err
                raise StorageError(
                    f"{key} changed since it was read", reason_code="store_object_changed"
                ) from err
            if _code(err) in _BAD_DIGEST:
                raise IntegrityError(
                    f"AWS S3 computed a different SHA-256 for {key}",
                    reason_code="store_checksum_mismatch",
                ) from err
            raise
        found = self._head(key)
        if found is None or found[0].sha256 != sha256 or found[0].size != size:
            raise IntegrityError(
                f"AWS S3 didn't confirm the length and SHA-256 written to {key}",
                reason_code="store_checksum_mismatch",
            )
        return found[0]

    # @spec STORE-S3-007
    def _get(self, key: str, dest: Path) -> StoredObject:
        try:
            r = self._client.get_object(Bucket=self._bucket, Key=key, ChecksumMode="ENABLED")
        except ClientError as err:
            if _code(err) in _MISSING or _status(err) == 404:
                raise StorageError(
                    f"no object at {key}", reason_code="store_object_missing"
                ) from err
            raise
        stored = _hex(r.get("ChecksumSHA256"))
        digest = hashlib.sha256()
        size = 0
        try:
            with open(dest, "wb") as out:
                for chunk in r["Body"].iter_chunks(CHUNK):
                    digest.update(chunk)
                    size += len(chunk)
                    out.write(chunk)
            if digest.hexdigest() != stored:
                raise IntegrityError(
                    f"{key} doesn't match its stored SHA-256",
                    reason_code="store_checksum_mismatch",
                )
        except BaseException:
            dest.unlink(missing_ok=True)
            raise
        return StoredObject(key, size, stored, dict(r.get("Metadata", {})))

    # @spec STORE-S3-008
    def _list(self, prefix: str) -> Iterator[str]:
        pages = self._client.get_paginator("list_objects_v2").paginate(
            Bucket=self._bucket, Prefix=prefix
        )
        return [obj["Key"] for page in pages for obj in page.get("Contents", [])]

    def _delete(self, key: str) -> None:
        self._client.delete_object(Bucket=self._bucket, Key=key)

    def read_bytes(self, key: str) -> bytes | None:
        def op() -> bytes | None:
            try:
                r = self._client.get_object(Bucket=self._bucket, Key=key, ChecksumMode="ENABLED")
            except ClientError as err:
                if _code(err) in _MISSING or _status(err) == 404:
                    return None
                raise
            data = r["Body"].read()
            if hashlib.sha256(data).hexdigest() != _hex(r.get("ChecksumSHA256")):
                raise IntegrityError(
                    f"{key} doesn't match its stored SHA-256",
                    reason_code="store_checksum_mismatch",
                )
            return data

        return self._run(op)

    # --- DuckDB and filesystems -------------------------------------------------------------

    # @spec STORE-S3-009
    def table_glob(self, prefix: str) -> str:
        return f"s3://{self._bucket}/{prefix}**/*.parquet"

    # @spec STORE-S3-009
    def configure_duckdb(self, conn: Any) -> None:
        def quoted(value: str) -> str:
            return "'" + value.replace("'", "''") + "'"

        conn.execute("INSTALL httpfs")
        conn.execute("LOAD httpfs")
        conn.execute(
            "CREATE OR REPLACE SECRET urbanbio_store (TYPE s3, "
            f"KEY_ID {quoted(self._key_id)}, SECRET {quoted(self._secret_key)}, "
            f"REGION {quoted(self._region)})"
        )

    # @spec STORE-SAME-001
    def same_filesystem_as(self, path: Path) -> bool:
        return False
