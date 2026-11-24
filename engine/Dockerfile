FROM python:3.12-slim

WORKDIR /app

COPY pyproject.toml ./
COPY src ./src
RUN pip install --no-cache-dir .

# Migrations and bootstrap ship with the image so `migrate` can run as
# adapt_owner; the runtime container never runs them.
COPY alembic.ini ./
COPY migrations ./migrations
COPY scripts ./scripts

EXPOSE 8000

# Entrypoint refuses to start on missing or weak configuration.
CMD ["python", "-m", "adapt_engine.main"]
