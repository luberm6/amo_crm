const cards = [
  ['Agents', 'Следующий этап: карточки агентов и конфигурация маршрутов.'],
  ['Prompts', 'Место для prompt profiles и prompt revision history.'],
  ['Knowledge Base', 'Подготовлено пространство под QA базы знаний и indexing status.'],
  ['Browser Call', 'Уже подключён как реальный QA-контур поверх Direct session runtime.'],
]

export default function DashboardPage() {
  return (
    <section className="page-grid">
      <article className="hero-card">
        <p className="eyebrow">Control Surface</p>
        <h3>Каркас админки поднят как отдельный frontend.</h3>
        <p>
          Backend остаётся на FastAPI, а browser-based voice QA теперь можно развивать из нормального web-admin,
          а не из временной HTML-страницы.
        </p>
      </article>
      <div className="dashboard-cards">
        {cards.map(([title, body]) => (
          <article key={title} className="info-card">
            <h4>{title}</h4>
            <p>{body}</p>
          </article>
        ))}
      </div>
    </section>
  )
}
