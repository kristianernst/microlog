FROM python:3.12-slim

WORKDIR /workspace/examples/otel/app

COPY examples/otel/app/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt

COPY examples/otel/app/main.py main.py

CMD ["python", "main.py"]
