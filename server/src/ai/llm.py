"""LLM 引擎封装

统一接口调用大模型，支持多模型切换。
使用 httpx 直接调用 DashScope / OpenAI 兼容 API。
"""

import os
import json
import logging
import httpx

from server.src.core.config import server_config

logger = logging.getLogger("lanshan-server.llm")


class LLMEngine:
    """LLM推理引擎"""

    # 各provider的API配置
    PROVIDER_CONFIG = {
        "qwen": {
            "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
            "api_key_env": "DASHSCOPE_API_KEY",
        },
        "openai": {
            "base_url": "https://api.openai.com/v1",
            "api_key_env": "OPENAI_API_KEY",
        },
        "deepseek": {
            "base_url": "https://api.deepseek.com/v1",
            "api_key_env": "DEEPSEEK_API_KEY",
        },
        "zhipu": {
            "base_url": "https://open.bigmodel.cn/api/paas/v4",
            "api_key_env": "ZHIPUAI_API_KEY",
        },
    }

    def __init__(self):
        self.model = server_config.llm_model
        self.provider = server_config.llm_provider
        self.temperature = server_config.llm_temperature
        self._api_key = server_config.llm_api_key
        self._setup_env()

    def _setup_env(self):
        """设置环境变量"""
        provider_cfg = self.PROVIDER_CONFIG.get(self.provider, {})
        env_key = provider_cfg.get("api_key_env")
        if env_key:
            os.environ[env_key] = self._api_key
            logger.info(f"LLM配置: provider={self.provider}, model={self.model}")

    @property
    def _base_url(self) -> str:
        return self.PROVIDER_CONFIG.get(self.provider, {}).get("base_url", "")

    @property
    def is_configured(self) -> bool:
        return bool(self._api_key and self.model)

    async def generate(self, prompt: str, messages: list | None = None) -> str | None:
        """调用LLM生成响应"""
        try:
            if messages is None:
                messages = [{"role": "user", "content": prompt}]

            logger.info(f"调用LLM: {self.model}, provider={self.provider}")

            payload = {
                "model": self.model,
                "messages": messages,
                "temperature": self.temperature,
                "max_tokens": 4096,
            }

            async with httpx.AsyncClient(timeout=120) as client:
                resp = await client.post(
                    f"{self._base_url}/chat/completions",
                    headers={
                        "Authorization": f"Bearer {self._api_key}",
                        "Content-Type": "application/json",
                    },
                    json=payload,
                )
                resp.raise_for_status()
                data = resp.json()
                content = data["choices"][0]["message"]["content"]
                logger.info(f"LLM响应长度: {len(content)} 字符")
                return content
        except Exception as e:
            logger.error(f"LLM调用失败: {e}")
            return None

    async def health_check(self) -> dict:
        """验证LLM连接是否正常"""
        try:
            result = await self.generate("请只回复单词'ok'")
            return {
                "ok": True,
                "message": "LLM连接正常",
                "model": self.model,
                "response": result.strip() if result else "无响应",
            }
        except Exception as e:
            return {
                "ok": False,
                "message": f"LLM连接失败: {str(e)}",
                "model": self.model,
            }


llm_engine = LLMEngine()
