# 403 自动回退机制

## 概述

DashScope API 在模型额度耗尽或无权限时返回 HTTP 403。本项目在视频生成和 TTS 语音合成两个环节
均实现了 403 自动回退：当前模型不可用时，自动切换到下一个可用模型，无需人工干预。

> **⚠ 当前所有模型均为调试用临时模型，仅供功能验证。后续会替换为正式模型。**

## 回退机制架构

```
调用 API(model=当前模型)
    │
    ├── 200 → 成功 ✓
    │
    ├── 403 → 还有回退模型？
    │       ├── 是 → 打印日志，切换到下一模型，重试
    │       └── 否 → 报错（所有模型均不可用）
    │
    └── 其他错误 → 直接报错（不重试）
```

## 一、视频生成回退

### 可用模型（2026-06-28 统计，调试用）

| 优先级 | 模型 | 剩余额度 | 说明 |
|--------|------|---------|------|
| 1 | `wan2.7-t2v` | 50/50 | 主模型，额度最充足 |
| 2 | `wan2.7-t2v-2026-04-25` | 50/50 | 同版本固定快照 |
| 3 | `happyhorse-1.1-t2v` | 10/10 | 快马 1.1 文生视频 |
| 4 | `happyhorse-1.0-t2v` | 5/10 | 快马 1.0 文生视频 |

### 配置（.env）

```bash
# 主模型
DASHSCOPE_VIDEO_MODEL=wan2.7-t2v
# 回退模型列表（逗号分隔）
DASHSCOPE_VIDEO_FALLBACK_MODELS=wan2.7-t2v-2026-04-25,happyhorse-1.1-t2v,happyhorse-1.0-t2v
```

### 涉及文件

| 文件 | 说明 |
|------|------|
| `_video_gen/config.py` | 解析 `DASHSCOPE_VIDEO_FALLBACK_MODELS` 为 `list[str]` |
| `_video_gen/dashscope_provider.py` | `create_task()` 中检测 403 并自动切换 |

### 关键实现

```python
# __init__: 构建模型队列 [主模型, 回退1, 回退2, ...]
self._model_queue = [config.DASHSCOPE_VIDEO_MODEL] + config.DASHSCOPE_VIDEO_FALLBACK_MODELS
self._current_model_index = 0

# create_task: while 循环 + 403 检测
if rsp.status_code == 403:
    next_model = self._advance_model()
    if next_model:
        continue  # 用新模型重试
    else:
        raise RuntimeError(f"所有模型均返回 403，已无可用回退模型。")
```

### 运行日志示例

```
# 正常
[镜头1] 任务已创建 [wan2.7-t2v] -> task_id: abc-123

# 403 回退
[镜头1] ⚠ 模型 wan2.7-t2v 返回 403（额度耗尽/无权限），自动切换到 wan2.7-t2v-2026-04-25
[镜头1] 任务已创建 [wan2.7-t2v-2026-04-25] -> task_id: def-456

# 全部耗尽
RuntimeError: 所有模型均返回 403，已无可用回退模型。尝试过: wan2.7-t2v, ...
```

---

## 二、TTS 语音合成回退

### 可用模型（2026-06-28 统计，调试用）

| 优先级 | 模型 | 剩余额度 | 说明 |
|--------|------|---------|------|
| 1 | `sambert-zhide-v1` | 29,096/30,000 | 主模型，低沉男声 |
| 2 | `sambert-zhida-v1` | 30,000/30,000 | 温柔女声 |
| 3 | `sambert-zhihao-v1` | 30,000/30,000 | 标准男声 |
| 4 | `sambert-zhimo-v1` | 30,000/30,000 | 沉稳女声 |
| 5 | `sambert-zhiyuan-v1` | 30,000/30,000 | 磁性男声 |

其他可用 TTS 模型（未在默认回退链中，可手动添加到 `.env`）：

| 模型 | 剩余额度 | 说明 |
|------|---------|------|
| `sambert-zhishu-v1` | 30,000/30,000 | 少年声 |
| `sambert-zhiyue-v1` | 30,000/30,000 | 少女声 |
| `sambert-zhiye-v1` | 30,000/30,000 | 知性女声 |
| `sambert-zhiya-v1` | 30,000/30,000 | 甜美女声 |
| `sambert-zhiying-v1` | 30,000/30,000 | 英气女声 |
| `sambert-zhichu-v1` | 30,000/30,000 | 儒雅男声 |
| `sambert-zhiming-v1` | 30,000/30,000 | 明亮男声 |
| `sambert-zhijing-v1` | 30,000/30,000 | 安静女声 |
| `sambert-zhixiang-v1` | 30,000/30,000 | 温柔男声 |
| `sambert-zhiting-v1` | 30,000/30,000 | 活泼女声 |
| `sambert-zhijia-v1` | 30,000/30,000 | 温暖女声 |
| `sambert-zhifei-v1` | 30,000/30,000 | 清亮男声 |
| `sambert-zhiqi-v1` | 30,000/30,000 | 气质女声 |
| `sambert-zhinan-v1` | 30,000/30,000 | 沉稳男声 |
| `sambert-zhixiao-v1` | 30,000/30,000 | 活泼女声 |
| `sambert-zhimo-v1` | 30,000/30,000 | 知性男声 |
| `sambert-zhigui-v1` | 30,000/30,000 | 贵气女声 |
| `sambert-zhina-v1` | 30,000/30,000 | 邻家女声 |
| `sambert-zhimao-v1` | 30,000/30,000 | 萌系女声 |
| `sambert-zhilun-v1` | 30,000/30,000 | 浑厚男声 |
| `sambert-zhishuo-v1` | 30,000/30,000 | 磁性男声 |
| `sambert-zhiqian-v1` | 30,000/30,000 | 清新女声 |
| `sambert-zhiru-v1` | 30,000/30,000 | 柔和女声 |
| `sambert-zhiwei-v1` | 30,000/30,000 | 温柔女声 |
| `sambert-zhizhi-v1` | 30,000/30,000 | 知性女声 |
| `sambert-eva-v1` | 30,000/30,000 | 英文女声 |
| `sambert-beth-v1` | 30,000/30,000 | 英文女声 |
| `sambert-brian-v1` | 30,000/30,000 | 英文男声 |
| `sambert-betty-v1` | 30,000/30,000 | 英文女声 |
| `sambert-donna-v1` | 30,000/30,000 | 英文女声 |
| `sambert-hanna-v1` | 30,000/30,000 | 英文女声 |
| `sambert-cindy-v1` | 30,000/30,000 | 英文女声 |
| `sambert-cally-v1` | 30,000/30,000 | 英文女声 |
| `sambert-clara-v1` | 30,000/30,000 | 英文女声 |
| `sambert-indah-v1` | 30,000/30,000 | 印尼女声 |
| `sambert-waan-v1` | 30,000/30,000 | 泰语女声 |
| `sambert-camila-v1` | 30,000/30,000 | 西语女声 |
| `sambert-zhimiao-emo-v1` | 30,000/30,000 | 情感女声 |
| `sambert-zhistella-v1` | 30,000/30,000 | 甜美女声 |
| `sambert-perla-v1` | 30,000/30,000 | 英文女声 |
| `cosyvoice-v1` | 10,000/10,000 | CosyVoice 多语言 |
| `cosyvoice-clone-v1` | 10,000/10,000 | CosyVoice 声音克隆 |

### 配置（.env）

```bash
# 主模型
DASHSCOPE_TTS_MODEL=sambert-zhide-v1
# 回退模型列表（逗号分隔）
DASHSCOPE_TTS_FALLBACK_MODELS=sambert-zhida-v1,sambert-zhihao-v1,sambert-zhimo-v1,sambert-zhiyuan-v1
```

### 涉及文件

| 文件 | 说明 |
|------|------|
| `tts/config.py` | 解析 `DASHSCOPE_TTS_FALLBACK_MODELS` 为 `list[str]` |
| `tts/dashscope_provider.py` | SDK 和 HTTP 两个 Provider 均实现 403 检测与自动切换 |

### 关键实现

```python
# _build_model_queue: 构建 [主模型, 回退1, 回退2, ...]
def _build_model_queue(req_model: str) -> list[str]:
    base = req_model or config.MODEL
    queue = [base]
    for m in config.FALLBACK_MODELS:
        if m != base:
            queue.append(m)
    return queue

# synthesize: for 循环遍历模型队列
for idx, model in enumerate(model_queue):
    response = SpeechSynthesizer.call(model=model, ...)
    status = resp_dict.get("status_code", -1)

    if status == 200:        # 成功
        ...
    elif status == 403:      # 额度耗尽，尝试下一个
        continue
    else:                    # 其他错误，不重试
        break
```

### 运行日志示例

```
# 正常
[INFO] 模型: sambert-zhide-v1 | 语速: 0.92 | 音量: 50
[OK] 配音生成完成: ./output/audio/narration.mp3 (45.2 KB)

# 403 回退
⚠ TTS 模型 sambert-zhide-v1 返回 403（额度耗尽/无权限），自动切换到 sambert-zhida-v1
[INFO] TTS 已回退到模型: sambert-zhida-v1
[OK] 配音生成完成: ./output/audio/narration.mp3 (45.2 KB)
```

---

## 三、通用说明

### 视频 vs TTS 回退的区别

| 特性 | 视频生成 | TTS 合成 |
|------|---------|---------|
| 回退时机 | `create_task()` 创建阶段 | `synthesize()` 合成阶段 |
| 状态保持 | 实例级别（同一批次内模型切换持久化） | 单次调用级别（每次合成独立遍历） |
| 错误处理 | 403 → `continue` 重试，其他 → `raise` | 403 → `continue` 重试，其他 → `break` |

### 扩展模型

只需修改 `.env` 中的回退列表，无需改代码：

```bash
# 视频：添加新模型到回退链末尾
DASHSCOPE_VIDEO_FALLBACK_MODELS=wan2.7-t2v-2026-04-25,happyhorse-1.1-t2v,happyhorse-1.0-t2v,新模型名

# TTS：添加新模型到回退链末尾
DASHSCOPE_TTS_FALLBACK_MODELS=sambert-zhida-v1,sambert-zhihao-v1,...,新模型名
```

### 注意事项

- 不同模型的 `size`/`duration` 参数可能存在差异，切换后若出现参数不兼容，会以非 403 错误呈现
- 视频生成的回退是实例级别的状态——同一批次中，一旦切换到模型 B，后续镜头也会用模型 B
- TTS 的回退是调用级别的——每次合成都会从主模型开始遍历
- 所有模型均为调试用临时模型，额度用完即停，后续会替换为正式付费模型
