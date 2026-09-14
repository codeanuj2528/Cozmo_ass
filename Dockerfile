# Cozmo AI Case Study - Production Docker Container
FROM python:3.11-slim

# System dependencies for OpenCV, Open3D, and geometry libraries
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    git \
    libgl1-mesa-glx \
    libglib2.0-0 \
    libgomp1 \
    libgeom-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy dependency specifications and install
COPY pyproject.toml .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -e ".[dev,ml]"

# Copy application source code
COPY . /app/

# Environment variables
ENV PYTHONPATH=/app/src
ENV PYTHONUNBUFFERED=1

# Default entry point CLI
ENTRYPOINT ["python3", "-m", "cozmo.cli"]
CMD ["--help"]
