"""
GeminiLiveClient — WebSocket клиент для Google Gemini Live API.

Протокол:
  1. connect(system_prompt) — открыть WS, отправить setup, дождаться setupComplete
  2. Фоновый recv loop читает serverContent → вызывает on_text / on_audio callbacks
  3. inject_instruction(text) — отправить clientContent в живую сессию (steering)
  4. send_audio(pcm_bytes) — отправить аудио от телефонии
  5. close() — завершить WS, отменить recv task

WS endpoint:
  wss://generativelanguage.googleapis.com/ws/
  google.ai.generativelanguage.{version}.GenerativeService.BidiGenerateContent
  ?key={API_KEY}

Audio flags (независимые):
  - audio_input=True:  принимать PCM от браузера и отправлять в Gemini
  - audio_output=True: запросить AUDIO response modality (Gemini говорит голосом)
  Можно комбинировать: audio_input=True + audio_output=False → микрофон → Gemini TEXT → ElevenLabs TTS

Надёжность:
  - recv loop никогда не падает от неизвестных событий (log + skip)
  - connect() падает с TimeoutError если setupComplete не пришёл за setup_timeout
  - close() идемпотентен
"""
from __future__ import annotations

import asyncio
import base64
import json
from typing import Callable, Optional

import websockets
import websockets.exceptions

from app.core.config import settings
from app.core.logging import get_logger
from app.integrations.direct.schemas import (
    GeminiClientContent,
    GeminiGenerationConfig,
    GeminiRealtimeInput,
    GeminiServerContent,
    GeminiSetupMessage,
    GeminiSetupPayload,
    GeminiSystemInstruction,
)

log = get_logger(__name__)


class GeminiLiveClient:
    """
    WebSocket клиент для Gemini Live API.

    audio_input  — отправлять PCM от браузера в Gemini (микрофон → Gemini)
    audio_output — запрашивать AUDIO modality (Gemini отвечает голосом)

    Комбинации:
      input=True,  output=True  → full-duplex (Gemini voice)
      input=True,  output=False → voice input → Gemini TEXT → ElevenLabs TTS
      input=False, output=False → text steering only

    Callbacks (вызываются из recv loop — в asyncio event loop):
      on_text(role, text) — текстовая реплика от модели
      on_audio(pcm_bytes) — PCM аудио от модели (только при audio_output=True)
      on_close()          — WS соединение закрыто (штатно или с ошибкой)
    """

    def __init__(
        self,
        on_text: Callable[[str, str], None],
        on_audio: Callable[[bytes], None],
        on_close: Callable[[], None],
        on_interrupted: Optional[Callable[[], None]] = None,
        on_tool_call: Optional[Callable[[str, dict], None]] = None,
        audio_input: bool = False,
        audio_output: bool = False,
        voice_name: Optional[str] = None,
        language_code: str = "ru-RU",
        model_id: Optional[str] = None,
        api_version: Optional[str] = None,
    ) -> None:
        self._on_text = on_text
        self._on_audio = on_audio
        self._on_close = on_close
        self._on_interrupted = on_interrupted
        self._on_tool_call = on_tool_call
        self._audio_input = audio_input
        self._audio_output = audio_output
        self._voice_name = voice_name
        self._language_code = language_code
        self._model_id = model_id
        self._api_version = api_version
        self._ws: Optional[websockets.WebSocketClientProtocol] = None
        self._recv_task: Optional[asyncio.Task] = None
        self._setup_done: asyncio.Event = asyncio.Event()
        self._closed: bool = False

    # ── Public API ────────────────────────────────────────────────────────────

    async def connect(self, system_prompt: str) -> None:
        """
        Открыть WS соединение, отправить setup, дождаться setupComplete.
        Запустить recv_loop в background task.

        Raises:
          asyncio.TimeoutError — если setupComplete не пришёл за setup_timeout сек
          websockets.exceptions.WebSocketException — при ошибке подключения
        """
        url = self._build_url()
        log.info(
            "gemini_client.connecting",
            model=self._model_id or settings.gemini_model_id,
            api_version=self._api_version or settings.gemini_api_version,
        )
        self._ws = await websockets.connect(
            url,
            max_size=10 * 1024 * 1024,  # 10MB для аудио чанков
            ping_interval=20,
            ping_timeout=10,
        )
        # Запускаем recv loop ДО отправки setup — иначе setupComplete некому читать
        self._recv_task = asyncio.create_task(
            self._recv_loop(),
            name=f"gemini_recv_{id(self)}",
        )
        await self._send_setup(system_prompt)
        # Ждём setupComplete (Gemini отвечает быстро — обычно <1 сек)
        try:
            await asyncio.wait_for(
                self._setup_done.wait(),
                timeout=settings.gemini_setup_timeout,
            )
        except asyncio.TimeoutError:
            self._recv_task.cancel()
            raise
        log.info("gemini_client.connected", model=self._model_id or settings.gemini_model_id)

    async def inject_instruction(self, instruction: str) -> None:
        """
        Инжектировать steering instruction в живую сессию.

        В audio-output режиме используем realtimeInput.text —
        clientContent отклоняется audio-to-audio моделями (код 1007).
        В text-режиме используем clientContent.
        """
        if not self._ws or self._closed:
            log.warning("gemini_client.inject_instruction.no_ws")
            return
        if self._audio_output:
            # Audio-to-audio model: use realtimeInput.text
            payload = {"realtimeInput": {"text": instruction}}
        else:
            payload = GeminiClientContent.from_text(instruction, role="user").to_dict()
        await self._ws.send(json.dumps(payload))
        log.info(
            "gemini_client.instruction_injected",
            preview=instruction[:80],
        )

    async def send_audio(self, pcm_bytes: bytes) -> None:
        """
        Передать PCM аудио chunk от браузера в Gemini.
        audio_input=False: chunk пропускается.
        audio_input=True: отправляет GeminiRealtimeInput.audio.
        """
        if not self._ws or self._closed:
            return
        if not self._audio_input:
            return
        # Audio modality: send input chunk to Gemini
        data_b64 = base64.b64encode(pcm_bytes).decode()
        msg = GeminiRealtimeInput(mime_type="audio/pcm;rate=16000", data_b64=data_b64)
        await self._ws.send(json.dumps(msg.to_dict()))

    async def close(self) -> None:
        """Закрыть WS и отменить recv task. Idempotent."""
        if self._closed:
            return
        self._closed = True
        if self._recv_task and not self._recv_task.done():
            self._recv_task.cancel()
            try:
                await asyncio.wait_for(self._recv_task, timeout=2.0)
            except (asyncio.CancelledError, asyncio.TimeoutError):
                pass
        if self._ws:
            try:
                await self._ws.close()
            except Exception:
                pass
        log.info("gemini_client.closed")

    # ── Private ───────────────────────────────────────────────────────────────

    def _build_url(self) -> str:
        api_version = self._api_version or settings.gemini_api_version
        return (
            f"wss://generativelanguage.googleapis.com/ws/"
            f"google.ai.generativelanguage.{api_version}"
            f".GenerativeService.BidiGenerateContent"
            f"?key={settings.gemini_api_key}"
        )

    async def _send_setup(self, system_prompt: str) -> None:
        # Request AUDIO response modality only when audio_output=True
        gen_config = (
            GeminiGenerationConfig.for_audio_modality(
                voice_name=self._voice_name or "Aoede",
                language_code=self._language_code,
            )
            if self._audio_output
            else GeminiGenerationConfig.for_text_modality()
        )
        effective_model = self._model_id or settings.gemini_model_id
        msg = GeminiSetupMessage(
            setup=GeminiSetupPayload(
                model=f"models/{effective_model}",
                generation_config=gen_config,
                system_instruction=GeminiSystemInstruction.from_text(system_prompt),
                tools=[
                    {"googleSearch": {}},
                    {
                        "functionDeclarations": [
                            {
                                "name": "end_call",
                                "description": (
                                    "Завершить телефонный разговор. "
                                    "Вызывай когда разговор естественно завершился: "
                                    "клиент попрощался, все вопросы решены, "
                                    "или клиент явно хочет закончить звонок."
                                ),
                                "parameters": {
                                    "type": "object",
                                    "properties": {},
                                },
                            }
                        ]
                    },
                ],
            )
        )
        await self._ws.send(json.dumps(msg.to_dict()))
        log.info(
            "gemini_client.setup_sent",
            model=f"models/{effective_model}",
            audio_input=self._audio_input,
            audio_output=self._audio_output,
        )

    async def _recv_loop(self) -> None:
        """
        Основной цикл чтения событий от Gemini.
        Никогда не падает от неизвестных событий — log + skip.
        Завершается при закрытии WS или отмене task.
        """
        try:
            assert self._ws is not None
            async for raw_message in self._ws:
                try:
                    await self._dispatch(json.loads(raw_message))
                except Exception as exc:
                    log.warning(
                        "gemini_client.dispatch_error",
                        error=str(exc),
                        raw_preview=str(raw_message)[:200],
                    )
        except websockets.exceptions.ConnectionClosed as exc:
            log.info(
                "gemini_client.connection_closed",
                code=exc.code,
                reason=exc.reason,
            )
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            log.exception("gemini_client.recv_loop_error", error=str(exc))
        finally:
            if not self._closed:
                self._on_close()

    async def _dispatch(self, msg: dict) -> None:
        """Роутить входящее сообщение на нужный обработчик."""
        if "setupComplete" in msg:
            self._setup_done.set()
            log.debug("gemini_client.setup_complete")
            return

        if "error" in msg:
            err = msg["error"]
            log.error(
                "gemini_client.api_error",
                code=err.get("code"),
                status=err.get("status"),
                message=err.get("message"),
            )
            # Помечаем setup как завершённый с ошибкой — чтобы не ждать таймаута
            self._setup_done.set()
            raise RuntimeError(
                f"Gemini API error {err.get('code')}: {err.get('message')}"
            )

        if "serverContent" in msg:
            sc = GeminiServerContent.from_dict(msg["serverContent"])
            if sc.interrupted:
                log.debug("gemini_client.interrupted")
                if self._on_interrupted:
                    self._on_interrupted()
                return
            if sc.model_turn:
                for part in sc.model_turn.parts:
                    if part.text:
                        self._on_text("assistant", part.text.strip())
                    elif part.inline_data:
                        # Phase 2: аудио от Gemini
                        pcm = base64.b64decode(part.inline_data.data_b64)
                        self._on_audio(pcm)
            return

        if "toolCall" in msg:
            for fc in msg["toolCall"].get("functionCalls", []):
                name = fc.get("name", "")
                args = fc.get("args") or {}
                call_id = fc.get("id", "")
                log.info("gemini_client.tool_call", name=name, call_id=call_id)
                if self._on_tool_call:
                    self._on_tool_call(name, args)
                # Acknowledge the tool call so Gemini doesn't wait for a response.
                if self._ws and not self._closed:
                    ack = {
                        "toolResponse": {
                            "functionResponses": [
                                {"id": call_id, "name": name, "response": {"result": "ok"}}
                            ]
                        }
                    }
                    await self._ws.send(json.dumps(ack))
            return

        # Неизвестное поле — логируем ключи для диагностики
        log.debug("gemini_client.unknown_message", keys=list(msg.keys()))
