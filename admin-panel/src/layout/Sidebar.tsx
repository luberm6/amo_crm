import { NavLink } from 'react-router-dom'

const items = [
  { to: '/', label: 'Dashboard' },
  { to: '/agents', label: 'Agents' },
  { to: '/prompts', label: 'Prompts' },
  { to: '/knowledge-base', label: 'Knowledge Base' },
  { to: '/browser-call', label: 'Browser Call' },
  { to: '/providers', label: 'Providers' },
]

export default function Sidebar() {
  return (
    <aside className="sidebar">
      <div className="brand-block">
        <p className="eyebrow">Internal Admin</p>
        <h1>AMO CRM Voice</h1>
        <p className="sidebar-copy">Рабочий контур для настройки агентов, промптов и browser-based voice QA.</p>
      </div>
      <nav className="nav-list" aria-label="Admin navigation">
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
