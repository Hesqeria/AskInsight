from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from app.core.auth import create_token, USERS, verify_password

auth_router = APIRouter()


class LoginSchema(BaseModel):
    username: str
    password: str


@auth_router.post("/api/login")
async def login(body: LoginSchema):
    user = USERS.get(body.username)
    if not user or not verify_password(body.password, user["password_hash"]):
        raise HTTPException(status_code=401, detail="Invalid username or password")
    token = await create_token(body.username)
    # SQLBot #1355 practice: flag well-known default credentials so the
    # client can nag the operator to change them.
    import os
    default_pw = os.getenv("ADMIN_PASSWORD", "change-me")
    must_change = (body.username == "admin"
                   and body.password == default_pw == "change-me")
    if must_change:
        from app.core.log import logger
        logger.warning("admin is logging in with the DEFAULT password - "
                       "change ADMIN_PASSWORD in .env before production")
    return {"token": token, "username": body.username, "role": user["role"],
            "must_change_password": must_change}
