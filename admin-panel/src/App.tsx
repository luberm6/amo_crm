import { Navigate, Route, Routes } from 'react-router-dom'

import ProtectedRoute from './components/ProtectedRoute'
import AdminLayout from './layout/AdminLayout'
import AgentEditorPage from './pages/AgentEditorPage'
import AgentsPage from './pages/AgentsPage'
import BrowserCallPage from './pages/BrowserCallPage'
import DashboardPage from './pages/DashboardPage'
import KnowledgeBasePage from './pages/KnowledgeBasePage'
import LoginPage from './pages/LoginPage'
import PromptsPage from './pages/PromptsPage'
import ProvidersPage from './pages/ProvidersPage'

export default function App() {
  return (
    <Routes>
      <Route path="/login" element={<LoginPage />} />
      <Route element={<ProtectedRoute />}>
        <Route element={<AdminLayout />}>
          <Route path="/" element={<DashboardPage />} />
          <Route path="/agents" element={<AgentsPage />} />
          <Route path="/agents/:agentId" element={<AgentEditorPage />} />
          <Route path="/prompts" element={<PromptsPage />} />
          <Route path="/knowledge-base" element={<KnowledgeBasePage />} />
          <Route path="/browser-call" element={<BrowserCallPage />} />
          <Route path="/providers" element={<ProvidersPage />} />
        </Route>
      </Route>
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  )
}
