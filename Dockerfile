# Multi-Stage Dockerfile for Hinglish Voice Agent Worker & Control Plane
FROM python:3.11-slim as builder

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

# Final Runtime Image
FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Copy installed Python packages from builder
COPY --from=builder /install /usr/local
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1

# LiveKit Cloud requires an unprivileged user
RUN adduser --disabled-password --gecos "" --home /app --uid 10001 appuser

# Copy project source code (owned by appuser: the worker writes cached phrases under assets/)
COPY --chown=appuser:appuser . /app
USER appuser

EXPOSE 8000

# Default command starts the call worker (LiveKit Cloud needs a fixed start command);
# Compose and the Vercel dialer Daemon pass their own commands
CMD ["python", "-m", "agent.worker", "start"]
