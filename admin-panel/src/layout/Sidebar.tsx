import { NavLink } from 'react-router-dom'

const items = [
  { to: '/', label: 'Главная' },
  { to: '/agents', label: 'Агенты' },
  { to: '/prompts', label: 'Промпты' },
  { to: '/knowledge-base', label: 'База знаний' },
  { to: '/browser-call', label: 'Браузерный звонок' },
  { to: '/providers', label: 'Провайдеры' },
]

export default function Sidebar() {
  return (
    <aside className="sidebar">
      <div className="brand-block">
        <p className="eyebrow">Внутренняя панель</p>
        <h1>AMO CRM Voice</h1>
        <p className="sidebar-copy">Рабочий контур для настройки агентов, промптов и browser-based voice QA.</p>
      </div>
      <nav className="nav-list" aria-label="Навигация администратора">
        {items.map((item) => (
          <NavLink
            key={item.to}
            to={item.to}
            end={item.to === '/'}
            className={({ isActive }) => `nav-link${isActive ? ' active' : ''}`}
          >
            {item.label}
          </NavLink>
        ))}
      </nav>
    </aside>
  )
}
