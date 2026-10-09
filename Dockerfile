FROM python:3.12-slim

WORKDIR /app

COPY pyproject.toml ./
COPY turo_mcp ./turo_mcp

RUN pip install --no-cache-dir --upgrade pip && pip install --no-cache-dir .

ENV PYTHONUNBUFFERED=1 \
    MCP_HOST=0.0.0.0 \
    MCP_PORT=8080

EXPOSE 8080

# ARTURO_API_BASE_URL is required. ARTURO_SESSION is optional: set it only for a
# single-account demo, and pair it with an unguessable MCP_PATH. Leave it unset
# for the real deployment so each caller supplies their own cookie via the
# X-Arturo-Session connection header.
CMD ["sh", "-c", "turo-mcp --transport streamable-http --host $MCP_HOST --port $MCP_PORT"]
