FROM python:3.14.4-slim@sha256:2ca02f32b4d9d893863367ce07ec1972819f476dd38d8612f2a9cb6a41cbb727
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PYTHONPATH=/app HOME=/tmp \
    MLFLOW_DISABLE_TELEMETRY=true DO_NOT_TRACK=true MLFLOW_DISABLE_AGENT_HINT=1
COPY infra/tracking/requirements.txt /tmp/requirements.txt
RUN python -m pip install --no-cache-dir --require-hashes -r /tmp/requirements.txt && rm /tmp/requirements.txt
WORKDIR /app
COPY scripts/tracking/ /app/scripts/tracking/
COPY scripts/platform/tracking_backup/ /app/scripts/platform/tracking_backup/
COPY fraud_detection_mlops/ /app/fraud_detection_mlops/
USER 65532:65532
CMD ["python", "-m", "scripts.platform.tracking_backup.worker", "runtime-check"]
