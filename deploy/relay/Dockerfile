FROM python:3.12-slim

WORKDIR /app

COPY pyproject.toml README.md LICENSE ./
COPY src/ ./src/
COPY cli/ ./cli/
RUN pip install --no-cache-dir .

COPY deploy/relay/relay-entrypoint.sh /usr/local/bin/relay-entrypoint.sh
RUN chmod +x /usr/local/bin/relay-entrypoint.sh

RUN mkdir -p /data

EXPOSE 8765

ENTRYPOINT ["/usr/local/bin/relay-entrypoint.sh"]
CMD ["fern-relay", "--host", "0.0.0.0", "--port", "8765", "--store", "/data/relay.db", "--key-file", "/data/relay.key", "--name", "FERN Relay"]
