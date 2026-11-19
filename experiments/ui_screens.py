"""Screenshots of the HDFS Web UI (localhost:9870) and the MinIO Console
(localhost:9002) with Playwright, driving the locally installed Chrome.

    python experiments/ui_screens.py hdfs  PREFIX     # overview, datanodes, block info
    python experiments/ui_screens.py minio            # login, bucket, object metadata
"""
import os
import sys

from playwright.sync_api import sync_playwright

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SHOTS = os.path.join(ROOT, "screenshots")
HDFS_UI = "http://localhost:9870"
MINIO_UI = "http://localhost:9002"
VIEWPORT = {"width": 1366, "height": 900}


def _browser(p):
    return p.chromium.launch(channel="chrome", args=["--no-sandbox"])


def _save(page, name, full_page=True):
    os.makedirs(SHOTS, exist_ok=True)
    path = os.path.join(SHOTS, name)
    page.screenshot(path=path, full_page=full_page)
    print("screenshot", os.path.relpath(path, ROOT))
    return path


def hdfs(prefix, file_path=None, block_index=0):
    """Overview + Datanodes tabs; with file_path also the explorer's block
    information dialog for that file."""
    with sync_playwright() as p:
        browser = _browser(p)
        page = browser.new_page(viewport=VIEWPORT)

        page.goto(f"{HDFS_UI}/dfshealth.html#tab-overview")
        page.wait_for_selector("#tab-overview table", timeout=20000)
        page.wait_for_timeout(500)
        _save(page, f"{prefix}_overview.png")

        page.goto(f"{HDFS_UI}/dfshealth.html#tab-datanode")
        page.wait_for_selector("#table-datanodes tbody tr", timeout=20000)
        page.wait_for_timeout(500)
        _save(page, f"{prefix}_datanodes.png")

        if file_path:
            directory, name = file_path.rsplit("/", 1)
            page.goto(f"{HDFS_UI}/explorer.html#{directory}")
            page.wait_for_selector(f"a.explorer-browse-links:text-is('{name}')", timeout=20000)
            page.wait_for_timeout(300)
            _save(page, f"{prefix}_explorer.png")
            page.click(f"a.explorer-browse-links:text-is('{name}')")
            page.wait_for_selector("#file-info-blockinfo-list option", state="attached",
                                   timeout=20000)
            options = page.locator("#file-info-blockinfo-list option")
            if block_index < options.count():
                page.select_option("#file-info-blockinfo-list", index=block_index)
            page.wait_for_timeout(500)
            _save(page, f"{prefix}_blockinfo.png", full_page=False)
        browser.close()


def minio(keys=("datasets/sample_10m.bin", "reports/lab-report-draft.txt"),
          bucket="distributed-course-bucket"):
    """Log in, open the bucket, open each object's details (metadata)."""
    user = os.environ.get("S3_ACCESS_KEY", "minioadmin")
    password = os.environ.get("S3_SECRET_KEY", "minioadminpassword")
    with sync_playwright() as p:
        browser = _browser(p)
        page = browser.new_page(viewport=VIEWPORT)
        page.goto(MINIO_UI)
        page.fill("#accessKey", user)
        page.fill("#secretKey", password)
        page.click("button[type=submit]")
        page.wait_for_url("**/browser**", timeout=20000)
        page.wait_for_timeout(1500)
        _save(page, "minio_buckets.png")

        page.goto(f"{MINIO_UI}/browser/{bucket}")
        page.wait_for_timeout(2500)
        _save(page, "minio_bucket_root.png")
        for key in keys:
            directory, name = key.rsplit("/", 1)
            page.goto(f"{MINIO_UI}/browser/{bucket}/{directory}/")
            page.wait_for_timeout(2000)
            page.get_by_text(name, exact=True).first.click()
            page.wait_for_timeout(2000)
            _save(page, f"minio_object_{name.replace('.', '_')}.png")
        browser.close()


if __name__ == "__main__":
    what = sys.argv[1] if len(sys.argv) > 1 else "hdfs"
    if what == "hdfs":
        hdfs(sys.argv[2] if len(sys.argv) > 2 else "hdfs",
             sys.argv[3] if len(sys.argv) > 3 else None)
    else:
        minio()
