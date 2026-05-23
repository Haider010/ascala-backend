# Use Python 3.11 slim image
FROM python:3.11-slim

# Set working directory
WORKDIR /app

# Copy requirements file
COPY requirements.txt .

# Install dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY . .

# Expose port (Railway uses PORT environment variable)
EXPOSE 8000

# Use Railway's PORT environment variable if set, otherwise default to 8000
CMD uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}
