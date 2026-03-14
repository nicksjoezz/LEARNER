FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Expose the port Flask runs on
EXPOSE 5000

# Using standard gthread worker for better threading support
CMD ["gunicorn", "--worker-class", "gthread", "-w", "1", "--threads", "10", "--timeout", "600", "--bind", "0.0.0.0:5000", "app:app"]
