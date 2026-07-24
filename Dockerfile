# Use an official Python runtime as a parent image
# Using slim-bullseye for a smaller image size based on Debian Bullseye
FROM python:3.12-slim-bullseye

# Set environment variables
# Ensures print statements and logs are sent straight to the terminal without buffering
ENV PYTHONUNBUFFERED=1
# Only affects the timestamps logging prints, so `docker logs` reads in local time.
# The schedule and every group date/time window come from cadence.timezone in the
# config, not from this — the zone data itself arrives via the tzdata dependency.
ENV TZ=America/New_York

# Set the working directory in the container
WORKDIR /app

# Copy the package sources and project metadata
COPY pyproject.toml ./
COPY src ./src

# Install the package (pulls deps from pyproject and registers the plex-home console script)
# --no-cache-dir reduces image size, --upgrade pip ensures pip is recent
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir .

# Run the daemon when the container starts.
# plex-home has subcommands (run/list/pin/unpin/move); the container runs the daemon.
# The YAML config is mounted at runtime (e.g. -v $(pwd)/config.yaml:/app/config.yaml);
# logs go to stdout, so no logs directory is needed.
CMD ["plex-home", "run"]
