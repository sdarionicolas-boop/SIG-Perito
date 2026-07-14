"""Endpoint de monitoreo de tareas en segundo plano."""
from fastapi import APIRouter, HTTPException

from app.jobs import get_job
from app.schemas import JobOut

router = APIRouter(prefix="/api/jobs", tags=["jobs"])


@router.get("/{job_id}", response_model=JobOut)
def estado_job(job_id: str):
    job = get_job(job_id)
    if not job:
        raise HTTPException(404, "Job no encontrado.")
    return job
