FROM python:3.11-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY . /app

# ✅ Damit "utils", "models", "services", "main", "worker" top-level sind
ENV PYTHONPATH=/app/src

EXPOSE 8000

# ✅ Dann importiert uvicorn wieder "main:app" (ohne src.)
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]