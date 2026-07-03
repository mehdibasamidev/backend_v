FROM python:3.11-slim

# Set environment variables
ENV PYTHONDONTWRITEBYTECODE 1
ENV PYTHONUNBUFFERED 1

# Install OS-level dependencies
RUN apt-get update && apt-get install -y \
    wget \
    git \
    g++ \
    gcc \
    pkg-config \
    vim \
    curl \
    build-essential \
    postgresql-client \
    && rm -rf /var/lib/apt/lists/*

# Upgrade pip and tools
RUN pip install --upgrade pip setuptools wheel

# Set work directory
WORKDIR /app

# Copy requirements and install them
COPY requirements.txt .
RUN pip install -r requirements.txt

# Copy project files
COPY . .

# Ensure your shell script is executable
RUN chmod +x app.sh

# Default command
CMD ["./app.sh"]
