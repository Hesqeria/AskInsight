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
    return {"token": token, "username": body.username, "role": user["role"]}
