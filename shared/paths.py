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
              ├── script_result.json / final-A.mp4 / final-B.mp4 / subtitles.srt
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
load_dotenv(_PROJECT_ROOT / ".env")


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


def characters_bank_dir() -> Path:
    return global_dir() / "characters_bank"


def style_bank_dir() -> Path:
    return global_dir() / "style_bank"


def chapter_cache_dir() -> Path:
    return global_dir() / "chapter_cache"
