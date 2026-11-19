from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_session
from app.schemas.link import LinkCreate, LinkResponse
from app.services import link_service

router = APIRouter(prefix="/api/links", tags=["links"])


@router.post("", response_model=LinkResponse, status_code=status.HTTP_201_CREATED)
async def create_link(
    payload: LinkCreate,
    session: AsyncSession = Depends(get_session),
) -> LinkResponse:
    """Create a short link from a long URL."""
    try:
        link = await link_service.create_link(session, payload)
    except link_service.AliasConflictError:
        raise HTTPException(status_code=409, detail="Alias already in use")
    except link_service.SlugGenerationError:
        raise HTTPException(status_code=500, detail="Could not generate a unique slug")
    return link_service.link_to_response(link)
