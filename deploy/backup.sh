#!/bin/bash
# Simple backup script for AIP 0.1 (Phase 7 9.6)
set -e

BACKUP_DIR=${BACKUP_DIR:-./backups}
mkdir -p "$BACKUP_DIR"

TIMESTAMP=$(date +%Y%m%d-%H%M%S)
BACKUP_NAME="aip-backup-${TIMESTAMP}.tar.gz"

echo "Creating backup: $BACKUP_NAME"

tar -czf "$BACKUP_DIR/$BACKUP_NAME" \
  --exclude='__pycache__' \
  --exclude='*.pyc' \
  db/ \
  config/ \
  2>/dev/null || true

echo "Backup complete: $BACKUP_DIR/$BACKUP_NAME"
