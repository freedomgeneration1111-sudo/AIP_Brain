#!/bin/bash
# Simple restore script for AIP 0.1 (Phase 7 9.6)
set -e

if [ -z "$1" ]; then
  echo "Usage: $0 <backup.tar.gz>"
  exit 1
fi

BACKUP_FILE=$1

echo "Restoring from: $BACKUP_FILE"

tar -xzf "$BACKUP_FILE" --overwrite

echo "Restore complete. Restart the AIP service."
