#!/bin/bash
set -e

# Script to view the logs of the app and database containers

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

# Display logs with follow option
echo "Following logs for the $ENV environment (Ctrl+C to exit)"
if [ -f "$ENV_FILE" ]; then
  APP_ENV=$ENV docker compose --env-file "$ENV_FILE" logs -f app db
else
  APP_ENV=$ENV docker compose logs -f app db
fi
