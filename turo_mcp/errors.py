"""Error types that turn Arturo's HTTP responses into one clear sentence.

Everything here subclasses the SDK's `ToolError` deliberately. The SDK only
forwards a `ToolError` message to the model; any other exception is treated as a
crash and the caller sees nothing but "Error executing tool <name>". Since these
messages are the entire recovery UX, they must be anticipated failures.
"""

from __future__ import annotations

from mcp.server.mcpserver.exceptions import ToolError


class TuroMcpError(ToolError):
    """Base class — anything raised here is shown to the caller verbatim."""


class InvalidArgument(TuroMcpError):
    """Arguments the server rejects before any network call."""


class SessionExpired(TuroMcpError):
    """Also the recovery path: this message is what tells the model to re-auth.

    It names `set_session` because the message is the only channel back to the
    caller — a client that cannot send connection headers has no other way to
    learn how to recover.
    """

    def __init__(self, header: str = "X-Arturo-Session") -> None:
        super().__init__(
            "Arturo session expired or invalid. Sessions last 24 hours from login "
            "and do not renew on use. Recover by calling `set_session` with a valid "
            "arturo_session cookie — reuse one already supplied in this conversation "
            "before asking the user for a new one. (Deployments where the caller "
            f"sends the {header} connection header should refresh that header instead.)"
        )


class UpstreamUnavailable(TuroMcpError):
    def __init__(self, detail: str) -> None:
        super().__init__(
            f"Arturo could not reach a downstream service ({detail}). This is a "
            "transient infrastructure failure, not a content failure — retry "
            "before assuming the generation was rejected."
        )


class RequestRejected(TuroMcpError):
    """A 4xx the caller can act on."""


class MissingPrerequisite(TuroMcpError):
    """A step was attempted before the step it depends on."""
