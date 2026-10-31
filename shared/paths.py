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
import hashlib
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


# ---- step01 正式产物与提交清单（**文件名单源**：`shared.quality_gate` 从这里取）------
#: 三份正式产物（提交时按 audit → quality → script 顺序替换）。
STEP01_ARTIFACT_FILES = ("script_result.json", "step01_quality.json", "step01_audit.json")
#: 提交清单：三份正式产物替换完成后写的 manifest，是"提交完成"的唯一标志。
STEP01_COMMIT_FILENAME = "step01_commit.json"


def step01_quality_file(chapter: int | None = None) -> Path:
    """本章质量门报告（step01 正式产物之一）."""
    return chapter_dir(chapter) / "step01_quality.json"


def step01_audit_file(chapter: int | None = None) -> Path:
    """本章语义审计报告（step01 正式产物之一）."""
    return chapter_dir(chapter) / "step01_audit.json"


def step01_commit_file(chapter: int | None = None) -> Path:
    """本章**提交清单**（manifest）：缺失/非 COMMITTED/摘要不符一律 fail-closed."""
    return chapter_dir(chapter) / STEP01_COMMIT_FILENAME


def step02b_quality_file(chapter: int | None = None) -> Path:
    """本章 step02b 质量门（下游阶段写的，同属"正式产物"保护范围）."""
    return chapter_dir(chapter) / "step02b_quality.json"


#: step02b 生成期**派生指标**（每镜实际时长 / 与期望时长的偏差）的 sidecar 文件名。
#:
#: 为什么不写回 `script_result.json`：它是 step01 的**已提交正式产物**，
#: `step01_commit.json` 锁着它的 SHA1，下游每个阶段入口都核
#: （`shared.quality_gate.verify_step01_commit_manifest`）。运行期派生数据一旦回写，
#: 摘要即失配 ⇒ step02b 跑成功之后 step03/step04 与任何重跑都被判
#: `artifact changed after commit` 拒收（跨阶段硬阻断）。派生数据落 sidecar，
#: 正式产物保持字节级只读。sidecar 与它描述的产物**同目录**（由 `script_file()`
#: 定目录 + 本常量定文件名），不另拼目录层级。
STEP02_VIDEO_METRICS_FILENAME = "step02_video_metrics.json"


def step01_log_file(chapter: int | None = None) -> Path:
    """step01 日志文件路径（全库唯一来源，禁止各处自行拼 `chapter_dir()/step01.log`）.

    `STEP01_LOG_PATH` 可整体覆盖：诊断脚本 / 探针 / 并行进程导入
    `step01_script_gen.main` 时，模块级 logger 会绑定到**生产章目录**的
    step01.log，把探针自己的 CONSTRAINT-WARN 混进产线日志（09-26 实测：
    `.tmp/probe_contract_anchoring.py` 单次运行即写入 142 行，事后无法从
    step01.log 区分哪几行属于正式 run——正是排查 P0 时踩到的取证障碍）。
    覆盖后三处 logger 初始化（main / timecode / validation）一起跟随。
    """
    raw = os.getenv("STEP01_LOG_PATH", "").strip()
    if raw:
        return Path(raw)
    return chapter_dir(chapter) / "step01.log"


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
KEYFRAME_FILENAME_FMT = "keyframe_{}.png"
CLIP_FILENAME_FMT = "clip_{}.mp4"
SHOT_AUDIO_FILENAME_FMT = "shot_{}.mp3"
#: 图外旁白音轨：一条旁白一个文件（序号 = 计划里 `narration_lines` 的 N 编号）。
#: 2026-09-30 复核第 7 轮：旁白走**独立 TTS 音轨**（合并时叠在原声之上），
#: 不能混进 `shot_<idx>.mp3`（那是角色对白轨，音色由说话人决定）。
NARRATION_AUDIO_FILENAME_FMT = "narration_{:02d}.mp3"


def stretched_dir(chapter: int | None = None) -> Path:
    """时长拉伸片段目录（step04 对短片段放速后的输出）."""
    return chapter_dir(chapter) / "stretched"


def vimax_shots_dir(chapter: int | None = None) -> Path:
    """逐镜工作根目录: <vimax_working_dir>/shots/."""
    return vimax_working_dir(chapter) / "shots"


def shot_work_dir(idx: int, chapter: int | None = None) -> Path:
    """单镜工作目录: <vimax_working_dir>/shots/<idx>/（帧/视频/哈希文件落此）."""
    return vimax_shots_dir(chapter) / str(idx)


def production_video_attempts_root(shot_dir: Path) -> Path:
    """Per-shot execution journal; separate from accepted media and candidate packages."""
    return Path(shot_dir) / "video_attempts"


def shot_video_file(idx: int, chapter: int | None = None) -> Path:
    """单镜视频产物（视频 provider 统一输出）: shots/<idx>/video.mp4."""
    return shot_work_dir(idx, chapter) / SHOT_VIDEO_FILENAME


def first_frame_file(idx: int, chapter: int | None = None) -> Path:
    """单镜首帧图: shots/<idx>/first_frame.png."""
    return shot_work_dir(idx, chapter) / FIRST_FRAME_FILENAME


def last_frame_file(idx: int, chapter: int | None = None) -> Path:
    """单镜尾帧图: shots/<idx>/last_frame.png."""
    return shot_work_dir(idx, chapter) / LAST_FRAME_FILENAME


def keyframe_file(idx: int, kf_index: int, chapter: int | None = None) -> Path:
    """单镜中间关键帧图: shots/<idx>/keyframe_<kf_index>.png."""
    return shot_work_dir(idx, chapter) / KEYFRAME_FILENAME_FMT.format(kf_index)


def is_core_keyframe_file(path) -> bool:
    """是否为核心关键帧文件（`keyframe_<N>.png`，v6.110）.

    **必须排除二修标注中间产物** `keyframe_<N>_doodle.png`：那是区域编辑
    （`vimax_bridge._doodle_refine_first_frame`）画了红框的**输入**标注图，
    编辑结果会覆盖原文件，doodle 文件本身不是产物。把它当成关键帧有两个后果：

      1. **掩盖缺图**——实测 shot 3 缺 `keyframe_0.png`，却因为多了一张
         `keyframe_1_doodle.png` 让 `len(kfs) >= expected` 成立，
         step02a 验收报 `PASS / missing 0`（假的绿）；
      2. **下游把标注图当帧发**——step02b 的 `frame_paths` 会带上它，
         视频模型看到的是带红框的图。
    """
    import re as _re
    m = _re.match(r"^keyframe_(\d+)\.png$", getattr(path, "name", "") or "")
    return bool(m)


def core_keyframe_files(idx: int, chapter: int | None = None) -> list[Path]:
    """单镜**核心**关键帧文件，按**数字序号**排序（`keyframe_10` 不再排在
    `keyframe_2` 前）。"""
    out = [p for p in shot_work_dir(idx, chapter).glob("keyframe_*.png")
           if is_core_keyframe_file(p)]
    return sorted(out, key=lambda p: int(p.stem.split("_")[1]))


def keyframe_files(idx: int, chapter: int | None = None) -> list[Path]:
    """单镜全部已生成关键帧（排除二修标注中间产物），按文件名字典序。

    排序口径保持原样（`keyframe_10` 仍在 `keyframe_2` 前）——顺序迁移见
    `docs/step01-and-awesome-seedance-master-plan-2026-09-23.md` §9.4「已知取舍」，
    需要数字序的调用点请用 `core_keyframe_files()`。
    """
    return sorted(p for p in shot_work_dir(idx, chapter).glob("keyframe_*.png")
                  if is_core_keyframe_file(p))


def clip_file(idx: int, chapter: int | None = None) -> Path:
    """单镜成片片段: clips/clip_<idx>.mp4."""
    return clips_dir(chapter) / CLIP_FILENAME_FMT.format(idx)


def audio_file(idx: int, chapter: int | None = None) -> Path:
    """单镜 TTS 音频: audio/shot_<idx>.mp3."""
    return audio_dir(chapter) / SHOT_AUDIO_FILENAME_FMT.format(idx)


def narration_audio_file(index: int, chapter: int | None = None) -> Path:
    """图外旁白 TTS 音轨: audio/narration_<NN>.mp3（N 编号，与计划一致）."""
    return audio_dir(chapter) / NARRATION_AUDIO_FILENAME_FMT.format(int(index))


def final_file(chapter: int | None = None) -> Path:
    """最终成片: chapter/final.mp4."""
    return chapter_dir(chapter) / "final.mp4"


def subtitles_file(chapter: int | None = None) -> Path:
    """字幕文件: chapter/subtitles.srt."""
    return chapter_dir(chapter) / "subtitles.srt"


# ---- 增强实验根与执行日志单源（2026-10-03 计划 §F1/§F4/§15） ----------------
#
# 约定：实验产物一律落在**本实验自己的**子树 <chapter>/_enhancement/<experiment_id>/
# 下，与正式产物（script/final/srt/clips/audio/vimax_working/stretched）物理分离；
# 候选路径由 shared.enhancement_io 的守卫核验（绝对、同卷、非输入/正式路径、无 .. 逃逸）。
# 正式产物布局（上面的 chapter_dir/final_file/... 一族）保持原单源不在此改动。

ENHANCEMENT_DIRNAME = "_enhancement"

#: F2 约定的包 root（每包各自 attempts/<attempt_id>/）。
ENHANCEMENT_PACKAGES = ("video", "tts", "merge", "motion", "inspect", "alignment", "integration")

_EXPERIMENT_ID_ALLOWED = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-")


def validate_experiment_id(experiment_id: str) -> str:
    """实验 id 字符校验（计划 §F1）：``[A-Za-z0-9][A-Za-z0-9_-]{0,63}``。"""
    eid = str(experiment_id or "")
    if not (1 <= len(eid) <= 64):
        raise ValueError(f"experiment_id 长度非法（1..64）: {experiment_id!r}")
    if eid[0] not in _EXPERIMENT_ID_ALLOWED or eid[0] in "-_":
        raise ValueError(f"experiment_id 首字符必须是字母或数字: {experiment_id!r}")
    if any(ch not in _EXPERIMENT_ID_ALLOWED for ch in eid):
        raise ValueError(f"experiment_id 含非法字符（仅 [A-Za-z0-9_-]）: {experiment_id!r}")
    return eid


#: 增强交付与阶段快照的根：``<OUTPUT_BASE>/_enhancement/<名字>``。
#: ⚠️ 这两处此前是**测试与脚本各自手拼**的（`REPO/"output"/"video"/"_enhancement"/…`），
#: 而 CLAUDE.md 的「编码与查阅入口」写明"输出路径由 `shared/paths.py` 计算，不自行拼目录
#: 层级"。放在这里是为了让"交付留档放哪""阶段快照放哪"只有一处定义。
ENHANCEMENT_DELIVERY_DIRNAME = "_delivery"
ENHANCEMENT_BASELINE_DIRNAME = "_baseline"


def default_output_base() -> Path:
    """**不读 env** 的默认输出根：``<项目根>/output/video``.

    用于"核对正式产物"这类**必须锚在仓库上**的场景——`OUTPUT_BASE` 是测试隔离手段，
    隔离之后那里没有正式产物，拿隔离根去核"产物有没有被改动"只会一路跳过。
    """
    return _PROJECT_ROOT / "output" / "video"


def enhancement_root(base: Path | None = None) -> Path:
    """``<base>/_enhancement``（交付留档与阶段快照共同的父目录）.

    `base=None` ⇒ 生效的 `OUTPUT_BASE`；要锚在仓库上就传 `default_output_base()`。
    """
    return (Path(base) if base is not None else _output_base()) / ENHANCEMENT_DIRNAME


def enhancement_delivery_root(base: Path | None = None) -> Path:
    """交付留档根：``<base>/_enhancement/_delivery``（每次实跑的 `pre/` `post/` 放这）."""
    return enhancement_root(base) / ENHANCEMENT_DELIVERY_DIRNAME


def enhancement_baseline_root(base: Path | None = None) -> Path:
    """阶段快照根：``<base>/_enhancement/_baseline``（每阶段一份，旧的一律保留）."""
    return enhancement_root(base) / ENHANCEMENT_BASELINE_DIRNAME


def enhancement_experiment_dir(experiment_id: str, chapter: int | None = None) -> Path:
    """实验根单源: ``<chapter_dir>/_enhancement/<experiment_id>``（全包唯一来源）。"""
    return chapter_dir(chapter) / ENHANCEMENT_DIRNAME / validate_experiment_id(experiment_id)


def enhancement_package_dir(package: str, experiment_id: str, chapter: int | None = None) -> Path:
    """包 root: ``<实验根>/<package>``；包名必须在 ``ENHANCEMENT_PACKAGES`` 内。"""
    if package not in ENHANCEMENT_PACKAGES:
        raise ValueError(f"未知包名 {package!r}（合法: {ENHANCEMENT_PACKAGES}）")
    return enhancement_experiment_dir(experiment_id, chapter) / package


def video_attempts_dir(experiment_id: str, chapter: int | None = None) -> Path:
    """视频包 attempts 目录：**仅委托**实验根（不得自行拼另一套路径）。"""
    return enhancement_package_dir("video", experiment_id, chapter) / "attempts"


def screen_card_asset_dir(attempt_dir: "os.PathLike | str | None" = None,
                          experiment_id: str | None = None,
                          chapter: int | None = None) -> Path:
    """文字卡资产的**路径单源**（主专题 §6.3：卡片资产路径单源）.

    - 给了 `attempt_dir`（Step04 的候选执行上下文）⇒ 落在该 attempt 的 `work/cards/`；
      资产**必须**在当前 merge attempt 内——增强执行的临时文件、图片与成片都在 attempt 内，
      跨 attempt 复用会让"这次用的资产是不是这次画的"变成查不到的事实。
    - 没给 ⇒ `<实验根>/merge/cards/`（离线预检/资产生成器用）。

    两条路径都**不碰正式产物目录**。
    """
    _root = Path(attempt_dir) if attempt_dir else \
        enhancement_package_dir("merge", experiment_id, chapter)
    return _root / "work" / "cards" if attempt_dir else _root / "cards"


def screen_card_plan_file(attempt_dir: "os.PathLike | str | None" = None,
                          experiment_id: str | None = None,
                          chapter: int | None = None) -> Path:
    """`screen_card_plan.v1` 清单的路径单源（与 `screen_card_asset_dir` 同根）."""
    _root = Path(attempt_dir) if attempt_dir else \
        enhancement_package_dir("merge", experiment_id, chapter)
    return _root / "work" / "screen_card_plan.json" if attempt_dir \
        else _root / "screen_card_plan.json"


def flat_clip_file(idx: int, explicit_root: "os.PathLike | str | None" = None,
                   chapter: int | None = None) -> Path:
    """非 vimax 通道的扁平片段: ``<VIDEO_OUTPUT_DIR 或 clips/>/clip_<idx>.mp4``.

    hook 与正文镜头同规则（``idx=0`` 就是 hook），不再走历史特殊路径。
    """
    root = Path(explicit_root) if explicit_root else clips_dir(chapter)
    return root / CLIP_FILENAME_FMT.format(int(idx))


def execution_log_file(stage: str, context=None, chapter: int | None = None) -> Path:
    """执行日志单源：有实验上下文 → attempt 日志；无上下文 → 原生产日志合同。

    ``context`` 需要提供 ``log_dir``（优先）或 ``attempt_dir``；两者都没有即视为
    调用错误（**不**静默回退到生产日志——那正是 09-26 事故的形态）。
    生产分支保持与各 main 既有 ``<chapter>/<stage>.log`` 完全一致。
    """
    if context is not None:
        log_dir = getattr(context, "log_dir", None) or getattr(context, "attempt_dir", None)
        if log_dir is None:
            raise ValueError(
                "execution_log_file: 传入的 context 既无 log_dir 也无 attempt_dir，"
                "拒绝回退到生产日志路径"
            )
        return Path(log_dir) / f"{stage}.log"
    return chapter_dir(chapter) / f"{stage}.log"


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


# ---------------------------------------------------------------------------
# 一次性审计授权（M09，2026-10-29）
# ---------------------------------------------------------------------------
# 审计授权是**费用上限**，不是普通缓存：同一份授权无论重复启动、换运行根还是并发
# 启动，累计只能进入一次外部发送。因此它的消费状态必须落在一个**与运行根无关**的
# 固定位置（不能跟着 OUTPUT_BASE / --out-root / 时间戳目录 / 审计包文件名漂移），
# 否则换一个运行根就等于凭空多出一次额度。

AUDIT_AUTHORIZATION_STATE_DIRNAME = "audit_authorizations"
#: 只在**测试**里重定向登记根（每个用例独立临时目录）。生产启动脚本不得设置它——
#: 真实 CLI 没有「换登记根」参数，避免用改名/换根绕过已消费记录。
#: 授权登记根的**唯一事实源**。生产解析不读任何环境变量：能改环境变量就能把
#: 同一授权指到另一个空登记目录，从而绕过"已消费"记录（2026-10-29 复核 P1）。
#: 测试要隔离时，在自己进程里替换本模块常量（sitecustomize / monkeypatch），
#: 而不是在运行期给生产代码留开关。
AUDIT_AUTHORIZATION_ROOT = _PROJECT_ROOT / ".p1a_exp" / "step01_delivery_20261028"

#: 授权 id 的字符集与长度：只允许 `[A-Za-z0-9_-]`，8..64 字符，首字符字母或数字。
#: 它会被拼进目录名（且另做 sha256 截断），必须先在这里挡住路径片段注入。
_AUTH_ID_ALLOWED = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-")


def validate_authorization_id(authorization_id: str) -> str:
    """严格校验一次性授权 id；不合法直接 `ValueError`（不猜、不修、不替换）."""
    aid = str(authorization_id or "")
    if not (8 <= len(aid) <= 64):
        raise ValueError(f"authorization_id 长度非法（8..64）: {authorization_id!r}")
    if aid[0] not in _AUTH_ID_ALLOWED or aid[0] in "-_":
        raise ValueError(f"authorization_id 首字符必须是字母或数字: {authorization_id!r}")
    if any(ch not in _AUTH_ID_ALLOWED for ch in aid) or aid in (".", ".."):
        raise ValueError(f"authorization_id 含非法字符（仅 [A-Za-z0-9_-]）: {authorization_id!r}")
    return aid


def step01_audit_authorization_dir(authorization_id: str) -> Path:
    """一份一次性审计授权的**固定**状态目录（同一 id 在任何运行根下都解析到这里）.

    目录键只由 `authorization_id` 决定（sha256 截断，避免长名/奇怪字符），**不掺**
    候选摘要、规则版本、通道或运行根：这些绑定的东西变了但 id 不变时，必须落到同一
    个目录并被拒绝，而不是悄悄生成第二个「还可以发一次」的目录。绑定比对在 claim 里做。
    """
    aid = validate_authorization_id(authorization_id)
    # **不读环境变量**（P1）：登记根只由模块常量决定。任何"换个目录再发一次"的
    # 运行期开关都会让已消费记录失效，因此只在测试进程内替换常量。
    base = Path(AUDIT_AUTHORIZATION_ROOT) / AUDIT_AUTHORIZATION_STATE_DIRNAME
    key = "auth_" + hashlib.sha256(aid.encode("utf-8")).hexdigest()[:32]
    return base / key
