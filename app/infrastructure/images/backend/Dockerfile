FROM python@sha256:090ba77e2958f6af52a5341f788b50b032dd4ca28377d2893dcf1ecbdfdfe203
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PYTHONPATH=/app/src
WORKDIR /app
COPY infrastructure/dependencies/backend/requirements.lock ./requirements.lock
RUN pip install --no-cache-dir --require-hashes -r requirements.lock && useradd --uid 10001 --create-home rag
RUN mkdir /usage /objects && chown 10001:10001 /usage /objects
COPY --chown=10001:10001 src ./src
USER 10001:10001
CMD ["python", "-m", "uvicorn", "rag_app.api:app", "--host", "0.0.0.0", "--port", "8000", "--no-access-log", "--timeout-graceful-shutdown", "15"]
