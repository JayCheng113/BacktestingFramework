import Navbar from './components/Navbar'
import Dashboard from './pages/Dashboard'
import './styles/global.css'

export default function App() {
  return (
    <div style={{ minHeight: '100vh', backgroundColor: 'var(--bg-primary)' }}>
      <Navbar />
      <Dashboard />
    </div>
  )
}
