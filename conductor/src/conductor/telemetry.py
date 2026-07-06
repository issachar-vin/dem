from fastapi import APIRouter, Response
from prometheus_client import CONTENT_TYPE_LATEST, Gauge, generate_latest

build_info = Gauge("conductor_build_info", "Conductor build info", ["version"])

router = APIRouter()


@router.get("/metrics")
async def metrics() -> Response:
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)
