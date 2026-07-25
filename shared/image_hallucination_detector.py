"""图片幻觉检测服务 — 使用 Agnes 2.0 Flash 图像理解能力.

三层检测机制：
1. 人脸一致性检测（需要参考图）
2. 物体清单校验（检测是否出现清单外物品）
3. 肢体畸形检测（六指、扭曲等）

调用方式：
    from shared.image_hallucination_detector import ImageHallucinationDetector

    detector = ImageHallucinationDetector()
    result = detector.detect(
        image_url="https://...",
        expected_objects=["剑", "桌子", "椅子"],  # 可选
        reference_image_url="https://...",  # 可选，人脸比对用
    )
    if result.has_hallucination:
        print(f"检测到幻觉: {result.issues}")
"""

import json
import time
from dataclasses import dataclass
from typing import List, Optional

import requests

from _text_gen import config as text_config


@dataclass
class HallucinationIssue:
    """检测到的问题."""

    category: str  # "face_consistency", "unexpected_object", "肢体畸形"
    description: str  # 问题描述
    severity: str  # "critical", "warning"


@dataclass
class HallucinationDetectionResult:
    """检测结果."""

    has_hallucination: bool
    issues: List[HallucinationIssue]
    raw_response: str  # 原始 LLM 响应（用于调试）


# 检测用的系统提示词
_IMAGE_ANALYSIS_SYSTEM_PROMPT = """你是一个专业的图片质量检测专家，专门检测 AI 生成的图片中是否存在视觉幻觉问题。

请严格按照以下 JSON 格式返回检测结果，不要包含任何其他内容：

```json
{
  "has_hallucination": true/false,
  "issues": [
    {
      "category": "face_consistency/unexpected_object/body_deformity/extra_object/text_watermark",
      "description": "具体问题描述",
      "severity": "critical/warning"
    }
  ]
}
```

检测项目：

1. **人脸一致性 (face_consistency)**：
   - 画面中人物的五官、脸型是否正常
   - 是否有换脸、五官扭曲、脸型明显改变的情况
   - 是否有面部模糊不清的情况

2. **肢体畸形 (body_deformity)**：
   - 是否有手指数量异常（多指、少指、六指等）
   - 是否有四肢扭曲、关节错位
   - 是否有身体比例严重失调

3. **多余物体/物理逻辑错误 (unexpected_object)**：
   - 画面中是否出现了剧本/提示词中没有提到的物体
   - 是否有凭空出现的家具、摆件、花草
   - 是否有漂浮的物体
   - 是否有半透明消散/漂浮效果（如人在水中半透明）
   - 是否有不自然的光束/光环/发光效果
   - 是否有人物肢体与物体融合、穿透等违反物理逻辑的情况

4. **文字水印 (text_watermark)**：
   - 是否有乱码文字
   - 是否有水印、logo、涂鸦

5. **场景一致性 (scene_consistency)**：
   - 场景布局是否合理
   - 是否有空间结构错乱

判断标准：
- 发现任何 critical 级别的问题 → has_hallucination = true
- 发现 2 个以上 warning 级别的问题 → has_hallucination = true
- unexpected_object 中出现物理逻辑错误（漂浮/半透明/光束/融合/穿透）→ 自动提升为 critical
- 一切正常 → has_hallucination = false
"""


# 物理逻辑错误关键词（检测到这些时自动提升为 critical）
_PHYSICS_VIOLATION_KEYWORDS = [
    "漂浮", "半透明", "消散", "透明", "发光光束", "光束", "光环", "融合",
    "穿透", "肢体融合", "身体融合", "人船融合", "人与物融合",
    "半透明漂浮", "不自然的发光", "违反物理", "不符合物理逻辑"
]


class ImageHallucinationDetector:
    """图片幻觉检测器.

    使用 Agnes 2.0 Flash 的图像理解能力进行检测。
    """

    _TIMEOUT = 60  # 请求超时（秒）
    _MAX_RETRIES = 3  # 最大重试次数

    def __init__(self, provider: str = "agnes"):
        self._provider = provider
        self._setup_provider()

    def _setup_provider(self):
        """初始化 provider."""
        if self._provider == "agnes":
            # 复用 text_config 中的 Agnes 配置
            text_config.validate()
            self._api_keys = [text_config.AGNES_API_KEY]
            if text_config.AGNES_API_KEY_2:
                self._api_keys.append(text_config.AGNES_API_KEY_2)
            # 取第一个可用 key
            self._api_key = self._api_keys[0]
            self._base_url = text_config.AGNES_BASE_URL
            self._model = text_config.AGNES_TEXT_MODEL

    def _build_content(
        self,
        image_url: str,
        reference_image_url: Optional[str] = None,
        expected_objects: Optional[List[str]] = None,
    ) -> List[dict]:
        """构建消息内容."""
        # 分析指令
        instruction = "请分析这张图片中是否存在视觉幻觉问题。"
        if expected_objects:
            instruction += f"\n\n画面中应该出现的物品（按提示词要求）：{', '.join(expected_objects)}"
        if reference_image_url:
            instruction += "\n\n【重要】以下链接是参考图片（人物的标准形象），请对比生成图与参考图中人物的长相是否一致："
            instruction += f"\n{reference_image_url}"

        content = [
            {"type": "text", "text": instruction},
            {"type": "image_url", "image_url": {"url": image_url}},
        ]
        return content

    def detect(
        self,
        image_url: str,
        expected_objects: Optional[List[str]] = None,
        reference_image_url: Optional[str] = None,
    ) -> HallucinationDetectionResult:
        """检测图片幻觉.

        Args:
            image_url: 待检测的图片 URL（需公开可访问）
            expected_objects: 期望出现在画面中的物体列表（可选）
            reference_image_url: 参考图 URL，用于人脸一致性比对（可选）

        Returns:
            HallucinationDetectionResult: 检测结果
        """
        url = f"{self._base_url.rstrip('/')}/v1/chat/completions"
        content = self._build_content(image_url, reference_image_url, expected_objects)

        payload = {
            "model": self._model,
            "messages": [
                {"role": "system", "content": _IMAGE_ANALYSIS_SYSTEM_PROMPT},
                {"role": "user", "content": content},
            ],
            "max_tokens": 2048,
            "temperature": 0.3,  # 低温度，更稳定的检测结果
        }

        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }

        last_error = ""
        for attempt in range(self._MAX_RETRIES):
            try:
                res = requests.post(url, json=payload, headers=headers, timeout=self._TIMEOUT)

                if res.status_code == 200:
                    data = res.json()
                    text = data.get("choices", [{}])[0].get("message", {}).get("content", "")
                    return self._parse_response(text)
                elif res.status_code == 429:
                    # 速率限制，等待后重试
                    wait = 5 * (attempt + 1)  # 指数退避
                    print(f"  [图像检测] 速率限制，{wait}s 后重试...")
                    time.sleep(wait)
                    continue
                elif res.status_code in (401, 402):
                    # 切换 key
                    if len(self._api_keys) > 1:
                        self._api_key = self._api_keys[1]
                        headers["Authorization"] = f"Bearer {self._api_key}"
                    continue
                else:
                    last_error = f"HTTP {res.status_code}: {res.text[:200]}"
            except Exception as e:
                last_error = str(e)

            # 重试前等待
            if attempt < self._MAX_RETRIES - 1:
                wait = 3 * (attempt + 1)
                print(f"  [图像检测] 异常: {last_error}，{wait}s 后重试...")
                time.sleep(wait)

        return HallucinationDetectionResult(
            has_hallucination=False,  # 检测失败时默认不过滤
            issues=[],
            raw_response=f"检测失败: {last_error}",
        )

    def _parse_response(self, text: str) -> HallucinationDetectionResult:
        """解析 LLM 响应."""
        issues = []
        has_hallucination = False

        # 尝试提取 JSON
        try:
            # 查找 JSON 块
            if "```json" in text:
                json_str = text.split("```json")[1].split("```")[0]
            elif "```" in text:
                json_str = text.split("```")[1].split("```")[0]
            else:
                # 尝试直接解析
                json_str = text

            data = json.loads(json_str.strip())
            has_hallucination = data.get("has_hallucination", False)

            for issue_data in data.get("issues", []):
                category = issue_data.get("category", "unknown")
                description = issue_data.get("description", "")
                severity = issue_data.get("severity", "warning")

                # 物理逻辑错误：自动提升为 critical
                if category == "unexpected_object":
                    if any(kw in description for kw in _PHYSICS_VIOLATION_KEYWORDS):
                        severity = "critical"
                        has_hallucination = True

                issues.append(HallucinationIssue(
                    category=category,
                    description=description,
                    severity=severity,
                ))
        except Exception as e:
            # 解析失败，记录原始响应
            print(f"  [WARN] 图像检测响应解析失败: {e}")
            # 尝试简单模式匹配
            text_lower = text.lower()
            if any(k in text_lower for k in ["多指", "畸形", "换脸", "多出", "漂浮", "水印", "半透明", "光束"]):
                has_hallucination = True
                severity = "critical" if any(k in text_lower for k in ["漂浮", "半透明", "光束", "融合", "穿透"]) else "warning"
                issues.append(HallucinationIssue(
                    category="detected_issues",
                    description="检测到潜在幻觉（解析失败，但关键词匹配）",
                    severity=severity,
                ))

        return HallucinationDetectionResult(
            has_hallucination=has_hallucination,
            issues=issues,
            raw_response=text,
        )


# ============================================================================
# 便捷函数
# ============================================================================

_detector: Optional[ImageHallucinationDetector] = None


def get_detector() -> ImageHallucinationDetector:
    """获取全局检测器单例."""
    global _detector
    if _detector is None:
        _detector = ImageHallucinationDetector()
    return _detector


def detect_image_hallucination(
    image_url: str,
    expected_objects: Optional[list[str]] = None,
    reference_image_url: Optional[str] = None,
) -> HallucinationDetectionResult:
    """便捷函数：检测图片幻觉."""
    return get_detector().detect(image_url, expected_objects, reference_image_url)