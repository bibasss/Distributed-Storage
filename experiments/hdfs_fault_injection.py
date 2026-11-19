"""Lab 8-9 Task 1: HDFS block inspection and DataNode fault injection.

Runs the lab sheet's commands (without -it, so it works from a script), then
stops hdfs_datanode1, records how the NameNode sees the failure, checks
reads and writes while degraded, restarts the DataNode and records recovery.

Evidence written to logs/ and screenshots/:
  logs/task1_commands.log         every command with its output
  logs/fsck_before.txt            block layout, both DataNodes up
  logs/fsck_degraded.txt          block layout with datanode1 dead
  logs/fsck_after.txt             block layout after datanode1 returned
  logs/fault_injection.log        timeline of NameNode JMX metrics (every 2 s)
  logs/namenode_events.log        NameNode log lines about the failure
  screenshots/hdfs_{before,degraded,recovered}_*.png

    docker compose up -d namenode datanode1 datanode2
    python experiments/hdfs_fault_injection.py
"""
import datetime
import json
import os
import re
import subprocess
import sys
import time
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ui_screens  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOGS = os.path.join(ROOT, "logs")
NN, DN1 = "hdfs_namenode", "hdfs_datanode1"
DATA = "/user/student/data"
SAMPLE = f"{DATA}/sample_100m.bin"                 # default 128 MB block size -> 1 block
SAMPLE_32M = f"{DATA}/sample_100m_32m-blocks.bin"  # 32 MB blocks -> 4 blocks
DEGRADED_WRITE = f"{DATA}/written_while_degraded.bin"
JMX = ("http://localhost:9870/jmx?qry=Hadoop:service=NameNode,name={}")

os.makedirs(LOGS, exist_ok=True)
_cmd_log = open(os.path.join(LOGS, "task1_commands.log"), "w")
_timeline = open(os.path.join(LOGS, "fault_injection.log"), "w")
T0 = time.monotonic()


def stamp():
    return datetime.datetime.now().strftime("%H:%M:%S")


def say(line, timeline=True):
    text = f"[{stamp()} +{time.monotonic() - T0:6.1f}s] {line}"
    print(text, flush=True)
    if timeline:
        _timeline.write(text + "\n")
        _timeline.flush()


def run(*cmd, check=True, log=True):
    shown = " ".join(cmd)
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    out = r.stdout + (r.stderr if r.returncode else "")
    if log:
        _cmd_log.write(f"$ {shown}\n{out}{'' if out.endswith(chr(10)) or not out else chr(10)}"
                       f"(exit {r.returncode})\n\n")
        _cmd_log.flush()
    if check and r.returncode:
        raise RuntimeError(f"{shown} failed ({r.returncode}):\n{out}")
    return r.stdout


def nn(*args, **kw):
    return run("docker", "exec", NN, *args, **kw)


def metrics():
    def bean(name):
        with urllib.request.urlopen(JMX.format(name), timeout=5) as resp:
            return json.load(resp)["beans"][0]
    fs, state = bean("FSNamesystem"), bean("FSNamesystemState")
    return {
        "live": state["NumLiveDataNodes"], "dead": state["NumDeadDataNodes"],
        "stale": state["NumStaleDataNodes"], "blocks": fs["BlocksTotal"],
        "under_replicated": fs["UnderReplicatedBlocks"], "missing": fs["MissingBlocks"],
        "pending_replication": fs["PendingReplicationBlocks"],
    }


def fmt(m):
    return (f"live={m['live']} dead={m['dead']} stale={m['stale']} blocks={m['blocks']} "
            f"under_replicated={m['under_replicated']} missing={m['missing']} "
            f"pending_replication={m['pending_replication']}")


def watch(until, timeout_s, every_s=2.0, label=""):
    """Poll JMX, log every change (and every 10 s), return when until(m)."""
    last, last_log, start = None, 0.0, time.monotonic()
    while True:
        m = metrics()
        now = time.monotonic()
        if m != last or now - last_log >= 10:
            say(f"{label}{fmt(m)}")
            last, last_log = m, now
        if until(m):
            return m, now - start
        if now - start > timeout_s:
            raise TimeoutError(f"condition not reached in {timeout_s}s: {fmt(m)}")
        time.sleep(every_s)


def save(name, text):
    with open(os.path.join(LOGS, name), "w") as f:
        f.write(text)
    say(f"saved logs/{name}", timeline=False)


def fsck(path=DATA):
    return nn("hdfs", "fsck", path, "-files", "-blocks", "-locations", check=False)


def md5_in_hdfs(path):
    return nn("sh", "-c", f"hdfs dfs -cat {path} | md5sum").split()[0]


def main():
    say("=== phase 0: cluster check")
    m = metrics()
    say(fmt(m))
    if m["live"] != 2:
        sys.exit("expected 2 live DataNodes - is the cluster up?")

    say("=== phase 1: write the 100 MB payload (lab sheet commands)")
    nn("hdfs", "dfs", "-rm", "-r", "-f", "-skipTrash", DATA, check=False)
    nn("hdfs", "dfs", "-mkdir", "-p", DATA)
    nn("dd", "if=/dev/urandom", "of=sample_100m.bin", "bs=1M", "count=100")
    nn("hdfs", "dfs", "-put", "sample_100m.bin", f"{DATA}/")
    # Same bytes with a 32 MB block size, so the layout shows several blocks.
    nn("hdfs", "dfs", "-D", "dfs.blocksize=33554432", "-put", "sample_100m.bin", SAMPLE_32M)
    original_md5 = nn("sh", "-c", "md5sum < sample_100m.bin").split()[0]
    say(f"local md5 {original_md5}")
    nn("hdfs", "dfs", "-ls", "-h", DATA)
    nn("hdfs", "dfs", "-stat", "%n: replication=%r blocksize=%o size=%b", SAMPLE, SAMPLE_32M)

    say("=== phase 2: block inspection")
    before = fsck()
    save("fsck_before.txt", before)
    save("dfsadmin_before.txt", nn("hdfs", "dfsadmin", "-report"))
    blocks = re.findall(r"blk_\d+_\d+ len=\d+ Live_repl=\d+", before)
    say(f"fsck: {len(blocks)} blocks, healthy={'Status: HEALTHY' in before}")
    say(fmt(metrics()))
    ui_screens.hdfs("hdfs_before", SAMPLE_32M)

    say("=== phase 3: fault injection - docker stop hdfs_datanode1")
    since = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
    run("docker", "stop", DN1)
    stopped = time.monotonic()
    say("datanode1 stopped")
    m, waited = watch(lambda m: m["dead"] == 1 and m["under_replicated"] > 0, timeout_s=180)
    say(f"NameNode declared datanode1 dead and flagged blocks under-replicated "
        f"{time.monotonic() - stopped:.0f} s after the stop")

    say("=== phase 4: degraded cluster - reads, writes, no re-replication target")
    degraded = fsck()
    save("fsck_degraded.txt", degraded)
    save("dfsadmin_degraded.txt", nn("hdfs", "dfsadmin", "-report", check=False))
    ui_screens.hdfs("hdfs_degraded", SAMPLE_32M)
    for path in (SAMPLE, SAMPLE_32M):
        got = md5_in_hdfs(path)
        say(f"read {path} with 1 live replica: md5 {got} "
            f"{'== original' if got == original_md5 else '!= ORIGINAL'}")
    nn("dd", "if=/dev/urandom", "of=degraded.bin", "bs=1M", "count=8")
    nn("hdfs", "dfs", "-put", "-f", "degraded.bin", DEGRADED_WRITE)
    say(f"write while degraded succeeded: {DEGRADED_WRITE} "
        f"(replication {nn('hdfs', 'dfs', '-stat', '%r', DEGRADED_WRITE).strip()})")
    say("holding the degraded state for 30 s: with replication 2 and one DataNode "
        "left there is no node to re-replicate to")
    hold_end = time.monotonic() + 30
    while time.monotonic() < hold_end:
        say(fmt(metrics()))
        time.sleep(10)

    say("=== phase 5: recovery - docker start hdfs_datanode1")
    run("docker", "start", DN1)
    restarted = time.monotonic()
    m, _ = watch(lambda m: m["live"] == 2 and m["under_replicated"] == 0, timeout_s=300)
    say(f"recovered: 2 live DataNodes, 0 under-replicated blocks "
        f"{time.monotonic() - restarted:.0f} s after the restart")
    after = fsck()
    save("fsck_after.txt", after)
    ui_screens.hdfs("hdfs_recovered", SAMPLE_32M)
    for path in (SAMPLE, SAMPLE_32M, DEGRADED_WRITE):
        repl = re.findall(r"Live_repl=(\d+)", fsck(path))
        say(f"{path}: Live_repl per block = {repl}")

    events = subprocess.run(["docker", "logs", "--since", since, NN], capture_output=True,
                            text=True).stdout + subprocess.run(
        ["docker", "logs", "--since", since, NN], capture_output=True, text=True).stderr
    keep = [line for line in events.splitlines() if re.search(
        r"dead|Removing a node|removeDeadDatanode|lost heartbeat|heartbeat|Registered "
        r"DN|registerDatanode|Adding new node|stale|underReplicated|neededReconstruction|"
        r"BLOCK\* processReport|Number of failed storages", line, re.I)]
    save("namenode_events.log", "\n".join(keep) + "\n")
    say("=== done")


if __name__ == "__main__":
    main()
