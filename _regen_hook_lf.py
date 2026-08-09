# -*- coding: utf-8 -*-
"""用 token-plan wan2.7-image-pro 重新生成 hook 尾帧（船保持正确大小）.
以现有 last_frame.png 为构图底 + 船图 + 角色肖像做图生图，prompt 用 hook lf_desc。
直接覆盖 hook 目录 last_frame.png。
"""
import sys, json, base64, urllib.request, os
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

AUTH = "sk-sp-H.YMDPL.2Ufa.MEUCIQCgHYv3cdvV62KNGlReaHWwM6TlAQNy61P3OCoxv6cAjQIgK3mRUYQNVu0REyzFsDO3TDMd_nKazwmlHxC2GWV6akg"
URL = "https://token-plan.cn-beijing.maas.aliyuncs.com/api/v1/services/aigc/multimodal-generation/generation"


def b64(p):
    with open(p, "rb") as f:
        return base64.b64encode(f.read()).decode()


def main():
    from shared.paths import vimax_working_dir, chapter_dir
    from shared.assets_bank import PropAssetsBank

    shot_dir = vimax_working_dir() / "shots" / "0"
    lf_path = shot_dir / "last_frame.png"
    print(f"现有 hook 尾帧: {lf_path} ({os.path.getsize(lf_path)} bytes)")

    pbank = PropAssetsBank()
    pbank.load()
    boat = pbank.get_asset_image("舴艋舟")

    chars_dir = Path("output/video/锦衣夜行/global/characters_bank")
    refs = {
        "杨旭": chars_dir / "杨旭/front.png",
        "听香": chars_dir / "听香/front.png",
        "刺客": chars_dir / "刺客/front.png",
    }

    content = [
        {
            "type": "text",
            "text": (
                "写实古风真人，9:16竖屏。保持参考图的整体构图、人物姿势与夜色氛围"
                "（弦月当空，荷花丛，水面浪花高卷，杨旭仰面倒在船头、心口素白袍暗红洇痕、"
                "钓杆脱手，刺客已反身没入水中仅余一截刀尖刺破水面，听香瘫坐船板双手捂嘴"
                "双眼圆睁、脸上溅暗红红点）。"
                "仅把画面中央的小舟改画成一艘完整的窄身无篷舴艋舟：木质船体深褐色，"
                "长6米、宽1.4米，船头/中舱/船梢三段结构明确，船体在画面中清晰可见、"
                "占据合理主体比例，人物都在船上活动。禁止把船画小、画成小舢板或独木舟大小。"
            ),
        },
        {"type": "image", "image": "data:image/png;base64," + b64(str(lf_path))},
        {"type": "image", "image": "data:image/png;base64," + b64(boat)},
        {"type": "image", "image": "data:image/png;base64," + b64(str(refs["杨旭"]))},
        {"type": "image", "image": "data:image/png;base64," + b64(str(refs["听香"]))},
        {"type": "image", "image": "data:image/png;base64," + b64(str(refs["刺客"]))},
    ]
    body = {
        "model": "wan2.7-image-pro",
        "input": {"messages": [{"role": "user", "content": content}]},
        "parameters": {"size": "720*1280"},
    }
    req = urllib.request.Request(
        URL,
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        headers={"Authorization": "Bearer " + AUTH, "Content-Type": "application/json"},
    )
    print("提交生成请求 ...")
    d = json.loads(urllib.request.urlopen(req, timeout=300).read().decode("utf-8"))
    if "output" not in d:
        print("FAIL:", json.dumps(d, ensure_ascii=False)[:400])
        return 1
    url = d["output"]["choices"][0]["message"]["content"][0]["image"]
    print("图片 URL:", url[:120])

    tmp = str(shot_dir / "last_frame_new.png")
    urllib.request.urlretrieve(url, tmp)
    os.replace(tmp, str(lf_path))
    print(f"已覆盖 hook 尾帧: {lf_path} ({os.path.getsize(lf_path)} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
