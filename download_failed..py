"""快速下载失败的视频"""
import requests
import os
from dotenv import load_dotenv
from pathlib import Path

load_dotenv(Path(".") / ".env")

BASE_URL = os.getenv("AGNES_BASE_URL", "").rstrip("/")
keys = []
for i in range(1, 6):
    k = os.getenv(f"AGNES_API_KEY_{i}" if i > 1 else "AGNES_API_KEY")
    if k:
        keys.append(k)
API_KEYS = keys

tasks = [
    (2, "task_js3LZm9hYy5YO3yVbuPHFE8sPgTnTbg9"),
    (4, "task_8aLJc1mBS7g36EkbclR50Ph3Xmt5u2q9"),
    (6, "task_lp7RiAmRhRC9uYjAoXzcxsguWO6m0sFh"),
    (8, "task_2g9cZvNrVClOCn0JEBMcQHIPaSMi6UYW"),
]

# 章节 clips 目录（统一路径来源: shared/paths.py）
from shared.paths import clips_dir
CLIPS_DIR = str(clips_dir())
os.makedirs(CLIPS_DIR, exist_ok=True)

for shot_idx, vid in tasks:
    print(f"=> 镜头{shot_idx}: {vid}")
    url = BASE_URL + "/agnesapi?video__id=" + vid
    res = requests.get(url, headers={"Authorization": "Bearer " + API_KEYS[0]}, timeout=30)
    data = res.json()

    output = data.get("output", {})
    video_url = output.get("video_url") or output.get("url")
    if not video_url and output.get("video_urls"):
        video_url = output["video_urls"][0]

    if video_url:
        print(f"  下载: {video_url[:60]}...")
        video_bin = requests.get(video_url, timeout=120).content
        save_path = os.path.join(CLIPS_DIR, "clip_" + str(shot_idx) + ".mp4")
        with open(save_path, "wb") as f:
            f.write(video_bin)
        print(f"  OK -> {save_path} ({len(video_bin)/10.24/10.24:.1f} MB)")
    else:
        print(f"  无 video_url: {output}")