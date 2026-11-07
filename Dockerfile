# Харнесс openJiuwen Challenge. Базовый образ закреплён по digest (python 3.12.16, Debian 13).
FROM python:3.12-slim@sha256:4840d514c583a4d3d8cad3940e68dee6f0f6f3635c12fcd0a3d74c21a5b93a54

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    HOME=/tmp \
    HARNESS_LOG_DIR=/tmp/harness-logs

WORKDIR /app
COPY requirements.lock /app/requirements.lock
RUN pip install --no-deps -r /app/requirements.lock

COPY config /app/config
COPY prompts /app/prompts
COPY harness /app/harness
COPY run_agent.py /app/run_agent.py
# байткод собираем при сборке: в контейнере ФС только для чтения
RUN python -m compileall -q /app /usr/local/lib/python3.12/site-packages

# openjiuwen при импорте создаёт ./logs в текущей папке — запускаемся из /tmp (tmpfs, доступен на запись)
WORKDIR /tmp
CMD ["python", "/app/run_agent.py", "--tasks-dir", "/data/tasks"]
