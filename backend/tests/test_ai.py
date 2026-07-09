from pathlib import Path

import httpx

from app.ai import OpenAICompatibleClient


def test_doubao_image_generation_uses_ark_payload(monkeypatch, tmp_path: Path):
    calls = {}

    def fake_post(url, headers, json, timeout):
        calls["post"] = {"url": url, "headers": headers, "json": json, "timeout": timeout}
        return httpx.Response(200, json={"data": [{"url": "https://example.com/image.png"}]}, request=httpx.Request("POST", url))

    def fake_get(url, timeout):
        calls["get"] = {"url": url, "timeout": timeout}
        return httpx.Response(200, content=b"image-bytes", headers={"content-type": "image/png"}, request=httpx.Request("GET", url))

    monkeypatch.setenv("IMAGE_PROVIDER", "doubao")
    monkeypatch.setenv("IMAGE_BASE_URL", "https://ark.cn-beijing.volces.com/api/v3")
    monkeypatch.setenv("IMAGE_API_KEY", "ark-test-key")
    monkeypatch.setenv("IMAGE_MODEL", "doubao-seedream-5-0-260128")
    monkeypatch.setattr("app.ai.httpx.post", fake_post)
    monkeypatch.setattr("app.ai.httpx.get", fake_get)

    image = OpenAICompatibleClient().generate_image("测试提示词", "cover")

    assert calls["post"]["url"] == "https://ark.cn-beijing.volces.com/api/v3/images/generations"
    assert calls["post"]["headers"]["Authorization"] == "Bearer ark-test-key"
    assert calls["post"]["json"] == {
        "model": "doubao-seedream-5-0-260128",
        "prompt": "测试提示词",
        "sequential_image_generation": "disabled",
        "response_format": "url",
        "size": "2K",
        "stream": False,
        "watermark": True,
    }
    assert calls["get"]["url"] == "https://example.com/image.png"
    assert image.content == b"image-bytes"
