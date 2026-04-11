import { Link } from 'react-router-dom'

const cards = [
  { to: '/agents',         title: 'Агенты',           body: 'Следующий этап: карточки агентов и конфигурация маршрутов.' },
  { to: '/prompts',        title: 'Промпты',           body: 'Место для prompt profiles и prompt revision history.' },
  { to: '/knowledge-base', title: 'База знаний',       body: 'Подготовлено пространство под QA базы знаний и indexing status.' },
  { to: '/browser-call',   title: 'Браузерный звонок', body: 'Уже подключён как реальный QA-контур поверх Direct session runtime.' },
]

export default function DashboardPage() {
  return (
    <section className="page-grid">
      <article className="hero-card">
        <p className="eyebrow">Панель управления</p>
        <h3>Каркас админки поднят как отдельный frontend.</h3>
        <p>
          Backend остаётся на FastAPI, а browser-based voice QA теперь можно развивать из нормального web-admin,
          а не из временной HTML-страницы.
        </p>
      </article>
      <div className="dashboard-cards">
        {cards.map((card) => (
          <Link key={card.to} to={card.to} className="info-card">
            <h4>{card.title}</h4>
            <p>{card.body}</p>
          </Link>
        ))}
      </div>
    </section>
  )
}
