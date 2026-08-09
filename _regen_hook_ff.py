# -*- coding: utf-8 -*-
"""用 token-plan wan2.7-image-pro 重新生成 hook 首帧（v5：高清+站位修正）.

v5 修正三个问题：
1. 分辨率提到 1440*2560（高清，解决模糊）
2. 站位按脚本：杨旭在船头（手持钓杆仰卧），听香在中舱（两人不挤在左侧）
3. 刺客潜伏水下（仅露头肩），面向画右船头的杨旭，不是面向镜头
"""
import sys, json, base64, urllib.request, os
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

AUTH = "sk-sp-H.YMDPL.2Ufa.MEUCIQCgHYv3cdvV62KNGlReaHWwM6TlAQNy61P3OCoxv6cAjQIgK3mRUYQNVu0REyzFsDO3TDMd_nKazwmlHxC2GWV6akg"
URL = "https://token-plan.cn-beijing.maas.aliyuncs.com/api/v1/services/aigc/multimodal-generation/generation"
SIZE = "1440*2560"


def b64(p):
    with open(p, "rb") as f:
        return base64.b64encode(f.read()).decode()


def main():
    from shared.paths import vimax_working_dir

    shot_dir = vimax_working_dir() / "shots" / "0"
    ff_path = shot_dir / "first_frame.png"

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
                "写实古风真人电影，9:16竖屏，高分辨率细节，中景镜头，镜头与水面接近平行略俯。\n\n"
                "一艘窄身无篷木质舴艋舟横贯画面前景，船头在画面右、船尾在画面左，"
                "船身深褐色旧木，船头微微翘起、中舱低平、船梢收窄。船身横向贯穿画面，"
                "至少占画面宽度90%，船舷高度到人腰部。\n\n"
                "船头（画面右侧）：杨旭（白衣）仰卧，左手钓杆垂入水面，右手提酒壶，"
                "双眼微闭，神情闲适，完全未察觉危险。\n"
                "中舱（画面中部）：听香（浅色衣）跪坐，披头散发，双眼圆睁，"
                "张嘴欲呼，身体前倾，视线急切地朝画面右下方水面看过去——"
                "她看到了水中的刺客，正欲尖叫示警。\n\n"
                "画面右下方水中（紧贴船身中段外侧，介于听香和杨旭之间的水面）："
                "刺客（黑衣）潜伏在水面下，只露出头部和肩部，额头以下浸在水中。"
                "他侧身位于水面，脸转向画右方（朝船头方向），目光朝画面右上方船头的"
                "杨旭紧盯——他从水中游近船身，正要向船头的杨旭下手。"
                "观众看到的是他的侧脸（右脸轮廓），不是正对镜头，"
                "更不是背对或远离杨旭。匕首尚未出水。\n\n"
                "三人空间关系与视线：杨旭在船右（船头）仰卧闭眼、听香在中舱（船中）"
                "朝画右下方看、刺客在画右船身外水中侧身朝画右船头杨旭。"
                "听香朝画右下方看正好看到水中刺客→她先发现、惊恐欲呼；"
                "杨旭仰卧朝上闭眼→看不到船外刺客，浑然未察。\n\n"
                "关键：画面清晰不模糊，人物五官锐利，船体完整横贯，"
                "禁止把船画小，禁止让刺客背对或远离杨旭，禁止刺客正对镜头。"
            ),
        },
        {"type": "image", "image": "data:image/png;base64," + b64(str(refs["杨旭"]))},
        {"type": "image", "image": "data:image/png;base64," + b64(str(refs["听香"]))},
        {"type": "image", "image": "data:image/png;base64," + b64(str(refs["刺客"]))},
    ]
    body = {
        "model": "wan2.7-image-pro",
        "input": {"messages": [{"role": "user", "content": content}]},
        "parameters": {"size": SIZE},
    }
    req = urllib.request.Request(
        URL,
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        headers={"Authorization": "Bearer " + AUTH, "Content-Type": "application/json"},
    )
    print(f"提交生成请求（v5：1440*2560 高清，站位/朝向修正）...")
    d = json.loads(urllib.request.urlopen(req, timeout=300).read().decode("utf-8"))
    if "output" not in d:
        print("FAIL:", json.dumps(d, ensure_ascii=False)[:400])
        return 1
    url = d["output"]["choices"][0]["message"]["content"][0]["image"]
    print("图片 URL:", url[:120])

    tmp = str(shot_dir / "first_frame_new.png")
    urllib.request.urlretrieve(url, tmp)
    # 检查实际尺寸，确认不是被缩小的
    from PIL import Image
    img = Image.open(tmp)
    print(f"生成尺寸: {img.size}")
    os.replace(tmp, str(ff_path))
    print(f"已覆盖 hook 首帧: {ff_path} ({os.path.getsize(ff_path)} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
