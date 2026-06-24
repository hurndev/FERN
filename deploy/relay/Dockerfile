FROM python:3.12-slim

WORKDIR /app

COPY pyproject.toml README.md LICENSE ./
COPY src/ ./src/
COPY cli/ ./cli/
RUN pip install --no-cache-dir .

RUN mkdir -p /data

EXPOSE 8765

ENTRYPOINT ["fern-relay"]
CMD ["--config", "/data/config.json"]