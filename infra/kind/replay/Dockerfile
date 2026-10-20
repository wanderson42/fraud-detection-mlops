FROM python:3.14.4-slim@sha256:2ca02f32b4d9d893863367ce07ec1972819f476dd38d8612f2a9cb6a41cbb727
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 HOME=/tmp OMP_NUM_THREADS=2 OPENBLAS_NUM_THREADS=2 MKL_NUM_THREADS=2
COPY infra/kind/replay/requirements.txt /tmp/requirements.txt
RUN python -m pip install --no-cache-dir --only-binary=:all: --no-deps --require-hashes -r /tmp/requirements.txt && python -m pip check && rm /tmp/requirements.txt
WORKDIR /app
COPY fraud_detection_mlops/ /app/fraud_detection_mlops/
COPY examples/serving_smoke.py /app/examples/serving_smoke.py
COPY scripts/platform/kind_replay_worker.py /app/scripts/platform/kind_replay_worker.py
USER 10001:10001
ENTRYPOINT ["python", "-m", "scripts.platform.kind_replay_worker"]
