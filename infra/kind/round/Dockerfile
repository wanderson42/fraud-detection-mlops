FROM python:3.14.4-slim@sha256:2ca02f32b4d9d893863367ce07ec1972819f476dd38d8612f2a9cb6a41cbb727
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PYTHONPATH=/app HOME=/tmp TMPDIR=/tmp XDG_CACHE_HOME=/tmp/cache MLFLOW_DISABLE_TELEMETRY=true MLFLOW_DISABLE_AGENT_HINT=1 OMP_NUM_THREADS=2 OPENBLAS_NUM_THREADS=2 MKL_NUM_THREADS=2
COPY infra/kind/round/requirements.txt /tmp/requirements.txt
RUN python -m pip install --no-cache-dir --only-binary=:all: --no-deps --require-hashes -r /tmp/requirements.txt && python -m pip check && rm /tmp/requirements.txt
WORKDIR /app
COPY fraud_detection_mlops/ /app/fraud_detection_mlops/
COPY scripts/operational_round/ /app/scripts/operational_round/
COPY scripts/storage/ /app/scripts/storage/
COPY references/handbook_source.json references/silver_contract_v1.json references/gold_contract_v1.json references/temporal_protocol_v1.json references/baseline_protocol_v1.json references/operational_round_policy_v1.json /app/references/
USER 10001:10001
ENTRYPOINT ["python", "-m", "scripts.operational_round.training"]
