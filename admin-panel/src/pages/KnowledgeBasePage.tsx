import { FormEvent, useCallback, useEffect, useMemo, useRef, useState } from 'react'

import { useAuth } from '../auth/AuthContext'
import { ApiError, apiFetch } from '../lib/api'

type KnowledgeDocumentListItem = {
  id: string
  title: string
  category: string
  is_active: boolean
  created_at: string
  updated_at: string
}

type KnowledgeDocumentRead = KnowledgeDocumentListItem & {
  content: string
  notes?: string | null
  metadata: Record<string, unknown>
}

type KnowledgeDocumentListResponse = {
  items: KnowledgeDocumentListItem[]
  total: number
}

type CompanyProfile = {
  id?: string
  name: string
  legal_name?: string | null
  description?: string | null
  value_proposition?: string | null
  target_audience?: string | null
  contact_info?: string | null
  website_url?: string | null
  working_hours?: string | null
  compliance_notes?: string | null
  is_active: boolean
  config: Record<string, unknown>
  created_at?: string
  updated_at?: string
}

type DocumentFormState = {
  title: string
  category: string
  content: string
  is_active: boolean
  notes: string
  metadataSource: string
  metadataText: string
}

type CompanyFormState = {
  name: string
  legal_name: string
  description: string
  value_proposition: string
  target_audience: string
  contact_info: string
  website_url: string
  working_hours: string
  compliance_notes: string
  is_active: boolean
  locale: string
  configText: string
}

const CATEGORY_OPTIONS = [
  { value: 'services', label: 'Услуги' },
  { value: 'pricing', label: 'Цены' },
  { value: 'conditions', label: 'Условия' },
  { value: 'faq', label: 'FAQ' },
  { value: 'scripts', label: 'Скрипты' },
  { value: 'objections', label: 'Возражения' },
  { value: 'company_policy', label: 'Политика компании' },
]

const EMPTY_DOCUMENT_FORM: DocumentFormState = {
  title: '',
  category: 'services',
  content: '',
  is_active: true,
  notes: '',
  metadataSource: 'manual',
  metadataText: '{\n  "source": "manual"\n}',
}

const EMPTY_COMPANY_FORM: CompanyFormState = {
  name: '',
  legal_name: '',
  description: '',
  value_proposition: '',
  target_audience: '',
  contact_info: '',
  website_url: '',
  working_hours: '',
  compliance_notes: '',
  is_active: true,
  locale: 'ru-RU',
  configText: '{\n  "locale": "ru-RU"\n}',
}

function toDocumentFormState(document: KnowledgeDocumentRead): DocumentFormState {
  return {
    title: document.title,
    category: document.category,
    content: document.content,
    is_active: document.is_active,
    notes: document.notes || '',
    metadataSource: typeof document.metadata?.source === 'string' ? document.metadata.source : 'manual',
    metadataText: JSON.stringify(document.metadata || {}, null, 2),
  }
}

function toCompanyFormState(profile: CompanyProfile | null): CompanyFormState {
  if (!profile) {
    return EMPTY_COMPANY_FORM
  }
  return {
    name: profile.name,
    legal_name: profile.legal_name || '',
    description: profile.description || '',
    value_proposition: profile.value_proposition || '',
    target_audience: profile.target_audience || '',
    contact_info: profile.contact_info || '',
    website_url: profile.website_url || '',
    working_hours: profile.working_hours || '',
    compliance_notes: profile.compliance_notes || '',
    is_active: profile.is_active,
    locale: typeof profile.config?.locale === 'string' ? profile.config.locale : 'ru-RU',
    configText: JSON.stringify(profile.config || {}, null, 2),
  }
}

function buildDocumentMetadata(form: DocumentFormState): Record<string, unknown> {
  let metadata: Record<string, unknown> = {}
  try {
    metadata = JSON.parse(form.metadataText || '{}') as Record<string, unknown>
  } catch {
    metadata = {}
  }
  metadata.source = form.metadataSource || 'manual'
  return metadata
}

function buildCompanyConfig(form: CompanyFormState): Record<string, unknown> {
  let config: Record<string, unknown> = {}
  try {
    config = JSON.parse(form.configText || '{}') as Record<string, unknown>
  } catch {
    config = {}
  }
  config.locale = form.locale || 'ru-RU'
  return config
}

export default function KnowledgeBasePage() {
  const { token } = useAuth()
  const [documents, setDocuments] = useState<KnowledgeDocumentListItem[]>([])
  const [documentsLoading, setDocumentsLoading] = useState(true)
  const [documentsError, setDocumentsError] = useState<string | null>(null)
  const [selectedDocumentId, setSelectedDocumentId] = useState<string | null>(null)
  const [documentForm, setDocumentForm] = useState<DocumentFormState>(EMPTY_DOCUMENT_FORM)
  const [documentSaving, setDocumentSaving] = useState(false)
  const [documentLoading, setDocumentLoading] = useState(false)

  const [companyProfile, setCompanyProfile] = useState<CompanyProfile | null>(null)
  const [companyForm, setCompanyForm] = useState<CompanyFormState>(EMPTY_COMPANY_FORM)
  const [companyLoading, setCompanyLoading] = useState(true)
  const [companySaving, setCompanySaving] = useState(false)
  const [companyError, setCompanyError] = useState<string | null>(null)

  const [filterCategory, setFilterCategory] = useState('all')
  const [filterState, setFilterState] = useState<'all' | 'active' | 'inactive'>('all')
  const documentFormRef = useRef<HTMLFormElement | null>(null)

  const selectedDocument = useMemo(
    () => documents.find((item) => item.id === selectedDocumentId) || null,
    [documents, selectedDocumentId],
  )

  const loadDocuments = useCallback(async () => {
    if (!token) {
      return
    }
    setDocumentsLoading(true)
    setDocumentsError(null)
    try {
      const params = new URLSearchParams()
      if (filterCategory !== 'all') {
        params.set('category', filterCategory)
      }
      if (filterState === 'active') {
        params.set('active_only', 'true')
      }
      if (filterState === 'inactive') {
        params.set('active_only', 'false')
      }
      const suffix = params.toString() ? `?${params.toString()}` : ''
      const response = await apiFetch<KnowledgeDocumentListResponse>(
        `/v1/knowledge-documents${suffix}`,
        {},
        token,
      )
      setDocuments(response.items)
      if (selectedDocumentId && !response.items.some((item) => item.id === selectedDocumentId)) {
        setSelectedDocumentId(null)
        setDocumentForm(EMPTY_DOCUMENT_FORM)
      }
    } catch (err) {
      setDocumentsError(err instanceof ApiError ? err.message : 'Не удалось загрузить документы базы знаний.')
    } finally {
      setDocumentsLoading(false)
    }
  }, [filterCategory, filterState, selectedDocumentId, token])

  const loadCompanyProfile = useCallback(async () => {
    if (!token) {
      return
    }
    setCompanyLoading(true)
    setCompanyError(null)
    try {
      const response = await apiFetch<CompanyProfile | null>('/v1/company-profile', {}, token)
      setCompanyProfile(response)
      setCompanyForm(toCompanyFormState(response))
    } catch (err) {
      setCompanyError(err instanceof ApiError ? err.message : 'Не удалось загрузить профиль компании.')
    } finally {
      setCompanyLoading(false)
    }
  }, [token])

  useEffect(() => {
    void loadDocuments()
  }, [loadDocuments])

  useEffect(() => {
    void loadCompanyProfile()
  }, [loadCompanyProfile])

  useEffect(() => {
    if (!token || !selectedDocumentId) {
      return
    }
    let mounted = true
    setDocumentLoading(true)
    setDocumentsError(null)
    apiFetch<KnowledgeDocumentRead>(`/v1/knowledge-documents/${selectedDocumentId}`, {}, token)
      .then((response) => {
        if (mounted) {
          setDocumentForm(toDocumentFormState(response))
        }
      })
      .catch((err) => {
        if (mounted) {
          setDocumentsError(err instanceof ApiError ? err.message : 'Не удалось загрузить детали документа.')
        }
      })
      .finally(() => {
        if (mounted) {
          setDocumentLoading(false)
        }
      })
    return () => {
      mounted = false
    }
  }, [selectedDocumentId, token])

  function updateDocumentField<K extends keyof DocumentFormState>(key: K, value: DocumentFormState[K]) {
    setDocumentForm((current) => ({ ...current, [key]: value }))
  }

  function updateCompanyField<K extends keyof CompanyFormState>(key: K, value: CompanyFormState[K]) {
    setCompanyForm((current) => ({ ...current, [key]: value }))
  }

  async function handleDocumentSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (!token) {
      return
    }
    setDocumentSaving(true)
    setDocumentsError(null)

    const metadata = buildDocumentMetadata(documentForm)

    const payload = {
      title: documentForm.title,
      category: documentForm.category,
      content: documentForm.content,
      is_active: documentForm.is_active,
      notes: documentForm.notes,
      metadata,
    }

    try {
      const response = await apiFetch<KnowledgeDocumentRead>(
        selectedDocumentId ? `/v1/knowledge-documents/${selectedDocumentId}` : '/v1/knowledge-documents',
        {
          method: selectedDocumentId ? 'PATCH' : 'POST',
          body: JSON.stringify(payload),
        },
        token,
      )
      setSelectedDocumentId(response.id)
      setDocumentForm(toDocumentFormState(response))
      await loadDocuments()
    } catch (err) {
      setDocumentsError(err instanceof ApiError ? err.message : 'Не удалось сохранить документ.')
    } finally {
      setDocumentSaving(false)
    }
  }

  async function handleDocumentDisable() {
    if (!token || !selectedDocumentId) {
      return
    }
    setDocumentSaving(true)
    setDocumentsError(null)
    try {
      const response = await apiFetch<KnowledgeDocumentRead>(
        `/v1/knowledge-documents/${selectedDocumentId}`,
        { method: 'DELETE' },
        token,
      )
      setDocumentForm(toDocumentFormState(response))
      await loadDocuments()
    } catch (err) {
      setDocumentsError(err instanceof ApiError ? err.message : 'Не удалось деактивировать документ.')
    } finally {
      setDocumentSaving(false)
    }
  }

  async function handleCompanySubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (!token) {
      return
    }
    setCompanySaving(true)
    setCompanyError(null)

    const config = buildCompanyConfig(companyForm)

    try {
      const response = await apiFetch<CompanyProfile>(
        '/v1/company-profile',
        {
          method: 'PUT',
          body: JSON.stringify({
            name: companyForm.name,
            legal_name: companyForm.legal_name,
            description: companyForm.description,
            value_proposition: companyForm.value_proposition,
            target_audience: companyForm.target_audience,
            contact_info: companyForm.contact_info,
            website_url: companyForm.website_url,
            working_hours: companyForm.working_hours,
            compliance_notes: companyForm.compliance_notes,
            is_active: companyForm.is_active,
            config,
          }),
        },
        token,
      )
      setCompanyProfile(response)
      setCompanyForm(toCompanyFormState(response))
    } catch (err) {
      setCompanyError(err instanceof ApiError ? err.message : 'Не удалось сохранить профиль компании.')
    } finally {
      setCompanySaving(false)
    }
  }

  function startCreateDocument() {
    setSelectedDocumentId(null)
    setDocumentForm(EMPTY_DOCUMENT_FORM)
    setDocumentsError(null)
    setTimeout(() => {
      documentFormRef.current?.scrollIntoView({ behavior: 'smooth', block: 'start' })
    }, 50)
  }

  return (
    <section className="stack-page">
      <article className="hero-card split-card">
        <div>
          <p className="eyebrow">Слой знаний</p>
          <h3>Данные компании и документы базы знаний</h3>
          <p>
            Здесь слой знаний разделён на три части: профиль компании, документы базы знаний и привязки к
            агентам. Документы управляются отдельно и потом могут перейти к более серьёзному поиску без
            переписывания модели.
          </p>
        </div>
        <div className="button-row">
          <button type="button" className="primary-button" onClick={startCreateDocument}>
            Создать документ
          </button>
        </div>
      </article>

      {documentsError ? <div className="error-banner">{documentsError}</div> : null}
      {companyError ? <div className="error-banner">{companyError}</div> : null}

      <div className="editor-grid knowledge-grid">
        <div className="editor-form">
          <section className="panel-card form-section">
            <div className="panel-header">
              <div>
                <p className="eyebrow">Документы базы знаний</p>
                <h4>{documentsLoading ? 'Загрузка…' : `${documents.length} документов`}</h4>
              </div>
              <div className="button-row compact-actions">
                <label>
                  Категория
                  <select value={filterCategory} onChange={(event) => setFilterCategory(event.target.value)}>
                    <option value="all">Все категории</option>
                    {CATEGORY_OPTIONS.map((option) => (
                      <option key={option.value} value={option.value}>
                        {option.label}
                      </option>
                    ))}
                  </select>
                </label>
                <label>
                  Статус
                  <select
                    value={filterState}
                    onChange={(event) => setFilterState(event.target.value as 'all' | 'active' | 'inactive')}
                  >
                    <option value="all">Все</option>
                    <option value="active">Только активные</option>
                    <option value="inactive">Только неактивные</option>
                  </select>
                </label>
              </div>
            </div>

            {documentsLoading ? (
              <div className="empty-state">Загружаем документы базы знаний…</div>
            ) : documents.length === 0 ? (
              <div className="empty-state">
                Под текущий фильтр документов нет. Можно вручную создать первую запись базы знаний.
              </div>
            ) : (
              <div className="data-table-wrap">
                <table className="data-table">
                  <thead>
                    <tr>
                      <th>Название</th>
                      <th>Категория</th>
                      <th>Статус</th>
                      <th>Обновлён</th>
                    </tr>
                  </thead>
                  <tbody>
                    {documents.map((document) => (
                      <tr
                        key={document.id}
                        className={selectedDocumentId === document.id ? 'selected-row' : ''}
                        onClick={() => setSelectedDocumentId(document.id)}
                      >
                        <td>
                          <div className="table-primary">{document.title}</div>
                          <div className="table-secondary mono-inline">{document.id}</div>
                        </td>
                        <td>{document.category}</td>
                        <td>
                          <span className={`status-pill${document.is_active ? ' live' : ''}`}>
                            {document.is_active ? 'активен' : 'отключён'}
                          </span>
                        </td>
                        <td>{new Date(document.updated_at).toLocaleString()}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </section>

          <form ref={documentFormRef} className="panel-card form-section" onSubmit={handleDocumentSubmit}>
            <div className="panel-header">
              <div>
                <p className="eyebrow">Редактор документа</p>
                <h4>{selectedDocument ? selectedDocument.title : 'Создать документ базы знаний'}</h4>
              </div>
              <div className="button-row compact-actions">
                {selectedDocumentId ? (
                  <button type="button" className="ghost-button" onClick={startCreateDocument}>
                    Новый
                  </button>
                ) : null}
                {selectedDocumentId ? (
                  <button
                    type="button"
                    className="danger-button"
                    onClick={() => void handleDocumentDisable()}
                    disabled={documentSaving}
                  >
                    Деактивировать
                  </button>
                ) : null}
              </div>
            </div>

            {documentLoading ? <div className="empty-state">Загружаем документ…</div> : null}

            <div className="two-column-fields">
              <label>
                Название
                <input
                  value={documentForm.title}
                  onChange={(event) => updateDocumentField('title', event.target.value)}
                  required
                />
              </label>
              <label>
                Категория
                <select
                  value={documentForm.category}
                  onChange={(event) => updateDocumentField('category', event.target.value)}
                >
                  {CATEGORY_OPTIONS.map((option) => (
                    <option key={option.value} value={option.value}>
                      {option.label}
                    </option>
                  ))}
                </select>
              </label>
            </div>

            <label className="toggle-row boxed-toggle">
              <input
                type="checkbox"
                checked={documentForm.is_active}
                onChange={(event) => updateDocumentField('is_active', event.target.checked)}
              />
              <span>Документ активен</span>
            </label>

            <label>
              Содержимое
              <textarea
                value={documentForm.content}
                onChange={(event) => updateDocumentField('content', event.target.value)}
                rows={12}
                required
              />
            </label>

            <label>
              Примечания
              <textarea
                value={documentForm.notes}
                onChange={(event) => updateDocumentField('notes', event.target.value)}
                rows={4}
              />
            </label>
            <label>
              Источник документа
              <select
                value={documentForm.metadataSource}
                onChange={(event) => updateDocumentField('metadataSource', event.target.value)}
              >
                <option value="manual">Добавлен вручную</option>
                <option value="import">Импортирован</option>
                <option value="generated">Сгенерирован системой</option>
              </select>
            </label>
            <details className="advanced-settings">
              <summary>Расширенные метаданные для техподдержки</summary>
              <p className="compact-copy table-secondary">
                Обычному пользователю этот JSON не нужен. Он нужен только если мы специально диагностируем источник или служебные поля документа.
              </p>
              <label>
                Служебные метаданные
                <textarea
                  value={documentForm.metadataText}
                  onChange={(event) => updateDocumentField('metadataText', event.target.value)}
                  rows={8}
                  className="mono-textarea"
                />
              </label>
            </details>

            <div className="button-row">
              <button type="submit" className="primary-button" disabled={documentSaving}>
                {documentSaving ? 'Сохранение…' : selectedDocumentId ? 'Сохранить документ' : 'Создать документ'}
              </button>
            </div>
          </form>
        </div>

        <aside className="editor-sidebar">
          <form className="panel-card form-section" onSubmit={handleCompanySubmit}>
            <div className="panel-header">
              <div>
                <p className="eyebrow">Профиль компании</p>
                <h4>{companyProfile?.name || 'Данные компании'}</h4>
              </div>
            </div>

            {companyLoading ? <div className="empty-state">Загружаем профиль компании…</div> : null}

            <label>
              Название компании
              <input
                value={companyForm.name}
                onChange={(event) => updateCompanyField('name', event.target.value)}
                required
              />
            </label>
            <label>
              Юридическое название
              <input
                value={companyForm.legal_name}
                onChange={(event) => updateCompanyField('legal_name', event.target.value)}
              />
            </label>
            <label>
              Описание
              <textarea
                value={companyForm.description}
                onChange={(event) => updateCompanyField('description', event.target.value)}
                rows={4}
              />
            </label>
            <label>
              Ценностное предложение
              <textarea
                value={companyForm.value_proposition}
                onChange={(event) => updateCompanyField('value_proposition', event.target.value)}
                rows={4}
              />
            </label>
            <label>
              Целевая аудитория
              <textarea
                value={companyForm.target_audience}
                onChange={(event) => updateCompanyField('target_audience', event.target.value)}
                rows={4}
              />
            </label>
            <label>
              Контактная информация
              <textarea
                value={companyForm.contact_info}
                onChange={(event) => updateCompanyField('contact_info', event.target.value)}
                rows={3}
              />
            </label>
            <label>
              Сайт компании
              <input
                value={companyForm.website_url}
                onChange={(event) => updateCompanyField('website_url', event.target.value)}
              />
            </label>
            <label>
              Часы работы
              <textarea
                value={companyForm.working_hours}
                onChange={(event) => updateCompanyField('working_hours', event.target.value)}
                rows={3}
              />
            </label>
            <label>
              Заметки по соответствию
              <textarea
                value={companyForm.compliance_notes}
                onChange={(event) => updateCompanyField('compliance_notes', event.target.value)}
                rows={4}
              />
            </label>
            <label className="toggle-row boxed-toggle">
              <input
                type="checkbox"
                checked={companyForm.is_active}
                onChange={(event) => updateCompanyField('is_active', event.target.checked)}
              />
              <span>Профиль компании активен</span>
            </label>
            <label>
              Язык профиля компании
              <select
                value={companyForm.locale}
                onChange={(event) => updateCompanyField('locale', event.target.value)}
              >
                <option value="ru-RU">Русский</option>
                <option value="en-US">English</option>
              </select>
            </label>
            <details className="advanced-settings">
              <summary>Расширенная конфигурация для техподдержки</summary>
              <p className="compact-copy table-secondary">
                Этот JSON не нужен обычному пользователю. Мы оставили его только для редких служебных правок без отдельной миграции интерфейса.
              </p>
              <label>
                Служебная конфигурация компании
                <textarea
                  value={companyForm.configText}
                  onChange={(event) => updateCompanyField('configText', event.target.value)}
                  rows={8}
                  className="mono-textarea"
                />
              </label>
            </details>
            <div className="button-row">
              <button type="submit" className="primary-button" disabled={companySaving}>
                {companySaving ? 'Сохранение…' : 'Сохранить профиль компании'}
              </button>
            </div>
          </form>

          <section className="panel-card form-section">
            <div className="panel-header">
              <div>
                <p className="eyebrow">Контролируемая сборка</p>
                <h4>Заметка о рантайме</h4>
              </div>
            </div>
            <p className="sidebar-copy compact-copy">
              На этом этапе слой знаний хранится и привязывается отдельно. Рантайм получает подготовленный
              структурированный контекст, но мы не вставляем всю базу знаний слепо в системный промпт.
            </p>
          </section>
        </aside>
      </div>
    </section>
  )
}
