FROM docker.io/library/python@sha256:cfe2e24a75302a15934d37c2d86412893c0aa934dc3a97cbb439d04c01890ca9 AS dependencies
COPY python.tar.gz /build/python.tar.gz
RUN --network=none python -c 'import hashlib,pathlib,tarfile; p=pathlib.Path("/build/python.tar.gz"); assert hashlib.file_digest(p.open("rb"),"sha256").hexdigest()=="4595c5589fff7bf0cb158d9a88a797e0d791fa33830770fcb7bf3f4b104feeae"; tarfile.open(p,"r:gz").extractall("/opt/classifier",filter="data")'
RUN --network=none /opt/classifier/python/bin/python3.13 -B -s -c 'import sys; assert sys.version_info[:3]==(3,13,16)' && /opt/classifier/python/bin/python3.13 -B -s -m venv /opt/classifier/venv
COPY wheels/ /build/wheels/
COPY requirements.lock /build/requirements.lock
RUN --network=none /opt/classifier/venv/bin/python -B -s -m pip --isolated install --no-index --find-links=/build/wheels --only-binary=:all: --require-hashes --no-cache-dir --no-compile -r /build/requirements.lock

FROM docker.io/library/python@sha256:cfe2e24a75302a15934d37c2d86412893c0aa934dc3a97cbb439d04c01890ca9
COPY --from=dependencies /opt/classifier/ /opt/classifier/
COPY app/ /app/
COPY model/ /opt/classifier/model/
RUN --network=none chmod -R a-w /opt/classifier /app
ENV PYTHONPATH=/app/mvp:/app PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 HF_HUB_DISABLE_TELEMETRY=1 TOKENIZERS_PARALLELISM=false OMP_NUM_THREADS=2 MKL_NUM_THREADS=2 CLASSIFIER_MODEL_DIRECTORY=/opt/classifier/model CLASSIFIER_TOKEN_FILE=/config/token CLASSIFIER_LANGUAGE_PROFILE=en
USER 10001:10001
WORKDIR /app
EXPOSE 8443
ENTRYPOINT ["/opt/classifier/venv/bin/python", "-B", "-s", "-m", "uvicorn", "services.classifier.app:create_app", "--factory", "--workers", "1", "--host", "0.0.0.0", "--port", "8443", "--loop", "asyncio", "--http", "h11", "--ws", "none", "--lifespan", "on", "--no-access-log", "--no-proxy-headers", "--limit-concurrency", "33", "--timeout-keep-alive", "2", "--h11-max-incomplete-event-size", "16384", "--timeout-graceful-shutdown", "5", "--ssl-certfile", "/config/tls.crt", "--ssl-keyfile", "/config/tls.key"]
