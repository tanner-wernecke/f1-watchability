FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
CMD python -m gunicorn wsgi:app --bind 0.0.0.0:${PORT:-8080} --timeout 120 --workers 2
