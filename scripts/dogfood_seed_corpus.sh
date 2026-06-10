#!/usr/bin/env bash
# Dogfood Seed Corpus — Ingest AIP's own documentation into its knowledge base.
#
# Sprint 9: This script ingests the AIP project's own documentation,
# making the system self-aware of its architecture, decisions, and status.
#
# Usage:
#   bash scripts/dogfood_seed_corpus.sh [--db-path DB_PATH]
#
# What gets ingested:
#   - Architecture docs (ARCHITECTURE.md, README.md)
#   - Sprint summaries and changelogs (CHANGELOG.md)
#   - Governance (AIP_GOVERNANCE.md)
#   - Roadmap (ROADMAP.md)
#   - Config docs (CONFIGURATION.md)
#   - Implementation status (implementation_status.md, STATUS.md)
#   - ADRs (all ADR-*.md files)
#   - Dogfood guide (DOGFOOD_READY.md)
#   - Domain registry (beast_domain_registry_v1.md)
#   - API reference (API_REFERENCE.md)
#   - Tech debt (TECH_DEBT.md)
#   - Deployment guide (DEPLOYMENT_GUIDE.md, DEVELOPER_GUIDE.md)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

# Default DB path
DB_PATH="${1:-}"
if [ -z "$DB_PATH" ]; then
    DB_PATH="${AIP_DB_PATH:-db/state.db}"
fi

echo "=== AIP Dogfood Seed Corpus Ingestion ==="
echo "Project dir: $PROJECT_DIR"
echo "DB path: $DB_PATH"
echo ""

INGESTED=0
SKIPPED=0
FAILED=0

ingest_file() {
    local filepath="$1"
    local label="${2:-$(basename "$filepath")}"

    if [ ! -f "$filepath" ]; then
        echo "  SKIP: $label (file not found: $filepath)"
        SKIPPED=$((SKIPPED + 1))
        return
    fi

    echo -n "  Ingesting: $label ... "
    if uv run aip corpus ingest "$filepath" --source-model document --source-account dogfood_seed --db-path "$DB_PATH" 2>/dev/null; then
        echo "OK"
        INGESTED=$((INGESTED + 1))
    else
        echo "FAILED"
        FAILED=$((FAILED + 1))
    fi
}

ingest_directory() {
    local dirpath="$1"
    local label="${2:-$(basename "$dirpath")}"

    if [ ! -d "$dirpath" ]; then
        echo "  SKIP: $label (directory not found: $dirpath)"
        SKIPPED=$((SKIPPED + 1))
        return
    fi

    echo "  Ingesting directory: $label"
    if uv run aip corpus ingest "$dirpath" --source-model document --source-account dogfood_seed --recursive --db-path "$DB_PATH" 2>/dev/null; then
        INGESTED=$((INGESTED + 1))
    else
        FAILED=$((FAILED + 1))
    fi
}

echo "--- Architecture & Core Docs ---"
ingest_file "$PROJECT_DIR/docs/ARCHITECTURE.md" "Architecture"
ingest_file "$PROJECT_DIR/README.md" "Project README"
ingest_file "$PROJECT_DIR/docs/README.md" "Docs Index"

echo ""
echo "--- Sprint Summaries & Changelog ---"
ingest_file "$PROJECT_DIR/docs/CHANGELOG.md" "Changelog"

echo ""
echo "--- Governance & Roadmap ---"
ingest_file "$PROJECT_DIR/AIP_GOVERNANCE.md" "Governance"
ingest_file "$PROJECT_DIR/ROADMAP.md" "Roadmap"

echo ""
echo "--- Configuration & Status ---"
ingest_file "$PROJECT_DIR/docs/CONFIGURATION.md" "Configuration Reference"
ingest_file "$PROJECT_DIR/docs/implementation_status.md" "Implementation Status"
ingest_file "$PROJECT_DIR/STATUS.md" "System Status"

echo ""
echo "--- ADRs (Architecture Decision Records) ---"
ingest_directory "$PROJECT_DIR/docs/decisions" "ADRs"

echo ""
echo "--- Dogfood Guide ---"
ingest_file "$PROJECT_DIR/DOGFOOD_READY.md" "Dogfood Guide"

echo ""
echo "--- Domain Registry ---"
ingest_file "$PROJECT_DIR/docs/beast_domain_registry_v1.md" "Domain Registry"

echo ""
echo "--- API Reference ---"
ingest_file "$PROJECT_DIR/docs/API_REFERENCE.md" "API Reference"

echo ""
echo "--- Technical Debt ---"
ingest_file "$PROJECT_DIR/TECH_DEBT.md" "Tech Debt"

echo ""
echo "--- Deployment & Development Guides ---"
ingest_file "$PROJECT_DIR/docs/DEPLOYMENT_GUIDE.md" "Deployment Guide"
ingest_file "$PROJECT_DIR/docs/DEVELOPER_GUIDE.md" "Developer Guide"

echo ""
echo "--- Entity Aliases ---"
ingest_file "$PROJECT_DIR/docs/entity_aliases.md" "Entity Aliases"

echo ""
echo "=== Dogfood Seed Corpus Complete ==="
echo "Ingested: $INGESTED"
echo "Skipped:  $SKIPPED"
echo "Failed:   $FAILED"
echo ""
echo "Run 'uv run aip corpus status --db-path $DB_PATH' to verify."
echo "Run 'uv run aip corpus audit --db-path $DB_PATH' for full audit."
