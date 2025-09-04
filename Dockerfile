FROM debian:bookworm-slim

ENV DEBIAN_FRONTEND=noninteractive \
    GLAMA_VERSION="1.0.0"

# Install system dependencies and Node.js
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    curl \
    git \
    && curl -fsSL https://deb.nodesource.com/setup_24.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && npm install -g mcp-proxy@5.5.4 pnpm@10.14.0 \
    && node --version

# Install Python via uv
RUN curl -LsSf https://astral.sh/uv/install.sh | UV_INSTALL_DIR="/usr/local/bin" sh \
    && uv python install 3.13 --default --preview \
    && ln -s $(uv python find) /usr/local/bin/python \
    && python --version

# Clean up apt cache
RUN apt-get clean && rm -rf /var/lib/apt/lists/* /tmp/* /var/tmp/*

# Set working directory
WORKDIR /app

# Clone SuperMCP repository
RUN git clone https://github.com/YakupAtahanov/SuperMCP . \
    && git checkout 3ce59d8705e1030005ff82b8aad9b69bafd03856

# Install Python MCP SDK with CLI tools
RUN uv pip install "mcp[cli]"

# Install any Node.js dependencies if needed
RUN if [ -f package.json ]; then pnpm install; fi

# Create directory for MCP servers
RUN mkdir -p available_mcps

# Expose any necessary ports (adjust as needed)
EXPOSE 3000

# Default command - you might want to change this based on how SuperMCP should run
CMD ["python", "SuperMCP.py"]