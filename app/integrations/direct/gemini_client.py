"""
GeminiLiveClient — WebSocket клиент для Google Gemini Live API.

Протокол:
  1. connect(system_prompt) — открыть WS, отправить setup, дождаться setupComplete
  2. Фоновый recv loop читает serverContent → вызывает on_text / on_audio callbacks
  3. inject_instruction(text) — отправить clientContent в живую сессию (steering)
  4. send_audio(pcm_bytes) — отправить аудио от телефонии (Phase 2)
  5. close() — завершить WS, отменить recv task

WS endpoint:
  wss://generativelanguage.googleapis.com/ws/
  google.ai.generativelanguage.{version}.GenerativeService.BidiGenerateContent
  ?key={API_KEY}

Audio modality:
  - audio_modality=False: TEXT-only mode, send_audio() is skipped
  - audio_modality=True: bidirectional audio chunks are sent/received

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
    Runtime supports both TEXT-only and audio modality depending on flag.

    Callbacks (вызываются из recv loop — в asyncio event loop):
      on_text(role, text) — новая текстовая реплика от модели
      on_audio(pcm_bytes) — PCM аудио от модели (Phase 2)
      on_close()          — WS соединение закрыто (штатно или с ошибкой)
    """

    def __init__(
        self,
        on_text: Callable[[str, str], None],
        on_audio: Callable[[bytes], None],
        on_close: Callable[[], None],
        audio_modality: bool = False,
    ) -> None:
        self._on_text = on_text
        self._on_audio = on_audio
        self._on_close = on_close
        self._audio_modality = audio_modality
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
            model=settings.gemini_model_id,
            api_version=settings.gemini_api_version,
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
        log.info("gemini_client.connected", model=settings.gemini_model_id)

    async def inject_instruction(self, instruction: str) -> None:
        """
        Инжектировать steering instruction в живую сессию.

        В audio-only режиме (audio_modality=True) используем realtimeInput.text —
        clientContent с текстом отклоняется audio-to-audio моделями (код 1007).
        В text-режиме используем clientContent как раньше.
        """
        if not self._ws or self._closed:
            log.warning("gemini_client.inject_instruction.no_ws")
            return
        if self._audio_modality:
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
        Передать PCM аудио chunk от телефонии в Gemini.
        TEXT-only mode (audio_modality=False): chunk is skipped.
        Audio mode (audio_modality=True): sends GeminiRealtimeInput.
        """
        if not self._ws or self._closed:
            return
        if not self._audio_modality:
            log.debug(
                "gemini_client.send_audio.skipped",
                bytes_len=len(pcm_bytes),
                note="TEXT-only mode, audio input ignored",
            )
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
        return (
            f"wss://generativelanguage.googleapis.com/ws/"
            f"google.ai.generativelanguage.{settings.gemini_api_version}"
            f".GenerativeService.BidiGenerateContent"
            f"?key={settings.gemini_api_key}"
        )

    async def _send_setup(self, system_prompt: str) -> None:
        # Use AUDIO modality if enabled, else TEXT only
        gen_config = (
            GeminiGenerationConfig.for_audio_modality()
            if self._audio_modality
            else GeminiGenerationConfig()
        )
        msg = GeminiSetupMessage(
            setup=GeminiSetupPayload(
                model=f"models/{settings.gemini_model_id}",
                generation_config=gen_config,
                system_instruction=GeminiSystemInstruction.from_text(system_prompt),
                tools=[],
            )
        )
        payload = msg.to_dict()
        log.info(
            "gemini_client.setup_sending",
            model=f"models/{settings.gemini_model_id}",
            audio_modality=self._audio_modality,
            payload_keys=list(payload.get("setup", {}).keys()),
            generation_config=payload.get("setup", {}).get("generationConfig"),
        )
        await self._ws.send(json.dumps(payload))
        log.info(
            "gemini_client.setup_sent",
            model=f"models/{settings.gemini_model_id}",
            audio_modality=self._audio_modality,
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
                log.info(
                    "gemini_client.raw_message",
                    preview=str(raw_message)[:500],
                )
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

        # Неизвестное поле — логируем ключи для диагностики
        log.debug("gemini_client.unknown_message", keys=list(msg.keys()))
