"""S3 object lifecycle against the local MinIO server (Lab 8-9, Task 2).

The first three functions are the ones given in the lab sheet. The rest
complete the lifecycle named in the objectives: GET, HEAD metadata, listing
by prefix, delete, and a read-after-write ("bounded staleness") probe.

Endpoint and credentials default to the docker-compose setup and can be
overridden with S3_ENDPOINT / S3_ACCESS_KEY / S3_SECRET_KEY (the benchmark
container uses http://minio:9000).

    python s3_client.py demo          # bucket + sample objects with metadata
    python s3_client.py staleness     # read-after-write probe, 200 versions
"""
import hashlib
import os
import statistics
import sys
import time

import boto3
from botocore.client import Config
from botocore.exceptions import ClientError

ENDPOINT_URL = os.environ.get("S3_ENDPOINT", "http://localhost:9001")
ACCESS_KEY = os.environ.get("S3_ACCESS_KEY", "minioadmin")
SECRET_KEY = os.environ.get("S3_SECRET_KEY", "minioadminpassword")
BUCKET_NAME = 'distributed-course-bucket'


def make_client(endpoint_url=ENDPOINT_URL):
    return boto3.client(
        's3',
        endpoint_url=endpoint_url,
        aws_access_key_id=ACCESS_KEY,
        aws_secret_access_key=SECRET_KEY,
        config=Config(signature_version='s3v4'),
        region_name='us-east-1'
    )


s3 = make_client()


# --- functions from the lab sheet -------------------------------------------

def init_storage():
    buckets = [b['Name'] for b in s3.list_buckets()['Buckets']]
    if BUCKET_NAME not in buckets:
        try:
            s3.create_bucket(Bucket=BUCKET_NAME)
        except ClientError as err:          # created concurrently by another client
            if err.response['Error']['Code'] != 'BucketAlreadyOwnedByYou':
                raise


def upload_object(key_name, file_path, metadata=None):
    """Upload a file (multipart above 8 MB, done by boto3). Returns seconds.
    metadata=None instead of the sheet's mutable default {}."""
    start_time = time.perf_counter()
    s3.upload_file(
        Filename=file_path,
        Bucket=BUCKET_NAME,
        Key=key_name,
        ExtraArgs={'Metadata': dict(metadata or {})}
    )
    return time.perf_counter() - start_time


def read_object_metadata(key_name):
    """User metadata (x-amz-meta-*) of an object. S3 returns keys lower-cased."""
    response = s3.head_object(Bucket=BUCKET_NAME, Key=key_name)
    return response['Metadata']


# --- rest of the object lifecycle -------------------------------------------

def put_bytes(key_name, data, metadata=None, content_type='application/octet-stream'):
    """Single PUT. Returns the ETag (the MD5 of the body for a single-part PUT)."""
    response = s3.put_object(Bucket=BUCKET_NAME, Key=key_name, Body=data,
                             Metadata=dict(metadata or {}), ContentType=content_type)
    return response['ETag'].strip('"')


def get_object_bytes(key_name):
    return s3.get_object(Bucket=BUCKET_NAME, Key=key_name)['Body'].read()


def download_object(key_name, file_path):
    """Download to a file. Returns seconds."""
    start_time = time.perf_counter()
    s3.download_file(Bucket=BUCKET_NAME, Key=key_name, Filename=file_path)
    return time.perf_counter() - start_time


def head_object(key_name):
    """System and user metadata of an object, without its body."""
    r = s3.head_object(Bucket=BUCKET_NAME, Key=key_name)
    return {
        'size': r['ContentLength'],
        'etag': r['ETag'].strip('"'),
        'last_modified': r['LastModified'],
        'content_type': r.get('ContentType'),
        'metadata': r['Metadata'],
    }


def object_exists(key_name):
    try:
        s3.head_object(Bucket=BUCKET_NAME, Key=key_name)
        return True
    except ClientError as err:
        if err.response['Error']['Code'] in ('404', 'NoSuchKey', 'NotFound'):
            return False
        raise


def list_keys(prefix='', delimiter=None):
    """Keys under a prefix. With delimiter='/' the "directories" one level
    down come back as common prefixes: the namespace itself is flat."""
    kwargs = {'Bucket': BUCKET_NAME, 'Prefix': prefix}
    if delimiter:
        kwargs['Delimiter'] = delimiter
    keys, prefixes = [], []
    for page in s3.get_paginator('list_objects_v2').paginate(**kwargs):
        keys += [o['Key'] for o in page.get('Contents', [])]
        prefixes += [p['Prefix'] for p in page.get('CommonPrefixes', [])]
    return keys, prefixes


def delete_object(key_name):
    s3.delete_object(Bucket=BUCKET_NAME, Key=key_name)


def read_after_write_staleness(key_name, versions=200, timeout_s=5.0):
    """Bounded-staleness probe: overwrite key_name `versions` times and after
    every PUT read it back until the new version is visible. Returns how many
    reads returned an older version and the largest staleness window seen.
    A strongly consistent store (MinIO, AWS S3 since Dec 2020) gives 0 / 0."""
    stale_reads, windows = 0, []
    for i in range(versions):
        body = f"version-{i}".encode()
        put_bytes(key_name, body)
        written = time.perf_counter()
        while True:
            if get_object_bytes(key_name) == body:
                windows.append((time.perf_counter() - written) * 1000)
                break
            stale_reads += 1
            if time.perf_counter() - written > timeout_s:
                raise TimeoutError(f"version {i} not visible after {timeout_s}s")
    return {
        'versions': versions,
        'stale_reads': stale_reads,
        'max_window_ms': max(windows),
        'median_read_ms': statistics.median(windows),
    }


def md5_of_file(file_path):
    digest = hashlib.md5()
    with open(file_path, 'rb') as f:
        for chunk in iter(lambda: f.read(1 << 20), b''):
            digest.update(chunk)
    return digest.hexdigest()


# --- command line -------------------------------------------------------------

def demo():
    """Objects with custom metadata for the MinIO Console screenshot."""
    init_storage()
    os.makedirs('data', exist_ok=True)
    report = os.path.join('data', 'lab-report-draft.txt')
    with open(report, 'w') as f:
        f.write("Lab 8-9 sample object\n" * 200)
    sample = os.path.join('data', 'sample_10m.bin')
    if not os.path.exists(sample):
        with open(sample, 'wb') as f:
            f.write(os.urandom(10 * 1024 * 1024))
    objects = [
        ('reports/lab-report-draft.txt', report,
         {'course': 'CSS4004', 'lab': '8-9', 'author': 'student', 'content-kind': 'text'}),
        ('datasets/sample_10m.bin', sample,
         {'course': 'CSS4004', 'origin': 'os.urandom', 'md5': md5_of_file(sample)}),
    ]
    for key, path, meta in objects:
        seconds = upload_object(key, path, meta)
        print(f"PUT  {key:32s} {os.path.getsize(path):>10,d} B  {seconds * 1000:7.1f} ms")
        print(f"HEAD {key:32s} metadata={read_object_metadata(key)}")
    keys, prefixes = list_keys(delimiter='/')
    print(f"LIST bucket root: keys={keys} prefixes={prefixes}")


def main():
    command = sys.argv[1] if len(sys.argv) > 1 else 'demo'
    if command == 'demo':
        demo()
    elif command == 'staleness':
        init_storage()
        print(read_after_write_staleness('consistency/probe.txt'))
    else:
        sys.exit(f"unknown command {command!r} (demo | staleness)")


if __name__ == '__main__':
    main()
