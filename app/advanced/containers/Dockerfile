FROM python@sha256:090ba77e2958f6af52a5341f788b50b032dd4ca28377d2893dcf1ecbdfdfe203
WORKDIR /advanced
COPY advanced/dependencies/requirements.lock /tmp/advanced-requirements.lock
RUN pip install --no-cache-dir --require-hashes -r /tmp/advanced-requirements.lock && pip check && useradd --uid 10001 --create-home rag && mkdir /tasks /usage && chown 10001:10001 /tasks /usage
COPY --chown=10001:10001 src /app/src
COPY --chown=10001:10001 advanced /advanced
ENV PYTHONPATH=/advanced/models:/advanced/tasks:/advanced/evaluation:/advanced/dependencies:/advanced/checks:/app/src DEEPEVAL_TELEMETRY_OPT_OUT=YES LANGSMITH_TRACING=false
USER 10001:10001
CMD ["python", "-m", "task_lab"]
