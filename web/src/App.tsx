import { useState } from 'react'
import Navbar from './components/Navbar'
import Dashboard from './pages/Dashboard'
import ExperimentPanel from './components/ExperimentPanel'
import CodeEditor from './components/CodeEditor'
import DocsPage from './pages/DocsPage'
import './styles/global.css'

export default function App() {
  const [activeTab, setActiveTab] = useState('dashboard')

  return (
    <div style={{ minHeight: '100vh', backgroundColor: 'var(--bg-primary)' }}>
      <Navbar activeTab={activeTab} onTabChange={setActiveTab} />
      {activeTab === 'dashboard' && <Dashboard />}
      {activeTab === 'experiments' && <ExperimentPanel />}
      {activeTab === 'editor' && <CodeEditor />}
      {activeTab === 'docs' && <DocsPage />}
    </div>
  )
}
