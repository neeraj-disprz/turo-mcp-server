"""Server configuration, read once from the environment at startup."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

# Project-local .env, gitignored. Lets an operator keep the session cookie out of
# both the MCP client config and their shell history. Real environment variables
# always win.
_ENV_FILE = Path(__file__).resolve().parent.parent / ".env"


def _load_env_file() -> None:
    if not _ENV_FILE.is_file():
        return
    for line in _ENV_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip("\"'"))

# Production defaults lifted from Arturo-Web's ContentGenerationModal so reels
# generated through MCP look identical to reels generated through the UI.
DEFAULT_BRAND_COLORS = {
    "primary": "#E82028",
    "secondary": "#26C6FF",
    "on_primary": "#FFFFFF",
}
DEFAULT_ART_STYLE = "soft-gouache"
DEFAULT_MODEL_ID = "eleven_v3"
DEFAULT_INTRO_BGMUSIC = "cinematic-corporate-bed.mp3"
DEFAULT_CULTURE_CONTEXT = "ind"
DEFAULT_VIDEO_FREQUENCY = "medium"
DEFAULT_ASPECT_RATIO = "16:9"


def _env(name: str, default: str = "") -> str:
    return (os.environ.get(name) or default).strip()


def _hex(name: str, default: str) -> str:
    value = _env(name) or default
    return value if value.startswith("#") else f"#{value}"


@dataclass(frozen=True)
class Settings:
    """Everything the server needs to talk to Arturo.

    `session_cookie` is a *fallback only*. The supported path is for each caller
    to supply their own cookie through the `session_header` MCP connection
    header, which keeps the credential out of the model's context entirely.
    """

    base_url: str
    # Django routes the API at /api/, but deployments expose it through an
    # ingress prefix (turo.disprz.ai serves it at /ui/api/).
    api_prefix: str = "/ui/api"
    session_header: str = "X-Arturo-Session"
    session_cookie: str = ""
    timeout_seconds: float = 60.0
    # Where a human opens a pathway. Separate from base_url because the web app
    # and the API need not share a host, even though today they do.
    web_url: str = ""
    pathway_path: str = "/ui/workspace/documentToLearning/"
    # Some Arturo endpoints block until an AI service finishes and set no
    # timeout of their own — script generation, image regeneration, TTS. The
    # web app waits indefinitely (browser fetch); 60s cuts them off mid-run.
    long_timeout_seconds: float = 600.0
    brand_colors: dict[str, str] = field(default_factory=lambda: dict(DEFAULT_BRAND_COLORS))
    art_style: str = DEFAULT_ART_STYLE
    model_id: str = DEFAULT_MODEL_ID
    intro_bgmusic: str = DEFAULT_INTRO_BGMUSIC
    culture_context: str = DEFAULT_CULTURE_CONTEXT

    def pathway_url(self, learning_pathway_id: str) -> str:
        """Clickable link to a pathway in the Turo UI.

        Returned alongside pathway reads so the user can open what the model is
        describing without hunting for it in the workspace.
        """
        base = (self.web_url or self.base_url).rstrip("/")
        return f"{base}{self.pathway_path}?pathwayId={learning_pathway_id}"

    @classmethod
    def from_env(cls) -> "Settings":
        _load_env_file()
        base_url = _env("ARTURO_API_BASE_URL")
        if not base_url:
            raise RuntimeError(
                "ARTURO_API_BASE_URL is not set. Point it at the Arturo API root, "
                "e.g. https://turo-api.example.com"
            )
        return cls(
            base_url=base_url.rstrip("/"),
            api_prefix="/" + _env("ARTURO_API_PREFIX", "/ui/api").strip("/"),
            session_header=_env("ARTURO_SESSION_HEADER", "X-Arturo-Session"),
            session_cookie=_env("ARTURO_SESSION"),
            timeout_seconds=float(_env("ARTURO_TIMEOUT_SECONDS", "60")),
            long_timeout_seconds=float(_env("ARTURO_LONG_TIMEOUT_SECONDS", "600")),
            web_url=_env("ARTURO_WEB_URL").rstrip("/"),
            pathway_path="/" + _env(
                "ARTURO_PATHWAY_PATH", "/ui/workspace/documentToLearning/"
            ).strip("/") + "/",
            brand_colors={
                "primary": _hex("REEL3_BRAND_PRIMARY", DEFAULT_BRAND_COLORS["primary"]),
                "secondary": _hex("REEL3_BRAND_SECONDARY", DEFAULT_BRAND_COLORS["secondary"]),
                "on_primary": _hex("REEL3_BRAND_ON_PRIMARY", DEFAULT_BRAND_COLORS["on_primary"]),
            },
            art_style=_env("REEL3_ART_STYLE", DEFAULT_ART_STYLE),
            model_id=_env("REEL3_MODEL_ID", DEFAULT_MODEL_ID),
            intro_bgmusic=_env("REEL3_INTRO_BGMUSIC", DEFAULT_INTRO_BGMUSIC),
            culture_context=_env("REEL3_CULTURE_CONTEXT", DEFAULT_CULTURE_CONTEXT),
        )
