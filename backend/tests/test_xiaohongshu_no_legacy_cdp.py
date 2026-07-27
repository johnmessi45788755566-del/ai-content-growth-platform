from pathlib import Path

import app.publishing as publishing


def test_xiaohongshu_does_not_export_legacy_browser_publisher():
    assert not hasattr(publishing, "XiaoHongShuBrowserPublisher")


def test_xiaohongshu_legacy_module_does_not_contain_cdp_mouse_clicks():
    module_path = Path(__file__).parents[1] / "app" / "publishing" / "xiaohongshu.py"
    if not module_path.exists():
        return
    source = module_path.read_text(encoding="utf-8")

    forbidden_tokens = (
        "clickPoint",
        "cdp_mouse",
        "dispatchMouseEvent",
        "Input.dispatchMouseEvent",
        "_dispatch_mouse_click",
    )
    for token in forbidden_tokens:
        assert token not in source


def test_cdp_mouse_helper_is_wechat_specific_not_generic():
    gateway_path = Path(__file__).parents[1] / "app" / "publishing" / "browser_gateway.py"
    source = gateway_path.read_text(encoding="utf-8")

    assert "def _dispatch_mouse_click(" not in source
    assert "self._dispatch_mouse_click(" not in source
    assert "def _dispatch_wechat_mouse_click(" in source
