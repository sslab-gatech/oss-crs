# Use Python 3.12 slim as the base image
FROM python:3.12-slim

# Set environment variables
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1

# Set the working directory inside the container
WORKDIR /app

# Install system dependencies if needed
RUN apt-get update && apt-get install -y \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements file first for better Docker layer caching
COPY pyproject.toml .

# Copy the key provisioner script
COPY key_provisioner.py .

# Install Python dependencies
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir .

# Make the script executable
RUN chmod +x key_provisioner.py

# Create keys directory for storing generated keys
RUN mkdir -p /keys

# Run as root to ensure write permissions to mounted volumes

# Health check to ensure the service is working
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD python -c "import requests; requests.get('http://localhost:8000/health', timeout=5)" || exit 1

# Run the key provisioner script
CMD ["python", "key_provisioner.py"]
