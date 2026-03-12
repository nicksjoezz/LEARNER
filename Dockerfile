FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Expose the port Flask runs on
EXPOSE 5000

# Command to run the application
# We use eventlet with gunicorn for SocketIO support
# Increased timeout to 600s to handle long initial sync/training periods on Railway
CMD ["gunicorn", "--worker-class", "eventlet", "-w", "1", "--timeout", "600", "--bind", "0.0.0.0:5000", "app:app"]
