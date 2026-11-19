"""Lab 8-9: HDFS checks through the NameNode container's CLI (docker exec).

Skipped when the hdfs_namenode container is not running. Each `hdfs` call
starts a JVM, so these take a few seconds each.
"""
import re
import shutil
import subprocess
import uuid

import pytest

NAMENODE = "hdfs_namenode"
BASE = f"/user/student/pytest-{uuid.uuid4().hex[:8]}"


def namenode_running():
    if shutil.which("docker") is None:
        return False
    r = subprocess.run(["docker", "inspect", "-f", "{{.State.Running}}", NAMENODE],
                       capture_output=True, text=True)
    return r.stdout.strip() == "true"


pytestmark = pytest.mark.skipif(not namenode_running(), reason="hdfs_namenode is not running")


def nn(*args, check=True):
    r = subprocess.run(["docker", "exec", NAMENODE, *args], capture_output=True, text=True,
                       timeout=180)
    if check and r.returncode:
        raise AssertionError(f"{' '.join(args)} failed:\n{r.stdout}\n{r.stderr}")
    return r.stdout


@pytest.fixture(scope="module", autouse=True)
def workdir():
    nn("hdfs", "dfs", "-mkdir", "-p", BASE)
    yield BASE
    nn("hdfs", "dfs", "-rm", "-r", "-f", "-skipTrash", BASE, check=False)


def test_two_datanodes_live():
    report = nn("hdfs", "dfsadmin", "-report")
    assert re.search(r"Live datanodes \(2\)", report), report


def test_file_is_split_into_blocks_with_two_replicas():
    # 10 MiB with a 1 MiB block size (the minimum HDFS allows) -> 10 blocks.
    nn("sh", "-c", "dd if=/dev/urandom of=/tmp/t10m.bin bs=1M count=10 2>/dev/null")
    path = f"{BASE}/t10m.bin"
    nn("hdfs", "dfs", "-D", "dfs.blocksize=1048576", "-put", "-f", "/tmp/t10m.bin", path)

    assert nn("hdfs", "dfs", "-stat", "%r %o %b", path).split() == ["2", "1048576", "10485760"]
    fsck = nn("hdfs", "fsck", path, "-files", "-blocks", "-locations")
    assert "Status: HEALTHY" in fsck
    blocks = re.findall(r"len=\d+ Live_repl=(\d+)", fsck)
    assert len(blocks) == 10 and set(blocks) == {"2"}
    # Every block has one replica on each of the two DataNodes.
    for line in fsck.splitlines():
        if "Live_repl=" in line:
            assert line.count("DatanodeInfoWithStorage") == 2


def test_hdfs_read_returns_written_bytes():
    nn("sh", "-c", "head -c 3000000 /dev/urandom > /tmp/t3m.bin")
    path = f"{BASE}/t3m.bin"
    nn("hdfs", "dfs", "-put", "-f", "/tmp/t3m.bin", path)
    local = nn("sh", "-c", "md5sum < /tmp/t3m.bin").split()[0]
    remote = nn("sh", "-c", f"hdfs dfs -cat {path} | md5sum").split()[0]
    assert local == remote
