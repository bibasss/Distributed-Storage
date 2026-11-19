# Lab 8-9: Distributed File Systems (HDFS) & S3-Compatible Object Storage (MinIO)

A local HDFS cluster (NameNode + 2 DataNodes, replication 2) and a MinIO
object store in Docker Compose. Includes block inspection, a DataNode
fault-injection experiment, an S3 client on boto3, integration tests and a
read-latency comparison of HDFS and S3.

| Task | Files | Evidence |
|---|---|---|
| Infrastructure | `docker-compose.yml`, `hadoop.env` | `logs/environment.txt` |
| 1 — HDFS blocks & fault injection | `experiments/hdfs_fault_injection.py` | `logs/fsck_*.txt`, `logs/fault_injection.log`, `logs/namenode_events.log`, `screenshots/hdfs_*` |
| 2 — S3 API | `s3_client.py` | `screenshots/minio_*` |
| 3 — Tests | `tests/test_storage.py` (10), `tests/test_hdfs.py` (3) | `logs/pytest_output.txt` |
| 4 — Comparison | `bench/storage_bench.py` | `logs/storage_bench_results.md` / `.json` |
| Report | `Lab_Report_Storage.pdf` | |

## Setup

```bash
docker compose up -d                       # namenode :9870/:9000, minio :9001 (S3) / :9002 (console)
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

The cluster is ready when `docker exec hdfs_namenode hdfs dfsadmin -report`
shows `Live datanodes (2)` (about 10 s). Web UIs: HDFS http://localhost:9870,
MinIO Console http://localhost:9002 (`minioadmin` / `minioadminpassword`).

## Run

```bash
# Task 1: lab-sheet commands, fsck, stop/start datanode1, timeline (~2.5 min)
python experiments/hdfs_fault_injection.py

# Task 2: bucket + sample objects with custom metadata, read-after-write probe
python s3_client.py demo
python s3_client.py staleness

# Task 3: integration tests (13 tests, ~15 s; one test restarts the MinIO container)
python -m pytest -v

# Task 4: HDFS vs S3 read latency, from a container inside the compose network
docker compose run --rm -e BENCH_REPEATS=5 bench

# Screenshots (installed Google Chrome via Playwright) and report images
python experiments/ui_screens.py minio
python experiments/report_images.py

# Report (HTML from logs/ and screenshots/, printed to PDF by headless Chrome)
python experiments/build_report.py . report.html
google-chrome --headless=new --no-pdf-header-footer --allow-file-access-from-files \
    --print-to-pdf=Lab_Report_Storage.pdf report.html
```

The Task 1 commands from the lab sheet use `docker exec -it`; the script
runs them without `-it`, which needs a terminal.

## Deviations from the lab sheet

| Change | Why |
|---|---|
| MinIO image `minio/minio` → `cgr.dev/chainguard/minio:latest` | The sheet's image no longer exists: Docker Hub says "repository does not exist", `quay.io/minio/minio` gives 401 and the binary archive dl.min.io gives 410 Gone (checked 19 Nov 2026). Chainguard builds the same MinIO server source (RELEASE.2026-11-16). Command, ports and credentials are as in the sheet. |
| `hadoop.env`: `dfs.namenode.heartbeat.recheck-interval=10000` | A stopped DataNode is declared dead after 2 × recheck + 10 × heartbeat = 50 s instead of 10.5 min, so Under-Replicated Blocks shows up within a minute. |
| `hadoop.env`: WebHDFS on, permissions off | WebHDFS is the HTTP read path for the benchmark. |
| Second 100 MB copy with `-D dfs.blocksize=32m` | With the default 128 MB block size the 100 MB file is a single block; the copy shows a file split over 4 blocks. |
| `hostname:` for the HDFS services, a named volume for MinIO, a `bench` service (profile `tools`) | Readable node names in the UI; objects survive container re-creation; WebHDFS redirects reads to DataNode hostnames that only resolve inside the compose network. |
| Top-level `version: '3'` removed | Ignored by Compose v2+ (prints a warning). |
| `upload_object(..., metadata=None)` | The sheet's `metadata={}` is a shared mutable default. |
| `tests/test_storage.py` creates the bucket in a module fixture | On a fresh MinIO `test_s3_bucket_existence` fails otherwise. |

On Apple Silicon the bde2020 images are amd64 only: add
`platform: linux/amd64` to the three HDFS services.

## Layout

```
docker-compose.yml, hadoop.env     cluster
s3_client.py                       S3 object lifecycle (boto3)
tests/test_storage.py              S3 integration tests
tests/test_hdfs.py                 HDFS checks via docker exec
experiments/hdfs_fault_injection.py  Task 1 experiment
experiments/ui_screens.py          Web UI / Console screenshots (Playwright)
experiments/report_images.py       fsck terminal renders and screenshot crops
experiments/build_report.py        Lab_Report_Storage.pdf source (HTML)
bench/                             benchmark container + script
logs/, screenshots/                captured evidence
```
