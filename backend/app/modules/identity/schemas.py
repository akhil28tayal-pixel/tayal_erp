import uuid

from pydantic import BaseModel, ConfigDict, EmailStr, Field


class LoginIn(BaseModel):
    phone: str = Field(min_length=6, max_length=20)
    password: str = Field(min_length=1, max_length=200)
    device_id: str | None = Field(default=None, max_length=100)
    use_cookie: bool = Field(default=False, description="Web: keep the refresh token in an HttpOnly cookie")


class RefreshIn(BaseModel):
    """Mobile sends the token in the body; the web client relies on the HttpOnly cookie."""

    refresh_token: str | None = Field(default=None, min_length=10)


class CompanyAccess(BaseModel):
    company_id: uuid.UUID
    company_code: str
    company_name: str
    roles: list[str]
    permissions: list[str]


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    phone: str
    email: str | None
    full_name: str
    is_active: bool
    employee_id: uuid.UUID | None


class TokenPair(BaseModel):
    access_token: str
    refresh_token: str | None = None
    token_type: str = "bearer"
    expires_in: int


class LoginOut(TokenPair):
    user: UserOut
    companies: list[CompanyAccess]


class MeOut(BaseModel):
    user: UserOut
    companies: list[CompanyAccess]


class UserListOut(UserOut):
    roles: list[str] = []
    godown_ids: list[uuid.UUID] = []


class UserCreate(BaseModel):
    phone: str = Field(min_length=6, max_length=20)
    email: EmailStr | None = None
    full_name: str = Field(min_length=1, max_length=200)
    password: str = Field(min_length=8, max_length=200)
    employee_id: uuid.UUID | None = None
    roles: list[str] = Field(default_factory=list, description="Role codes granted in the current company")
    godown_ids: list[uuid.UUID] = Field(default_factory=list)


class UserUpdate(BaseModel):
    email: EmailStr | None = None
    full_name: str | None = Field(default=None, max_length=200)
    employee_id: uuid.UUID | None = None


class RolesAssign(BaseModel):
    roles: list[str]
    godown_ids: list[uuid.UUID] | None = None


class RoleOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    code: str
    name: str
    is_system: bool
    permissions: list[str] = []


class PasswordReset(BaseModel):
    new_password: str = Field(min_length=8, max_length=200)


class PasswordChange(BaseModel):
    current_password: str
    new_password: str = Field(min_length=8, max_length=200)
