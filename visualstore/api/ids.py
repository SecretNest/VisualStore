"""Path-parameter id parsing shared by the profile-scoped routers."""

import uuid

from fastapi import HTTPException


def parse_id(text: str) -> bytes:
    try:
        return uuid.UUID(text).bytes
    except ValueError:
        raise HTTPException(status_code=404, detail="No such item.") from None
