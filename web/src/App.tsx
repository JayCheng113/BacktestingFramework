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
      {/* CodeEditor uses display:none to keep state alive across tab switches */}
      <div style={{ display: activeTab === 'editor' ? 'block' : 'none', height: 'calc(100vh - 48px)' }}>
        <CodeEditor />
      </div>
      {activeTab === 'docs' && <DocsPage />}
    </div>
  )
}
