import uuid
from fastapi import APIRouter, Depends, UploadFile, File

from src.api.deps import verify_api_key
from src.intake.parser import DocumentParser
from src.intake.normalizer import normalize

router = APIRouter(prefix="/api/v1/documents", tags=["documents"])
_parser = DocumentParser()


@router.post("/upload", dependencies=[Depends(verify_api_key)])
async def upload_document(project_id: str, file: UploadFile = File(...)):
    data = await file.read()
    doc_id = str(uuid.uuid4())
    canonical = normalize(
        _parser.parse_bytes(file.filename or "doc.txt", data, doc_id=doc_id)
    )
    canonical.metadata.project = project_id
    return canonical.model_dump()
