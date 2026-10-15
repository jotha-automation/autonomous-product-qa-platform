from typing import Optional

from pydantic import BaseModel, Field


class CreateProjectRequest(BaseModel):
    name: str
    drive_folder_id: Optional[str] = None


class CreateProjectResponse(BaseModel):
    id: str
    name: str
    drive_folder_id: Optional[str] = None


class RunRequest(BaseModel):
    project_id: str = "demo"
    mode: str = Field(default="full", description="phase1 | full")
    documents: list = Field(default_factory=list)
    document_paths: list = Field(default_factory=list)
    drive_folder_id: Optional[str] = Field(
        default=None,
        description="Project folder ID under APTP (optional if use_system_root)",
    )
    use_system_root: bool = Field(
        default=False,
        description="Sync from GOOGLE_DRIVE_SYSTEM_ROOT_FOLDER_ID (APTP/)",
    )
    project_folder_name: Optional[str] = Field(
        default=None,
        description="Filter to one child folder under APTP, e.g. APTP-JSH-Portfolio-R1",
    )
    local_intake_root: Optional[str] = Field(
        default=None,
        description="Local project root for discovery (Drive is the primary intake source)",
    )
    force_reprocess: bool = Field(
        default=False,
        description="Re-parse all files even if registry shows no changes",
    )
    run_id: Optional[str] = None
    metadata: dict = Field(default_factory=dict)


class RunResponse(BaseModel):
    run_id: str
    status: str
    stage: str
    duration_seconds: float = 0
    results: dict = Field(default_factory=dict)
    error: Optional[str] = None
