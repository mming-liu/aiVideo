"""shared.paths - 输出目录统一计算（全流水线唯一路径来源）

层级对应小说自身结构「小说 → 部 → 章」（如《锦衣夜行》第一部 杀青州 第002章）:
  {OUTPUT_BASE}/                              默认 ./output/video
  └── {NOVEL_NAME}/                           小说级，如 锦衣夜行
      ├── global/                             全书跨部/跨章共享（角色形象、风格锚点在此，跨部统一）
      │   ├── characters_bank/                角色注册表 + 肖像
      │   ├── style_bank/                     风格锚点
      │   └── chapter_cache/                  章节文本解析缓存
      └── part_{BOOK_INDEX:02d}[_{BOOK_NAME}]/   部级，如 part_01_杀青州
          └── chapter_{NOVEL_CHAPTER:03d}/    章级，如 chapter_002（对齐小说 第NNN章）
              ├── script_result.json / final.mp4 / subtitles.srt
              └── audio/ clips/ images/ vimax_working/ stretched/

约定：
- 环境变量: OUTPUT_BASE / NOVEL_NAME / BOOK_INDEX（部序号）/ BOOK_NAME（部名）/ NOVEL_CHAPTER
- NOVEL_NAME 为空时退化为无小说层；BOOK_NAME 为空时目录为 part_01
- 各模块一律 from shared.paths import ...，禁止自行拼接路径
"""
import os
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent

# 自行加载 .env（幂等）：路径计算不能依赖调用方的导入顺序，
# 否则先于 load_dotenv 导入本模块的代码会拿到缺少 NOVEL_NAME/BOOK_NAME 的错误路径
from dotenv import load_dotenv
load_dotenv(_PROJECT_ROOT / ".env.content")
load_dotenv(_PROJECT_ROOT / ".env.keys")

# step01 snapshots 轮转上限（main._snapshot_run_artifacts 用；env SNAPSHOTS_KEEP 可覆盖）。
# v6.50 P3 设计意图：snapshot 是 LLM 原始输出的唯一回滚手段，默认保留 20 份
# （按 script_result_*.json 计数，配对删除同名 step01_*.log）。
MAX_SNAPSHOTS_PER_CHAPTER = max(1, int(os.getenv("SNAPSHOTS_KEEP", "20")))


def _output_base() -> Path:
    raw = os.getenv("OUTPUT_BASE", "./output/video")
    p = Path(raw)
    return p if p.is_absolute() else _PROJECT_ROOT / p


def novel_name() -> str:
    return os.getenv("NOVEL_NAME", "").strip()


def book_index() -> int:
    """部序号（第一部=1）."""
    return int(os.getenv("BOOK_INDEX", "1"))


def book_name() -> str:
    """部名（如 杀青州）."""
    return os.getenv("BOOK_NAME", "").strip()


def chapter_index() -> int:
    return int(os.getenv("NOVEL_CHAPTER", "1"))


def novel_dir() -> Path:
    """小说根目录: 锦衣夜行（NOVEL_NAME 为空时退化为 OUTPUT_BASE 本身）."""
    name = novel_name()
    return _output_base() / name if name else _output_base()


def book_dir(book: int | None = None) -> Path:
    """部目录: <novel_dir>/part_01_杀青州（BOOK_NAME 为空时 part_01）."""
    idx = book if book is not None else book_index()
    name = book_name()
    suffix = f"_{name}" if name else ""
    return novel_dir() / f"part_{idx:02d}{suffix}"


def global_dir() -> Path:
    """全书跨部/跨章共享目录: <novel_dir>/global（角色形象跨部统一的关键）."""
    return novel_dir() / "global"


def chapter_dir(chapter: int | None = None, book: int | None = None) -> Path:
    """本章产物目录: <book_dir>/chapter_002（3位编号对齐小说 第NNN章）."""
    ch = chapter if chapter is not None else chapter_index()
    return book_dir(book) / f"chapter_{ch:03d}"


# ---- 常用子路径 ----------------------------------------------------------

def script_file(chapter: int | None = None) -> Path:
    return chapter_dir(chapter) / "script_result.json"


def audio_dir(chapter: int | None = None) -> Path:
    return chapter_dir(chapter) / "audio"


def clips_dir(chapter: int | None = None) -> Path:
    return chapter_dir(chapter) / "clips"


def images_dir(chapter: int | None = None) -> Path:
    return chapter_dir(chapter) / "images"


def vimax_working_dir(chapter: int | None = None) -> Path:
    """ViMax 工作目录（VIMAX_WORKING_DIR 可显式覆盖）."""
    override = os.getenv("VIMAX_WORKING_DIR", "").strip()
    if override:
        return Path(override)
    return chapter_dir(chapter) / "vimax_working"


# ---- 逐镜产物文件名（跨模块契约常量） --------------------------------------
# provider 落盘 / step02 缓存判定 / step04 合并读取必须同名；
# 下面的 accessor 与 param 驱动代码（working_dir 由调用方传入时）共用这组常量。
SHOT_VIDEO_FILENAME = "video.mp4"
FIRST_FRAME_FILENAME = "first_frame.png"
LAST_FRAME_FILENAME = "last_frame.png"
CLIP_FILENAME_FMT = "clip_{}.mp4"
SHOT_AUDIO_FILENAME_FMT = "shot_{}.mp3"


def stretched_dir(chapter: int | None = None) -> Path:
    """时长拉伸片段目录（step04 对短片段放速后的输出）."""
    return chapter_dir(chapter) / "stretched"


def vimax_shots_dir(chapter: int | None = None) -> Path:
    """逐镜工作根目录: <vimax_working_dir>/shots/."""
    return vimax_working_dir(chapter) / "shots"


def shot_work_dir(idx: int, chapter: int | None = None) -> Path:
    """单镜工作目录: <vimax_working_dir>/shots/<idx>/（帧/视频/哈希文件落此）."""
    return vimax_shots_dir(chapter) / str(idx)


def shot_video_file(idx: int, chapter: int | None = None) -> Path:
    """单镜视频产物（视频 provider 统一输出）: shots/<idx>/video.mp4."""
    return shot_work_dir(idx, chapter) / SHOT_VIDEO_FILENAME


def first_frame_file(idx: int, chapter: int | None = None) -> Path:
    """单镜首帧图: shots/<idx>/first_frame.png."""
    return shot_work_dir(idx, chapter) / FIRST_FRAME_FILENAME


def last_frame_file(idx: int, chapter: int | None = None) -> Path:
    """单镜尾帧图: shots/<idx>/last_frame.png."""
    return shot_work_dir(idx, chapter) / LAST_FRAME_FILENAME


def clip_file(idx: int, chapter: int | None = None) -> Path:
    """单镜成片片段: clips/clip_<idx>.mp4."""
    return clips_dir(chapter) / CLIP_FILENAME_FMT.format(idx)


def audio_file(idx: int, chapter: int | None = None) -> Path:
    """单镜 TTS 音频: audio/shot_<idx>.mp3."""
    return audio_dir(chapter) / SHOT_AUDIO_FILENAME_FMT.format(idx)


def final_file(chapter: int | None = None) -> Path:
    """最终成片: chapter/final.mp4."""
    return chapter_dir(chapter) / "final.mp4"


def subtitles_file(chapter: int | None = None) -> Path:
    """字幕文件: chapter/subtitles.srt."""
    return chapter_dir(chapter) / "subtitles.srt"


def objects_bank_dir() -> Path:
    """物件资产库（scene_objects_bank 场景内小物件）."""
    return global_dir() / "objects_bank"


def characters_bank_dir() -> Path:
    return global_dir() / "characters_bank"


def style_bank_dir() -> Path:
    return global_dir() / "style_bank"


def scenes_bank_dir() -> Path:
    """场景资产库（大场景图 + 近景功能场景图），全书跨章共享."""
    return global_dir() / "scenes_bank"


def props_bank_dir() -> Path:
    """道具资产库（白底道具资产图），全书跨章共享."""
    return global_dir() / "props_bank"


def chapter_cache_dir() -> Path:
    return global_dir() / "chapter_cache"
