"""FastAPI server: 2FA API + demo sayt. Ishga tushirish: uvicorn main:app"""
import hmac
import logging
import os
import threading
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import APIRouter, Depends, FastAPI, Header, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

import core

logging.basicConfig(level=logging.INFO)
core.load_env()

BASE = Path(__file__).parent
tf = None  # TwoFA obyekti, startda yaratiladi


@asynccontextmanager
async def lifespan(app):
    global tf
    token = os.getenv("BOT_TOKEN", "").strip()
    if not token:
        raise RuntimeError("BOT_TOKEN topilmadi. .env faylini to'ldiring (README ga qarang).")
    tf = core.build(token)
    stop = threading.Event()
    threading.Thread(target=core.poll_forever, args=(tf, stop), daemon=True).start()
    logging.info("Bot ishga tushdi: @%s", tf.bot_username)
    yield
    stop.set()


class UserReq(BaseModel):
    user_id: str


class VerifyReq(BaseModel):
    user_id: str
    code: str


def check_key(x_api_key: str = Header(default="")):
    key = os.getenv("API_KEY", "")
    if not key:
        raise HTTPException(503, "API_KEY sozlanmagan")
    if not hmac.compare_digest(x_api_key.encode(), key.encode()):
        raise HTTPException(401, "API kalit noto'g'ri")


def check_demo():
    if os.getenv("DEMO_MODE", "1") != "1":
        raise HTTPException(404, "Demo o'chirilgan")


def run(fn, *args):
    try:
        return fn(*args)
    except core.TwoFAError as e:
        raise HTTPException(e.status, e.message)


def add_routes(r: APIRouter):
    @r.post("/link")
    def link(b: UserReq):
        return run(tf.create_link, b.user_id)

    @r.get("/link/{user_id}")
    def linked(user_id: str):
        return {"linked": run(tf.is_linked, user_id)}

    @r.post("/send")
    def send(b: UserReq):
        return run(tf.send_code, b.user_id)

    @r.post("/verify")
    def verify(b: VerifyReq):
        return run(tf.verify_code, b.user_id, b.code)

    @r.post("/approve")
    def approve(b: UserReq):
        return run(tf.request_approval, b.user_id)

    @r.get("/approve/{request_id}")
    def approve_status(request_id: str):
        return run(tf.approval_status, request_id)


app = FastAPI(title="Telegram 2FA", lifespan=lifespan)
api = APIRouter(prefix="/api", dependencies=[Depends(check_key)])
demo = APIRouter(prefix="/demo", dependencies=[Depends(check_demo)])
add_routes(api)
add_routes(demo)
app.include_router(api)
app.include_router(demo)


@app.get("/")
def index():
    return FileResponse(BASE / "static" / "index.html")


@app.api_route("/health", methods=["GET", "HEAD"])
def health():
    return {"ok": True}
