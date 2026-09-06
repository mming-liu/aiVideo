"""Deprecated 兼容 shim — style_prefix 已迁至 shared/style_prefix.py.

历史上本文件在仓库根目录（87KB），是唯一不在 shared/ 的公共模块。
v6.60.1（2026-09-06）迁移到 shared/ 统一管理；本 shim 保留观察期，
消费方应逐步改为 `from shared.style_prefix import ...`，观察期后删除。
"""
import sys
import warnings

warnings.warn(
    "顶层 style_prefix 已迁至 shared.style_prefix，请改用新路径导入",
    DeprecationWarning,
    stacklevel=2,
)

from shared.style_prefix import *  # noqa: F401,F403
from shared import style_prefix as _impl  # noqa: E402

# 转发模块级私有名（_WB_POSE_TO_UNDERWATER 等——import * 不覆盖下划线名，
# 消费方存在 `from style_prefix import ORIENTATION_KW` 等公开名之外的使用）
_sys_modules_self = sys.modules[__name__]
for _k, _v in vars(_impl).items():
    if _k.startswith("_") and not _k.startswith("__"):
        setattr(_sys_modules_self, _k, _v)
