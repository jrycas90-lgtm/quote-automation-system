#!/usr/bin/env bash
#
# dev-env.sh
#
# Sets environment variables needed for local development against this
# project's Docker Postgres container, which runs on port 5433 (not the
# default 5432) to avoid conflicting with other local projects' databases.
#
# IMPORTANT: this must be "sourced", not executed, so the environment
# variables persist in your current shell session:
#
#   source scripts/dev-env.sh
#
# Running it as "./scripts/dev-env.sh" will NOT work -- the variables
# would only apply inside the script's own subshell and disappear
# immediately after.
#
# Run this once per new terminal window before running any src/*.py
# script, scripts/*.py script, or psql command against this project's
# database.

export QUOTE_DB_HOST="localhost"
export QUOTE_DB_PORT="5433"
export QUOTE_DB_NAME="quote_automation"
export QUOTE_DB_USER="postgres"
export QUOTE_DB_PASSWORD="postgres"

# psql reads PGPASSWORD automatically, so this also unblocks psql commands
# without an interactive password prompt.
export PGPASSWORD="$QUOTE_DB_PASSWORD"

echo "Dev environment set:"
echo "  QUOTE_DB_HOST = $QUOTE_DB_HOST"
echo "  QUOTE_DB_PORT = $QUOTE_DB_PORT"
echo "  QUOTE_DB_NAME = $QUOTE_DB_NAME"
echo "  QUOTE_DB_USER = $QUOTE_DB_USER"
echo "  (QUOTE_DB_PASSWORD and PGPASSWORD are set but not printed)"
