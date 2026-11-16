#!/bin/bash
set -e

# Script to stop and remove the app and database containers (the data volumes are kept)

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

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
ENV_FILE="$PROJECT_ROOT/.env.$ENV"

cd "$PROJECT_ROOT"

echo "Stopping containers for the $ENV environment"
if [ -f "$ENV_FILE" ]; then
    APP_ENV=$ENV docker compose --env-file "$ENV_FILE" down
else
    APP_ENV=$ENV docker compose down
fi
