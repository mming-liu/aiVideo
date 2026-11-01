# -*- coding: utf-8 -*-
"""视觉复核（OpenCode Go 套餐）——先裁切放大，再问可观察事实.

为什么不是"把整张图缩到 512 再问"：首帧里"箫离嘴唇还有一点距离"这种细节，
缩略图会丢掉，模型会误判成"已经在吹"。本工具按区域裁切（face/hands/boat/full）
并放大到 640px，再连同问题发给视觉模型。

端点（nexus-audit 同款）：https://opencode.ai/zen/go/v1/chat/completions
鉴权：Bearer AGENT_PLAN_API_KEY（或 OPENCODE_API_KEY）
必需头：User-Agent（标识客户端）+ x-opencode-session（稳定会话）
"""
from __future__ import annotations

import argparse
import base64
import io
import json
import os
import sys
import uuid

DEFAULT_MODEL = "glm-5.3-flash"
BASE_URL = "https://opencode.ai/zen/go/v1/chat/completions"

#: 相对裁切框 (left, top, right, bottom)，按比例
CROPS = {
    "full": (0.0, 0.0, 1.0, 1.0),
    # 脸部要带到嘴、手和箫：只切到 0.50 会把箫切掉（实测首帧只剩头部）
    "face": (0.18, 0.05, 0.82, 0.62),
    "hands": (0.10, 0.30, 0.90, 0.80),
    "boat": (0.0, 0.30, 1.0, 0.95),
}


def _api_key() -> str:
    for name in ("OPENCODE_API_KEY", "AGENT_PLAN_API_KEY"):
        value = (os.getenv(name) or "").strip()
        if value:
            return value
    try:
        from dotenv import load_dotenv
        load_dotenv(".env.keys")
        for name in ("OPENCODE_API_KEY", "AGENT_PLAN_API_KEY"):
            value = (os.getenv(name) or "").strip()
            if value:
                return value
    except Exception:
        pass
    return ""


def encode_image(path: str, crop: str = "full", width: int = 640) -> str:
    from PIL import Image
    box = CROPS.get(crop, CROPS["full"])
    im = Image.open(path).convert("RGB")
    w, h = im.size
    left, top, right, bottom = (int(box[0] * w), int(box[1] * h),
                                int(box[2] * w), int(box[3] * h))
    im = im.crop((left, top, right, bottom))
    height = max(1, int(im.height * width / im.width))
    im = im.resize((width, height))
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=90)
    return base64.b64encode(buf.getvalue()).decode()


def ask(prompt: str, images: list[tuple[str, str]], *, model: str = DEFAULT_MODEL,
        width: int = 640, timeout: int = 90) -> dict:
    """images: [(path, crop_name), ...] -> 返回 {status, text, usage}."""
    import requests
    key = _api_key()
    if not key:
        return {"status": 0, "text": "缺少 OPENCODE_API_KEY / AGENT_PLAN_API_KEY"}
    content: list[dict] = [{"type": "text", "text": prompt}]
    for idx, (path, crop) in enumerate(images, 1):
        content.append({"type": "text", "text": "图%d（%s 裁切）" % (idx, crop)})
        content.append({"type": "image_url",
                        "image_url": {"url": "data:image/jpeg;base64,"
                                              + encode_image(path, crop, width)}})
    resp = requests.post(
        BASE_URL,
        headers={"Authorization": "Bearer " + key,
                 "User-Agent": "aiVideo-vision/1.0",
                 "x-opencode-session": str(uuid.uuid4())},
        json={"model": model, "messages": [{"role": "user", "content": content}],
              "max_tokens": 600},
        timeout=timeout,
    )
    if resp.status_code != 200:
        return {"status": resp.status_code, "text": resp.text[:800], "usage": None}
    data = resp.json()
    return {"status": 200,
            "text": data["choices"][0]["message"].get("content") or "",
            "usage": data.get("usage", {}).get("opencode")}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="视觉复核（裁切放大 + OpenCode Go）")
    ap.add_argument("--image", action="append", required=True,
                    help="图片路径，可重复；顺序对应图1、图2…")
    ap.add_argument("--crop", default="face", choices=sorted(CROPS),
                    help="裁切区域（默认 face）")
    ap.add_argument("--prompt", required=True, help="问题（只问可观察事实）")
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument("--width", type=int, default=640)
    ap.add_argument("--json", action="store_true", help="输出 JSON")
    args = ap.parse_args(argv)

    result = ask(args.prompt, [(p, args.crop) for p in args.image],
                 model=args.model, width=args.width)
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=1))
    else:
        print(result["text"])
        if result.get("usage"):
            print("\n[usage]", json.dumps(result["usage"], ensure_ascii=False))
    return 0 if result["status"] == 200 else 2


if __name__ == "__main__":
    sys.exit(main())
