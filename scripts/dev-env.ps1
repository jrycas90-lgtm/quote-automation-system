# dev-env.ps1
#
# Sets environment variables needed for local development against this
# project's Docker Postgres container, which runs on port 5433 (not the
# default 5432) to avoid conflicting with other local projects' databases.
#
# IMPORTANT: this must be "dot-sourced", not just run, so the environment
# variables persist in your current PowerShell session:
#
#   . .\scripts\dev-env.ps1
#
# (Note the leading ". " before the path -- that's what makes it dot-sourced.
# Running it as ".\scripts\dev-env.ps1" without the leading dot will NOT work;
# the variables would only apply inside the script's own child scope and
# disappear immediately after.)
#
# Run this once per new PowerShell terminal window before running any
# src/*.py script, scripts/*.py script, or psql command against this
# project's database.

$env:QUOTE_DB_HOST = "localhost"
$env:QUOTE_DB_PORT = "5433"
$env:QUOTE_DB_NAME = "quote_automation"
$env:QUOTE_DB_USER = "postgres"
$env:QUOTE_DB_PASSWORD = "postgres"

# psql reads PGPASSWORD automatically, so this also unblocks psql commands
# without an interactive password prompt.
$env:PGPASSWORD = $env:QUOTE_DB_PASSWORD

Write-Host "Dev environment set:"
Write-Host "  QUOTE_DB_HOST = $env:QUOTE_DB_HOST"
Write-Host "  QUOTE_DB_PORT = $env:QUOTE_DB_PORT"
Write-Host "  QUOTE_DB_NAME = $env:QUOTE_DB_NAME"
Write-Host "  QUOTE_DB_USER = $env:QUOTE_DB_USER"
Write-Host "  (QUOTE_DB_PASSWORD and PGPASSWORD are set but not printed)"
