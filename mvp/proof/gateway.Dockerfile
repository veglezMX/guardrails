FROM ghcr.io/berriai/litellm:v1.104.2@sha256:3d88bd757134a00e8c00237b85a021ab1f6d54abb9c1df750f411dbe7256a500
COPY mvp/connector /app/proof-packages/connector
COPY mvp/guardrails /app/proof-packages/guardrails
ENV PYTHONPATH=/app/proof-packages LITELLM_LOCAL_MODEL_COST_MAP=True DISABLE_AIOHTTP_TRANSPORT=True SSL_CERT_FILE=/pki/ca.crt
ENTRYPOINT ["python", "-m", "connector.gateway_app"]
