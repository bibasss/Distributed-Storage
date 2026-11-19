"""Images for the lab report, built from the captured evidence.

- fsck_*.png: the saved `hdfs fsck` output (logs/fsck_*.txt) rendered as a
  terminal window, so the block layout can be shown as a screenshot.
- report/*.png: crops of the full-page Web UI screenshots to the relevant panel.

    python experiments/report_images.py
"""
import html
import os

from PIL import Image
from playwright.sync_api import sync_playwright

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SHOTS = os.path.join(ROOT, "screenshots")
OUT = os.path.join(SHOTS, "report")
FSCK_CMD = "docker exec hdfs_namenode hdfs fsck /user/student/data -files -blocks -locations"

TERMINALS = [
    ("fsck_before.txt", "fsck_before.png"),
    ("fsck_degraded.txt", "fsck_degraded.png"),
]
# (source, target, (left, top, right, bottom)) in CSS px of the 1366 px wide pages
CROPS = [
    ("hdfs_before_overview.png", "overview_before.png", (100, 415, 1266, 1170)),
    ("hdfs_degraded_overview.png", "overview_degraded.png", (100, 905, 1266, 1130)),
    ("hdfs_recovered_overview.png", "overview_recovered.png", (100, 905, 1266, 1130)),
    ("hdfs_before_datanodes.png", "datanodes_before.png", (100, 560, 1266, 900)),
    ("hdfs_degraded_datanodes.png", "datanodes_degraded.png", (100, 560, 1266, 900)),
    ("hdfs_before_blockinfo.png", "blockinfo_before.png", (380, 25, 990, 555)),
    ("hdfs_degraded_blockinfo.png", "blockinfo_degraded.png", (380, 25, 990, 555)),
    # MinIO screenshots are 1366 x 1450 (tall window, see ui_screens.minio)
    ("minio_bucket_root.png", "minio_root.png", (0, 0, 1366, 515)),
    ("minio_object_lab-report-draft_txt.png", "minio_meta_report.png", (1040, 555, 1335, 1340)),
    ("minio_object_sample_10m_bin.png", "minio_meta_sample.png", (1040, 555, 1335, 1340)),
    ("minio_object_sample_10m_bin.png", "minio_list_sample.png", (250, 100, 1366, 350)),
]


def render_terminal(page, text, path):
    lines = text.rstrip("\n").splitlines()
    # Keep the per-file block listing and the summary, drop the erasure-coding tail.
    if "Erasure Coded Block Groups:" in text:
        lines = lines[:next(i for i, l in enumerate(lines) if l.startswith("Erasure Coded"))]
    body = html.escape("\n".join(lines))
    page.set_content(f"""<html><body style="margin:0;background:#1e1e1e">
<div style="font:12px/1.45 'DejaVu Sans Mono',Menlo,monospace;color:#d4d4d4;padding:14px 16px;
white-space:pre-wrap;word-break:break-all;width:1150px"><span style="color:#6a9955">$ {html.escape(FSCK_CMD)}</span>
{body}</div></body></html>""")
    page.locator("div").first.screenshot(path=path)
    print("rendered", os.path.relpath(path, ROOT))


def main():
    os.makedirs(OUT, exist_ok=True)
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="chrome", args=["--no-sandbox"])
        page = browser.new_page(viewport={"width": 1200, "height": 900})
        for source, target in TERMINALS:
            with open(os.path.join(ROOT, "logs", source)) as f:
                render_terminal(page, f.read(), os.path.join(SHOTS, target))
        browser.close()
    for source, target, box in CROPS:
        with Image.open(os.path.join(SHOTS, source)) as img:
            right, bottom = min(box[2], img.width), min(box[3], img.height)
            img.crop((box[0], box[1], right, bottom)).save(os.path.join(OUT, target))
        print("cropped", target)


if __name__ == "__main__":
    main()
