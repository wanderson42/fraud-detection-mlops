# syntax=docker/dockerfile:1
# The frozen release requires this exact Python version. A verified registry
# digest can also be supplied through PYTHON_IMAGE; both stages use the same base.
ARG PYTHON_IMAGE=python:3.14.4-slim-trixie
FROM ${PYTHON_IMAGE} AS builder

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    POETRY_NO_INTERACTION=1 \
    POETRY_VIRTUALENVS_CREATE=false \
    VIRTUAL_ENV=/opt/runtime

RUN python -m venv /opt/poetry \
    && /opt/poetry/bin/pip install poetry==2.4.3 \
    && python -m venv /opt/runtime
WORKDIR /build
COPY pyproject.toml poetry.lock README.md ./
RUN /opt/poetry/bin/poetry check --lock \
    && test "$(/opt/poetry/bin/poetry env info --path)" = /opt/runtime \
    && /opt/poetry/bin/poetry sync --only main --no-root
COPY fraud_detection_mlops/ fraud_detection_mlops/
RUN /opt/poetry/bin/poetry build --format wheel \
    && /opt/runtime/bin/pip install --no-deps dist/*.whl \
    && rm -rf /root/.cache

FROM ${PYTHON_IMAGE} AS runtime
ENV PATH=/opt/runtime/bin:$PATH \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    FRAUD_SERVING_RELEASE=/model \
    OMP_NUM_THREADS=4 \
    OPENBLAS_NUM_THREADS=4 \
    MKL_NUM_THREADS=4
COPY --from=builder /opt/runtime /opt/runtime
WORKDIR /app
USER 10001:10001
EXPOSE 8000
HEALTHCHECK --interval=5s --timeout=3s --start-period=30s --retries=3 \
    CMD ["/opt/runtime/bin/python", "-I", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/ready', timeout=2).read()"]
ENTRYPOINT ["/opt/runtime/bin/python", "-I", "-m", "uvicorn", "fraud_detection_mlops.serving.http_service:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000", "--workers", "1", "--no-access-log"]
