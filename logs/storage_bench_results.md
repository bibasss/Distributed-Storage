# Lab 8-9 Task 4: read latency, HDFS vs S3 (MinIO)

Client container in the compose network (Linux-6.8.0-1073-azure-x86_64-with-glibc2.41, Python 3.12.16, boto3 1.44.6, requests 2.34.2). HDFS read = WebHDFS OPEN (NameNode lookup + 307 redirect + DataNode stream); S3 read = GetObject. Keep-alive on both. 5 repeats; values are mean ± standard deviation of the per-repeat median / p95.

| Payload | System | Reads per repeat | Median (ms) | p95 (ms) | Throughput at median (MiB/s) |
|---|---|---|---|---|---|
| 4 KiB | HDFS (WebHDFS) | 500 | 2.31 ± 0.14 | 3.04 ± 0.51 | 2 |
| 4 KiB | S3 / MinIO | 500 | 1.36 ± 0.23 | 1.89 ± 0.54 | 3 |
| 1 MiB | HDFS (WebHDFS) | 300 | 3.47 ± 0.38 | 5.28 ± 1.46 | 288 |
| 1 MiB | S3 / MinIO | 300 | 2.39 ± 0.38 | 3.30 ± 0.88 | 419 |
| 16 MiB | HDFS (WebHDFS) | 60 | 14.84 ± 0.74 | 19.61 ± 5.49 | 1078 |
| 16 MiB | S3 / MinIO | 60 | 17.22 ± 5.03 | 19.17 ± 5.45 | 929 |
| 100 MiB | HDFS (WebHDFS) | 15 | 133.53 ± 2.24 | 140.97 ± 3.24 | 749 |
| 100 MiB | S3 / MinIO | 15 | 116.81 ± 2.55 | 130.98 ± 8.65 | 856 |

| Metadata call | Calls per repeat | Median (ms) | p95 (ms) |
|---|---|---|---|
| HDFS GETFILESTATUS (NameNode) | 500 | 0.70 ± 0.02 | 0.88 ± 0.03 |
| HDFS OPEN, NameNode step only (307 to DataNode) | 500 | 0.77 ± 0.03 | 0.98 ± 0.16 |
| S3 HEAD object | 500 | 1.10 ± 0.02 | 1.29 ± 0.06 |

## Individual repeats

- repeat 1, 4 KiB: HDFS median 2.39 / p95 3.33 ms; S3 median 1.21 / p95 1.42 ms
- repeat 1, 1 MiB: HDFS median 3.34 / p95 4.27 ms; S3 median 2.15 / p95 2.94 ms
- repeat 1, 16 MiB: HDFS median 14.99 / p95 16.60 ms; S3 median 13.60 / p95 14.58 ms
- repeat 1, 100 MiB: HDFS median 136.90 / p95 143.09 ms; S3 median 119.53 / p95 122.20 ms
- repeat 2, 4 KiB: HDFS median 2.32 / p95 3.24 ms; S3 median 1.22 / p95 1.44 ms
- repeat 2, 1 MiB: HDFS median 3.33 / p95 5.00 ms; S3 median 2.12 / p95 2.66 ms
- repeat 2, 16 MiB: HDFS median 14.25 / p95 16.37 ms; S3 median 22.77 / p95 24.65 ms
- repeat 2, 100 MiB: HDFS median 131.14 / p95 135.47 ms; S3 median 115.59 / p95 129.40 ms
- repeat 3, 4 KiB: HDFS median 2.34 / p95 2.73 ms; S3 median 1.40 / p95 2.17 ms
- repeat 3, 1 MiB: HDFS median 4.02 / p95 7.07 ms; S3 median 2.86 / p95 4.29 ms
- repeat 3, 16 MiB: HDFS median 15.84 / p95 29.08 ms; S3 median 14.08 / p95 18.13 ms
- repeat 3, 100 MiB: HDFS median 132.24 / p95 143.38 ms; S3 median 116.32 / p95 144.14 ms
- repeat 4, 4 KiB: HDFS median 2.07 / p95 2.32 ms; S3 median 1.21 / p95 1.73 ms
- repeat 4, 1 MiB: HDFS median 3.02 / p95 3.60 ms; S3 median 2.08 / p95 2.44 ms
- repeat 4, 16 MiB: HDFS median 13.98 / p95 16.28 ms; S3 median 13.00 / p95 13.48 ms
- repeat 4, 100 MiB: HDFS median 134.49 / p95 142.14 ms; S3 median 119.16 / p95 124.98 ms
- repeat 5, 4 KiB: HDFS median 2.44 / p95 3.59 ms; S3 median 1.75 / p95 2.70 ms
- repeat 5, 1 MiB: HDFS median 3.64 / p95 6.47 ms; S3 median 2.73 / p95 4.19 ms
- repeat 5, 16 MiB: HDFS median 15.12 / p95 19.71 ms; S3 median 22.65 / p95 25.02 ms
- repeat 5, 100 MiB: HDFS median 132.87 / p95 140.76 ms; S3 median 113.44 / p95 134.21 ms
