FROM python:3.12-slim-bookworm

# Prevent python from buffering stdout/stderr
ENV PYTHONUNBUFFERED=1

# Install system dependencies including those needed for networking and playwright
RUN apt-get update && apt-get install -y \
    iputils-ping \
    traceroute \
    telnet \
    curl \
    openssh-client \
    git \
    && rm -rf /var/lib/apt/lists/*

# Install uv for fast dependency management
RUN pip install uv

WORKDIR /app

# Copy dependency files
COPY pyproject.toml uv.lock /app/

# Install dependencies using uv system environment (excluding eval tools)
# Note: Since trulens is moved/removed from pyproject.toml, this solve will succeed.
RUN uv pip install --system -r pyproject.toml

# Install Playwright browsers
RUN playwright install --with-deps chromium



# Copy application source code
COPY . /app/

# Install the application package in editable mode
RUN uv pip install --system -e .

# Entrypoint via the CLI
ENTRYPOINT ["workerxy"]
