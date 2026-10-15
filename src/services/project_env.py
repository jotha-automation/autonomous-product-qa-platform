"""Read/merge .env files for generated Playwright projects."""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Dict
from urllib.parse import urlparse

_ENV_LINE = re.compile(r"^([^#=\s]+)\s*=\s*(.*)$")


def origin_base_url(url: str | None) -> str:
    if not url:
        return "https://example.com"
    parsed = urlparse(url.strip())
    if not parsed.scheme:
        parsed = urlparse(f"https://{url.strip()}")
    return f"{parsed.scheme}://{parsed.netloc}" or "https://example.com"


def read_env_file(path: Path) -> Dict[str, str]:
    if not path.is_file():
        return {}
    values: Dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        match = _ENV_LINE.match(line.strip())
        if match:
            values[match.group(1)] = match.group(2).strip()
    return values


def write_env_file(path: Path, values: Dict[str, str]) -> None:
    lines = [f"{k}={v}" for k, v in sorted(values.items())]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    os.chmod(path, 0o600)


def merge_project_env(path: Path, updates: Dict[str, str]) -> Dict[str, str]:
    """Merge keys into project .env without dropping existing APTP_SECRET_* lines."""
    merged = read_env_file(path)
    merged.update({k: v for k, v in updates.items() if v is not None and str(v).strip()})
    write_env_file(path, merged)
    return merged


def strip_secret_env_vars(project_dir: Path) -> None:
    """Remove APTP_SECRET_* entries from project .env after evidence collection."""
    env_path = Path(project_dir) / ".env"
    if not env_path.is_file():
        return
    kept_lines: list[str] = []
    for line in env_path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            kept_lines.append(line)
            continue
        match = _ENV_LINE.match(stripped)
        if match and match.group(1).startswith("APTP_SECRET_"):
            continue
        kept_lines.append(line)
    env_path.write_text("\n".join(kept_lines) + ("\n" if kept_lines else ""), encoding="utf-8")
    os.chmod(env_path, 0o600)
