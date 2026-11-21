FROM python:3.13-slim
RUN pip install --no-cache-dir numpy==2.5.5
WORKDIR /app
COPY . .
ENTRYPOINT ["python3", "-u", "/app/main.py"]
