"""视频生成（ViMax 后端） — 业务入口.

两阶段流水线:
  1. 批量提交：每个镜头间隔 VIDEO_CLIP_INTERVAL 秒提交任务（不管前一个是否完成）
  2. 并发轮询：全部提交后并发轮询所有结果（asyncio.gather）

经 ViMax 适配器调用 aiVideo 的 DashScope/Agnes 视频生成 provider，
产出存入 ViMax working_dir 布局（output/vimax_working/shots/<idx>/video.mp4）.
"""

import asyncio
import json
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()
_project_root = Path(__file__).resolve().parent

from vimax_adapters import ImageGeneratorAiVideoAdapter, VideoGeneratorAiVideoAdapter
from vimax_bridge import build_working_dir

SCRIPT_FILE = _project_root / "script_result.json"
WORKING_DIR = Path(os.getenv("VIMAX_WORKING_DIR", "./output/vimax_working"))
SUBMIT_INTERVAL = float(os.getenv("VIDEO_CLIP_INTERVAL", "65"))
MAX_RETRIES = 3  # 单个镜头失败后最大重试次数


def load_script(path: Path) -> dict:
    if not path.exists():
        raise FileNotFoundError(f"分镜脚本不存在: {path}\n请先运行 01_script_gen.py")
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _build_prompt(shot: dict, script: dict) -> str:
    """构建单镜头 prompt，含上下文与人物设定."""
    motion_desc = shot.get("prompt", "")
    audio_desc = shot.get("subtitle", "").strip()
    prompt = f"{motion_desc}\n{audio_desc}" if audio_desc else motion_desc

    extras = []
    cp = script.get("character_profile", "").strip()
    if cp:
        extras.append(f"全局人物统一外貌设定：{cp}")
    cb = shot.get("context_bridge", "").strip()
    if cb:
        extras.append(f"叙事衔接（保持与上一镜视觉连贯）：{cb}")
    if extras:
        prompt = f"{prompt}\n\n{'；'.join(extras)}。请确保本镜头与上述设定/衔接描述一致。"
    return prompt


async def generate_all(script: dict, working_dir: Path, force_first_frame: bool = False) -> int:
    """两阶段批量生成 + 失败重试（每镜最多 MAX_RETRIES 次）."""
    image_adapter = ImageGeneratorAiVideoAdapter()
    video_adapter = VideoGeneratorAiVideoAdapter()

    # 1. 翻译 + 预生成首帧（Shot 1 锚点 → Shot 2-6 统一引用）
    await build_working_dir(SCRIPT_FILE, working_dir, image_adapter, force_first_frame=force_first_frame)

    shots = script.get("shots", [])
    shots_dir = working_dir / "shots"
    script_mtime = SCRIPT_FILE.stat().st_mtime if SCRIPT_FILE.exists() else None

    print(f"\n[INFO] 开始两阶段批量生成（{len(shots)} 镜头，每镜最多 {MAX_RETRIES} 次重试）...\n")

    # ------------------------------------------------------------------
    # 阶段一：批量提交（间隔只控制提交之间，不等视频生成完成）
    # ------------------------------------------------------------------
    pending: list[dict] = []  # [{idx, shot_dir, video_path, req, task_id, attempts}]
    succeeded = 0
    failed = 0

    for i, shot in enumerate(shots, 1):
        idx = shot["index"]
        shot_dir = shots_dir / str(idx)
        video_path = shot_dir / "video.mp4"
        print(f"{'─'*50}")
        print(f"[提交 {i}/{len(shots)}] 镜头{idx} 字幕: {shot.get('subtitle', 'N/A')}")

        # 缓存跳过 + 陈旧检测
        if video_path.exists() and video_path.stat().st_size > 20 * 1024:
            clip_mtime = video_path.stat().st_mtime
            if script_mtime is not None and clip_mtime >= script_mtime:
                print(f"  [OK] 跳过已生成（{video_path.stat().st_size//1024}KB）")
                succeeded += 1
                continue
            else:
                print(f"  [!] prompt 已更新，重新生成")

        prompt = _build_prompt(shot, script)
        first_frame = str(shot_dir / "first_frame.png")

        try:
            req = await _make_request_async(
                video_adapter, prompt, [first_frame] if Path(first_frame).exists() else [], idx,
            )
            task_id = await video_adapter.submit_task(req)
            pending.append({
                "idx": idx,
                "shot_dir": shot_dir,
                "video_path": video_path,
                "req": req,
                "task_id": task_id,
                "attempts": 1,
            })
            print(f"  [已提交] task_id={task_id[:40]}...")
        except Exception as e:
            print(f"  [FAIL] 提交失败: {e}")
            failed += 1

        if i < len(shots):
            print(f"  [间隔] {SUBMIT_INTERVAL:.0f}s 后提交下一个镜头...")
            await asyncio.sleep(SUBMIT_INTERVAL)

    print(f"\n[INFO] 阶段一完成：{len(pending)} 个任务已提交，开始轮询...\n")

    # ------------------------------------------------------------------
    # 阶段二：并发轮询 + 失败重试循环
    # ------------------------------------------------------------------
    while pending:
        # 并发轮询当前这批
        async def _fetch_one(item: dict) -> tuple[dict, int]:
            """返回 (item, result_code): 0=成功, 1=失败需重试, 2=最终失败."""
            idx = item["idx"]
            video_path = item["video_path"]
            print(f"[轮询] 镜头{idx} (第{item['attempts']}次) 等待完成...")
            try:
                result = await video_adapter.fetch_result(item["req"], item["task_id"])
                if result.success and result.save_path:
                    import shutil
                    shutil.move(result.save_path, str(video_path))
                    print(f"  [OK] 镜头{idx} 生成完毕（{video_path.stat().st_size//1024}KB）")
                    return item, 0
                else:
                    print(f"  [FAIL] 镜头{idx} 生成失败: {result.error}")
                    return item, 1
            except Exception as e:
                print(f"  [FAIL] 镜头{idx} 轮询异常: {e}")
                return item, 1

        results = await asyncio.gather(*[_fetch_one(item) for item in pending])
        pending = []

        for item, code in results:
            if code == 0:
                succeeded += 1
            elif item["attempts"] < MAX_RETRIES:
                # 重试：重新提交
                item["attempts"] += 1
                idx = item["idx"]
                print(f"  [重试] 镜头{idx} 第{item['attempts']}/{MAX_RETRIES} 次重试...")
                try:
                    new_task_id = await video_adapter.submit_task(item["req"])
                    item["task_id"] = new_task_id
                    print(f"  [已重提] 镜头{idx} task_id={new_task_id[:40]}...")
                    # 重提交后等待间隔再轮询
                    await asyncio.sleep(SUBMIT_INTERVAL)
                    pending.append(item)
                except Exception as e:
                    print(f"  [FAIL] 镜头{idx} 重提交也失败: {e}")
                    failed += 1
            else:
                idx = item["idx"]
                print(f"  [放弃] 镜头{idx} 已达最大重试次数 {MAX_RETRIES}，跳过")
                failed += 1

        if pending:
            print(f"\n[INFO] {len(pending)} 个镜头等待重试轮询...\n")

    print(f"\n{'='*50}")
    print(f"视频片段生成完毕：总计 {len(shots)} | 成功 {succeeded} | 失败 {failed}")
    print(f"{'='*50}")
    return failed


async def _make_request_async(video_adapter, prompt: str, frame_paths: list, clip_index: int):
    """构建 VideoGenerationRequest（纯 CPU，直接同步构建）."""
    import _video_gen
    from _video_gen import VideoGenerationRequest
    from style_prefix import STYLE_PREFIX, NEGATIVE_PROMPT, ensure_prefix, sanitize_prompt

    full_prompt = sanitize_prompt(ensure_prefix(prompt, STYLE_PREFIX))
    # 优先 first_frame，其次任意可用参考图
    image_path = None
    for fp in (frame_paths or []):
        if fp and Path(fp).exists():
            image_path = fp
            break

    # 回退：从 character_portraits_registry 找角色正面肖像
    if not image_path:
        image_path = _find_fallback_reference(Path(os.getenv("VIMAX_WORKING_DIR", "./output/vimax_working")))

    return VideoGenerationRequest(
        prompt=full_prompt,
        clip_index=clip_index,
        duration=5,
        negative_prompt=NEGATIVE_PROMPT,
        size="960x1792",
        image_path=image_path,
    )


def _find_fallback_reference(working_dir: Path) -> str | None:
    """first_frame 不存在时，从 character_portraits_registry.json 找可用参考图."""
    registry_path = working_dir / "character_portraits_registry.json"
    if not registry_path.exists():
        return None
    try:
        with open(registry_path, "r", encoding="utf-8") as f:
            registry = json.load(f)
        for ident, views in registry.items():
            front = views.get("front", {}).get("path", "")
            if front and Path(front).exists():
                print(f"  [fallback] 使用角色 {ident} 正面肖像作为视频参考图")
                return front
    except Exception:
        pass
    return None


# ---------------------------------------------------------------------------
if __name__ == "__main__":
    print("=" * 60)
    print("  ViMax 后端视频生成任务启动（两阶段批量）")
    print("=" * 60)

    script = load_script(SCRIPT_FILE)
    shots = script.get("shots", [])
    narration = script.get("narration", "")
    print(f"\n分镜脚本: {len(shots)} 个镜头")
    print(f"旁白({len(narration)}字): {narration[:60]}...\n")

    auto_confirm = os.getenv("VIDEO_AUTO_CONFIRM", "").strip() == "1"
    print(f"[提示] 每镜提交间隔 {SUBMIT_INTERVAL}s，全部提交后并发轮询")
    if auto_confirm:
        answer = "y"
        print("确认开始生成？(y/N): y（VIDEO_AUTO_CONFIRM=1 自动确认）")
    else:
        answer = input("确认开始生成？(y/N): ").strip().lower()
    if answer not in ("y", "yes"):
        print("已取消。")
        sys.exit(0)

    force_ff = os.getenv("SCRIPT_FIRST_FRAME_FORCE", "0").strip() == "1"
    failed = asyncio.run(generate_all(script, WORKING_DIR, force_first_frame=force_ff))
    if failed:
        sys.exit(1)
