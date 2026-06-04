#!/usr/bin/env bash
# AIP Seed Corpus Ingest Script
# Ingests the AIP self-knowledge seed corpus into the personal corpus.
# Safe to run multiple times — ingestion is idempotent (duplicates skipped).
# 
# Run from the AIP_Brain root directory:
#   bash examples/seed_corpus/seed_ingest.sh

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

echo "=== AIP Seed Corpus Ingest ==="
echo "Repo root: $REPO_ROOT"
echo ""

cd "$REPO_ROOT"

# Ingest architecture Q&A
echo "Ingesting AIP architecture Q&A..."
uv run aip corpus ingest \
  examples/seed_corpus/conversations/aip_architecture_qa.json \
  --source-model aip_seed \
  --source-account aip_v0.1_seed \
  --export-date 2026-06-04

echo ""
echo "=== Seed corpus ingest complete ==="
echo ""
echo "Next steps:"
echo "  1. Run Beast tagging: uv run aip corpus tag --limit 500"
echo "  2. Review Beast tags: uv run aip status"
echo "  3. Start augmented chat and ask about AIP architecture"
echo ""
echo "The seed corpus teaches AIP about itself."
echo "Augmented chat can now answer questions about AIP's design,"
echo "actors, domains, and operational workflow."
