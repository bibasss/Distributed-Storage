"""Lab 8-9 Task 3: integration tests against the local MinIO server.

Needs `docker compose up -d` (S3 API on http://localhost:9001). The first two
tests are the ones given in the lab sheet; the module fixture `bucket` makes
sure the bucket exists first (on a fresh MinIO it does not).

Objects written by the added tests live under a per-run prefix and are
deleted at the end; the sheet's own test_dir/sample.txt is left in place.
"""
import hashlib
import os
import shutil
import subprocess
import time
import uuid

import boto3
import pytest
from botocore.client import Config
from botocore.exceptions import BotoCoreError, ClientError, EndpointConnectionError

import s3_client as storage

BUCKET = 'distributed-course-bucket'
RUN = f"pytest-{uuid.uuid4().hex[:8]}"          # prefix for this run's objects


@pytest.fixture(scope="module")
def s3_client():
    return boto3.client(
        's3',
        endpoint_url='http://localhost:9001',
        aws_access_key_id='minioadmin',
        aws_secret_access_key='minioadminpassword',
        config=Config(signature_version='s3v4'),
        region_name='us-east-1'
    )


@pytest.fixture(scope="module", autouse=True)
def bucket(s3_client):
    try:
        storage.init_storage()
    except EndpointConnectionError:
        pytest.exit("MinIO is not reachable on localhost:9001 - run `docker compose up -d`",
                    returncode=2)
    yield BUCKET
    keys, _ = storage.list_keys(prefix=f"{RUN}/")
    for key in keys:
        storage.delete_object(key)


def md5(data):
    return hashlib.md5(data).hexdigest()


# --- tests from the lab sheet --------------------------------------------------

def test_s3_bucket_existence(s3_client):
    buckets = [b['Name'] for b in s3_client.list_buckets()['Buckets']]
    assert 'distributed-course-bucket' in buckets


def test_s3_object_upload_and_read(s3_client):
    key = "test_dir/sample.txt"
    content = b"Stream Data Content"
    s3_client.put_object(Bucket='distributed-course-bucket', Key=key, Body=content)
    obj = s3_client.get_object(Bucket='distributed-course-bucket', Key=key)
    assert obj['Body'].read() == content


# --- metadata --------------------------------------------------------------------

def test_upload_file_with_custom_metadata_and_head(tmp_path):
    path = tmp_path / "notes.txt"
    path.write_bytes(b"metadata travels with the object\n" * 100)
    key = f"{RUN}/meta/notes.txt"

    seconds = storage.upload_object(key, str(path), {'Course': 'CSS4004', 'lab-id': '8-9'})
    assert seconds > 0

    # HEAD returns metadata without the body; user-metadata keys come back lower-cased.
    assert storage.read_object_metadata(key) == {'course': 'CSS4004', 'lab-id': '8-9'}
    head = storage.head_object(key)
    assert head['size'] == path.stat().st_size
    assert head['etag'] == md5(path.read_bytes())          # single-part PUT: ETag = MD5


def test_overwrite_replaces_data_and_metadata():
    key = f"{RUN}/overwrite/object.txt"
    etag1 = storage.put_bytes(key, b"version one", {'state': 'draft'})
    etag2 = storage.put_bytes(key, b"version two")

    # Objects are immutable: a PUT to the same key swaps the whole object,
    # including its metadata (no partial update, no append).
    assert etag1 != etag2
    assert storage.get_object_bytes(key) == b"version two"
    assert storage.read_object_metadata(key) == {}


# --- persistence and integrity ------------------------------------------------------

def test_large_file_multipart_upload_integrity(tmp_path):
    path = tmp_path / "large.bin"
    data = os.urandom(20 * 1024 * 1024)                   # 20 MiB -> 3 parts of 8 MiB
    path.write_bytes(data)
    key = f"{RUN}/large/large.bin"

    storage.upload_object(key, str(path), {'parts': 'multipart'})
    head = storage.head_object(key)
    assert head['size'] == len(data)
    assert head['etag'].endswith('-3')                    # multipart ETag: md5-of-md5s "-N"

    out = tmp_path / "large.out"
    storage.download_object(key, str(out))
    assert md5(out.read_bytes()) == md5(data)


def test_bucket_integrity_listing_matches_objects():
    prefix = f"{RUN}/integrity/"
    bodies = {f"{prefix}obj-{i}.bin": os.urandom(1024 * (i + 1)) for i in range(5)}
    for key, body in bodies.items():
        storage.put_bytes(key, body)

    page = storage.s3.list_objects_v2(Bucket=BUCKET, Prefix=prefix)
    listed = {o['Key']: o for o in page['Contents']}
    assert set(listed) == set(bodies)
    for key, body in bodies.items():
        assert listed[key]['Size'] == len(body)
        assert listed[key]['ETag'].strip('"') == md5(body)
        assert storage.get_object_bytes(key) == body


@pytest.mark.skipif(shutil.which("docker") is None, reason="needs the docker CLI")
def test_object_persists_across_minio_restart():
    key = f"{RUN}/persist/survivor.txt"
    body = os.urandom(4096)
    storage.put_bytes(key, body, {'written-before': 'restart'})

    subprocess.run(["docker", "restart", "local_s3_minio"], check=True,
                   capture_output=True, timeout=60)
    deadline = time.monotonic() + 30
    while True:
        try:
            storage.s3.head_bucket(Bucket=BUCKET)
            break
        except (BotoCoreError, ClientError):              # connection refused / closed, 503
            if time.monotonic() > deadline:
                raise
            time.sleep(0.5)

    assert storage.get_object_bytes(key) == body
    assert storage.read_object_metadata(key) == {'written-before': 'restart'}


# --- semantics: consistency, namespace, deletes ---------------------------------------

def test_read_after_write_is_immediately_consistent():
    result = storage.read_after_write_staleness(f"{RUN}/consistency/probe.txt", versions=50)
    assert result['versions'] == 50
    assert result['stale_reads'] == 0                     # staleness bound = 0


def test_flat_namespace_directories_are_key_prefixes():
    base = f"{RUN}/tree/"
    for key in ("a.txt", "sub/b.txt", "sub/deeper/c.txt"):
        storage.put_bytes(base + key, key.encode())

    keys, prefixes = storage.list_keys(prefix=base, delimiter='/')
    assert keys == [base + "a.txt"]
    assert prefixes == [base + "sub/"]                    # a "directory" is only a common prefix
    all_keys, _ = storage.list_keys(prefix=base)
    assert len(all_keys) == 3
    assert not storage.object_exists(base + "sub/")       # no directory object exists


def test_missing_object_and_idempotent_delete():
    key = f"{RUN}/delete/temp.txt"
    with pytest.raises(ClientError) as info:
        storage.s3.head_object(Bucket=BUCKET, Key=key)
    assert info.value.response['Error']['Code'] in ('404', 'NotFound')

    storage.put_bytes(key, b"short-lived")
    assert storage.object_exists(key)
    storage.delete_object(key)
    assert not storage.object_exists(key)
    storage.delete_object(key)                            # deleting again is not an error
