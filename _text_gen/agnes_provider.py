"""agnes ai 文本生成 — OpenAI 兼容协议.

POST {AGNES_BASE_URL}/v1/chat/completions，OpenAI chat completions 格式.
403 自动回退到 AGNES_TEXT_FALLBACK_MODELS 中的下一个模型.
5xx/连接错误自动重试（AGNES_TEXT_SUBMIT_RETRIES 次）.

模型: agnes-2.0-flash
上下文窗口: 512K
最大输出: 65.5K tokens
支持 Thinking 模式: chat_template_kwargs.enable_thinking
"""

import time
from typing import Optional

import requests

from .config import config
from .models import TextGenerationRequest, TextGenerationResult, truncated_result
from .service import TextGenerationService
from shared.error_fmt import parse_retry_after


class AgnesTextService(TextGenerationService):
    """agnes ai (OpenAI 兼容) 文本生成实现.

    403 回退: 主模型返回 403 时，自动切换到 AGNES_TEXT_FALLBACK_MODELS 中的下一个.
    429 回退: 速率限制时自动切换到下一个 API Key.

    Thinking 模式: 在 .env.content 中设置 AGNES_TEXT_ENABLE_THINKING=1 启用.
    """

    def __init__(self) -> None:
        config.validate()
        # 构建完整模型队列: [主模型, 回退1, 回退2, ...]
        self._model_queue: list[str] = [
            config.AGNES_TEXT_MODEL
        ] + config.AGNES_TEXT_FALLBACK_MODELS
        self._enable_thinking: bool = config.AGNES_TEXT_ENABLE_THINKING
        # 多 Key 切换（最多8个）
        self._api_keys = [config.AGNES_API_KEY]
        if config.AGNES_API_KEY_2:
            self._api_keys.append(config.AGNES_API_KEY_2)
        if config.AGNES_API_KEY_3:
            self._api_keys.append(config.AGNES_API_KEY_3)
        if config.AGNES_API_KEY_4:
            self._api_keys.append(config.AGNES_API_KEY_4)
        if config.AGNES_API_KEY_5:
            self._api_keys.append(config.AGNES_API_KEY_5)
        if config.AGNES_API_KEY_6:
            self._api_keys.append(config.AGNES_API_KEY_6)
        if config.AGNES_API_KEY_7:
            self._api_keys.append(config.AGNES_API_KEY_7)
        if config.AGNES_API_KEY_8:
            self._api_keys.append(config.AGNES_API_KEY_8)
        self._current_key_index: int = 0

    @property
    def _api_key(self) -> str:
        return self._api_keys[self._current_key_index]

    def _switch_to_backup_key(self) -> bool:
        """切换到备用 API Key. 返回是否切换成功."""
        if self._current_key_index + 1 < len(self._api_keys):
            self._current_key_index += 1
            return True
        return False

    def _advance_index(self, idx: int) -> Optional[int]:
        """返回下一个回退模型的下标，无可用模型时返回 None."""
        if idx + 1 >= len(self._model_queue):
            return None
        return idx + 1

    def generate(self, req: TextGenerationRequest) -> TextGenerationResult:
        url = f"{config.AGNES_BASE_URL.rstrip('/')}/v1/chat/completions"
        idx = 0
        last_error = ""

        # M7 修复 2026-08-14：每次调用从主 Key 开始，避免一次 401/429 造成
        # 的 Key 切换状态跨调用永久残留（否则后续请求永远跳过主 Key）。
        self._current_key_index = 0

        while idx < len(self._model_queue):
            model = self._model_queue[idx]
            headers = {
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
            }
            # v6.37：结构化多轮输入（user_inputs）优先于 user_prompt 字符串
            if req.user_inputs:
                _messages = [{"role": "system", "content": req.system_prompt}]
                _messages.extend(req.user_inputs)
            else:
                _messages = [
                    {"role": "system", "content": req.system_prompt},
                    {"role": "user", "content": req.user_prompt},
                ]
            payload: dict = {
                "model": model,
                "messages": _messages,
                "max_tokens": req.max_tokens,
                "temperature": req.temperature,
            }

            # Thinking 模式: 用于复杂任务（分镜生成属于复杂创意任务，建议开启）
            if self._enable_thinking:
                payload["chat_template_kwargs"] = {"enable_thinking": True}

            print(f"[INFO] 正在调用 Agnes ({model}) 生成文本..."
                  + (" [Thinking]" if self._enable_thinking else ""))
            _submit_retry = 0
            try:
                while True:
                    try:
                        res = requests.post(url, json=payload, headers=headers, timeout=300)
                        break
                    except Exception as e:
                        _submit_retry += 1
                        if _submit_retry <= config.AGNES_TEXT_SUBMIT_RETRIES:
                            print(f"  [!] 提交异常: {e}，"
                                  f"{config.AGNES_TEXT_SUBMIT_RETRY_DELAY}s 后重试 "
                                  f"({_submit_retry}/{config.AGNES_TEXT_SUBMIT_RETRIES})")
                            time.sleep(config.AGNES_TEXT_SUBMIT_RETRY_DELAY)
                        else:
                            last_error = f"Agnes 异常（重试{_submit_retry}次）: {e}"
                            break
                if last_error:
                    idx = len(self._model_queue)
                    break

                # ---- 422: 内容违规，返回错误 ----
                if res.status_code == 422:
                    last_error = f"Agnes 内容违规 [422]: {res.text[:300]}"
                    break

                # ---- 401/402: 认证失败/余额不足，切换到下一个 API Key ----
                # M7 修复 2026-08-14：换 Key 预算不再与重试预算（_submit_retry）混用——
                # 401/402 不会因重试，直接逐个换 Key 直到全部尝试（预算 = 可用 Key 数）。
                if res.status_code in (401, 402):
                    if self._switch_to_backup_key():
                        print(f"  [!] HTTP {res.status_code}，"
                              f"切换到 API Key {self._current_key_index + 1}/{len(self._api_keys)} 并重试")
                        continue
                    last_error = f"Agnes API Key 无效/过期 [HTTP {res.status_code}]，所有 Key 均已尝试: {res.text[:300]}"
                    break

                # ---- 408/499/502/503/504/520/522/524: 临时错误，等待后重试 ----
                if res.status_code in (408, 499, 502, 503, 504, 520, 522, 524):
                    _submit_retry += 1
                    if _submit_retry <= config.AGNES_TEXT_SUBMIT_RETRIES:
                        print(f"  [!] 临时错误 {res.status_code}，"
                              f"{config.AGNES_TEXT_SUBMIT_RETRY_DELAY}s 后重试 "
                              f"({_submit_retry}/{config.AGNES_TEXT_SUBMIT_RETRIES})")
                        time.sleep(config.AGNES_TEXT_SUBMIT_RETRY_DELAY)
                        continue
                    last_error = f"Agnes 失败 [{res.status_code}] (重试{_submit_retry}次): {res.text[:300]}"
                    idx = len(self._model_queue)
                    break

                # ---- 5xx: 服务端暂时不可用，等待后重试 ----
                if 500 <= res.status_code < 600:
                    _submit_retry += 1
                    if _submit_retry <= config.AGNES_TEXT_SUBMIT_RETRIES:
                        print(f"  [!] 服务端 {res.status_code}，"
                              f"{config.AGNES_TEXT_SUBMIT_RETRY_DELAY}s 后重试 "
                              f"({_submit_retry}/{config.AGNES_TEXT_SUBMIT_RETRIES})")
                        time.sleep(config.AGNES_TEXT_SUBMIT_RETRY_DELAY)
                        continue
                    last_error = f"Agnes 失败 [{res.status_code}] (重试{_submit_retry}次): {res.text[:300]}"
                    idx = len(self._model_queue)
                    break

                # ---- 200: 提取文本 ----
                if res.status_code == 200:
                    data = res.json()
                    choices = data.get("choices", [])
                    if not choices:
                        last_error = f"Agnes 返回无 choices: {str(data)[:300]}"
                        break
                    text = choices[0].get("message", {}).get("content", "")
                    if not text:
                        last_error = "Agnes 返回文本为空"
                        break
                    if idx > 0:
                        print(f"  [INFO] 文本已回退到模型: {model}")
                    if self._current_key_index > 0:
                        print(f"  [INFO] 文本已回退到 API Key: {self._current_key_index + 1}/{len(self._api_keys)}")
                    # 截断（v6.62.6，09-09）：与其它 provider 同契约——直接返回，
                    # 不进模型回退队列（换模型不解决"输出超上限"）。
                    if choices[0].get("finish_reason") == "length":
                        return truncated_result(
                            text=text,
                            usage=None,
                            allow_truncated=req.allow_truncated,
                            error=(
                                f"Agnes 输出被截断（finish_reason=length, model={model}, "
                                f"max_tokens={req.max_tokens}）——输出 JSON 不完整，已整份丢弃。"
                            ),
                        )
                    return TextGenerationResult(success=True, text=text)

                # ---- 429: 速率限制，优先切换 API Key（不同 key 限速独立），再用新 key 重试 ----
                if res.status_code == 429:
                    if self._switch_to_backup_key():
                        print(f"  [!] 文本 API Key {self._current_key_index} 速率限制 429，"
                              f"切换到 Key {self._current_key_index + 1}/{len(self._api_keys)} 并重试")
                        continue
                    # 所有 Key 都 429，按服务端返回的剩余时间精确等待后重试
                    _submit_retry += 1
                    if _submit_retry <= config.AGNES_TEXT_SUBMIT_RETRIES:
                        wait = parse_retry_after(res.text)
                        delay = (wait + 0.5) if wait is not None \
                            else config.AGNES_TEXT_SUBMIT_RETRY_DELAY
                        print(f"  [!] 所有 API Key 均限速 429，"
                              f"{delay:.1f}s 后重试 "
                              f"({_submit_retry}/{config.AGNES_TEXT_SUBMIT_RETRIES})")
                        time.sleep(delay)
                        continue
                    last_error = (f"Agnes 文本速率限制 [429] (重试{_submit_retry}次): "
                                  f"{res.text[:300]}")
                    break

                # ---- 403: 额度耗尽或无权限，尝试模型回退 ----
                if res.status_code == 403:
                    nxt = self._advance_index(idx)
                    if nxt is not None:
                        print(f"  [!] 文本模型 {model} 返回 403（额度耗尽/无权限），"
                              f"自动切换到 {self._model_queue[nxt]}")
                        idx = nxt
                        continue
                    last_error = (f"所有模型均返回 403，已无可用回退模型。"
                                  f"尝试过: {', '.join(self._model_queue)}")
                    break

                # ---- 其他错误 ----
                last_error = f"Agnes 失败 ({res.status_code}): {res.text[:300]}"
                break

            except Exception as e:
                last_error = f"Agnes 异常: {e}"
                break

        return TextGenerationResult(success=False, error=last_error)
