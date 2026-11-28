"""画风切换（VIDEO_STYLE）必须贯通到图片/视频实际请求文本.

2026-11-02：ink_painting → anime 切换时发现视频负向词与视频前缀仍写死古风真人
（NEGATIVE_PROMPT 含「动漫风格、2D渲染、卡通脸」，STYLE_PREFIX 是「古风真人影视剧，
16:9」），anime 首帧会与「禁止动漫 / 16:9」的反向提示和画幅自相矛盾。这里锁定：

- 视频负向词随当前画风：anime 不再禁二次元；gufeng 与旧 NEGATIVE_PROMPT_CN 同集合
- 视频前缀 = 当前画风 + 调用方画幅（9:16）
- 风格收束句不拼出「画风风格」
- 图片 QC 不再把作品说成水墨

全部为纯函数，无外部请求、无生产产物读写。
"""
from __future__ import annotations



def _terms(text: str) -> set:
    return {t.strip() for t in text.replace("，", "、").replace(",", "、").split("、")
            if t.strip()}


def test_anime_video_negative_does_not_ban_anime(monkeypatch):
    monkeypatch.setenv("VIDEO_STYLE", "anime")
    monkeypatch.setenv("VIDEO_PROVIDER", "volcengine")
    from shared.style_prefix import video_negative_prompt

    negative = video_negative_prompt()
    for banned in ("动漫风格", "动画", "2D渲染", "卡通脸", "卡通人物", "Q版"):
        assert banned not in negative, f"anime 负向词仍在禁二次元: {banned}"
    # 画风自身的反向词与通用词必须保留
    assert "真人写实照片" in negative
    assert "水印" in negative
    # 现代物件禁令只属于古风类预设：anime 不应禁现代服装/建筑/汽车
    for term in ("现代服装", "现代建筑", "汽车", "摩托车"):
        assert term not in negative, f"通用负向词仍含现代物件禁令: {term}"


def test_gufeng_video_negative_keeps_legacy_terms(monkeypatch):
    monkeypatch.setenv("VIDEO_STYLE", "gufeng")
    monkeypatch.setenv("VIDEO_PROVIDER", "volcengine")
    from shared.style_prefix import video_negative_prompt, NEGATIVE_PROMPT_CN

    # 旧 NEGATIVE_PROMPT 集合 + 古风专用的西式建筑/机械齿轮（全局默认里移回预设）
    assert _terms(video_negative_prompt()) == (_terms(NEGATIVE_PROMPT_CN)
                                               | {"西式建筑", "机械齿轮"})
    for term in ("现代服装", "现代建筑", "汽车", "摩托车"):
        assert term in video_negative_prompt()


def test_global_render_config_follows_video_style(monkeypatch):
    """render_style/negative_prompt 跟随 VIDEO_STYLE；天气/氛围不设题材兜底."""
    from shared.style_prefix import get_global_render_config, style_label

    for name in ("gufeng", "anime"):
        monkeypatch.setenv("VIDEO_STYLE", name)
        monkeypatch.delenv("RENDER_STYLE", raising=False)
        config = get_global_render_config()
        assert config["render_style"] == style_label(name)
        assert config["global_weather"] == ""
        assert config["global_atmosphere"] == ""
        assert ("动漫风格" in config["negative_prompt"]) == (name == "gufeng")
        assert ("现代建筑" in config["negative_prompt"]) == (name == "gufeng")

    # 显式 RENDER_STYLE 仍可覆盖元数据
    monkeypatch.setenv("RENDER_STYLE", "自定义画风名")
    assert get_global_render_config()["render_style"] == "自定义画风名"


def test_agnes_keeps_english_text_negative(monkeypatch):
    monkeypatch.setenv("VIDEO_STYLE", "anime")
    monkeypatch.setenv("VIDEO_PROVIDER", "agnes")
    from shared.style_prefix import video_negative_prompt

    assert "English text" in video_negative_prompt()


def test_video_prefix_follows_style_and_canvas(monkeypatch):
    monkeypatch.setenv("VIDEO_STYLE", "anime")
    from shared.style_prefix import style_prefix_for_canvas, ensure_prefix

    prefix = style_prefix_for_canvas("9:16")
    assert "二次元" in prefix
    assert "9:16画幅" in prefix
    assert "16:9" not in prefix
    assert ensure_prefix("画面内容").startswith(prefix)


def test_style_lock_avoids_duplicated_style_suffix(monkeypatch):
    monkeypatch.setenv("VIDEO_STYLE", "anime")
    from shared.style_prefix import style_lock_prompt

    lock = style_lock_prompt()
    assert "高清国漫二次元漫剧风格" in lock
    assert "画风风格" not in lock
    assert "古风真人" not in lock
    assert "真人写实照片" in lock  # 禁止漂移的目标来自当前画风负向词


def test_image_qc_prompt_follows_style(monkeypatch):
    monkeypatch.setenv("VIDEO_STYLE", "anime")
    from shared.image_qc import build_qc_prompt

    shot = {"index": 1, "scene": "河边小舟", "main_subject": "少年",
            "ff_desc": "少年坐于船头，手持竹箫", "core_keywords": ["竹箫"]}
    prompt = build_qc_prompt(shot, "first_frame", characters=[], empty_scene=False)
    assert "水墨" not in prompt
    assert "高清国漫二次元漫剧" in prompt


# ---------------------------------------------------------------------------
# 单一入口：13 个预设都必须由 style_profile() 一次取齐
# ---------------------------------------------------------------------------

def _presets() -> list:
    from shared.style_prefix import STYLE_PRESETS
    return list(STYLE_PRESETS)


def test_style_profile_is_single_source_for_every_preset(monkeypatch):
    monkeypatch.setenv("VIDEO_PROVIDER", "volcengine")
    monkeypatch.setenv("IMAGE_PROVIDER", "agent_plan")
    from shared.style_prefix import (style_profile, style_prefix_for_canvas,
                                     video_negative_prompt, image_negative_prompt)

    for name in _presets():
        profile = style_profile(name, "9:16")
        assert profile.name == name
        assert profile.head and profile.label
        assert profile.prefix == style_prefix_for_canvas("9:16", name)
        assert profile.video_negative == video_negative_prompt(name)
        assert profile.negative == image_negative_prompt(name)
        assert "9:16画幅" in profile.prefix
        assert "16:9" not in profile.prefix
        # 画风不得出现在自己的反向词里（本次 anime 事故的通用形态）
        assert profile.head not in profile.video_negative
        assert profile.head not in profile.negative


def test_style_profile_follows_env(monkeypatch):
    monkeypatch.setenv("VIDEO_STYLE", "anime")
    from shared.style_prefix import style_profile

    profile = style_profile()
    assert profile.name == "anime"
    assert "二次元" in profile.head


def test_qc_prompt_never_mentions_other_styles(monkeypatch):
    monkeypatch.setenv("VIDEO_PROVIDER", "volcengine")
    monkeypatch.setenv("IMAGE_PROVIDER", "agent_plan")
    from shared.style_prefix import style_head
    from shared.image_qc import build_qc_prompt

    heads = {name: style_head(name) for name in _presets()}
    shot = {"index": 1, "scene": "河边小舟", "main_subject": "少年",
            "ff_desc": "少年坐于船头，手持竹箫", "core_keywords": ["竹箫"]}
    for name, own in heads.items():
        monkeypatch.setenv("VIDEO_STYLE", name)
        prompt = build_qc_prompt(shot, "first_frame", characters=[], empty_scene=False)
        assert own in prompt
        for other, head in heads.items():
            if other == name:
                continue
            assert head not in prompt, f"{name} 的 QC 出现了 {other} 的画风词"


# ---------------------------------------------------------------------------
# 守卫：运行时不再引用任何「某画风专用」常量
# ---------------------------------------------------------------------------

_LEGACY_STYLE_NAMES = {
    "STYLE_PREFIX", "STYLE_PREFIX_SHORT", "NEGATIVE_PROMPT",
    "NEGATIVE_PROMPT_CN", "NEGATIVE_LAYERS",
}
_SCAN_ROOTS = ("shared", "step01_script_gen", "step02a_frame_gen",
               "step02_video_gen", "step02b_video_gen", "_image_gen",
               "_video_gen", "tools", "vimax")


def _legacy_style_references() -> list:
    import ast
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    offenders = []
    for sub in _SCAN_ROOTS:
        base = root / sub
        if not base.is_dir():
            continue
        for path in base.rglob("*.py"):
            if path.name == "style_prefix.py" and path.parent.name == "shared":
                continue
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"))
            except (OSError, SyntaxError):
                continue
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and node.module == "shared.style_prefix":
                    for alias in node.names:
                        if alias.name in _LEGACY_STYLE_NAMES:
                            offenders.append(
                                f"{path.relative_to(root)}:{node.lineno}:{alias.name}")
                elif (isinstance(node, ast.Attribute)
                      and isinstance(node.value, ast.Name)
                      and node.value.id == "style_prefix"
                      and node.attr in _LEGACY_STYLE_NAMES):
                    offenders.append(
                        f"{path.relative_to(root)}:{node.lineno}:{node.attr}")
    return offenders


def test_runtime_no_longer_imports_style_constants():
    offenders = _legacy_style_references()
    assert not offenders, "运行时入口不得再引用画风常量：" + ", ".join(offenders)
