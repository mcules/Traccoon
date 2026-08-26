# Deliberately its own image and not the one of the backend: this service has to keep
# standing while the backend is being replaced, and two services out of one image are
# recreated together.
FROM python:3.12-slim
WORKDIR /app
RUN pip install --no-cache-dir "fastapi==0.115.6" "uvicorn[standard]==0.34.0" "asyncpg==0.30.0"
COPY main.py /app/main.py
EXPOSE 8900
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8900", "--no-access-log"]
