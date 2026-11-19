from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_session
from app.services import link_service

router = APIRouter()


@router.get("/{slug}", response_class=RedirectResponse)
async def redirect_to_target(
    slug: str,
    session: AsyncSession = Depends(get_session),
) -> RedirectResponse:
    """Redirect a short slug to its target URL."""
    link = await link_service.get_link(session, slug)
    if link is None:
        raise HTTPException(status_code=404, detail="Link not found")
    if link_service.is_expired(link):
        raise HTTPException(status_code=410, detail="Link has expired")
    return RedirectResponse(url=link.target_url, status_code=302)
