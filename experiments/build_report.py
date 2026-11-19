"""Build Lab_Report_Storage.html from the captured evidence, then print it to PDF:

    python experiments/build_report.py . report.html
    google-chrome --headless=new --no-pdf-header-footer --allow-file-access-from-files \\
        --print-to-pdf=Lab_Report_Storage.pdf report.html
"""
import html
import json
import pathlib
import re
import sys

repo = pathlib.Path(sys.argv[1]).resolve()
out = pathlib.Path(sys.argv[2])
logs, shots = repo / "logs", repo / "screenshots"


def img(name, caption, width="100%", border=True):
    uri = (shots / name).as_uri()
    style = f"width:{width};" + ("border:1px solid #bbb;" if border else "")
    return (f'<figure><img src="{uri}" style="{style}"><figcaption>{caption}</figcaption></figure>')


def esc(text):
    return html.escape(text)


env = dict(line.split(": ", 1) for line in (logs / "environment.txt").read_text().splitlines())
pytest_out = (logs / "pytest_output.txt").read_text()
pytest_summary = re.findall(r"=+ (\d+ passed[^=]*?) =+", pytest_out)[-1]
bench = json.loads((logs / "storage_bench_results.json").read_text())
agg = bench["aggregate"]
timeline = (logs / "fault_injection.log").read_text()
fsck_before = (logs / "fsck_before.txt").read_text()
demo = (logs / "s3_client_demo.txt").read_text()
events = [l for l in (logs / "namenode_events.log").read_text().splitlines()
          if re.search(r"removeDeadDatanode|Removing a node|registerDatanode", l)
          and "17:0" in l]

# DataNode IP -> name, from dfsadmin -report
ip_name = dict(re.findall(r"Name: (\d+\.\d+\.\d+\.\d+):\d+ \(hdfs_(datanode\d)",
                          (logs / "dfsadmin_before.txt").read_text()))


def block_rows(fsck):
    rows, current = [], None
    for line in fsck.splitlines():
        m = re.match(r"(/\S+) (\d+) bytes, replicated: replication=(\d+), (\d+) block", line)
        if m:
            current = m.group(1).rsplit("/", 1)[1]
            continue
        m = re.match(r"(\d+)\. \S+:(blk_\d+)_\d+ len=(\d+) Live_repl=(\d+)\s+\[(.*)\]", line)
        if m and current:
            ips = re.findall(r"DatanodeInfoWithStorage\[(\d+\.\d+\.\d+\.\d+):", m.group(5))
            nodes = ", ".join(ip_name.get(ip, ip) for ip in ips)
            rows.append(f"<tr><td><code>{current}</code></td><td class=num>{m.group(1)}</td>"
                        f"<td><code>{m.group(2)}</code></td>"
                        f"<td class=num>{int(m.group(3)) / 2**20:.0f} MiB</td>"
                        f"<td class=num>{m.group(4)}</td><td>{nodes}</td></tr>")
    return "".join(rows)


def pm(x):
    return f"{x[0]:.2f} <span class=sd>± {x[1]:.2f}</span>"


lat_rows = ""
for row in agg["payloads"]:
    h, s = row["hdfs"], row["s3"]
    ratio = h["median_ms"][0] / s["median_ms"][0]
    lat_rows += (f"<tr><td rowspan=2><b>{row['label']}</b><br><span class=small>{row['n']} reads "
                 f"&times; {bench['repeats']}</span></td><td>HDFS (WebHDFS)</td>"
                 f"<td class=num>{pm(h['median_ms'])}</td><td class=num>{pm(h['p95_ms'])}</td>"
                 f"<td class=num>{h['throughput_mib_s']:.0f}</td>"
                 f"<td class=num rowspan=2>&times;{ratio:.2f}</td></tr>"
                 f"<tr><td>S3 / MinIO</td><td class=num>{pm(s['median_ms'])}</td>"
                 f"<td class=num>{pm(s['p95_ms'])}</td>"
                 f"<td class=num>{s['throughput_mib_s']:.0f}</td></tr>")
md = agg["metadata"]
meta_rows = "".join(
    f"<tr><td>{name}</td><td class=num>{pm(md[k]['median_ms'])}</td><td class=num>{pm(md[k]['p95_ms'])}</td></tr>"
    for k, name in (("hdfs", "HDFS <code>GETFILESTATUS</code> (NameNode, in-memory namespace)"),
                    ("hdfs_locate", "HDFS <code>OPEN</code>, NameNode step only (returns 307 to a DataNode)"),
                    ("s3", "S3 <code>HEAD</code> object (MinIO reads xl.meta from disk)")))
p = {r["label"]: r for r in agg["payloads"]}
s16 = [r["payloads"][2]["s3"]["median_ms"] for r in bench["runs"]]
s16_txt = ", ".join(f"{v:.1f}" for v in s16)

env_rows = "".join(f"<tr><td>{esc(k)}</td><td>{esc(v)}</td></tr>" for k, v in env.items())

tests = [
    ("test_s3_bucket_existence", "Lab sheet: bucket <code>distributed-course-bucket</code> is listed"),
    ("test_s3_object_upload_and_read", "Lab sheet: put_object / get_object round trip"),
    ("test_upload_file_with_custom_metadata_and_head", "upload_object with metadata; HEAD returns it (keys lower-cased), size, ETag = MD5"),
    ("test_overwrite_replaces_data_and_metadata", "PUT on an existing key replaces body <i>and</i> metadata; ETag changes"),
    ("test_large_file_multipart_upload_integrity", "20 MiB upload_file &rarr; multipart (ETag <code>-3</code>), download MD5 matches"),
    ("test_bucket_integrity_listing_matches_objects", "LIST sizes and ETags match the 5 objects written"),
    ("test_object_persists_across_minio_restart", "<code>docker restart local_s3_minio</code>; body and metadata survive"),
    ("test_read_after_write_is_immediately_consistent", "50 overwrites, every read right after a PUT sees it (0 stale reads)"),
    ("test_flat_namespace_directories_are_key_prefixes", "&ldquo;directories&rdquo; are only common prefixes; no directory object exists"),
    ("test_missing_object_and_idempotent_delete", "HEAD of a missing key &rarr; 404; DELETE twice is not an error"),
    ("test_two_datanodes_live", "HDFS: <code>dfsadmin -report</code> shows 2 live DataNodes"),
    ("test_file_is_split_into_blocks_with_two_replicas", "HDFS: 10 MiB with 1 MiB blocks &rarr; 10 blocks, each on both DataNodes"),
    ("test_hdfs_read_returns_written_bytes", "HDFS: <code>hdfs dfs -cat</code> MD5 equals the local file"),
]
test_rows = ""
for name, what in tests:
    ok = re.search(rf"::{name} PASSED", pytest_out) is not None
    file = "test_hdfs.py" if name in ("test_two_datanodes_live", "test_file_is_split_into_blocks_with_two_replicas",
                                      "test_hdfs_read_returns_written_bytes") else "test_storage.py"
    test_rows += (f"<tr><td><code>{file}::{name}</code></td><td>{what}</td>"
                  f"<td class={'ok' if ok else 'bad'}>{'PASS' if ok else 'FAIL'}</td></tr>")

# Timeline highlights straight from the log
def t_of(pattern):
    for line in timeline.splitlines():
        if re.search(pattern, line):
            return float(re.search(r"\+\s*([\d.]+)s\]", line).group(1))
    return None

t_stop_cmd = t_of(r"phase 3")
t_stopped = t_of(r"datanode1 stopped")
t_stale = t_of(r"stale=1")
t_dead = t_of(r"dead=1")
t_under = t_of(r"under_replicated=5")
t_start = t_of(r"phase 5")
t_live2 = next(float(re.search(r"\+\s*([\d.]+)s\]", l).group(1)) for l in timeline.splitlines()
               if "live=2" in l and float(re.search(r"\+\s*([\d.]+)s\]", l).group(1)) > t_start)
t_rec = t_of(r"recovered:")

page = f"""<!doctype html>
<html><head><meta charset="utf-8">
<title>Distributed File Systems &amp; Object Storage — Lab Report</title>
<style>
@page {{ size: A4; margin: 15mm 14mm; }}
body {{ font-family: "Inter","Helvetica Neue",Arial,sans-serif; font-size: 9.8pt; line-height: 1.45; color: #1d1d1f; background: #fff; }}
h1 {{ font-size: 18pt; margin: 0 0 4px; }}
h2 {{ font-size: 13.5pt; margin: 20px 0 8px; border-bottom: 1.5px solid #222; padding-bottom: 3px; break-after: avoid; }}
h3 {{ font-size: 10.8pt; margin: 13px 0 5px; break-after: avoid; }}
p {{ margin: 5px 0; }}
table {{ border-collapse: collapse; width: 100%; margin: 6px 0 10px; font-size: 8.8pt; }}
tr, figure {{ break-inside: avoid; }}
th, td {{ border: 1px solid #bbb; padding: 3.5px 6px; vertical-align: top; text-align: left; }}
th {{ background: #f0f0f2; font-weight: 600; }}
td.num {{ text-align: right; font-variant-numeric: tabular-nums; white-space: nowrap; }}
.sd {{ color: #777; font-size: 7.8pt; }}
.ok {{ color: #106b2b; font-weight: 600; }} .bad {{ color: #a1170f; font-weight: 600; }}
.small {{ font-size: 7.8pt; color: #555; }}
code, pre {{ font-family: "DejaVu Sans Mono", Menlo, Consolas, monospace; font-size: 7.9pt; }}
pre {{ background: #f6f6f8; border: 1px solid #ddd; padding: 6px 8px; white-space: pre-wrap; word-break: break-all; margin: 5px 0; }}
figure {{ margin: 8px 0 12px; }}
figcaption {{ font-size: 8.3pt; color: #444; margin-top: 3px; }}
.meta td {{ border: none; padding: 1px 10px 1px 0; }} .meta {{ width: auto; margin-bottom: 8px; }}
.two {{ display: flex; gap: 12px; }} .two > figure {{ flex: 1; margin-top: 0; }}
ul {{ margin: 4px 0 6px 18px; padding: 0; }} li {{ margin: 2px 0; }}
.pb {{ break-before: page; }}
</style></head><body>

<h1>Distributed File Systems (HDFS) &amp; S3-Compatible Object Storage — Lab Report</h1>
<table class=meta>
<tr><td><b>Course</b></td><td>CSS4004 – Distributed Systems, Lab 8-9</td></tr>
<tr><td><b>Topic</b></td><td>Block vs. object storage, replication, fault tolerance, S3 API integration</td></tr>
<tr><td><b>Student</b></td><td>Manap Beibarys</td></tr>
<tr><td><b>Repository</b></td><td>Distributed-Storage-Lab</td></tr>
<tr><td><b>Date</b></td><td>19 November 2026</td></tr>
</table>

<p>A Docker Compose cluster with an HDFS NameNode, two DataNodes (replication factor 2) and a MinIO object
store; <code>s3_client.py</code> implements the object lifecycle on boto3; <code>tests/</code> holds
13 integration tests (<b>{pytest_summary}</b>, <code>logs/pytest_output.txt</code>). Every figure and number below
comes from files captured in <code>logs/</code> and <code>screenshots/</code>.</p>

<h3>Environment and deviations from the lab sheet</h3>
<table>{env_rows}</table>
<table>
<tr><th style="width:30%">Change</th><th>Reason</th></tr>
<tr><td>MinIO image <code>minio/minio</code> &rarr; <code>cgr.dev/chainguard/minio:latest</code></td>
<td>The sheet's image is no longer distributed: Docker Hub answers &ldquo;repository does not exist&rdquo;,
<code>quay.io/minio/minio</code> answers 401 and the binary archive dl.min.io answers 410 Gone (checked 19 Nov 2026).
Chainguard's image is built from the same MinIO server source; command, ports and credentials are unchanged.</td></tr>
<tr><td><code>dfs.namenode.heartbeat.recheck-interval = 10 s</code></td>
<td>A DataNode is declared dead after 2 &times; recheck + 10 &times; heartbeat: 50 s instead of the default 10.5 min.</td></tr>
<tr><td>Second copy of the payload with 32 MiB blocks</td>
<td>With the default 128 MiB block size the 100 MiB file is a single block; the copy shows a file split into blocks.</td></tr>
<tr><td>WebHDFS on, permissions off; <code>bench</code> container</td>
<td>WebHDFS is the HTTP read path for Task 4. Its 307 redirect names the DataNode's container hostname, which
only resolves inside the compose network, so the benchmark client runs there.</td></tr>
</table>

<h2>Section 1: HDFS Web UI and block layout</h2>
<p>After <code>docker compose up -d</code> both DataNodes registered within about 10 s. The lab-sheet commands
(<code>hdfs dfs -mkdir</code>, <code>dd</code> 100 MiB from <code>/dev/urandom</code>, <code>hdfs dfs -put</code>, all
logged in <code>logs/task1_commands.log</code>) were run by <code>experiments/hdfs_fault_injection.py</code>.</p>
{img("report/overview_before.png", "Figure 1. NameNode Overview (http://localhost:9870): Live Nodes 2, Dead Nodes 0, 0 under-replicated blocks.")}
{img("report/datanodes_before.png", "Figure 2. Datanodes tab: datanode1 and datanode2 in service, 5 blocks each.")}
{img("fsck_before.png", "Figure 3. <code>hdfs fsck /user/student/data -files -blocks -locations</code> with both DataNodes up (logs/fsck_before.txt).")}
<table>
<tr><th>File</th><th>#</th><th>Block</th><th>Length</th><th>Live replicas</th><th>Locations</th></tr>
{block_rows(fsck_before)}
</table>
<p class=small>DataNode addresses from <code>dfsadmin -report</code>: {", ".join(f"{ip} = {n}" for ip, n in sorted(ip_name.items()))}.</p>
<ul>
<li>The 100 MiB file with the default 128 MiB block size is <b>one block of 100 MiB</b>: a block is a maximum
size, the last block only takes the bytes it holds. With 32 MiB blocks the same bytes become 3 &times; 32 MiB + 4 MiB.</li>
<li>Every block has <b>2 live replicas, one on each DataNode</b>. With two DataNodes and replication 2 the placement
has no choice; only the order of the replicas (which node the client reads first) differs per block.</li>
<li><code>fsck</code> reports <i>Status: HEALTHY</i>, 0 under-replicated, 0 missing, average block replication 2.0.</li>
</ul>
{img("report/blockinfo_before.png", "Figure 4. File browser, block information of sample_100m_32m-blocks.bin, block 0: available on datanode1 and datanode2.", width="46%")}

<h2>Section 2: MinIO Console — objects and custom metadata</h2>
<p><code>python s3_client.py demo</code> creates the bucket and uploads two objects with user metadata
(<code>logs/s3_client_demo.txt</code>):</p>
<pre>{esc(demo)}</pre>
{img("report/minio_root.png", "Figure 5. MinIO Console (http://localhost:9002), bucket distributed-course-bucket. The flat key space is shown as folders by key prefix.")}
{img("report/minio_list_sample.png", "Figure 6. Object list under the prefix datasets/ with the selected object sample_10m.bin (10 MiB).")}
<div class=two style="justify-content:center">
{img("report/minio_meta_report.png", "Figure 7. Object Info of reports/lab-report-draft.txt: custom metadata X-Amz-Meta-Author / -Content-Kind / -Course / -Lab.", width="300px")}
{img("report/minio_meta_sample.png", "Figure 8. Object Info of datasets/sample_10m.bin: multipart upload (ETag ends in -2); custom metadata includes its MD5.", width="300px")}
</div>
<p>The read-after-write probe overwrote one key 200 times and read it back after every PUT: <b>0 stale reads</b>, so the
staleness bound for a single MinIO node is 0 (AWS S3 has given the same strong read-after-write guarantee since December 2020).</p>

<h3>Automated tests ({pytest_summary})</h3>
<table><tr><th style="width:47%">Test</th><th>What it verifies</th><th>Result</th></tr>{test_rows}</table>

<h2>Section 3: Fault injection — DataNode termination</h2>
<p>Timeline from <code>logs/fault_injection.log</code> (NameNode JMX metrics every 2 s; times relative to the start of the script):</p>
<table>
<tr><th>t (s)</th><th>Event</th></tr>
<tr><td class=num>{t_stop_cmd:.1f}</td><td><code>docker stop hdfs_datanode1</code> issued (Docker waits 10 s for SIGTERM, then kills the process)</td></tr>
<tr><td class=num>{t_stopped:.1f}</td><td>container stopped; NameNode still shows 2 live nodes</td></tr>
<tr><td class=num>{t_stale:.1f}</td><td>datanode1 marked <b>stale</b> (no heartbeat for 30 s); still counted as live</td></tr>
<tr><td class=num>{t_dead:.1f}</td><td>datanode1 declared <b>dead</b> ({t_dead - t_stopped:.0f} s after the kill; configured 50 s); NameNode log: <code>removeDeadDatanode: lost heartbeat</code></td></tr>
<tr><td class=num>{t_under:.1f}</td><td><b>Under-replicated blocks = 5</b> (all blocks), missing blocks = 0</td></tr>
<tr><td class=num>{t_under:.0f}–{t_start:.0f}</td><td>degraded: both files read back with the original MD5; a new 8 MiB file was written with 1 replica
(under-replicated = 6); no re-replication, because the only other DataNode already holds every block</td></tr>
<tr><td class=num>{t_start:.1f}</td><td><code>docker start hdfs_datanode1</code></td></tr>
<tr><td class=num>{t_live2:.1f}</td><td>datanode1 re-registered, live = 2</td></tr>
<tr><td class=num>{t_rec:.1f}</td><td><b>recovered</b>: block report processed, under-replicated = 0 ({t_rec - t_start:.0f} s after the start); every block Live_repl = 2 again, including the file written while degraded</td></tr>
</table>
{img("report/overview_degraded.png", "Figure 9. NameNode Overview while degraded: Live Nodes 1, Dead Nodes 1, Under-Replicated Blocks 5.")}
{img("report/overview_recovered.png", "Figure 10. Overview after datanode1 returned: Live Nodes 2, Dead Nodes 0, Under-Replicated Blocks 0.")}
{img("report/datanodes_degraded.png", "Figure 11. Datanodes tab while degraded: datanode1 shown as down with its last contact time.")}
{img("fsck_degraded.png", "Figure 12. fsck while degraded (logs/fsck_degraded.txt): every block Live_repl=1 on datanode2, “Target Replicas is 2 but found 1 live replica”, Under-replicated 5 (100 %), Missing 0, Status HEALTHY.")}
<p><b>Recovery state.</b> HEALTHY in fsck means &ldquo;no missing or corrupt blocks&rdquo;, not &ldquo;fully replicated&rdquo;:
the data stayed readable and writable with one replica. HDFS repairs under-replication by copying a block from a live
replica to another DataNode, but with replication 2 and only two DataNodes there is no eligible target, so the cluster
stayed at 5–6 under-replicated blocks until datanode1 came back. Its block report then restored 2 replicas without
copying data. With a third DataNode the NameNode would have re-replicated the blocks on its own after the node was declared dead.</p>
<h3>Raw logs</h3>
<pre>{esc(timeline)}</pre>
<pre>{esc(chr(10).join(l[:220] for l in events))}</pre>

<h2 class=pb>Section 4: Comparative storage paradigm evaluation</h2>
<h3>4.1 Read latency (Task 4)</h3>
<p><code>bench/storage_bench.py</code> in a container on the compose network; random payloads written once to both systems,
MD5-verified, then read repeatedly over keep-alive HTTP. {bench['repeats']} repeats; values are mean &plusmn; standard deviation of
the per-repeat median and p95 (<code>logs/storage_bench_results.md</code>). HDFS is read through WebHDFS: <code>OPEN</code> at the
NameNode, a 307 redirect, then the data from a DataNode. S3 is a single <code>GetObject</code>.</p>
<table>
<tr><th>Payload</th><th>System</th><th>Median (ms)</th><th>p95 (ms)</th><th>MiB/s at median</th><th>HDFS / S3 median</th></tr>
{lat_rows}
</table>
<table><tr><th>Metadata-only call (500 &times; {bench['repeats']})</th><th>Median (ms)</th><th>p95 (ms)</th></tr>{meta_rows}</table>
<ul>
<li><b>Small objects favour S3.</b> A 4 KiB read costs {p['4 KiB']['hdfs']['median_ms'][0]:.2f} ms on HDFS vs
{p['4 KiB']['s3']['median_ms'][0]:.2f} ms on S3. HDFS needs two requests: the NameNode lookup ({md['hdfs_locate']['median_ms'][0]:.2f} ms, measured alone) and
the DataNode request, which accounts for the remaining ≈{p['4 KiB']['hdfs']['median_ms'][0] - md['hdfs_locate']['median_ms'][0]:.2f} ms; S3 answers in one request.</li>
<li><b>Large objects converge.</b> At 16 and 100 MiB the per-request overhead disappears into the transfer and both stream at
roughly 750–1080 MiB/s on one host; MinIO was ≈{(p['100 MiB']['hdfs']['median_ms'][0] / p['100 MiB']['s3']['median_ms'][0] - 1) * 100:.0f} % faster at 100 MiB.
The 16 MiB S3 result was bimodal across repeats (medians {s16_txt} ms), hence its large deviation; the HDFS side was stable.</li>
<li><b>Metadata favours the NameNode.</b> <code>GETFILESTATUS</code> is answered from the NameNode's in-memory namespace
({md['hdfs']['median_ms'][0]:.2f} ms) and is faster than an S3 <code>HEAD</code> ({md['s3']['median_ms'][0]:.2f} ms), which MinIO serves from per-object metadata on disk.</li>
<li>Caveats: all containers share one host, so there is no network cost and no parallelism across DataNodes; WebHDFS is
the HTTP gateway, not the native RPC client (the CLI is unusable for timing: each <code>hdfs dfs -cat</code> starts a JVM: 1.15 s for a 4 KiB file, measured);
MinIO runs on a single drive without erasure coding.</li>
</ul>

<h3>4.2 Evaluation matrix</h3>
<table>
<tr><th style="width:21%">Evaluation attribute</th><th>HDFS (block storage)</th><th>S3 / MinIO (object storage)</th></tr>
<tr><td>Storage hierarchy</td><td>POSIX-like hierarchical tree of directories and files (<code>/user/student/data/…</code>)</td>
<td>Flat namespace: bucket + key; &ldquo;directories&rdquo; are key prefixes listed with a delimiter (test: no directory object exists)</td></tr>
<tr><td>Modification pattern</td><td>Append-only, single writer per file (lease held by the writer); no in-place update</td>
<td>Immutable objects: a PUT replaces the whole object including its metadata (test: overwrite drops old metadata, new ETag)</td></tr>
<tr><td>Unit size</td><td>Fixed block slices, 128 MiB default (configurable per file: 32 MiB / 1 MiB here); last block partial</td>
<td>Arbitrary payload, 0 B to 5 TiB; above 8 MiB boto3 splits uploads into multipart parts (ETag <code>-N</code>)</td></tr>
<tr><td>Metadata management</td><td>NameNode in-memory namespace and block map (GETFILESTATUS {md['hdfs']['median_ms'][0]:.2f} ms); single metadata server</td>
<td>Per-object metadata stored with the object, user metadata as <code>x-amz-meta-*</code> headers (HEAD {md['s3']['median_ms'][0]:.2f} ms)</td></tr>
<tr><td>Read path (measured)</td><td>2 hops (NameNode &rarr; DataNode); 4 KiB median {p['4 KiB']['hdfs']['median_ms'][0]:.2f} ms, 100 MiB {p['100 MiB']['hdfs']['median_ms'][0]:.0f} ms</td>
<td>1 hop; 4 KiB median {p['4 KiB']['s3']['median_ms'][0]:.2f} ms, 100 MiB {p['100 MiB']['s3']['median_ms'][0]:.0f} ms</td></tr>
<tr><td>Redundancy</td><td>Whole-block replicas (factor 2 here) placed by the NameNode; re-replication from live replicas</td>
<td>Erasure coding across drives/nodes in distributed mode; a single drive here (no redundancy)</td></tr>
<tr><td>Failure behaviour (observed)</td><td>Node loss detected after {t_dead - t_stopped:.0f} s; reads and writes continue on 1 replica; recovery via block report in {t_rec - t_start:.0f} s</td>
<td>Container restart: objects and metadata intact (test); API unavailable while the single server restarts</td></tr>
<tr><td>Consistency</td><td>Single writer, readers see data once the block is complete / file closed</td>
<td>Strong read-after-write: 0 stale reads in 200 overwrites</td></tr>
<tr><td>Access interface</td><td>HDFS RPC (Java client), WebHDFS REST, CLI</td><td>S3 REST API over HTTP (boto3, any S3 SDK)</td></tr>
</table>

<h3>Limitations</h3>
<ul>
<li>Two DataNodes cannot show automatic re-replication; it would need a third DataNode.</li>
<li>All services run on one machine, so the latency numbers show per-request software cost, not network or
multi-node throughput. Results are from {bench['repeats']} repeats on this machine only.</li>
</ul>

<h2>Appendix: reproduction</h2>
<pre>docker compose up -d
python3 -m venv .venv &amp;&amp; source .venv/bin/activate &amp;&amp; pip install -r requirements.txt
python experiments/hdfs_fault_injection.py         # Section 1 and 3
python s3_client.py demo &amp;&amp; python s3_client.py staleness
python -m pytest -v                                # 13 tests
docker compose run --rm -e BENCH_REPEATS=5 bench   # Section 4
python experiments/ui_screens.py minio &amp;&amp; python experiments/report_images.py</pre>
</body></html>
"""
out.write_text(page)
print("wrote", out)
