from fastapi import Depends, Header, HTTPException, status

from app.auth.tokens import validate_bearer
from app.schemas.auth import Principal


async def get_principal(authorization: str = Header(...)) -> Principal:
    if not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid auth header")
    token = authorization.split(" ", 1)[1]
    try:
        principal = await validate_bearer(token)
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc
    return principal
