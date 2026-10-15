#!/usr/bin/env python3
"""One-shot generator for APTP platform source files."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def w(rel: str, content: str) -> None:
    p = ROOT / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content.strip() + "\n", encoding="utf-8")
    print(f"  {rel}")


def main() -> None:
    print("Bootstrapping APTP...")
    # Run via separate writes in repo - this file is a placeholder for incremental gen
    print("Use direct file writes from agent.")


if __name__ == "__main__":
    main()
