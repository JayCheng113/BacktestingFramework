import { useState } from 'react'
import Navbar from './components/Navbar'
import Dashboard from './pages/Dashboard'
import ExperimentPanel from './components/ExperimentPanel'
import CodeEditor from './components/CodeEditor'
import ResearchPanel from './components/ResearchPanel'
import PortfolioPanel from './components/PortfolioPanel'
import DocsPage from './pages/DocsPage'
import './styles/global.css'

export default function App() {
  const [activeTab, setActiveTab] = useState('dashboard')
  const [editorMounted, setEditorMounted] = useState(false)

  const handleTabChange = (tab: string) => {
    setActiveTab(tab)
    if (tab === 'editor') setEditorMounted(true)
  }

  return (
    <div style={{ minHeight: '100vh', backgroundColor: 'var(--bg-primary)' }}>
      <Navbar activeTab={activeTab} onTabChange={handleTabChange} />
      {activeTab === 'dashboard' && <Dashboard />}
      {activeTab === 'portfolio' && <PortfolioPanel />}
      {activeTab === 'experiments' && <ExperimentPanel />}
      {editorMounted && (
        <div style={{ display: activeTab === 'editor' ? 'block' : 'none', height: 'calc(100vh - 48px)' }}>
          <CodeEditor onNavigate={handleTabChange} />
        </div>
      )}
      {activeTab === 'research' && <ResearchPanel />}
      {activeTab === 'docs' && <DocsPage />}
    </div>
  )
}
