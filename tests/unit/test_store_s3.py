"""The `s3` backend against moto (store LLD § `s3` Backend)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from botocore.exceptions import EndpointConnectionError

from tests.conftest import (
    AWS_KEY_ID,
    AWS_SECRET_KEY,
    BUCKET,
    MARKER_KEY,
    AwsCalls,
    Project,
    sha256_b64,
    sha256_hex,
    store_init,
)
from urbanbio import config
from urbanbio.errors import ConfigError, IntegrityError, StorageError
from urbanbio.storage.store import open_store

DATA = b"synthetic object body\n" * 64
OTHER = b"a different synthetic body\n" * 32
CLIP = "clips/det-0123456789abcdef.flac"
ARCHIVE = "archive/site=site1/deployment=site1-dep001/date=2026-03-02/af-0123456789abcdef.flac"


@pytest.fixture
def s3_store(s3_env: Project, aws: AwsCalls) -> Any:
    result = store_init()
    assert result.exit_code == 0, result.output
    opened = open_store(config.load(with_sites=False))
    aws.calls.clear()
    return opened


def put(store: Any, key: str, data: bytes = DATA, **kwargs: Any) -> Any:
    return store.put(key, data, sha256=sha256_hex(data), **kwargs)


# --- Configuration and client ---------------------------------------------------------------


# @spec STORE-CFG-003
def test_bucket_comes_from_the_environment(s3_store: Any, aws: AwsCalls) -> None:
    put(s3_store, CLIP)
    assert {p["Bucket"] for _, p in aws.calls} == {BUCKET}


# @spec STORE-CFG-003
def test_a_bucket_key_in_configuration_is_refused(s3_env: Project) -> None:
    s3_env.operator_config(f'[store]\nbucket = "{BUCKET}"\n')
    with pytest.raises(ConfigError):
        config.load(with_sites=False)


# @spec STORE-CFG-003
@pytest.mark.parametrize("var", ["URBANBIO_BUCKET", "AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY"])
def test_opening_without_a_private_variable_is_a_config_error(s3_env: Project, var: str) -> None:
    assert store_init().exit_code == 0
    s3_env.monkeypatch.delenv(var)
    with pytest.raises(ConfigError):
        open_store(config.load(with_sites=False))


# @spec STORE-S3-001
def test_client_uses_only_the_environment_credentials_and_configured_region(
    s3_env: Project, aws: AwsCalls, tmp_path: Path
) -> None:
    # Shared files that name other credentials, another region, and a profile that doesn't
    # exist: reading any of them changes the client or fails.
    aws_dir = tmp_path / "aws"
    aws_dir.mkdir()
    (aws_dir / "credentials").write_text(
        "[default]\naws_access_key_id = AKIASHAREDFILE000000\naws_secret_access_key = shared\n"
    )
    (aws_dir / "config").write_text("[default]\nregion = eu-west-1\n")
    s3_env.monkeypatch.setenv("AWS_SHARED_CREDENTIALS_FILE", str(aws_dir / "credentials"))
    s3_env.monkeypatch.setenv("AWS_CONFIG_FILE", str(aws_dir / "config"))
    assert store_init().exit_code == 0
    s3_env.monkeypatch.setenv("AWS_PROFILE", "no-such-profile")
    store = open_store(config.load(with_sites=False))
    put(store, CLIP)
    assert aws.clients
    for client in aws.clients:
        creds = client._request_signer._credentials
        assert (creds.access_key, creds.secret_key) == (AWS_KEY_ID, AWS_SECRET_KEY)
        assert client.meta.region_name == "us-east-1"


# @spec STORE-S3-002
def test_client_retries_in_standard_mode_with_five_attempts(s3_store: Any, aws: AwsCalls) -> None:
    put(s3_store, CLIP)
    for client in aws.clients:
        assert client.meta.config.retries == {"mode": "standard", "total_max_attempts": 5}


# --- put ------------------------------------------------------------------------------------


# @spec STORE-S3-003
def test_put_is_one_checksummed_put_then_a_checksummed_head(s3_store: Any, aws: AwsCalls) -> None:
    put(s3_store, CLIP, metadata={"mask-version": "1"})
    assert aws.ops(CLIP) == ["PutObject", "HeadObject"]
    (sent,) = aws.params("PutObject", CLIP)
    assert sent["ChecksumAlgorithm"] == "SHA256"
    assert sent["ChecksumSHA256"] == sha256_b64(DATA)
    assert sent["Metadata"] == {"mask-version": "1"}
    (confirm,) = aws.params("HeadObject", CLIP)
    assert confirm["ChecksumMode"] == "ENABLED"
    assert "CreateMultipartUpload" not in aws.ops()


# @spec STORE-S3-003, STORE-API-003
@pytest.mark.parametrize("field", ["ChecksumSHA256", "ContentLength"])
def test_a_confirmation_that_disagrees_raises(s3_store: Any, aws: AwsCalls, field: str) -> None:
    def tamper(params: dict[str, Any], response: dict[str, Any]) -> dict[str, Any]:
        wrong = sha256_b64(OTHER) if field == "ChecksumSHA256" else len(DATA) + 1
        return {**response, field: wrong}

    aws.after["HeadObject"] = tamper
    with pytest.raises(IntegrityError) as caught:
        put(s3_store, CLIP)
    assert caught.value.reason_code == "store_checksum_mismatch"


# @spec STORE-S3-004
def test_put_without_overwrite_is_conditional(s3_store: Any, aws: AwsCalls) -> None:
    put(s3_store, CLIP)
    (sent,) = aws.params("PutObject", CLIP)
    assert sent["IfNoneMatch"] == "*"
    # One request decides existence: no HeadObject before the PUT.
    assert aws.ops(CLIP)[0] == "PutObject"


# @spec STORE-S3-004
def test_an_object_created_elsewhere_fails_the_precondition(s3_store: Any, aws: AwsCalls) -> None:
    aws.raw.put_object(Bucket=BUCKET, Key=CLIP, Body=OTHER, ChecksumAlgorithm="SHA256")
    with pytest.raises(StorageError) as caught:
        put(s3_store, CLIP)
    assert caught.value.reason_code == "store_object_exists"


# @spec STORE-S3-005, STORE-API-007
def test_an_allowed_overwrite_sends_the_etag_from_its_head(s3_store: Any, aws: AwsCalls) -> None:
    put(s3_store, ARCHIVE, metadata={"mask-version": "1"})
    etag = aws.raw.head_object(Bucket=BUCKET, Key=ARCHIVE)["ETag"]
    aws.calls.clear()
    put(s3_store, ARCHIVE, OTHER, metadata={"mask-version": "2"}, overwrite=True)
    ops = aws.ops(ARCHIVE)
    assert ops.index("HeadObject") < ops.index("PutObject")
    (sent,) = aws.params("PutObject", ARCHIVE)
    assert sent["IfMatch"] == etag
    assert "IfNoneMatch" not in sent


# @spec STORE-S3-005, STORE-API-009
def test_a_change_after_the_head_fails_the_overwrite(s3_store: Any, aws: AwsCalls) -> None:
    put(s3_store, ARCHIVE, metadata={"mask-version": "1"})
    changed = b"changed by someone else\n"
    done = False

    def change_after_head(params: dict[str, Any], response: dict[str, Any]) -> dict[str, Any]:
        nonlocal done
        if params["Key"] == ARCHIVE and not done:
            done = True
            aws.raw.put_object(
                Bucket=BUCKET,
                Key=ARCHIVE,
                Body=changed,
                ChecksumAlgorithm="SHA256",
                Metadata={"mask-version": "1"},
            )
        return response

    aws.after["HeadObject"] = change_after_head
    with pytest.raises(StorageError) as caught:
        put(s3_store, ARCHIVE, OTHER, metadata={"mask-version": "2"}, overwrite=True)
    assert caught.value.reason_code == "store_object_changed"
    body = aws.raw.get_object(Bucket=BUCKET, Key=ARCHIVE)["Body"].read()
    assert body == changed


# --- head, get, list ------------------------------------------------------------------------


# @spec STORE-S3-006
def test_head_asks_for_the_checksum(s3_store: Any, aws: AwsCalls) -> None:
    put(s3_store, CLIP)
    aws.calls.clear()
    assert s3_store.head(CLIP).sha256 == sha256_hex(DATA)
    assert s3_store.head("clips/det-ffffffffffffffff.flac") is None
    assert all(p["ChecksumMode"] == "ENABLED" for p in aws.params("HeadObject"))


# @spec STORE-S3-007
def test_get_asks_for_the_checksum(s3_store: Any, aws: AwsCalls, tmp_path: Path) -> None:
    put(s3_store, CLIP)
    aws.calls.clear()
    s3_store.get(CLIP, tmp_path / "clip.flac")
    (sent,) = aws.params("GetObject", CLIP)
    assert sent["ChecksumMode"] == "ENABLED"


# @spec STORE-API-012
def test_get_of_an_object_that_disagrees_with_its_checksum_raises(
    s3_store: Any, aws: AwsCalls, tmp_path: Path
) -> None:
    # moto keeps a stated checksum that doesn't match the body, which stands in for an object
    # damaged in the store.
    aws.raw.put_object(
        Bucket=BUCKET,
        Key=CLIP,
        Body=OTHER,
        ChecksumAlgorithm="SHA256",
        ChecksumSHA256=sha256_b64(DATA),
    )
    with pytest.raises(IntegrityError) as caught:
        s3_store.get(CLIP, tmp_path / "clip.flac")
    assert caught.value.reason_code == "store_checksum_mismatch"


# @spec STORE-S3-008
def test_list_pages_through_every_key(s3_store: Any, aws: AwsCalls) -> None:
    keys = [f"clips/det-{i:016x}.flac" for i in range(1005)]
    for key in keys:
        aws.raw.put_object(Bucket=BUCKET, Key=key, Body=b"x")
    assert list(s3_store.list("clips/")) == keys
    assert len(aws.params("ListObjectsV2")) >= 2


# --- DuckDB ---------------------------------------------------------------------------------


class FakeDuckDB:
    """Records what is asked of a DuckDB connection."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    def __getattr__(self, name: str) -> Any:
        def record(*args: Any, **kwargs: Any) -> FakeDuckDB:
            self.calls.append(f"{name} {args!r} {kwargs!r}")
            return self

        return record


# @spec STORE-S3-009
def test_table_glob_and_duckdb_secret(s3_store: Any) -> None:
    assert s3_store.table_glob("curated/detections/") == (
        f"s3://{BUCKET}/curated/detections/**/*.parquet"
    )
    conn = FakeDuckDB()
    s3_store.configure_duckdb(conn)
    text = "\n".join(conn.calls)
    assert "httpfs" in text
    assert "SECRET" in text.upper()
    assert "s3" in text.lower()
    for value in (AWS_KEY_ID, AWS_SECRET_KEY, "us-east-1"):
        assert value in text


# --- Failures and the marker ----------------------------------------------------------------


# @spec STORE-API-016
@pytest.mark.parametrize("op", ["PutObject", "HeadObject", "GetObject", "ListObjectsV2"])
def test_a_transient_failure_is_store_unavailable(
    s3_store: Any, aws: AwsCalls, tmp_path: Path, op: str
) -> None:
    if op != "PutObject":
        put(s3_store, CLIP)

    def unreachable(params: dict[str, Any]) -> None:
        raise EndpointConnectionError(endpoint_url="https://s3.us-east-1.amazonaws.com")

    aws.before[op] = unreachable
    with pytest.raises(StorageError) as caught:
        if op == "PutObject":
            put(s3_store, CLIP)
        elif op == "HeadObject":
            s3_store.head(CLIP)
        elif op == "GetObject":
            s3_store.get(CLIP, tmp_path / "clip.flac")
        else:
            list(s3_store.list("clips/"))
    assert caught.value.reason_code == "store_unavailable"


# @spec STORE-MARK-005
def test_open_store_reads_the_marker_first(s3_env: Project, aws: AwsCalls) -> None:
    assert store_init().exit_code == 0
    aws.calls.clear()
    open_store(config.load(with_sites=False))
    assert aws.calls
    first_op, first = aws.calls[0]
    assert first.get("Key") == MARKER_KEY
    assert first_op in ("GetObject", "HeadObject")


# @spec STORE-SAME-001
def test_same_filesystem_is_always_false_on_s3(s3_store: Any, s3_env: Project) -> None:
    assert s3_store.same_filesystem_as(s3_env.home / "urbanbio" / "quarantine") is False
