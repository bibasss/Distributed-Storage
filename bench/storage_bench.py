"""Lab 8-9 Task 4: read latency, HDFS (WebHDFS) vs S3 (MinIO).

Runs inside the compose network (`docker compose run --rm bench`), because a
WebHDFS read is answered with a 307 redirect to the DataNode's container
hostname. Both systems are read over HTTP with keep-alive connections:

  HDFS  GET namenode:9870/webhdfs/v1/<path>?op=OPEN  -> 307 -> datanode:9864 (data)
  S3    GET minio:9000/<bucket>/<key>                 (data)

plus the metadata-only path: HDFS GETFILESTATUS (NameNode) vs S3 HEAD.
Every payload is random bytes, written once to both systems, verified by MD5
on the first read, then read N times. Results go to logs/storage_bench_results.{md,json}.
"""
import hashlib
import json
import math
import os
import platform
import statistics
import sys
import time

import boto3
import requests
from botocore.client import Config

WEBHDFS = os.environ.get("WEBHDFS_URL", "http://namenode:9870") + "/webhdfs/v1"
S3_ENDPOINT = os.environ.get("S3_ENDPOINT", "http://minio:9000")
BUCKET = "distributed-course-bucket"
HDFS_DIR = "/user/student/bench"
USER = {"user.name": "root"}
OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "logs")

# (label, bytes, measured reads, warm-up reads)
PAYLOADS = [
    ("4 KiB", 4 * 1024, 500, 50),
    ("1 MiB", 1024 * 1024, 300, 20),
    ("16 MiB", 16 * 1024 * 1024, 60, 5),
    ("100 MiB", 100 * 1024 * 1024, 15, 2),
]
METADATA_READS = 500


def percentile(values, p):
    ordered = sorted(values)
    return ordered[max(1, math.ceil(p / 100 * len(ordered))) - 1]


def summary(samples_ms, size=None):
    med = statistics.median(samples_ms)
    out = {"n": len(samples_ms), "median_ms": med, "p95_ms": percentile(samples_ms, 95),
           "mean_ms": statistics.mean(samples_ms), "min_ms": min(samples_ms)}
    if size:
        out["throughput_mib_s"] = size / (1024 * 1024) / (med / 1000)
    return out


class HDFS:
    def __init__(self):
        self.http = requests.Session()

    def write(self, path, data):
        r = self.http.put(f"{WEBHDFS}{path}", params={"op": "CREATE", "overwrite": "true", **USER},
                          allow_redirects=False)
        assert r.status_code == 307, r.text
        r = self.http.put(r.headers["Location"], data=data)
        assert r.status_code == 201, r.text

    def read(self, path):
        r = self.http.get(f"{WEBHDFS}{path}", params={"op": "OPEN", **USER})
        r.raise_for_status()
        return r.content

    def locate(self, path):
        """First half of a read only: NameNode answers OPEN with a 307 to a DataNode."""
        r = self.http.get(f"{WEBHDFS}{path}", params={"op": "OPEN", **USER},
                          allow_redirects=False)
        assert r.status_code == 307, r.status_code
        return r.headers["Location"]

    def stat(self, path):
        r = self.http.get(f"{WEBHDFS}{path}", params={"op": "GETFILESTATUS", **USER})
        r.raise_for_status()
        return r.json()["FileStatus"]

    def mkdirs(self, path):
        self.http.put(f"{WEBHDFS}{path}", params={"op": "MKDIRS", **USER}).raise_for_status()


class S3:
    def __init__(self):
        self.s3 = boto3.client("s3", endpoint_url=S3_ENDPOINT, aws_access_key_id="minioadmin",
                               aws_secret_access_key="minioadminpassword",
                               config=Config(signature_version="s3v4"), region_name="us-east-1")
        if BUCKET not in [b["Name"] for b in self.s3.list_buckets()["Buckets"]]:
            self.s3.create_bucket(Bucket=BUCKET)

    def write(self, key, data):
        self.s3.put_object(Bucket=BUCKET, Key=key, Body=data)

    def read(self, key):
        return self.s3.get_object(Bucket=BUCKET, Key=key)["Body"].read()

    def stat(self, key):
        return self.s3.head_object(Bucket=BUCKET, Key=key)


def timed(fn, arg, n, warmup):
    for _ in range(warmup):
        fn(arg)
    samples = []
    for _ in range(n):
        start = time.perf_counter()
        fn(arg)
        samples.append((time.perf_counter() - start) * 1000)
    return samples


REPEATS = int(os.environ.get("BENCH_REPEATS", "3"))


def run_once(hdfs, s3, repeat):
    results = {"payloads": [], "metadata": {}}
    for i, (label, size, n, warmup) in enumerate(PAYLOADS):
        data = os.urandom(size)
        digest = hashlib.md5(data).hexdigest()
        name = f"payload_{size}.bin"
        hdfs.write(f"{HDFS_DIR}/{name}", data)
        s3.write(f"bench/{name}", data)
        assert hashlib.md5(hdfs.read(f"{HDFS_DIR}/{name}")).hexdigest() == digest
        assert hashlib.md5(s3.read(f"bench/{name}")).hexdigest() == digest
        print(f"repeat {repeat}: {label}, {n} reads each ...", file=sys.stderr, flush=True)
        # Alternate the order so neither system always runs first.
        order = [("hdfs", hdfs, f"{HDFS_DIR}/{name}"), ("s3", s3, f"bench/{name}")]
        if (i + repeat) % 2:
            order.reverse()
        row = {"label": label, "bytes": size}
        for system, client, target in order:
            row[system] = summary(timed(client.read, target, n, warmup), size)
        results["payloads"].append(row)

    small = f"{HDFS_DIR}/payload_{PAYLOADS[0][1]}.bin"
    print(f"repeat {repeat}: metadata, {METADATA_READS} calls each ...", file=sys.stderr, flush=True)
    results["metadata"]["hdfs"] = summary(timed(hdfs.stat, small, METADATA_READS, 50))
    results["metadata"]["hdfs_locate"] = summary(timed(hdfs.locate, small, METADATA_READS, 50))
    results["metadata"]["s3"] = summary(
        timed(s3.stat, f"bench/payload_{PAYLOADS[0][1]}.bin", METADATA_READS, 50))
    return results


def mean_sd(values):
    return statistics.mean(values), (statistics.stdev(values) if len(values) > 1 else 0.0)


def aggregate(runs):
    """Mean and standard deviation over repeats of each run's median / p95."""
    agg = {"payloads": [], "metadata": {}}
    for i, (label, size, n, _) in enumerate(PAYLOADS):
        row = {"label": label, "bytes": size, "n": n}
        for system in ("hdfs", "s3"):
            med = mean_sd([r["payloads"][i][system]["median_ms"] for r in runs])
            p95 = mean_sd([r["payloads"][i][system]["p95_ms"] for r in runs])
            row[system] = {"median_ms": med, "p95_ms": p95,
                           "throughput_mib_s": size / (1024 * 1024) / (med[0] / 1000)}
        agg["payloads"].append(row)
    for system in ("hdfs", "hdfs_locate", "s3"):
        agg["metadata"][system] = {
            "n": METADATA_READS,
            "median_ms": mean_sd([r["metadata"][system]["median_ms"] for r in runs]),
            "p95_ms": mean_sd([r["metadata"][system]["p95_ms"] for r in runs])}
    return agg


def main():
    hdfs, s3 = HDFS(), S3()
    hdfs.mkdirs(HDFS_DIR)
    runs = [run_once(hdfs, s3, repeat) for repeat in range(REPEATS)]
    results = {"repeats": REPEATS, "aggregate": aggregate(runs), "runs": runs,
               "environment": {
                   "platform": platform.platform(), "python": platform.python_version(),
                   "cpus": os.cpu_count(), "boto3": boto3.__version__,
                   "requests": requests.__version__}}
    os.makedirs(OUT, exist_ok=True)
    with open(os.path.join(OUT, "storage_bench_results.json"), "w") as f:
        json.dump(results, f, indent=2)
    md = render(results)
    with open(os.path.join(OUT, "storage_bench_results.md"), "w") as f:
        f.write(md)
    print(md)


def render(r):
    a, env = r["aggregate"], r["environment"]
    pm = lambda x: f"{x[0]:.2f} ± {x[1]:.2f}"  # noqa: E731
    lines = ["# Lab 8-9 Task 4: read latency, HDFS vs S3 (MinIO)", "",
             f"Client container in the compose network ({env['platform']}, Python {env['python']}, "
             f"boto3 {env['boto3']}, requests {env['requests']}). HDFS read = WebHDFS OPEN "
             f"(NameNode lookup + 307 redirect + DataNode stream); S3 read = GetObject. "
             f"Keep-alive on both. {r['repeats']} repeats; values are mean ± standard deviation "
             f"of the per-repeat median / p95.", "",
             "| Payload | System | Reads per repeat | Median (ms) | p95 (ms) | "
             "Throughput at median (MiB/s) |", "|---|---|---|---|---|---|"]
    for row in a["payloads"]:
        for system, name in (("hdfs", "HDFS (WebHDFS)"), ("s3", "S3 / MinIO")):
            s = row[system]
            lines.append(f"| {row['label']} | {name} | {row['n']} | {pm(s['median_ms'])} | "
                         f"{pm(s['p95_ms'])} | {s['throughput_mib_s']:.0f} |")
    lines += ["", "| Metadata call | Calls per repeat | Median (ms) | p95 (ms) |",
              "|---|---|---|---|"]
    for system, name in (("hdfs", "HDFS GETFILESTATUS (NameNode)"),
                         ("hdfs_locate", "HDFS OPEN, NameNode step only (307 to DataNode)"),
                         ("s3", "S3 HEAD object")):
        s = a["metadata"][system]
        lines.append(f"| {name} | {s['n']} | {pm(s['median_ms'])} | {pm(s['p95_ms'])} |")
    lines += ["", "## Individual repeats", ""]
    for i, run in enumerate(r["runs"], 1):
        for row in run["payloads"]:
            lines.append(f"- repeat {i}, {row['label']}: HDFS median {row['hdfs']['median_ms']:.2f} "
                         f"/ p95 {row['hdfs']['p95_ms']:.2f} ms; S3 median "
                         f"{row['s3']['median_ms']:.2f} / p95 {row['s3']['p95_ms']:.2f} ms")
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    main()
