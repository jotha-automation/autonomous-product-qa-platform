"""Mock Google Drive connector for local development."""
import uuid
from pathlib import Path
from typing import List, Dict, Any

from src.intake.parser import parse_text_file
from src.intake.normalizer import normalize


class MockGoogleDriveConnector:
    def __init__(self, fixtures_root: str | Path | None = None):
        self.fixtures_root = Path(fixtures_root or "tests/fixtures/documents")

    def list_files(self, folder_id: str) -> List[Dict[str, Any]]:
        if not self.fixtures_root.exists():
            return []
        return [
            {
                "id": f"mock-{p.stem}",
                "name": p.name,
                "mimeType": "text/plain",
                "path": str(p),
            }
            for p in sorted(self.fixtures_root.glob("*_sample.*"))
            if p.is_file()
        ] or [
            {
                "id": f"mock-{p.stem}",
                "name": p.name,
                "mimeType": "text/plain",
                "path": str(p),
            }
            for p in sorted(self.fixtures_root.glob("*"))
            if p.is_file()
        ]

    def download_and_parse(
        self, project_id: str, file_info: Dict[str, Any]
    ) -> Dict[str, Any]:
        path = Path(file_info.get("path", ""))
        doc_id = file_info.get("id", str(uuid.uuid4()))
        canonical = normalize(
            parse_text_file(path, project_id=project_id, document_id=doc_id)
        )
        d = canonical.model_dump()
        d["document_type"] = canonical.document_type.value
        d["project_id"] = project_id
        return d

    def sync_folder(self, project_id: str, folder_id: str) -> List[Dict[str, Any]]:
        return [
            self.download_and_parse(project_id, f)
            for f in self.list_files(folder_id)
        ]
