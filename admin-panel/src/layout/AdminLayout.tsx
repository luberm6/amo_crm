import { Outlet } from 'react-router-dom'

import Sidebar from './Sidebar'
import TopBar from './TopBar'

export default function AdminLayout() {
  return (
    <div className="admin-shell">
      <Sidebar />
      <div className="admin-main">
        <TopBar />
        <main className="content-area">
          <Outlet />
        </main>
      </div>
    </div>
  )
}
