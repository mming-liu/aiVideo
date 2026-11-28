"""测试辅助：以 ``__main__`` 身份执行阶段入口的"顶部"，验证运行范围 bootstrap 生效位置。

用法::

    python tests/shared/_stage_head_probe.py step01_script_gen/main.py --chapter 2

做法：读入口源码，**砍掉文件末尾的 ``if __name__ == "__main__": ... main()`` 调用块**
后 ``exec`` 执行，于是模块顶部的 ``if __name__ == "__main__": bootstrap(...)`` 会真的跑，
而 ``main()`` 不会被调用——**不会真的跑流水线、不产生任何模型调用**。

用途：证明「bootstrap 抢在第一个 config/paths 读取之前生效」这件事真的成立。
纯源码扫描测不出"位置对但没生效"，这个探针可以。执行完输出一行 JSON。

注意：只用于测试，不参与产线；文件名以下划线开头，pytest 不会收集它。
"""
import json
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

_CALL_BLOCK = '\nif __name__ == "__main__":'
_WANTED = ("NOVEL_CHAPTER", "CHAPTER_INDEX", "BOOK_INDEX", "BOOK_NAME",
           "CHAPTER_DIR", "OUTPUT_FILE", "SCRIPT_FILE", "AUDIO_OUTPUT_DIR",
           "FINAL_OUT", "SRT_OUT", "TEMP_DIR", "VIMAX_WD", "CLIP_DIR")


def main() -> int:
    if len(sys.argv) < 2:
        print(json.dumps({"error": "usage: _stage_head_probe.py <entry.py> [scope args...]"}))
        return 2
    entry = Path(sys.argv[1]).resolve()
    source = entry.read_text(encoding="utf-8")
    try:
        cut = source.rindex(_CALL_BLOCK)
    except ValueError:
        print(json.dumps({"error": f"{entry} 末尾没有 __main__ 调用块"}))
        return 2
    if "main()" not in source[cut:]:
        print(json.dumps({"error": f"{entry} 最后一段 __main__ 块里没有 main() 调用"}))
        return 2

    namespace = {"__name__": "__main__", "__file__": str(entry)}
    sys.argv = [str(entry)] + sys.argv[2:]
    exec(compile(source[:cut], str(entry), "exec"), namespace)  # noqa: S102 - 测试探针

    out = {"entry": entry.name}
    # 2026-11-19：step01 的模块级常量拆到 orchestrator/runtime.py（在 main.py 头部
    # bootstrap 之后导入）。入口命名空间里没有时，从 runtime 取同一份导入期求值结果。
    runtime = sys.modules.get("step01_script_gen.orchestrator.runtime")
    for key in _WANTED:
        if key in namespace:
            value = namespace[key]
        elif runtime is not None and hasattr(runtime, key):
            value = getattr(runtime, key)
        else:
            continue
        out[key] = value.name if isinstance(value, Path) else value
    print(json.dumps(out, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
