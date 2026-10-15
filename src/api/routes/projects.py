import uuid
from typing import Dict

from fastapi import APIRouter, Depends

from src.api.deps import verify_api_key
from src.api.schemas import CreateProjectRequest, CreateProjectResponse

router = APIRouter(prefix="/projects", tags=["projects"])

_projects: Dict[str, dict] = {}


@router.post("", response_model=CreateProjectResponse, dependencies=[Depends(verify_api_key)])
async def create_project(body: CreateProjectRequest):
    project_id = str(uuid.uuid4())
    _projects[project_id] = {"id": project_id, "name": body.name, "drive_folder_id": body.drive_folder_id}
    return CreateProjectResponse(id=project_id, name=body.name, drive_folder_id=body.drive_folder_id)


@router.get("/{project_id}/traceability", dependencies=[Depends(verify_api_key)])
async def get_traceability(project_id: str):
    from src.memory.enhanced_memory import get_memory
    memory = get_memory()
    # Return latest traceability from any run scoped to project (simplified)
    return {"project_id": project_id, "matrix": memory.read(f"project:{project_id}:traceability") or []}
