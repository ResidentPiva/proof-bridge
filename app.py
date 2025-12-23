import os
import asyncio
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from telethon import TelegramClient
from telethon.sessions import StringSession

APP = FastAPI(title="proof-bridge")

TG_API_ID = int(os.environ["TG_API_ID"])
TG_API_HASH = os.environ["TG_API_HASH"]
TG_SESSION = os.environ["TG_SESSION"]
PROOF_BOT = os.environ.get("PROOF_BOT", "@ProofreaderIZbot")

TIMEOUT_SEC = int(os.environ.get("PROOF_TIMEOUT_SEC", "60"))
MAX_TEXT = int(os.environ.get("PROOF_MAX_TEXT", "3500"))

client = TelegramClient(StringSession(TG_SESSION), TG_API_ID, TG_API_HASH)
lock = asyncio.Lock()

class ProofReq(BaseModel):
    corrId: str
    text: str

@APP.on_event("startup")
async def startup():
    await client.connect()
    if not await client.is_user_authorized():
        raise RuntimeError("TG_SESSION не авторизована. Пересоздаи ее.")

@APP.on_event("shutdown")
async def shutdown():
    await client.disconnect()

async def ask_proof_bot(text: str) -> str:
    entity = await client.get_entity(PROOF_BOT)
    try:
        async with client.conversation(entity, timeout=TIMEOUT_SEC) as conv:
            await conv.send_message(text)
            resp = await conv.get_response()
            return (resp.message or "").strip()
    except asyncio.TimeoutError:
        raise HTTPException(status_code=504, detail=f"Timeout: бот не ответил за {TIMEOUT_SEC} сек")

@APP.post("/proofread")
async def proofread(req: ProofReq):
    text = (req.text or "").replace("ё", "е").replace("Ё", "Е").strip()

    if not text:
        raise HTTPException(status_code=400, detail="Empty text")

    if len(text) > MAX_TEXT:
        raise HTTPException(
            status_code=413,
            detail=f"Text too long ({len(text)}). Limit is {MAX_TEXT}. Нужна нарезка на части."
        )

    async with lock:
        corrected = await ask_proof_bot(text)

    corrected = (corrected or "").replace("ё", "е").replace("Ё", "Е").strip()

    return {
        "corrId": req.corrId,
        "proof": {
            "corrected": corrected,
            "raw": corrected
        }
    }
