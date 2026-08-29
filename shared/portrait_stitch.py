"""肖像拼图兜底模块 — 单图模型的多角色一致性降级方案.

当 IMAGE_MULTI_REF >= 2 但 provider 只支持单张参考图时，把多张角色肖像
横向拼接为单张「角色参考表」再传入，让单图模型也能同时看到多个角色的长相。

当前 Agnes / Bailian 都原生支持多张参考图，此模块作为预留兜底，
仅在 provider 声明 supports_multi_ref=False 时由适配器层调用。
"""

from pathlib import Path

from PIL import Image


# 拼图统一高度（每张肖像缩放到此高度，保持横向排列整齐）
_STITCH_HEIGHT = 1024
# 相邻肖像之间的白色间隔（像素）
_GAP = 24

# 头肩胸像裁切窗口（v6.52，比例 left/top/right/bottom）。三张在库立绘
# （刺客/杨旭/听香 front.png）实测均为横构图、人物居中、头顶在 ~3-20% 高度
# （听香伞顶贴 0%），武器/手部在 ~55% 高度以下。竖向 [0, 0.52] 裁掉下半身
# 与持物（发光匕首实测在 ~60%），横向取中心 44% 带裁掉大面积留白。
_HEAD_BOX = (0.28, 0.0, 0.72, 0.52)


def crop_head_portrait(img: Image.Image) -> Image.Image:
    """把全身立绘裁成头肩胸像（v6.52 参考图防姿态/持物泄漏）.

    参考图只该提供身份特征（脸/发型/上装）——全身站姿会被图生图照抄成
    画面构图（hook ff 要「半身探出水面」画出站立巨人、船尾多出第二个蒙面
    人实测）。裁到头肩胸后，站姿与持物（含刺客像里的发光匕首）不再入参考。

    仅对 characters_bank 来源调用；场景/道具图不裁（环境需完整布局）。
    调用方保证只裁一次（vimax_bridge._head_portrait_path 在收集层裁，
    拼图路径与单图直传路径拿到的都是已裁缓存）。
    """
    try:
        w, h = img.size
        l, t, r, b = _HEAD_BOX
        box = (max(0, int(w * l)), max(0, int(h * t)),
               min(w, int(w * r)), min(h, int(h * b)))
        if box[2] - box[0] < 8 or box[3] - box[1] < 8:
            return img
        return img.crop(box)
    except Exception:
        return img


def stitch_portraits(paths: list[str], output_path: str) -> str:
    """将多张角色肖像横向拼接为单张参考表.

    布局: [角色A | 间隔 | 角色B | 间隔 | 角色C]，统一高度、白色背景。
    模型会把整张图当作参考，从而同时感知到多个角色的面部/服装特征。

    Args:
        paths: 角色肖像本地路径列表（按出镜顺序）
        output_path: 拼图输出路径（建议 vimax_working/shots/<idx>/ref_sheet.png）

    Returns:
        拼图文件路径；失败或输入不足时返回空字符串
    """
    valid = [p for p in paths if p and Path(p).exists()]
    if len(valid) < 2:
        # 少于 2 张无需拼图，调用方应直接用单张
        return ""

    images: list[Image.Image] = []
    for p in valid:
        try:
            img = Image.open(p).convert("RGB")
            # 等比缩放到统一高度
            ratio = _STITCH_HEIGHT / img.height
            new_w = max(1, int(img.width * ratio))
            images.append(img.resize((new_w, _STITCH_HEIGHT), Image.LANCZOS))
        except Exception:
            continue

    if len(images) < 2:
        return ""

    total_w = sum(im.width for im in images) + _GAP * (len(images) - 1)
    sheet = Image.new("RGB", (total_w, _STITCH_HEIGHT), (255, 255, 255))

    x = 0
    for im in images:
        sheet.paste(im, (x, 0))
        x += im.width + _GAP

    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    sheet.save(output_path, "PNG")
    return output_path
