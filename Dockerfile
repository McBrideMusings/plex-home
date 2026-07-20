# Use an official Python runtime as a parent image
# Using slim-bullseye for a smaller image size based on Debian Bullseye
FROM python:3.12-slim-bullseye

# Set environment variables
# Ensures print statements and logs are sent straight to the terminal without buffering
ENV PYTHONUNBUFFERED=1

# Set the working directory in the container
WORKDIR /app

# Copy the requirements file into the container at /app
COPY requirements.txt .

# Install any needed packages specified in requirements.txt
# --no-cache-dir reduces image size, --upgrade pip ensures pip is recent
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# Copy the application modules into the container at /app
COPY main.py config.py eligibility.py plex_client.py history.py resolver.py pinning.py ordering.py webhook.py ./

# Define the command to run the daemon when the container starts.
# The YAML config is mounted at runtime (e.g. -v $(pwd)/config.yaml:/app/config.yaml);
# logs go to stdout, so no logs directory is needed.
CMD ["python", "main.py"]