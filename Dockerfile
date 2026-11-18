FROM python:3.12.15-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY server.py .

EXPOSE 8633
CMD ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "8633", "--no-access-log", "--log-level", "warning"]
