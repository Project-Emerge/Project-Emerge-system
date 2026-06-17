import { useEffect, useState } from 'react'
import { useEdges } from './store'
import { EdgeCard } from './components/EdgeCard'

export default function App() {
  const { connected, edges } = useEdges()
  // Re-render ~5x/s so "last update" ages and offline/heartbeat states stay live.
  const [now, setNow] = useState(Date.now())
  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), 200)
    return () => clearInterval(t)
  }, [])

  const list = Object.values(edges).sort((a, b) => a.id - b.id)

  // Compute summary metrics for the stats panel
  const totalEdges = list.length
  const activeCount = list.filter((e) => {
    const age = now - e.rxAt
    return age <= 3000 && e.computed
  }).length

  const offloadedCount = list.filter((e) => {
    const age = now - e.rxAt
    return age <= 3000 && !e.computed
  }).length

  const offlineCount = list.filter((e) => {
    const age = now - e.rxAt
    return age > 3000
  }).length

  return (
    <div className="app-container">
      <header className="app-header">
        <div className="header-main">
          <div className="logo-section">
            <span className="logo-emoji">🧠</span>
            <div className="title-block">
              <h1>Edge Monitor</h1>
              <span className="sub">Real-time status of the decentralized EdgeRobotRuntimes</span>
            </div>
          </div>

          <div className="connection-status-wrapper">
            <div className={`status-pill ${connected ? 'connected' : 'disconnected'}`}>
              <span className="pulse-dot" />
              {connected ? 'MQTT CONNECTED' : 'MQTT DISCONNECTED'}
            </div>
          </div>
        </div>

        {/* Global Statistics Panel */}
        <div className="stats-panel">
          <div className="stat-card">
            <span className="stat-value">{totalEdges}</span>
            <span className="stat-label">Total Edges</span>
          </div>
          <div className="stat-card computing">
            <span className="stat-value">{activeCount}</span>
            <span className="stat-label">Computing</span>
          </div>
          <div className="stat-card offloaded">
            <span className="stat-value">{offloadedCount}</span>
            <span className="stat-label">Offloaded</span>
          </div>
          <div className="stat-card offline">
            <span className="stat-value">{offlineCount}</span>
            <span className="stat-label">Offline</span>
          </div>
        </div>
      </header>

      <main className="main-content">
        <div className="legend-container">
          <p className="legend">
            Each card represents an active <code>EdgeRobotRuntime</code>, transmitting state updates on{' '}
            <code>edge/&#123;id&#125;/status</code>.
          </p>
          <ul className="legend-list">
            <li>
              <span className="bullet driving" />
              <strong>COMPUTING &amp; DRIVING</strong>: Local edge instance owns and actuates the robot.
            </li>
            <li>
              <span className="bullet paused" />
              <strong>OFFLOADED</strong>: Computation is temporarily deferred to the central cluster.
            </li>
            <li>
              <span className="bullet offline" />
              <strong>OFFLINE</strong>: No telemetry received for over 3 seconds.
            </li>
          </ul>
        </div>

        <div className="cards-grid">
          {list.length === 0 ? (
            <div className="empty-state">
              <div className="empty-icon">📡</div>
              <h3>No edge telemetry received yet</h3>
              <p>
                Ensure an edge runtime is running. Start one using:
                <br />
                <code>control-panel/demo.sh 5 6</code> or check the manual steps.
              </p>
            </div>
          ) : (
            list.map((e) => <EdgeCard key={e.id} edge={e} now={now} />)
          )}
        </div>
      </main>
    </div>
  )
}
