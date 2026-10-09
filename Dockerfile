FROM python:3.12-slim

# Hugging Face Spaces runs the container as uid 1000 and will hit permission
# errors on anything owned by root, so the user is created before any COPY.
RUN useradd -m -u 1000 user
USER user
ENV HOME=/home/user \
    PATH=/home/user/.local/bin:$PATH
WORKDIR $HOME/app

COPY --chown=user pyproject.toml ./
COPY --chown=user turo_mcp ./turo_mcp

RUN pip install --no-cache-dir --upgrade pip && pip install --no-cache-dir .

ENV PYTHONUNBUFFERED=1 \
    MCP_HOST=0.0.0.0 \
    MCP_PORT=8080 \
    ARTURO_API_BASE_URL=https://turo.disprz.ai \
    ARTURO_API_PREFIX=/ui/api

# The SDK rejects unknown Host headers as DNS-rebinding protection, which would
# 421 every request behind a platform ingress. Accepting any host is safe here
# only because reaching the endpoint is not enough on its own: the unguessable
# MCP_PATH is the gate. Without MCP_PATH set, this endpoint is wide open.
ENV MCP_ALLOWED_HOSTS="*"

EXPOSE 8080

# Set MCP_PATH to a long random string in the platform's secret store. Leave
# ARTURO_SESSION unset: callers hand their own cookie over with `set_session`,
# or send it on the X-Arturo-Session connection header.
CMD ["sh", "-c", "turo-mcp --transport streamable-http --host $MCP_HOST --port $MCP_PORT"]
