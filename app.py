async def ask_proof_bot(text: str) -> str:
    entity = await client.get_entity(PROOF_BOT)

    loop = asyncio.get_running_loop()
    deadline = loop.time() + TIMEOUT_SEC

    def remaining() -> float:
        return deadline - loop.time()

    marker_low = (REPORT_MARKER or "").strip().lower()

    try:
        async with client.conversation(entity, timeout=TIMEOUT_SEC) as conv:
            await conv.send_message(text)

            last_text = ""
            last_kind = "status"
            received = 0

            while received < MAX_MESSAGES and remaining() > 0:
                # ВАЖНО: больше не считаем "тишину" признаком окончания.
                # Просто ждем следующего сообщения кусками, пока не истечет общий TIMEOUT_SEC.
                wait_sec = min(idle_for(last_kind), max(0.1, remaining()))

                try:
                    resp = await asyncio.wait_for(conv.get_response(), timeout=wait_sec)
                except asyncio.TimeoutError:
                    continue  # тишина -> продолжаем ждать до общего дедлайна

                msg = (getattr(resp, "raw_text", None) or getattr(resp, "message", None) or "").strip()
                if not msg:
                    continue

                last_text = msg
                received += 1
                last_kind = classify_message(msg)

                # Как только нашли "Комментарии корректора" — СРАЗУ возвращаем это сообщение
                # и режем строго с маркера (без "часть 1/2" и прочего).
                if marker_low and (marker_low in msg.lower()):
                    return cut_from_marker(msg, REPORT_MARKER)

            # Маркер не пришел — возвращаем последний текст, как страховку
            if not last_text:
                raise HTTPException(status_code=502, detail="Пустой ответ от бота")

            return cut_from_marker(last_text, REPORT_MARKER)

    except asyncio.TimeoutError:
        raise HTTPException(status_code=504, detail=f"Timeout: бот не завершил ответ за {TIMEOUT_SEC} сек")

