#!/usr/bin/env python3
"""
Claude Export Ingestion Script for AIP_Brain
"""
import sys
import json
import zipfile
import tempfile
import shutil
import subprocess
from pathlib import Path

def main(zip_path: str, project_name: str):
    zip_path = Path(zip_path).resolve()
    
    if not zip_path.exists():
        print(f"Error: File not found: {zip_path}")
        sys.exit(1)

    print(f"Processing Claude export: {zip_path.name}")

    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir = Path(tmpdir)
        
        # Extract ZIP
        with zipfile.ZipFile(zip_path, 'r') as z:
            z.extractall(tmpdir)
        
        # Find conversations.json
        conv_file = None
        for f in tmpdir.rglob("conversations.json"):
            conv_file = f
            break
        
        if not conv_file:
            print("Error: conversations.json not found in the export.")
            sys.exit(1)

        with open(conv_file, "r", encoding="utf-8") as f:
            conversations = json.load(f)

        print(f"Found {len(conversations)} conversations.")

        # Convert to simple markdown files (one per conversation)
        output_dir = tmpdir / "ingest_ready"
        output_dir.mkdir()

        for i, convo in enumerate(conversations):
            title = convo.get("name", f"Conversation {i+1}")
            messages = convo.get("chat_messages", [])
            
            md_lines = [f"# {title}\n"]
            
            for msg in messages:
                role = msg.get("sender", "unknown")
                text = msg.get("text", "").strip()
                if text:
                    md_lines.append(f"**{role.capitalize()}:**\n{text}\n")

            md_content = "\n".join(md_lines)
            safe_title = "".join(c if c.isalnum() or c in " -_" else "_" for c in title)[:80]
            md_file = output_dir / f"{i:04d}_{safe_title}.md"
            md_file.write_text(md_content, encoding="utf-8")

        print(f"Converted {len(conversations)} conversations to markdown.")

        # Ingest using existing aip ingest command
        cmd = [
            "uv", "run", "aip", "ingest", "directory",
            str(output_dir),
            "--project", project_name
        ]
        
        print(f"Running: {' '.join(cmd)}")
        result = subprocess.run(cmd, capture_output=True, text=True)
        
        print(result.stdout)
        if result.returncode != 0:
            print("STDERR:", result.stderr)
            print("Ingestion failed.")
            sys.exit(1)
        else:
            print("Ingestion completed successfully.")

if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Usage: python ingest_claude.py <zip_file> <project_name>")
        sys.exit(1)
    
    main(sys.argv[1], sys.argv[2])
