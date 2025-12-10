from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_principal
from app.db.session import get_session
from app.schemas.auth import Principal, TokenExchangeRequest, TokenExchangeResponse
from app.services.token_service import get_or_exchange_token

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/exchange", response_model=TokenExchangeResponse)
async def exchange(
    body: TokenExchangeRequest,
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_session),
) -> TokenExchangeResponse:
    return await get_or_exchange_token(
        session=session, principal=principal, scopes=body.scopes, purpose=body.purpose
    )
