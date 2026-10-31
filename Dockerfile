FROM python:3.11-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 INSTA_AUDIT_ENVIRONMENT=production
WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
COPY configs ./configs
RUN pip install --no-cache-dir . && useradd --create-home --uid 10001 appuser
USER appuser
EXPOSE 8080
CMD ["uvicorn", "insta_audit.api.main:app", "--host", "0.0.0.0", "--port", "8080"]
