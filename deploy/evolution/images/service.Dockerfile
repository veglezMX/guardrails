FROM docker.io/library/python@sha256:cfe2e24a75302a15934d37c2d86412893c0aa934dc3a97cbb439d04c01890ca9
ARG WHEELHOUSE
COPY ${WHEELHOUSE}/ /wheels/
COPY evolution-requirements.lock /build/requirements.lock
RUN python -m pip install --no-index --find-links=/wheels --require-hashes -r /build/requirements.lock && rm -rf /wheels /root/.cache
COPY mvp/guardrails/ /app/mvp/guardrails/
COPY mvp/proof/ /app/mvp/proof/
COPY deploy/evolution/application.py /app/application.py
RUN chmod -R a+rX /app
ENV PYTHONPATH=/app/mvp PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 HOME=/state
USER 10001:10001
WORKDIR /config
ENTRYPOINT ["python", "/app/application.py"]
