# Knowledge Base

## Purpose

Knowledge layer is split into three separate concerns:

1. `AgentProfile`
   - conversation style
   - system prompt and rules
   - greeting
   - voice strategy

2. `CompanyProfile`
   - shared company-level facts and positioning
   - contact and compliance notes
   - reusable business context

3. `KnowledgeDocument`
   - categorized operational knowledge entries
   - can be bound to one or more agents
   - prepared for later retrieval work

This separation keeps agent persona/config independent from company data and document-level knowledge.

## Entities

### CompanyProfile
Single active company profile used as shared business context.

Fields:
- `name`
- `legal_name`
- `description`
- `value_proposition`
- `target_audience`
- `contact_info`
- `website_url`
- `working_hours`
- `compliance_notes`
- `is_active`
- `config`

### KnowledgeDocument
Managed document that belongs to a specific category.

Fields:
- `title`
- `category`
- `content`
- `is_active`
- `notes`
- `metadata`

Supported categories:
- `services`
- `pricing`
- `conditions`
- `faq`
- `scripts`
- `objections`
- `company_policy`

### AgentKnowledgeBinding
Explicit relation between an `AgentProfile` and a `KnowledgeDocument`.

Fields:
- `agent_profile_id`
- `knowledge_document_id`
- `role`

## API

Knowledge documents:
- `GET /v1/knowledge-documents`
- `GET /v1/knowledge-documents/{id}`
- `POST /v1/knowledge-documents`
- `PATCH /v1/knowledge-documents/{id}`
- `DELETE /v1/knowledge-documents/{id}`

Company profile:
- `GET /v1/company-profile`
- `PUT /v1/company-profile`

Agent bindings:
- `GET /v1/agents/{id}/knowledge`
- `POST /v1/agents/{id}/knowledge/bind`
- `DELETE /v1/agents/{id}/knowledge/{binding_id}`

## Admin UI

### Knowledge Base page
The admin panel now provides:
- document list
- category filter
- active/inactive filter
- create/edit document form
- company profile editor

### Agent Editor page
Each agent can now:
- see active KB documents
- bind/unbind specific documents
- keep prompt editing separate from KB selection

## Runtime usage

Current runtime preparation is intentionally controlled:
- company profile and bound knowledge documents are assembled into structured runtime context on the backend
- this context is prepared for later retrieval or selective injection
- the system does **not** blindly append the full KB into the agent prompt
- if no KB is bound, runtime continues to work with agent profile only

Current status:
- `INTEGRATION_READY`: CRUD, bindings, structured context assembly
- `NEEDS_REAL_WORLD_VALIDATION`: browser/admin manual QA and future live voice usage of this context
- `PRODUCTION_READY`: no
- `MOCK_ONLY`: no

## Manual QA checklist

1. Open `/knowledge-base`
2. Create one document in `pricing`
3. Create one document in `faq`
4. Filter by category and confirm the list changes
5. Edit a document and confirm changes persist after refresh
6. Save company profile and confirm it reloads correctly
7. Open an existing agent in `/agents/{id}`
8. Bind one or more documents
9. Refresh the page and confirm bindings remain
10. Start Browser Call with that agent and confirm the selected agent still works normally

## Migration

New migration:
- `migrations/versions/0010_knowledge_base.py`

Apply before using the feature:

```bash
alembic upgrade head
```
