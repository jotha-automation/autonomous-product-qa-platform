from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class DocumentTypeEnum(str, Enum):
    BRD = "BRD"
    PRD = "PRD"
    UC = "UC"
    AC = "AC"
    OTHER = "OTHER"


class DocumentMetadata(BaseModel):
    project: Optional[str] = None
    release: Optional[str] = None
    version: Optional[str] = None
    author: Optional[str] = None
    document_type: DocumentTypeEnum = DocumentTypeEnum.OTHER


class CanonicalSection(BaseModel):
    title: str
    content: str
    level: int = 1


class CanonicalDocument(BaseModel):
    id: str
    filename: str
    document_type: DocumentTypeEnum
    metadata: DocumentMetadata
    sections: List[CanonicalSection] = Field(default_factory=list)
    raw_text: str = ""
    tables: List[List[List[str]]] = Field(default_factory=list)
    extra: Dict[str, Any] = Field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return self.model_dump()
