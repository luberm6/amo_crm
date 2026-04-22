"""
Тесты для TelephonyAdapter — Stub и Mango.

8 тестов:
- StubTelephonyAdapter.originate_call: returns TelephonyOriginateResult
- StubTelephonyAdapter.bridge_legs: no-op, нет исключения
- StubTelephonyAdapter.terminate_leg: idempotent
- StubTelephonyAdapter.get_leg_state: возвращает ANSWERED после connect
- StubTelephonyAdapter.audio_stream: возвращает silence chunks
- MangoTelephonyAdapter.originate_call: подписывает запрос, POST /commands/callback
- MangoTelephonyAdapter.terminate_leg: идемпотентен при 404/not found
- MangoTelephonyAdapter.audio_stream: raises NotImplementedError
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch
import json

import pytest

import app.core.config as cfg
from app.integrations.telephony.base import TelephonyLegState, TelephonyOriginateResult
from app.integrations.telephony.mango import TelephonyError
from app.integrations.telephony.mango_client import MangoExtensionPayload
from app.integrations.telephony.mango_runtime import ResolvedMangoFromExt
from app.integrations.telephony.mango_state_store import InMemoryMangoLegStateStore
from app.integrations.telephony.stub import StubTelephonyAdapter


# ── StubTelephonyAdapter тесты ────────────────────────────────────────────────

@pytest.mark.anyio
async def test_stub_originate_call_returns_result():
    """originate_call возвращает TelephonyOriginateResult с leg_id."""
    adapter = StubTelephonyAdapter()
    result = await adapter.originate_call("+79991234567")
    assert isinstance(result, TelephonyOriginateResult)
    assert result.leg_id
    assert "+79991234567" in result.leg_id or result.leg_id.startswith("stub-")


@pytest.mark.anyio
async def test_stub_bridge_legs_no_exception():
    """bridge_legs — no-op, без исключений."""
    adapter = StubTelephonyAdapter()
    await adapter.bridge_legs("customer-leg-1", "manager-leg-2")
    # Обе ноги переходят в BRIDGED
    assert adapter._leg_states.get("customer-leg-1") == TelephonyLegState.BRIDGED
    assert adapter._leg_states.get("manager-leg-2") == TelephonyLegState.BRIDGED


@pytest.mark.anyio
async def test_stub_terminate_leg_idempotent():
    """terminate_leg дважды — без исключений, состояние TERMINATED."""
    adapter = StubTelephonyAdapter()
    await adapter.terminate_leg("some-leg-id")
    await adapter.terminate_leg("some-leg-id")  # повторный вызов
    assert adapter._leg_states["some-leg-id"] == TelephonyLegState.TERMINATED


@pytest.mark.anyio
async def test_stub_get_leg_state_after_connect():
    """После connect() get_leg_state возвращает ANSWERED."""
    adapter = StubTelephonyAdapter()
    channel = await adapter.connect("+79991234567")
    state = await adapter.get_leg_state(channel.provider_leg_id)
    assert state == TelephonyLegState.ANSWERED


@pytest.mark.anyio
async def test_stub_audio_stream_yields_silence():
    """audio_stream возвращает silence bytes и завершается при disconnect."""
    adapter = StubTelephonyAdapter()
    channel = await adapter.connect("+79991234567")

    chunks = []
    async for chunk in adapter.audio_stream(channel):
        chunks.append(chunk)
        # Отключаем канал после первого chunk, чтобы поток завершился
        await adapter.disconnect(channel.phone)
        break

    assert len(chunks) == 1
    # Тишина = нулевые байты
    assert all(b == 0 for b in chunks[0])


# ── MangoTelephonyAdapter тесты ───────────────────────────────────────────────

def _make_mango_adapter():
    """Создать MangoTelephonyAdapter с мокнутым HTTP клиентом."""
    from app.integrations.telephony.mango import MangoTelephonyAdapter

    adapter = MangoTelephonyAdapter.__new__(MangoTelephonyAdapter)
    adapter._api_key = "test-key"
    adapter._api_salt = "test-salt"
    adapter._from_ext = "101"
    adapter._state = InMemoryMangoLegStateStore()
    from app.integrations.telephony.mango_freeswitch_correlation import InMemoryMangoFreeSwitchCorrelationStore
    adapter._corr = InMemoryMangoFreeSwitchCorrelationStore()
    adapter._http = AsyncMock()
    return adapter


class _FakeGateway:
    def __init__(self, replies=None):
        self.commands = []
        self.replies = replies or {}

    async def execute_command(self, command: str, *, background: bool = True) -> str:
        self.commands.append((command, background))
        return self.replies.get((command, background), self.replies.get(command, "+OK"))


@pytest.mark.anyio
async def test_mango_originate_call_posts_to_callback():
    """originate_call делает POST /commands/callback и возвращает TelephonyOriginateResult."""
    adapter = _make_mango_adapter()

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"success": 1, "uid": "mango-uid-abc"}
    adapter._http.post = AsyncMock(return_value=mock_resp)

    result = await adapter.originate_call("+79991234567")

    assert isinstance(result, TelephonyOriginateResult)
    assert result.leg_id == "mango-uid-abc"
    # Проверяем что был POST на /commands/callback
    adapter._http.post.assert_called_once()
    call_args = adapter._http.post.call_args
    assert "/commands/callback" in call_args[0][0]


@pytest.mark.anyio
async def test_mango_originate_call_uses_agent_bound_remote_line_id():
    """originate_call prefers the actual Mango line phone when runtime passes it."""
    adapter = _make_mango_adapter()

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"success": 1, "uid": "mango-uid-line-bound"}
    adapter._http.post = AsyncMock(return_value=mock_resp)

    await adapter.originate_call(
        "+79991234567",
        metadata={
            "telephony_remote_line_id": "405622036",
            "telephony_line_phone_number": "+79300350609",
        },
    )

    call_args = adapter._http.post.call_args
    assert "/commands/callback" in call_args[0][0]
    sent_form = call_args.kwargs["data"]
    signed_payload = json.loads(sent_form["json"])
    assert signed_payload["line_number"] == "89300350609"


@pytest.mark.anyio
async def test_mango_originate_call_auto_discovers_from_ext():
    """originate_call falls back to live/discovered extension when MANGO_FROM_EXT is empty."""
    adapter = _make_mango_adapter()
    adapter._from_ext = ""

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"success": 1, "uid": "mango-uid-auto-ext"}
    adapter._http.post = AsyncMock(return_value=mock_resp)

    with patch(
        "app.integrations.telephony.mango.resolve_mango_from_ext",
        AsyncMock(return_value=ResolvedMangoFromExt(value="12", source="auto_discovered_first_extension")),
    ):
        await adapter.originate_call("+79991234567")

    signed_payload = json.loads(adapter._http.post.call_args.kwargs["data"]["json"])
    assert signed_payload["from"]["extension"] == "12"


@pytest.mark.anyio
async def test_mango_originate_call_accepts_result_1000_without_uid():
    """Mango callback may accept command asynchronously and omit uid immediately."""
    adapter = _make_mango_adapter()

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"result": 1000}
    adapter._http.post = AsyncMock(return_value=mock_resp)

    result = await adapter.originate_call("+79991234567")

    signed_payload = json.loads(adapter._http.post.call_args.kwargs["data"]["json"])
    assert result.leg_id == signed_payload["command_id"]
    assert result.provider_response["command_id"] == signed_payload["command_id"]
    assert result.provider_response["callback_uid_present"] is False


@pytest.mark.anyio
async def test_mango_originate_call_uses_freeswitch_sip_gateway_when_configured():
    adapter = _make_mango_adapter()
    fake_gateway = _FakeGateway(replies={})

    with (
        patch.object(cfg.settings, "mango_primary_phone_number", "89300350609"),
        patch.object(cfg.settings, "mango_sip_login", "11"),
        patch.object(cfg.settings, "mango_sip_password", "secret"),
        patch.object(cfg.settings, "mango_sip_server", "vpbx400350317.mangosip.ru"),
        patch("app.integrations.telephony.mango.get_media_gateway", return_value=fake_gateway),
    ):
        result = await adapter.originate_call(
            "+79991234567",
            metadata={"call_id": "call-1", "telephony_line_phone_number": "+79300350609"},
        )

    assert result.provider_response["transport"] == "freeswitch_sip"
    assert result.provider_response["gateway"] == "mango_primary"
    assert result.provider_response["line_number"] == "89300350609"
    assert fake_gateway.commands
    command, background = fake_gateway.commands[0]
    assert background is True
    assert "originate " in command
    assert "sofia/gateway/mango_primary/89991234567" in command
    assert "origination_caller_id_number=89300350609" in command


@pytest.mark.anyio
async def test_mango_originate_call_coerces_ru_line_and_dial_numbers_to_trunk_format():
    adapter = _make_mango_adapter()
    fake_gateway = _FakeGateway(replies={})

    with (
        patch.object(cfg.settings, "mango_primary_phone_number", "89300350609"),
        patch.object(cfg.settings, "mango_sip_login", "11"),
        patch.object(cfg.settings, "mango_sip_password", "secret"),
        patch.object(cfg.settings, "mango_sip_server", "vpbx400350317.mangosip.ru"),
        patch("app.integrations.telephony.mango.get_media_gateway", return_value=fake_gateway),
    ):
        result = await adapter.originate_call(
            "+79265229998",
            metadata={"call_id": "call-1", "telephony_line_phone_number": "+79300350609"},
        )

    assert result.provider_response["line_number"] == "89300350609"
    command, background = fake_gateway.commands[0]
    assert background is True
    assert "sofia/gateway/mango_primary/89265229998" in command
    assert "origination_caller_id_number=89300350609" in command


@pytest.mark.anyio
async def test_mango_wait_for_answered_uses_freeswitch_probe_when_sip_trunk_configured():
    adapter = _make_mango_adapter()
    fake_gateway = _FakeGateway(
        replies={
            ("uuid_exists direct-test-leg", False): "true",
            ("uuid_getvar direct-test-leg answer_state", False): "answered",
            ("uuid_getvar direct-test-leg channel_call_state", False): "ACTIVE",
            ("uuid_getvar direct-test-leg endpoint_disposition", False): "ANSWER",
        }
    )

    with (
        patch.object(cfg.settings, "mango_sip_login", "11"),
        patch.object(cfg.settings, "mango_sip_password", "secret"),
        patch.object(cfg.settings, "mango_sip_server", "vpbx400350317.mangosip.ru"),
        patch("app.integrations.telephony.mango.get_media_gateway", return_value=fake_gateway),
    ):
        state = await adapter.wait_for_answered("direct-test-leg", timeout=0.2)

    assert state == TelephonyLegState.ANSWERED


@pytest.mark.anyio
async def test_mango_originate_call_requires_sip_trunk_when_api_disabled():
    adapter = _make_mango_adapter()

    with (
        patch.object(cfg.settings, "mango_api_key", ""),
        patch.object(cfg.settings, "mango_api_salt", ""),
        patch.object(cfg.settings, "mango_sip_login", ""),
        patch.object(cfg.settings, "mango_sip_password", ""),
        patch.object(cfg.settings, "mango_sip_server", ""),
    ):
        with pytest.raises(TelephonyError) as exc:
            await adapter.originate_call(
                "+79991234567",
                caller_id="11",
                metadata={"telephony_line_phone_number": "+79300350609"},
            )

    assert "Mango SIP trunk is not configured" in str(exc.value)


@pytest.mark.anyio
async def test_mango_originate_call_rejects_extension_target_outside_vps():
    adapter = _make_mango_adapter()

    with patch(
        "app.integrations.telephony.mango.MangoClient.from_settings",
        return_value=AsyncMock(
            list_extensions=AsyncMock(
                return_value=[
                    MangoExtensionPayload(
                        provider_resource_id="u11",
                        extension="11",
                        display_name="Primary",
                        line_provider_resource_id="405622036",
                        line_phone_number="+79300350609",
                        raw_payload={
                            "telephony": {
                                "numbers": [
                                    {"number": "89263358010", "protocol": "tel"},
                                ]
                            }
                        },
                    )
                ]
            ),
            aclose=AsyncMock(),
        ),
    ), patch.object(cfg.settings, "backend_url", "http://84.247.184.72"):
        with pytest.raises(TelephonyError) as exc:
            await adapter.originate_call(
                "+79991234567",
                caller_id="11",
                metadata={"telephony_line_phone_number": "+79300350609"},
            )

    assert "Primary Mango extension target is not configured for this VPS FreeSWITCH." in str(exc.value)


@pytest.mark.anyio
async def test_mango_connect_existing_leg_skips_originate():
    """Inbound runtime can attach to an already-existing Mango leg without callback originate."""
    adapter = _make_mango_adapter()
    adapter.originate_call = AsyncMock()
    adapter.wait_for_answered = AsyncMock()

    channel = await adapter.connect(
        "+79991234567",
        metadata={"existing_leg_id": "mango-leg-existing-1", "call_id": "internal-call-id"},
    )

    adapter.originate_call.assert_not_called()
    assert channel.provider_leg_id == "mango-leg-existing-1"
    assert channel.metadata["existing_leg"] is True


@pytest.mark.anyio
async def test_mango_terminate_leg_idempotent_on_not_found():
    """terminate_leg: если Mango говорит 'not found' — не бросать ошибку."""
    adapter = _make_mango_adapter()

    # Симулируем ответ "not found" от Mango
    mock_resp = MagicMock()
    mock_resp.status_code = 404
    mock_resp.json.return_value = {"message": "call not found"}
    mock_resp.text = "call not found"
    adapter._http.post = AsyncMock(return_value=mock_resp)

    # Не должно бросить исключение — просто логирует "already_gone"
    await adapter.terminate_leg("some-leg-id")

    snap = await adapter._state.get_leg_state("some-leg-id")
    assert snap is not None
    assert snap.state == TelephonyLegState.TERMINATED


@pytest.mark.anyio
async def test_mango_audio_stream_raises_not_implemented():
    """audio_stream всегда бросает NotImplementedError (требует SIP UA для Phase 2)."""
    from app.integrations.telephony.base import TelephonyChannel

    adapter = _make_mango_adapter()
    channel = TelephonyChannel(
        channel_id="ch-1",
        phone="+79991234567",
        sip_call_id=None,
        provider_leg_id="leg-1",
        state=TelephonyLegState.ANSWERED,
    )

    with pytest.raises(NotImplementedError):
        async for _ in adapter.audio_stream(channel):
            pass
