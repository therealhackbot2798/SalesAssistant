"""
Sales Assist – Stage 2 console runner (Document Ingestion).

Provides a simple CLI to ingest a folder of documents or query the FAISS index.

Usage:
    python -m sales_assist.ingest <folder_path>
    python -m sales_assist.ingest --query "What is the pricing?"
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from sales_assist import config
from sales_assist.docs.index import DocumentIndex

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("sales_assist")

# Hide httpx / openai verbose logs
logging.getLogger("httpx").setLevel(logging.WARNING)


def main() -> None:
    parser = argparse.ArgumentParser(description="Sales Assist - Document Ingestion")
    parser.add_argument(
        "folder", 
        nargs="?", 
        help="Path to the folder containing documents to ingest."
    )
    parser.add_argument(
        "-q", "--query", 
        type=str, 
        help="Test the index by querying for the most relevant chunks."
    )
    parser.add_argument(
        "--clear", 
        action="store_true", 
        help="Clear the entire index before starting."
    )

    args = parser.parse_args()

    # 1. Validate config
    errors = config.validate()
    # We don't strictly need Sarvam here, just OpenAI
    if not config.OPENAI_API_KEY:
        errors.append("OPENAI_API_KEY is not set in .env")
        
    if errors:
        for e in errors:
            logger.error("Config error: %s", e)
        sys.exit(1)

    logger.info("=== Sales Assist – Stage 2: Document Ingestion ===")

    # 2. Initialize index
    idx = DocumentIndex()

    if args.clear:
        idx.clear()

    # 3. Ingest if folder provided
    if args.folder:
        folder = Path(args.folder)
        if not folder.exists() or not folder.is_dir():
            logger.error("Folder not found or not a directory: %s", folder)
            sys.exit(1)
            
        logger.info("Ingesting documents from: %s", folder)
        summary = idx.ingest_folder(folder)
        summary.print_report()

    # 4. Test query if provided
    if args.query:
        logger.info("Searching for: '%s'", args.query)
        results = idx.search(args.query, top_k=3)
        
        print("\n" + "=" * 60)
        print(f"  🔍 TOP RESULTS FOR: '{args.query}'")
        print("=" * 60)
        if not results:
            print("  No results found. (Is the index empty?)")
        
        for i, (meta, score) in enumerate(results, 1):
            print(f"\n[{i}] Source: {meta.source} | Page: {meta.page} | Score: {score:.4f}")
            print("-" * 60)
            print(meta.text)
        print("=" * 60 + "\n")
        
    if not args.folder and not args.query and not args.clear:
        parser.print_help()


if __name__ == "__main__":
    main()
