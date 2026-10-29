FROM library/python:3.14.4-slim@sha256:2ca02f32b4d9d893863367ce07ec1972819f476dd38d8612f2a9cb6a41cbb727
WORKDIR /app
COPY scripts/session_monitoring/__init__.py scripts/session_monitoring/contract.py scripts/session_monitoring/server.py scripts/session_monitoring/details.py /app/scripts/session_monitoring/
USER 65532:65532
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
ENTRYPOINT ["python", "-m", "scripts.session_monitoring.server"]
