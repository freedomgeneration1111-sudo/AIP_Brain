#!/bin/bash

IMPORT_DIR="$HOME/AIP_ExoIntelligence/import"
ARCHIVE_DIR="$HOME/AIP_ExoIntelligence/archive"
FAILED_DIR="$HOME/AIP_ExoIntelligence/failed"
LOGS_DIR="$HOME/AIP_ExoIntelligence/logs"

mkdir -p "$ARCHIVE_DIR" "$FAILED_DIR" "$LOGS_DIR"

echo "[$(date)] Starting import watcher..."

inotifywait -m -e close_write,moved_to --format '%f' "$IMPORT_DIR" | while read FILE
do
    FULL_PATH="$IMPORT_DIR/$FILE"
    BASENAME="${FILE%.*}"          # filename without extension
    EXT="${FILE##*.}"              # file extension

    echo "[$(date)] New file detected: $FILE"

    # === Special handler for Claude exports ===
    if [[ "$FILE" == claude_*.zip ]]; then
        echo "[$(date)] Detected Claude export: $FILE"

        if uv run python scripts/ingest_claude.py "$FULL_PATH" "$BASENAME" >> "$LOGS_DIR/ingest.log" 2>&1; then
            mv "$FULL_PATH" "$ARCHIVE_DIR/"
            echo "[$(date)] Successfully ingested Claude export: $FILE" >> "$LOGS_DIR/ingest.log"
        else
            mv "$FULL_PATH" "$FAILED_DIR/"
            echo "[$(date)] FAILED to ingest Claude export: $FILE" >> "$LOGS_DIR/ingest.log"
        fi

    # === Handle other ZIP files ===
    elif [[ "$EXT" == "zip" ]]; then
        TEMP_DIR=$(mktemp -d)
        unzip -o "$FULL_PATH" -d "$TEMP_DIR" > /dev/null 2>&1

        if uv run aip ingest directory "$TEMP_DIR" --project "$BASENAME" >> "$LOGS_DIR/ingest.log" 2>&1; then
            mv "$FULL_PATH" "$ARCHIVE_DIR/"
            echo "[$(date)] Successfully ingested ZIP: $FILE" >> "$LOGS_DIR/ingest.log"
        else
            mv "$FULL_PATH" "$FAILED_DIR/"
            echo "[$(date)] FAILED to ingest ZIP: $FILE" >> "$LOGS_DIR/ingest.log"
        fi
        rm -rf "$TEMP_DIR"

    # === Handle normal files (PDF, JSON, HTML, TXT, etc.) ===
    else
        if uv run aip ingest file "$FULL_PATH" --project "$BASENAME" >> "$LOGS_DIR/ingest.log" 2>&1; then
            mv "$FULL_PATH" "$ARCHIVE_DIR/"
            echo "[$(date)] Successfully ingested: $FILE" >> "$LOGS_DIR/ingest.log"
        else
            mv "$FULL_PATH" "$FAILED_DIR/"
            echo "[$(date)] FAILED to ingest: $FILE" >> "$LOGS_DIR/ingest.log"
        fi
    fi
done
