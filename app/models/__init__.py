# Import all models here so Alembic autogenerate can discover them
from app.models.agent_knowledge_binding import AgentKnowledgeBinding
from app.models.agent_profile import AgentProfile
from app.models.audit import AuditEvent
from app.models.blocked_phone import BlockedPhone
from app.models.call import Call, CallMode, CallStatus
from app.models.company_profile import CompanyProfile
from app.models.knowledge_document import KnowledgeDocument
from app.models.manager import Manager
from app.models.provider_setting import ProviderSetting
from app.models.steering import SteeringInstruction
from app.models.transcript import TranscriptEntry, TranscriptRole
from app.models.transfer import TransferRecord, TransferStatus
from app.models.vapi_event import VapiEventLog

__all__ = [
    "Call",
    "CallStatus",
    "CallMode",
    "AgentKnowledgeBinding",
    "AgentProfile",
    "BlockedPhone",
    "CompanyProfile",
    "KnowledgeDocument",
    "Manager",
    "ProviderSetting",
    "SteeringInstruction",
    "AuditEvent",
    "TranscriptEntry",
    "TranscriptRole",
    "TransferRecord",
    "TransferStatus",
    "VapiEventLog",
]
