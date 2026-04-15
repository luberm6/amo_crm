import { Link } from 'react-router-dom'

const cards = [
  { to: '/agents',         title: 'Агенты',           body: 'Карточки агентов, голосовые настройки, телеметрия и маршрутизация.' },
  { to: '/prompts',        title: 'Промпты',           body: 'Системные инструкции, правила общения и история изменений.' },
  { to: '/knowledge-base', title: 'База знаний',       body: 'Документы компании, категории знаний и контекст для агентов.' },
  { to: '/browser-call',   title: 'Браузерный звонок', body: 'Реальный контур ручной проверки поверх direct-runtime контура.' },
]

export default function DashboardPage() {
  return (
    <section className="page-grid">
      <article className="hero-card">
        <p className="eyebrow">Панель управления</p>
        <h3>Внутренняя админка для настройки агентов и голосового контура.</h3>
        <p>
          Backend остаётся на FastAPI, а настройка агентов, провайдеров и голосовой проверки теперь живёт
          в полноценной веб-панели, а не во временных страницах и ручных скриптах.
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
