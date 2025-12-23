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

TIMEOUT_SEC = int(os.environ.get("PROOF_TIMEOUT_SEC", "360"))
MAX_TEXT = int(os.environ.get("PROOF_MAX_TEXT", "3500"))

REPORT_MARKER = os.environ.get("PROOF_REPORT_MARKER", "Комментарии корректора")

IDLE_DEFAULT_SEC = float(os.environ.get("PROOF_IDLE_DEFAULT_SEC", "15"))
IDLE_AFTER_STATUS_SEC = float(os.environ.get("PROOF_IDLE_AFTER_STATUS_SEC", "360"))
IDLE_AFTER_TEXT_SEC = float(os.environ.get("PROOF_IDLE_AFTER_TEXT_SEC", "120"))
IDLE_AFTER_REPORT_SEC = float(os.environ.get("PROOF_IDLE_AFTER_REPORT_SEC", "8"))

MAX_MESSAGES = int(os.environ.get("PROOF_MAX_MESSAGES", "25"))

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


def classify_message(msg: str) -> str:
    t = (msg or "").strip().lower()
    if ("обрабатываю запрос" in t) or ("обработка может занять" in t):
        return "status"

    marker = (REPORT_MARKER or "").strip().lower()
    if marker and marker in t:
        return "report"

    if ("отчет о юридической проверке" in t) or ("конец отчета" in t):
        return "report"

    return "text"


def idle_for(kind: str) -> float:
    if kind == "status":
        return IDLE_AFTER_STATUS_SEC
    if kind == "report":
        return IDLE_AFTER_REPORT_SEC
    if kind == "text":
        return IDLE_AFTER_TEXT_SEC
    return IDLE_DEFAULT_SEC


def cut_from_marker(s: str, marker: str) -> str:
    if not s:
        return ""
    if not marker:
        return s.strip()
    low = s.lower()
    m = marker.strip().lower()
    idx = low.find(m)
    if idx >= 0:
        return s[idx:].lstrip()
    return s.strip()


async def ask_proof_bot(text: str) -> str:
    entity = await client.get_entity(PROOF_BOT)

    loop = asyncio.get_event_loop()
    deadline = loop.time() + TIMEOUT_SEC

    def remaining() -> float:
        return deadline - loop.time()

    try:
        async with client.conversation(entity, timeout=TIMEOUT_SEC) as conv:
            await conv.send_message(text)

            last_text = ""
            last_kind = "status"
            last_report_text = ""
            received = 0

            # Первое сообщение
            resp = await asyncio.wait_for(conv.get_response(), timeout=max(0.1, remaining()))
            msg = (getattr(resp, "raw_text", None) or getattr(resp, "message", None) or "").strip()
            last_text = msg
            received += 1
            last_kind = classify_message(msg)

            if (REPORT_MARKER or "").lower() in msg.lower():
                last_report_text = msg

            # Дальше читаем до "тишины"
            while received < MAX_MESSAGES and remaining() > 0:
                wait_sec = min(idle_for(last_kind), max(0.1, remaining()))
                try:
                    resp2 = await asyncio.wait_for(conv.get_response(), timeout=wait_sec)
                    msg2 = (getattr(resp2, "raw_text", None) or getattr(resp2, "message", None) or "").strip()
                    last_text = msg2
                    received += 1
                    last_kind = classify_message(msg2)

                    if (REPORT_MARKER or "").lower() in msg2.lower():
                        last_report_text = msg2

                except asyncio.TimeoutError:
                    break

            result = last_report_text or last_text
            if not result:
                raise HTTPException(status_code=502, detail="Пустои ответ от бота")

            return cut_from_marker(result, REPORT_MARKER)

    except asyncio.TimeoutError:
        raise HTTPException(status_code=504, detail=f"Timeout: бот не завершил ответ за {TIMEOUT_SEC} сек")


@APP.post("/proofread")
async def proofread(req: ProofReq):
    # Замена "е/Е" без прямого использования символа "ё" в коде
    text = (req.text or "").replace("\u0451", "е").replace("\u0401", "Е").strip()

    if not text:
        raise HTTPException(status_code=400, detail="Empty text")

    if len(text) > MAX_TEXT:
        raise HTTPException(
            status_code=413,
            detail=f"Text too long ({len(text)}). Limit is {MAX_TEXT}. Нужна нарезка на части."
        )

    async with lock:
        corrected = await ask_proof_bot(text)

    corrected = (corrected or "").replace("\u0451", "е").replace("\u0401", "Е").strip()

    return {"corrId": req.corrId, "proof": {"corrected": corrected, "raw": corrected}}
