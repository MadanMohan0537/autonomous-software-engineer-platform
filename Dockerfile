FROM python:3.12-slim AS builder
WORKDIR /build
COPY pyproject.toml README.md LICENSE ./
COPY src ./src
RUN pip wheel --no-cache-dir --wheel-dir /wheels .

FROM python:3.12-slim
RUN useradd --create-home --uid 10001 ase && mkdir /data && chown ase:ase /data
WORKDIR /app
COPY --from=builder /wheels /wheels
RUN pip install --no-cache-dir /wheels/* && rm -rf /wheels
USER ase
ENV PORT=8000
ENV ASE_DATABASE_PATH=/data/ase.db
ENV ASE_FEEDBACK_PATH=/data/feedback.db
EXPOSE 8000
CMD ["uvicorn", "ase.api:app", "--host", "0.0.0.0", "--port", "8000"]
