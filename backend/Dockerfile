FROM python:3.12-slim

WORKDIR /app

# Keeps Python from writing .pyc files and enables optimised mode
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONOPTIMIZE=1

# Install system deps required by cffi / cryptography wheels
RUN apt-get update \
 && apt-get install -y --no-install-recommends libffi8 \
 && rm -rf /var/lib/apt/lists/*

# Install dependencies first (cached layer)
COPY requirements.txt ./
RUN pip install --no-cache-dir --upgrade pip \
 && pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY app ./app
COPY alembic ./alembic
COPY pyproject.toml ./

# Create directory for SQLite database
RUN mkdir -p /app/data

EXPOSE 8000

# Single worker + uvloop to stay within 512 MB free-tier RAM
CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000} --workers 1 --loop uvloop"]
