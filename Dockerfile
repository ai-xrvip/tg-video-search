# syntax=docker/dockerfile:1
# TG Video Search Bot - Build: 20260713-01
FROM python:3.12-slim

# Install system dependencies for curl_cffi and cloudscraper
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Set working directory
WORKDIR /app

# Copy requirements first for better caching
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the rest of the application
COPY . .

# Create data directory for database
RUN mkdir -p data

# Expose health check port
EXPOSE 8080

# Start the bot
CMD ["python", "main.py"]
