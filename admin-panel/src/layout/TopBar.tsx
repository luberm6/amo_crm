import { useLocation } from 'react-router-dom'

import { useAuth } from '../auth/AuthContext'

const titles: Record<string, string> = {
  '/': 'Главная',
  '/agents': 'Агенты',
  '/prompts': 'Промпты',
  '/knowledge-base': 'База знаний',
  '/browser-call': 'Браузерный звонок',
  '/providers': 'Провайдеры',
}

export default function TopBar() {
  const location = useLocation()
  const { user, logout } = useAuth()

  return (
    <header className="topbar">
      <div>
        <p className="eyebrow">Панель администратора</p>
        <h2>{titles[location.pathname] || 'Администратор'}</h2>
      </div>
      <div className="topbar-actions">
        <div className="user-badge">
          <span>{user?.email || 'admin'}</span>
          <small>только для администратора</small>
        </div>
        <button type="button" className="ghost-button" onClick={logout}>
          Выйти
        </button>
      </div>
    </header>
  )
}
