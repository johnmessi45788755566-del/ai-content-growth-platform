import base64
import json
import os
import re
import uuid
from dataclasses import dataclass
from typing import Any

import httpx


class AIConfigurationError(RuntimeError):
    pass


class AIResponseError(RuntimeError):
    pass


@dataclass
class TopicRecommendation:
    direction: str
    subtitle: str
    titles: list[str]


@dataclass
class GeneratedImage:
    filename: str
    content: bytes
    content_type: str


class OpenAICompatibleClient:
    def __init__(self) -> None:
        self.llm_base_url = os.getenv("LLM_BASE_URL", "").rstrip("/")
        self.llm_api_key = os.getenv("LLM_API_KEY", "")
        self.llm_model = os.getenv("LLM_MODEL", "")
        self.image_base_url = os.getenv("IMAGE_BASE_URL", self.llm_base_url).rstrip("/")
        self.image_api_key = os.getenv("IMAGE_API_KEY", self.llm_api_key)
        self.image_model = os.getenv("IMAGE_MODEL", "")
        self.image_provider = os.getenv("IMAGE_PROVIDER", "openai").strip().lower()

    def recommend_topics(self, user_prompt: str) -> list[TopicRecommendation]:
        payload = self._chat_json(
            system="你是资深微信公众号增长选题策划。只输出 JSON。",
            user=(
                "根据用户需求，为公众号文章生成 3 个选题方向，每个方向给 5 个标题。"
                "标题必须围绕用户输入的行业、主题、产品、受众和转化目标，不要强行套用任何固定行业或固定产品。"
                "如果用户没有说明行业或产品，就用通用新媒体文章选题方式处理。"
                '返回格式：{"directions":[{"direction":"","subtitle":"","titles":[""]}]}。\n'
                f"用户需求：{user_prompt}"
            ),
        )
        directions = payload.get("directions", [])
        return [
            TopicRecommendation(
                direction=str(item.get("direction", "选题方向")),
                subtitle=str(item.get("subtitle", "")),
                titles=[str(title) for title in item.get("titles", []) if str(title).strip()],
            )
            for item in directions
            if item.get("titles")
        ]

    def generate_article(self, title: str, direction: str) -> list[dict[str, str]]:
        payload = self._chat_json(
            system="你是微信公众号爆款文章写作专家。只输出 JSON。",
            user=(
                "根据标题和标题方向生成公众号文章正文。"
                "文章必须围绕标题本身，不要强行套用固定产品、固定行业或固定受众。"
                "如果标题或方向里包含明确产品、客户和转化目标，就自然承接；否则以观点表达和读者行动引导为主。"
                "必须分成 4 个段落模块：第一段 type 为 intro，label 为 引言；其余三段 type 为 body，label 统一为 正文。"
                '返回格式：{"blocks":[{"type":"intro","label":"引言","content":""},'
                '{"type":"body","label":"正文","content":""},'
                '{"type":"body","label":"正文","content":""},'
                '{"type":"body","label":"正文","content":""}]}。\n'
                f"标题：{title}\n标题方向：{direction}"
            ),
        )
        blocks = payload.get("blocks", [])
        return [
            {
                "type": str(block.get("type", "body")),
                "label": str(block.get("label", "正文段落")),
                "content": str(block.get("content", "")),
            }
            for block in blocks
            if str(block.get("content", "")).strip()
        ]

    def generate_image(self, prompt: str, image_type: str) -> GeneratedImage:
        if not self.image_base_url or not self.image_api_key or not self.image_model:
            raise AIConfigurationError("图片模型未配置，请设置 IMAGE_BASE_URL、IMAGE_API_KEY、IMAGE_MODEL")

        response = httpx.post(
            f"{self.image_base_url}/images/generations",
            headers={"Authorization": f"Bearer {self.image_api_key}"},
            json=self._image_payload(prompt, image_type),
            timeout=120,
        )
        _raise_for_status(response, "图片模型调用失败")
        data = response.json().get("data", [])
        if not data:
            raise AIResponseError("图片模型未返回图片")
        item = data[0]
        if item.get("b64_json"):
            return GeneratedImage(
                filename=f"{image_type}-{uuid.uuid4().hex}.png",
                content=base64.b64decode(item["b64_json"]),
                content_type="image/png",
            )
        if item.get("url"):
            image_response = httpx.get(item["url"], timeout=120)
            image_response.raise_for_status()
            content_type = image_response.headers.get("content-type", "image/png").split(";")[0]
            ext = "jpg" if "jpeg" in content_type else "png"
            return GeneratedImage(
                filename=f"{image_type}-{uuid.uuid4().hex}.{ext}",
                content=image_response.content,
                content_type=content_type,
            )
        raise AIResponseError("图片模型返回格式不支持")

    def _image_payload(self, prompt: str, image_type: str) -> dict[str, Any]:
        if self.image_provider == "doubao":
            return {
                "model": self.image_model,
                "prompt": prompt,
                "sequential_image_generation": "disabled",
                "response_format": "url",
                "size": "2K",
                "stream": False,
                "watermark": True,
            }
        return {
            "model": self.image_model,
            "prompt": prompt,
            "n": 1,
            "size": "1024x1024" if image_type == "inline" else "1536x1024",
        }

    def _chat_json(self, system: str, user: str) -> dict[str, Any]:
        if not self.llm_base_url or not self.llm_api_key or not self.llm_model:
            raise AIConfigurationError("文本模型未配置，请设置 LLM_BASE_URL、LLM_API_KEY、LLM_MODEL")

        response = httpx.post(
            f"{self.llm_base_url}/chat/completions",
            headers={"Authorization": f"Bearer {self.llm_api_key}"},
            json={
                "model": self.llm_model,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                "temperature": 0.7,
                "response_format": {"type": "json_object"},
            },
            timeout=120,
        )
        _raise_for_status(response, "文本模型调用失败")
        content = response.json()["choices"][0]["message"]["content"]
        return _parse_json_object(content)


def _parse_json_object(content: str) -> dict[str, Any]:
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", content, re.S)
        if not match:
            raise AIResponseError("模型没有返回 JSON")
        return json.loads(match.group(0))


def _raise_for_status(response: httpx.Response, message: str) -> None:
    try:
        response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        body = response.text.strip()
        detail = f"{message}：HTTP {response.status_code}"
        if body:
            detail = f"{detail}，返回：{body[:1000]}"
        raise AIResponseError(detail) from exc
