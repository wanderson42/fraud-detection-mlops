FROM library/python:3.14.4-slim@sha256:2ca02f32b4d9d893863367ce07ec1972819f476dd38d8612f2a9cb6a41cbb727
WORKDIR /app
COPY scripts/monitoring /app/scripts/monitoring
COPY scripts/platform/kind_observability_state.py /app/scripts/platform/kind_observability_state.py
COPY references/gold_contract_v1.json /app/references/gold_contract_v1.json
USER 65532:65532
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
ENTRYPOINT ["python", "-m", "scripts.monitoring.monitoring_exporter"]
CMD ["--state", "/state/snapshot", "--host", "0.0.0.0", "--interval", "10"]
