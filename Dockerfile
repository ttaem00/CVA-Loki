FROM python:3.12.15-slim-bookworm

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates git ffmpeg libgomp1 libsndfile1 \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 10001 loki \
    && useradd --uid 10001 --gid 10001 --create-home --shell /bin/sh loki \
    && mkdir /workspace \
    && chown 10001:10001 /workspace

ENV HOME=/home/loki \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

COPY --chown=10001:10001 . /opt/loki

USER 10001:10001
WORKDIR /workspace

ENTRYPOINT ["python", "/opt/loki/install.py", "--workspace", "/workspace"]
CMD ["--mode", "skill"]
