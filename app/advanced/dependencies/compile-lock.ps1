$ErrorActionPreference='Stop'
# Mechanical lock generation in an owned temporary container. No model or credentials.
# pip-tools calls setuptools an "unsafe" dependency because it is a packaging
# tool. Including its pinned hashes is necessary for a clean, reproducible image;
# this does not disable pip's --require-hashes integrity check.
docker run --rm --name rag-local-qa-lock-20261001 --entrypoint sh --mount type=bind,source=D:\RAG-Local\app,target=/work --workdir /work/advanced/dependencies rag-local-backend:0.1.0 -c "python -m pip install --quiet --target /tmp/compiler pip-tools==7.5.2 pip==25.1.1 && PYTHONPATH=/tmp/compiler python -m piptools compile --quiet --generate-hashes --allow-unsafe --strip-extras --no-header --output-file requirements.lock requirements.in"
if ($LASTEXITCODE -ne 0) { throw 'ADVANCED_LOCK_COMPILATION_FAILED' }
