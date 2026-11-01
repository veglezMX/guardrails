FROM ghcr.io/berriai/litellm@sha256:3d88bd757134a00e8c00237b85a021ab1f6d54abb9c1df750f411dbe7256a500
COPY mvp/guardrails/ /app/evolution/guardrails/
COPY mvp/connector/ /app/evolution/connector/
ENV PYTHONPATH=/app/evolution PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 HOME=/state EVOLUTION_GATEWAY_CONFIG_FILE=/config/gateway.json
USER 10001:10001
WORKDIR /config
ENTRYPOINT ["python", "-m", "connector.evolution_gateway"]
