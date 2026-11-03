from typing import Any, Literal

from pydantic import BaseModel, Field

Category = Literal["text", "image", "video", "audio", "embedding"]


class Message(BaseModel):
    role: Literal["system", "user", "assistant", "tool"]
    content: str


class RunRequest(BaseModel):
    """Flexible request body: send either chat messages or a bare prompt."""

    messages: list[Message] | None = None
    prompt: str | None = Field(default=None, max_length=32000)
    system: str | None = None
    temperature: float = Field(default=0.7, ge=0.0, le=2.0)
    max_tokens: int = Field(default=1024, ge=1, le=32768)
    top_p: float = Field(default=1.0, gt=0.0, le=1.0)
    stream: bool = False
    image_size: str = "1024x1024"
    steps: int = Field(default=25, ge=1, le=150)
    negative_prompt: str = ""
    extra: dict[str, Any] = Field(default_factory=dict)

    def to_messages(self) -> list[dict[str, str]]:
        if self.messages:
            msgs = [m.model_dump() for m in self.messages]
        elif self.prompt:
            msgs = [{"role": "user", "content": self.prompt}]
        else:
            msgs = []
        if self.system and not any(m["role"] == "system" for m in msgs):
            msgs.insert(0, {"role": "system", "content": self.system})
        return msgs


class ModelInfo(BaseModel):
    name: str
    provider: str
    category: str
    model_id: str
    is_free: bool
    requires_key: bool
    available: bool
    capabilities: list[str]
    context_window: int | None = None
    description: str = ""


class DiscoverResponse(BaseModel):
    models: list[ModelInfo]
    count: int
    providers: dict[str, bool]


class ImageOutput(BaseModel):
    b64: str | None = None
    url: str | None = None
    mime: str = "image/png"


class RunResponse(BaseModel):
    model: str
    provider: str
    category: str
    text: str | None = None
    images: list[ImageOutput] = Field(default_factory=list)
    usage: dict[str, Any] = Field(default_factory=dict)
    latency_ms: int | None = None
    raw: dict[str, Any] | None = None


class SkillField(BaseModel):
    name: str
    label: str
    type: str = "text"          # text | textarea | number
    required: bool = False
    default: Any | None = None
    placeholder: str | None = None


class SkillInfo(BaseModel):
    name: str
    title: str = ""
    category: str
    description: str
    params: list[str] = Field(default_factory=list)
    fields: list[SkillField] = Field(default_factory=list)
    executable: bool = True
    tier: str = "catalogue"
    risk: str = "safe"
    source: str = "community"
    tags: list[str] = Field(default_factory=list)


class SkillsResponse(BaseModel):
    skills: list[SkillInfo]
    count: int
    source: str
    total: int = 0
    limit: int = 24
    offset: int = 0
    query: str = ""
    mode: str = "browse"
    categories: dict[str, int] = Field(default_factory=dict)


class SkillRequest(BaseModel):
    params: dict[str, Any] = Field(default_factory=dict)
    # Accepted as a synonym so both `{"params": …}` and `{"inputs": …}` work.
    inputs: dict[str, Any] | None = None


class VideoRequest(BaseModel):
    prompt: str = Field(min_length=1, max_length=4000)
    negative_prompt: str = ""
    steps: int = Field(default=25, ge=1, le=150)
    width: int = Field(default=576, ge=256, le=1024)
    height: int = Field(default=1024, ge=256, le=1024)
    fps: int = Field(default=8, ge=4, le=30)
    motion_bucket_id: int = Field(default=127, ge=1, le=255)
    seed: int | None = None
    init_image_b64: str | None = None
