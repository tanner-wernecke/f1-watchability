FROM python:3.12-slim

WORKDIR /app

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy app
COPY . .

# Run
CMD gunicorn wsgi:app --bind 0.0.0.0:${PORT:-8080} --timeout 120 --workers 2
