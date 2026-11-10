# syntax=docker/dockerfile:1
# Reviewed Python 3.12 Debian image; update digest and revalidate deliberately.
FROM python:3.12-slim-bookworm@sha256:935c3c5b12357a7e10caa9c1e6ca5692344d97cf229832ef584be2cec9fc03df
ENV PATH=/opt/venv/bin:$PATH PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /app
# .dockerignore is an explicit source allowlist; no local data or credentials.
COPY --chmod=0555 . ./
# Optional managed-environment CA is a build secret, never an image layer.
RUN --mount=type=secret,id=proxy_ca \
    if [ -f /run/secrets/proxy_ca ]; then export PIP_CERT=/run/secrets/proxy_ca; fi; \
    python -m venv /opt/venv && \
    /opt/venv/bin/pip install --require-hashes --only-binary=:all: -r requirements.lock
USER 65532:65532
EXPOSE 8080
CMD ["python", "entrypoint.py"]
