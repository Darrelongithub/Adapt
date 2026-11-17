FROM python:3.12-slim

WORKDIR /app

COPY pyproject.toml ./
COPY src ./src
RUN pip install --no-cache-dir .

EXPOSE 8000

# Entrypoint refuses to start on missing or weak configuration.
CMD ["python", "-m", "adapt_engine.main"]
