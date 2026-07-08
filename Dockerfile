# syntax=docker/dockerfile:1
FROM python:3.12-slim

# OpenShift assigns an arbitrary non-root UID at runtime (in the root group, 0).
# We install everything system-wide and make the app dir group-writable so it
# works regardless of the UID it is run with.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Install Python dependencies first to leverage layer caching (pip only).
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the application source.
COPY app ./app

# OpenShift-friendly permissions: arbitrary UID, group root.
RUN chgrp -R 0 /app && chmod -R g=u /app

USER 1001

EXPOSE 8080

# Single worker: the in-process background cache thread should not be duplicated
# across workers. Scale horizontally via replicas instead.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080"]
