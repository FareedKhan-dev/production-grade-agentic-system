#!/bin/bash
set -e

# Script to securely build Docker images without exposing secrets in build output

if [ $# -ne 1 ]; then
    echo "Usage: $0 <environment>"
    echo "Environments: development, staging, production"
    exit 1
fi

ENV=$1

# Validate environment
if [[ ! "$ENV" =~ ^(development|staging|production)$ ]]; then
    echo "Invalid environment. Must be one of: development, staging, production"
    exit 1
fi

echo "Building Docker image for $ENV environment"

# Check if env file exists
ENV_FILE=".env.$ENV"
if [ ! -f "$ENV_FILE" ]; then
    echo "Warning: $ENV_FILE not found. Creating from .env.example"
    if [ ! -f .env.example ]; then
        echo "Error: .env.example not found"
        exit 1
    fi
    cp .env.example "$ENV_FILE"
    echo "Please update $ENV_FILE with your configuration before running the container"
fi

echo "Loading environment variables from $ENV_FILE (secrets masked)"

# Securely load environment variables
set -a
source "$ENV_FILE"
set +a

# Print confirmation with masked values
echo "Environment: $ENV"
# Add a helper to mask any set values
mask_env() {
    local value="$1"
    if [ -z "$value" ]; then
        echo "Not set"
    else
        echo "********"
    fi
}

echo "Environment: $ENV"
# Mask database connection metadata instead of printing it directly
echo "Database host: $(mask_env "${POSTGRES_HOST:-${DB_HOST:-}}")"
echo "Database port: $(mask_env "${POSTGRES_PORT:-${DB_PORT:-}}")"
echo "Database name: $(mask_env "${POSTGRES_DB:-${DB_NAME:-}}")"
echo "Database user: $(mask_env "${POSTGRES_USER:-${DB_USER:-}}")"

# Build the Docker image. Secrets are not build arguments: build arguments can end up in the
# image's history, and the container receives the secrets at runtime from the env file instead.
docker build --no-cache \
    --build-arg APP_ENV="$ENV" \
    -t fastapi-langgraph-template:"$ENV" .

echo "Docker image fastapi-langgraph-template:$ENV built successfully"
