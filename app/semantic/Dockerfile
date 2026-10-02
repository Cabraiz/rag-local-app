FROM python@sha256:090ba77e2958f6af52a5341f788b50b032dd4ca28377d2893dcf1ecbdfdfe203
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 TOKENIZERS_PARALLELISM=false
WORKDIR /srv
COPY requirements.lock ./requirements.lock
RUN pip install --no-cache-dir --require-hashes -r requirements.lock && useradd --uid 10001 --create-home embed
COPY model /models
COPY --from=policy retrieval/semantic_policy.py ./semantic_policy.py
COPY --from=dataset semantic-calibration-v1.json ./calibration.json
COPY engine.py calibrate.py api.py ./
RUN python calibrate.py
USER 10001:10001
CMD ["python","-m","uvicorn","api:app","--host","0.0.0.0","--port","8000","--no-access-log","--log-level","critical","--limit-concurrency","8"]
