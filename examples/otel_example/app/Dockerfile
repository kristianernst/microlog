FROM python:3.12-slim

WORKDIR /workspace/examples/otel_example/app

COPY examples/otel_example/app/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt

COPY examples/otel_example/app/main.py main.py

CMD ["python", "main.py"]
