FROM node@sha256:d32cdf619f63fe0471182d08996dd516c6275bb5fd31ae06e55a570bd9e1ad43 AS docs-assets
WORKDIR /assets
COPY web-assets/package.json web-assets/package-lock.json ./
RUN npm ci --ignore-scripts --no-audit --no-fund

FROM python@sha256:090ba77e2958f6af52a5341f788b50b032dd4ca28377d2893dcf1ecbdfdfe203
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PYTHONPATH=/app/src OTEL_SDK_DISABLED=true ADK_SUPPRESS_EXPERIMENTAL_WARNINGS=true
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends tesseract-ocr tesseract-ocr-por fonts-dejavu-core && rm -rf /var/lib/apt/lists/*
COPY requirements.txt /app/requirements.txt
COPY requirements.lock /app/requirements.lock
RUN pip install --no-cache-dir -r requirements.lock && pip check && useradd --uid 10001 --create-home clinic
COPY --chown=10001:10001 src /app/src
COPY --chown=10001:10001 examples /app/examples
COPY --chown=10001:10001 data /app/data
COPY --chown=10001:10001 tests /app/tests
COPY --chown=10001:10001 tools /app/tools
COPY --chown=10001:10001 docs /app/docs
COPY --chown=10001:10001 docker-compose.yml /app/docker-compose.yml
COPY --from=docs-assets /assets/node_modules/swagger-ui-dist/swagger-ui-bundle.js /app/static/docs/swagger-ui-bundle.js
COPY --from=docs-assets /assets/node_modules/swagger-ui-dist/swagger-ui.css /app/static/docs/swagger-ui.css
COPY --from=docs-assets /assets/node_modules/swagger-ui-dist/favicon-32x32.png /app/static/docs/favicon-32x32.png
COPY --from=docs-assets /assets/node_modules/swagger-ui-dist/LICENSE /app/static/docs/LICENSE
RUN mkdir /state /artifacts /samples && chown -R 10001:10001 /state /artifacts /samples && python tools/make_samples.py
USER 10001:10001
CMD ["python", "-m", "clinic_adk.cli", "--help"]
