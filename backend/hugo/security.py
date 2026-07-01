import hmac

from fastapi import HTTPException, Security, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from .config import get_settings

api_bearer = HTTPBearer(
    auto_error=False,
    scheme_name="HugoApiToken",
    bearerFormat="HUGO_API_TOKEN",
    description="Bearer token configured with HUGO_API_TOKEN.",
)
agent_bearer = HTTPBearer(
    auto_error=False,
    scheme_name="HermesAgentToken",
    bearerFormat="HUGO_AGENT_TOKEN",
    description="Bearer token configured with HUGO_AGENT_TOKEN for internal agent endpoints.",
)


def _verify(credentials: HTTPAuthorizationCredentials | None, expected: str) -> None:
    if (
        credentials is None
        or credentials.scheme.lower() != "bearer"
        or not hmac.compare_digest(credentials.credentials, expected)
    ):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid bearer token")


async def require_api_token(
    credentials: HTTPAuthorizationCredentials | None = Security(api_bearer),
) -> None:
    _verify(credentials, get_settings().api_token)


async def require_agent_token(
    credentials: HTTPAuthorizationCredentials | None = Security(agent_bearer),
) -> None:
    _verify(credentials, get_settings().agent_token)
