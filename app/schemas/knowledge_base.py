from __future__ import annotations

from typing import Any, Optional
import uuid
from datetime import datetime

from pydantic import BaseModel, Field, field_validator


KNOWLEDGE_CATEGORIES = {
    "services",
    "pricing",
    "conditions",
    "faq",
    "scripts",
    "objections",
    "company_policy",
}


class KnowledgeDocumentBase(BaseModel):
    title: str = Field(min_length=1, max_length=255)
    category: str
    content: str = Field(min_length=1)
    is_active: bool = True
    notes: Optional[str] = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("title", "category", "content")
    @classmethod
    def validate_required_text(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("field must not be blank")
        return cleaned

    @field_validator("category")
    @classmethod
    def validate_category(cls, value: str) -> str:
        if value not in KNOWLEDGE_CATEGORIES:
            raise ValueError(
                "category must be one of: services, pricing, conditions, faq, "
                "scripts, objections, company_policy"
            )
        return value


class KnowledgeDocumentCreate(KnowledgeDocumentBase):
    pass


class KnowledgeDocumentUpdate(BaseModel):
    title: Optional[str] = Field(default=None, min_length=1, max_length=255)
    category: Optional[str] = None
    content: Optional[str] = Field(default=None, min_length=1)
    is_active: Optional[bool] = None
    notes: Optional[str] = None
    metadata: Optional[dict[str, Any]] = None

    @field_validator("title", "category", "content")
    @classmethod
    def validate_optional_text(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("field must not be blank")
        return cleaned

    @field_validator("category")
    @classmethod
    def validate_optional_category(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        if value not in KNOWLEDGE_CATEGORIES:
            raise ValueError(
                "category must be one of: services, pricing, conditions, faq, "
                "scripts, objections, company_policy"
            )
        return value


class KnowledgeDocumentListItem(BaseModel):
    model_config = {"from_attributes": True}

    id: uuid.UUID
    title: str
    category: str
    is_active: bool
    created_at: datetime
    updated_at: datetime


class KnowledgeDocumentRead(BaseModel):
    model_config = {"from_attributes": True}

    id: uuid.UUID
    title: str
    category: str
    content: str
    is_active: bool
    notes: Optional[str] = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime
    updated_at: datetime


class KnowledgeDocumentListRead(BaseModel):
    items: list[KnowledgeDocumentListItem]
    total: int


class AgentKnowledgeBindingCreate(BaseModel):
    knowledge_document_id: uuid.UUID
    role: Optional[str] = None


class AgentKnowledgeBindingRead(BaseModel):
    model_config = {"from_attributes": True}

    id: uuid.UUID
    agent_profile_id: uuid.UUID
    knowledge_document_id: uuid.UUID
    role: Optional[str] = None
    created_at: datetime
    knowledge_document: KnowledgeDocumentRead


class AgentKnowledgeBindingListRead(BaseModel):
    items: list[AgentKnowledgeBindingRead]
    total: int


class CompanyProfileUpdate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    legal_name: Optional[str] = None
    description: Optional[str] = None
    value_proposition: Optional[str] = None
    target_audience: Optional[str] = None
    contact_info: Optional[str] = None
    website_url: Optional[str] = None
    working_hours: Optional[str] = None
    compliance_notes: Optional[str] = None
    is_active: bool = True
    config: dict[str, Any] = Field(default_factory=dict)

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("name must not be blank")
        return cleaned


class CompanyProfileRead(BaseModel):
    model_config = {"from_attributes": True}

    id: uuid.UUID
    name: str
    legal_name: Optional[str] = None
    description: Optional[str] = None
    value_proposition: Optional[str] = None
    target_audience: Optional[str] = None
    contact_info: Optional[str] = None
    website_url: Optional[str] = None
    working_hours: Optional[str] = None
    compliance_notes: Optional[str] = None
    is_active: bool
    config: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime
    updated_at: datetime
