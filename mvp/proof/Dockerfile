FROM python:3.14.8-slim@sha256:f85c5697265c178cc6887276c55fe16cf3d14ca35c3df6a5eab3b360534a55d2
RUN apt-get update && apt-get install -y --no-install-recommends openssl ca-certificates && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY dev-requirements.lock /app/dev-requirements.lock
COPY .proof/wheelhouse /wheelhouse
RUN python -m pip install --no-index --find-links=/wheelhouse -r dev-requirements.lock
COPY pyproject.toml /app/pyproject.toml
COPY mvp /app/mvp
RUN python -m pip install --no-deps --no-build-isolation /app
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
CMD ["python", "-m", "uvicorn", "guardrails.runtime.app:create_app", "--factory", "--host", "0.0.0.0", "--port", "8444", "--ssl-certfile", "/pki/runtime.crt", "--ssl-keyfile", "/pki/runtime.key", "--no-access-log"]
