"""Profile management (design §5.1 Profile, §8.1 Profile management page)."""

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field

from visualstore.api.deps import get_state, require_auth
from visualstore.profiles import ProfileInfo

router = APIRouter(prefix="/api/profiles", tags=["profiles"], dependencies=[Depends(require_auth)])


class ProfileOut(BaseModel):
    name: str
    display_name: str
    created_at: str
    sort_order: int

    @classmethod
    def of(cls, info: ProfileInfo) -> "ProfileOut":
        return cls(**info.__dict__)


class CreateProfile(BaseModel):
    name: str
    display_name: str = ""


class RenameProfile(BaseModel):
    display_name: str = Field(min_length=1)


class DeleteProfile(BaseModel):
    confirm_name: str


@router.get("")
def list_profiles(request: Request) -> list[ProfileOut]:
    return [ProfileOut.of(p) for p in get_state(request).registry.list()]


@router.post("", status_code=201)
def create_profile(body: CreateProfile, request: Request) -> ProfileOut:
    return ProfileOut.of(get_state(request).registry.create(body.name, body.display_name))


@router.patch("/{name}")
def rename_profile(name: str, body: RenameProfile, request: Request) -> ProfileOut:
    return ProfileOut.of(get_state(request).registry.rename(name, body.display_name))


@router.delete("/{name}")
def delete_profile(name: str, body: DeleteProfile, request: Request) -> dict:
    get_state(request).registry.delete(name, confirm_name=body.confirm_name)
    return {"ok": True}


@router.get("/{name}/usage")
def profile_usage(name: str, request: Request) -> dict:
    return {"bytes": get_state(request).registry.usage_bytes(name)}
