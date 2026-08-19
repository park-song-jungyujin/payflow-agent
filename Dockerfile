FROM python:3.12-slim

# uv.lock의 payflow-backend git 의존성을 받으려면 git 바이너리가 필요하다.
RUN apt-get update && apt-get install -y --no-install-recommends git && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir uv

WORKDIR /app

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

COPY main.py ./
COPY claimant/ claimant/
COPY executor/ executor/
COPY safety/ safety/
COPY shared/ shared/

ENV PATH="/app/.venv/bin:$PATH"

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8080"]
