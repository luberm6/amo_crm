from __future__ import annotations

from typing import Optional

from app.core.config import settings
from app.integrations.media_gateway.base import AbstractMediaGateway
from app.integrations.media_gateway.freeswitch import (
    FreeSwitchGatewayConfig,
    FreeSwitchMediaGateway,
)

_gateway: Optional[AbstractMediaGateway] = None


def get_media_gateway() -> AbstractMediaGateway:
    global _gateway
    if _gateway is not None:
        return _gateway

    cfg = FreeSwitchGatewayConfig(
        mode=settings.media_gateway_mode,
        esl_host=settings.freeswitch_esl_host,
        esl_port=settings.freeswitch_esl_port,
        esl_password=settings.freeswitch_esl_password,
        sip_profile=settings.freeswitch_sip_profile,
        sip_domain=settings.freeswitch_sip_domain,
        rtp_ip=settings.freeswitch_rtp_ip,
        rtp_port_start=settings.freeswitch_rtp_port_start,
        rtp_port_end=settings.freeswitch_rtp_port_end,
        session_timeout_seconds=settings.freeswitch_session_timeout_seconds,
        rtp_payload_type=settings.freeswitch_rtp_payload_type,
        attach_command_template=settings.freeswitch_attach_command_template,
        hangup_command_template=settings.freeswitch_hangup_command_template,
        esl_events=settings.freeswitch_esl_events,
        esl_connect_timeout_seconds=settings.freeswitch_esl_connect_timeout_seconds,
        esl_reconnect_enabled=settings.freeswitch_esl_reconnect_enabled,
        esl_reconnect_initial_delay_seconds=settings.freeswitch_esl_reconnect_initial_delay_seconds,
        esl_reconnect_max_delay_seconds=settings.freeswitch_esl_reconnect_max_delay_seconds,
        esl_reconnect_max_attempts=settings.freeswitch_esl_reconnect_max_attempts,
        rtp_inbound_codec=settings.freeswitch_rtp_inbound_codec,
        rtp_outbound_codec=settings.freeswitch_rtp_outbound_codec,
        rtp_sample_rate_hz=settings.freeswitch_rtp_sample_rate_hz,
        rtp_frame_bytes=settings.freeswitch_rtp_frame_bytes,
        rtp_inbound_timeout_seconds=settings.freeswitch_rtp_inbound_timeout_seconds,
        rtp_outbound_buffer_max_frames=settings.freeswitch_rtp_outbound_buffer_max_frames,
        event_queue_max=settings.freeswitch_event_queue_max,
    )
    _gateway = FreeSwitchMediaGateway(cfg)
    return _gateway
