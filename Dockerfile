# One Dockerfile, one lean stage per role. requirements.txt pins the direct dependencies and
# constraints.txt everything they pull in: each stage installs only what it needs, pinned with both.
# The base image is pinned by digest (the multi-platform index of python:3.12-slim): a moved tag changes nothing.
FROM python:3.12-slim@sha256:05cda9777409a9c3ffddd94a4c476b79f0769a0b4857f0c7ed9226b6800b0d6f AS base
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PYTHONPATH=/app
WORKDIR /app
COPY requirements.txt constraints.txt ./
RUN useradd --uid 10001 --create-home app

# Scheduling API: FastAPI + SQLite (stored in the /state volume).
FROM base AS api
RUN pip install --no-cache-dir -c requirements.txt -c constraints.txt fastapi uvicorn pydantic cryptography \
    && mkdir /state /keys && chown app:app /state /keys
COPY --chown=app:app api api
COPY --chown=app:app data data
USER app
# Idle connections kept past the clients' 5 s pool expiry (see KEEP_ALIVE_SECONDS in mcp_servers).
CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8000", "--timeout-keep-alive", "75"]

# RAG MCP server: search over the exam catalog, no OCR engine.
FROM base AS rag
RUN pip install --no-cache-dir -c requirements.txt -c constraints.txt mcp
COPY --chown=app:app catalogo.py catalogo.py
COPY --chown=app:app mcp_servers mcp_servers
COPY --chown=app:app data data
USER app
CMD ["python", "-m", "mcp_servers.rag"]

# Tesseract with Portuguese data, for the OCR server.
FROM base AS tesseract
RUN apt-get update \
    && apt-get install -y --no-install-recommends tesseract-ocr tesseract-ocr-por \
    && rm -rf /var/lib/apt/lists/*

# OCR MCP server: Tesseract + PII masking before anything leaves the container.
FROM tesseract AS ocr
RUN pip install --no-cache-dir -c requirements.txt -c constraints.txt mcp pillow pytesseract
COPY --chown=app:app catalogo.py catalogo.py
COPY --chown=app:app mcp_servers mcp_servers
COPY --chown=app:app guardrails guardrails
COPY --chown=app:app data data
USER app
CMD ["python", "-m", "mcp_servers.ocr"]

# Agent CLI (transpile and run): the ADK and only the code those commands use. No OCR engine and
# no image: the OCR container reads samples/; exemplos/ has the scripts and specs of the videos.
FROM base AS agent
# ADK's telemetry off: `adk run` and `adk web` ask "Enable telemetry? [Y/n]" (Enter says yes) until
# ~/.adk/config.json answers, and the read-only container cannot save the answer. No env var turns it off.
RUN pip install --no-cache-dir -c requirements.txt -c constraints.txt google-adk google-genai mcp httpx \
    && mkdir /app/generated /home/app/.adk && chown app:app /app/generated \
    && printf '{"telemetry": false}\n' > /home/app/.adk/config.json
COPY --chown=app:app cli.py catalogo.py ./
COPY --chown=app:app data data
COPY --chown=app:app runtime runtime
COPY --chown=app:app transpiler transpiler
COPY --chown=app:app specs specs
COPY --chown=app:app exemplos exemplos
USER app
CMD ["python", "-m", "cli", "--help"]

# Tests and quality checks (the `tests` service): the agent plus pytest, ruff and mypy, every
# runtime dependency (the tests run the servers in process), Tesseract and the whole project.
FROM agent AS test
USER root
RUN apt-get update \
    && apt-get install -y --no-install-recommends tesseract-ocr tesseract-ocr-por \
    && rm -rf /var/lib/apt/lists/*
COPY requirements-dev.txt .
RUN pip install --no-cache-dir -r requirements.txt -r requirements-dev.txt -c constraints.txt
COPY --chown=app:app . .
USER app
CMD ["pytest", "-q"]
