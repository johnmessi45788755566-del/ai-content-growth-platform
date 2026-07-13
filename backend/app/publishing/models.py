from dataclasses import dataclass, field
from typing import Any, Literal


PublishAction = Literal["draft", "publish"]


@dataclass(frozen=True)
class PublishResult:
    status: str
    platform_draft_id: str = ""
    platform_publish_id: str = ""
    platform_url: str = ""
    response: dict[str, Any] = field(default_factory=dict)
    requires_confirmation: bool = False
