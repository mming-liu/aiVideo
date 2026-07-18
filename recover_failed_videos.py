"""手动轮询失败的视频任务"""
import requests
import time
import os
import sys

# 从日志中提取的待轮询任务
PENDING_TASKS = [
    ("镜头2", "task_js3LZm9hYy5YO3yVbuPHFE8sPgTnTbg9"),
    ("镜头4", "task_8aLJc1mBS7g36EkbclR50Ph3Xmt5u2q9"),
    ("镜头6", "task_lp7RiAmRhRC9uYjAoXzcxsguWO6m0sFh"),
    ("镜头8", "task_2g9cZvNrVClOCn0JEBMcQHIPaSMi6UYW"),
]

# 获取 API Key
from dotenv import load_dotenv
from pathlib import Path
_ = load_dotenv(Path(__file__).parent / ".env")

from shared.error_fmt import fmt_err

AGNES_API_KEY = os.getenv("AGNES_API_KEY", "")
AGNES_API_KEY_2 = os.getenv("AGNES_API_KEY_2", "")
AGNES_API_KEY_3 = os.getenv("AGNES_API_KEY_3", "")
AGNES_API_KEY_4 = os.getenv("AGNES_API_KEY_4", "")
AGNES_API_KEY_5 = os.getenv("AGNES_API_KEY_5", "")

API_KEYS = [k for k in [AGNES_API_KEY, AGNES_API_KEY_2, AGNES_API_KEY_3, AGNES_API_KEY_4, AGNES_API_KEY_5] if k]

BASE_URL = os.getenv("AGNES_BASE_URL", "https://api.engagelist.com")

# 章节 clips 目录（统一路径来源: shared/paths.py）
from shared.paths import clips_dir
CLIPS_DIR = str(clips_dir())

def query_video(video_id, api_key):
    url = f"{BASE_URL}/ Agnesapi?video_id={video_id}"
    headers = {"Authorization": f"Bearer {api_key}"}
    try:
        res = requests.get(url, headers=headers, timeout=60)
        return res
    except Exception as e:
        print(f"  查询异常: {e}")
        return None

def poll_and_download(shot_name, video_id, max_attempts=30):
    key_idx = 0
    for attempt in range(1, max_attempts + 1):
        api_key = API_KEYS[key_idx % len(API_KEYS)]
        res = query_video(video_id, api_key)

        if res is None:
            time.sleep(5)
            continue

        if res.status_code == 429:
            print(f"[{shot_name}] Key {key_idx + 1} 限速，切换...")
            key_idx += 1
            time.sleep(3)
            continue

        if res.status_code != 200:
            print(f"[{shot_name}] 查询失败 [{res.status_code}]: {fmt_err(res.text)}")
            time.sleep(5)
            continue

        data = res.json()
        status = data.get("status", "").upper()

        if status in ("SUCCEEDED", "SUCCESS", "COMPLETED", "DONE"):
            # 获取 video_url
            output = data.get("output", {})
            video_url = None
            for key in ("video_url", "url", "remixed_from_video_video_id"):
                video_url = output.get(key)
                if video_url:
                    break
            if not video_url and output.get("video_urls"):
                video_url = output["video_urls"][0]

            if not video_url:
                print(f"[{shot_name}] 成功但无 video_url: {data}")
                return False

            # 下载
            print(f"[{shot_name}] 视频就绪，下载中...")
            try:
                video_bin = requests.get(video_url, timeout=120).content
                save_path = os.path.join(CLIPS_DIR, f"clip_{shot_name.replace('镜头', '')}.mp4")
                os.makedirs(CLIPS_DIR, exist_ok=True)
                with open(save_path, "wb") as f:
                    f.write(video_bin)
                size_mb = len(video_bin) / (1024 * 1024)
                print(f"[{shot_name}] OK -> {save_path} ({size_mb:.1f} MB)")
                return True
            except Exception as e:
                print(f"[{shot_name}] 下载失败: {e}")
                return False

        elif status in ("FAILED", "FAIL", "ERROR"):
            reason = data.get("message") or data.get("error") or "未知"
            print(f"[{shot_name}] 失败: {reason}")
            return False
        else:
            print(f"[{shot_name}] 状态: {status} ({attempt}/{max_attempts})")
            time.sleep(10)

    print(f"[{shot_name}] 轮询超时")
    return False

if __name__ == "__main__":
    print("手动轮询失败的视频任务...\n")
    for shot_name, video_id in PENDING_TASKS:
        print(f"=> {shot_name}: {video_id}")
        poll_and_download(shot_name, video_id)
        print()