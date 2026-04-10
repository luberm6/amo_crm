import { useLocation } from 'react-router-dom'

import { useAuth } from '../auth/AuthContext'

const titles: Record<string, string> = {
  '/': 'Dashboard',
  '/agents': 'Agents',
  '/prompts': 'Prompts',
  '/knowledge-base': 'Knowledge Base',
  '/browser-call': 'Browser Call',
  '/providers': 'Providers',
}

export default function TopBar() {
  const location = useLocation()
  const { user, logout } = useAuth()

  return (
    <header className="topbar">
      <div>
        <p className="eyebrow">Admin Panel</p>
        <h2>{titles[location.pathname] || 'Admin'}</h2>
      </div>
      <div className="topbar-actions">
        <div className="user-badge">
          <span>{user?.email || 'admin'}</span>
          <small>admin-only</small>
        </div>
        <button type="button" className="ghost-button" onClick={logout}>
          Logout
        </button>
      </div>
    </header>
  )
}
