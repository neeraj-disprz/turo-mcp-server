"""Server-level contract: the tool surface the model actually sees."""

from __future__ import annotations

import pytest

EXPECTED_TOOLS = {
    "whoami",
    "set_session",
    "list_pathways",
    "get_pathway",
    "create_chapter",
    "update_chapter",
    "delete_chapters",
    "create_concept",
    "update_concept",
    "delete_concepts",
    "reorder_pathway",
    "get_concept_kb",
    "set_concept_kb",
    "generate_reel_script",
    "get_reel_script",
    "list_reel_voices",
    "generate_reel3",
    "get_reel3_status",
    "generate_simulation_script",
    "generate_simulation",
    "get_simulation",
    "save_simulation",
    "regenerate_simulation_image",
    "replace_simulation_audio",
    "get_simulation_audio",
}

DESTRUCTIVE_TOOLS = {"delete_chapters", "delete_concepts"}


@pytest.fixture
async def tools(server):
    return {tool.name: tool for tool in await server.list_tools()}


async def test_the_documented_tool_set_is_exposed(tools):
    assert set(tools) == EXPECTED_TOOLS


async def test_no_tool_leaks_the_context_parameter_into_its_schema(tools):
    for name, tool in tools.items():
        assert "ctx" not in (tool.input_schema.get("properties") or {}), name


async def test_no_tool_accepts_a_credential_as_an_argument(tools):
    # The cookie travels on the connection header so it never enters model context.
    # `set_session` is the one deliberate exception: clients that cannot send
    # connection headers have no other way to hand over a cookie, so it arrives
    # in the conversation instead. Asserting the exemption set here keeps that a
    # conscious trade rather than something a later tool can repeat quietly.
    forbidden = ("cookie", "session", "token", "secret", "password", "auth")
    offenders = {
        f"{name}.{param}"
        for name, tool in tools.items()
        for param in tool.input_schema.get("properties") or {}
        if any(word in param.lower() for word in forbidden)
    }
    assert offenders == {"set_session.cookie"}


async def test_every_tool_is_described(tools):
    for name, tool in tools.items():
        assert tool.description and len(tool.description) > 40, name


async def test_destructive_tools_require_confirmation_and_say_so(tools):
    for name in DESTRUCTIVE_TOOLS:
        schema = tools[name].input_schema
        assert "confirm" in schema["properties"], name
        # confirm must default to False, i.e. not be required.
        assert "confirm" not in (schema.get("required") or []), name
        assert "DESTRUCTIVE" in tools[name].description, name


async def test_generation_tools_tell_the_model_to_poll(tools):
    for name in ("generate_reel3", "generate_simulation"):
        assert "poll" in tools[name].description.lower(), name


async def test_reel_generation_needs_only_four_arguments(tools):
    # The API requires eight strings plus a settings block; the rest is defaulted
    # server-side so the model is not asked to invent them.
    required = set(tools["generate_reel3"].input_schema["required"])
    assert required == {"learning_pitstop_id", "reel_title", "script", "voice_id"}


async def test_instructions_state_the_hard_rules(server):
    instructions = server.instructions or ""
    for rule in ("whoami", "poll", "overwrites", "knowledge base"):
        assert rule in instructions.lower()


class TestTransportSecurity:
    """The SDK 421s any Host header it was not told about.

    Localhost is fine by default, but behind a tunnel or platform ingress the
    Host is the public name — getting this wrong makes every hosted request fail.
    """

    def test_default_keeps_dns_rebinding_protection_on(self, monkeypatch):
        from turo_mcp.server import _transport_security

        monkeypatch.delenv("MCP_ALLOWED_HOSTS", raising=False)
        settings = _transport_security()
        assert settings.enable_dns_rebinding_protection is True
        assert settings.allowed_hosts == []

    def test_named_host_is_allowlisted_with_matching_origins(self, monkeypatch):
        from turo_mcp.server import _transport_security

        monkeypatch.setenv("MCP_ALLOWED_HOSTS", "turo-mcp.onrender.com")
        settings = _transport_security()
        assert settings.allowed_hosts == ["turo-mcp.onrender.com"]
        assert "https://turo-mcp.onrender.com" in settings.allowed_origins

    def test_several_hosts_can_be_listed(self, monkeypatch):
        from turo_mcp.server import _transport_security

        monkeypatch.setenv("MCP_ALLOWED_HOSTS", "a.example.com, b.example.com ")
        assert _transport_security().allowed_hosts == ["a.example.com", "b.example.com"]

    def test_wildcard_disables_the_check(self, monkeypatch):
        from turo_mcp.server import _transport_security

        monkeypatch.setenv("MCP_ALLOWED_HOSTS", "*")
        assert _transport_security().enable_dns_rebinding_protection is False


async def test_missing_base_url_fails_loudly_at_startup(monkeypatch, tmp_path):
    from turo_mcp import config

    # A developer's local .env would otherwise supply the value and mask this.
    monkeypatch.setattr(config, "_ENV_FILE", tmp_path / "absent.env")
    monkeypatch.delenv("ARTURO_API_BASE_URL", raising=False)
    with pytest.raises(RuntimeError, match="ARTURO_API_BASE_URL"):
        config.Settings.from_env()


async def test_env_file_supplies_config_values(monkeypatch, tmp_path):
    from turo_mcp import config

    env_file = tmp_path / ".env"
    env_file.write_text(
        "# comment line\nARTURO_API_BASE_URL=https://from-file.test\nARTURO_SESSION='quoted'\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(config, "_ENV_FILE", env_file)
    monkeypatch.delenv("ARTURO_API_BASE_URL", raising=False)
    monkeypatch.delenv("ARTURO_SESSION", raising=False)

    settings = config.Settings.from_env()
    assert settings.base_url == "https://from-file.test"
    assert settings.session_cookie == "quoted", "surrounding quotes must be stripped"


async def test_real_environment_beats_the_env_file(monkeypatch, tmp_path):
    from turo_mcp import config

    env_file = tmp_path / ".env"
    env_file.write_text("ARTURO_API_BASE_URL=https://from-file.test\n", encoding="utf-8")
    monkeypatch.setattr(config, "_ENV_FILE", env_file)
    monkeypatch.setenv("ARTURO_API_BASE_URL", "https://from-shell.test")

    assert config.Settings.from_env().base_url == "https://from-shell.test"
