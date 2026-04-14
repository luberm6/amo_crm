from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from app.integrations.telephony.mango_client import MangoExtensionPayload
from app.integrations.telephony.mango_runtime import resolve_mango_from_ext


@pytest.mark.anyio
async def test_resolve_mango_from_ext_prefers_metadata_extension():
    resolved = await resolve_mango_from_ext(
        metadata={"telephony_extension": "44"},
    )
    assert resolved.value == "44"
    assert resolved.source == "metadata"


@pytest.mark.anyio
async def test_resolve_mango_from_ext_matches_extension_by_line_id():
    client = AsyncMock()
    client.list_extensions.return_value = [
        MangoExtensionPayload(
            provider_resource_id="u1",
            extension="10",
            display_name="One",
            line_provider_resource_id="405519147",
            line_phone_number="+79585382099",
            raw_payload={},
        ),
        MangoExtensionPayload(
            provider_resource_id="u2",
            extension="12",
            display_name="Two",
            line_provider_resource_id="405622036",
            line_phone_number="+79300350609",
            raw_payload={},
        ),
    ]

    resolved = await resolve_mango_from_ext(
        metadata={"telephony_remote_line_id": "405622036"},
        client=client,
    )
    assert resolved.value == "12"
    assert resolved.source == "auto_discovered_by_line"
    client.list_extensions.assert_awaited_once()


@pytest.mark.anyio
async def test_resolve_mango_from_ext_falls_back_to_first_extension():
    client = AsyncMock()
    client.list_extensions.return_value = [
        MangoExtensionPayload(
            provider_resource_id="u2",
            extension="12",
            display_name="Two",
            line_provider_resource_id=None,
            line_phone_number=None,
            raw_payload={},
        ),
        MangoExtensionPayload(
            provider_resource_id="u1",
            extension="10",
            display_name="One",
            line_provider_resource_id=None,
            line_phone_number=None,
            raw_payload={},
        ),
    ]

    resolved = await resolve_mango_from_ext(client=client)
    assert resolved.value == "10"
    assert resolved.source == "auto_discovered_first_extension"
