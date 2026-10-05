FROM python:3.12-slim

WORKDIR /app
COPY pyproject.toml README.md ./
COPY app ./app
COPY run.py ./run.py
RUN pip install --no-cache-dir .

ENV PORT=8000
EXPOSE 8000
CMD ["python", "run.py"]

