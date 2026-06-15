import type { EdgeView } from '../types'

export function EdgeCard({ edge, now }: { edge: EdgeView; now: number }) {
  const age = now - edge.rxAt
  const offline = age > 3000

  const neighbors = edge.neighbors || []
  const program = edge.program || '—'
  const leader = edge.leader || '—'
  const actuation = edge.actuation || '—'
  const tick = edge.tick ?? 0
  const exportPaths = edge.exportPaths ?? 0
  const worldSize = edge.worldSize ?? 0

  let cls = 'offline'
  let label = 'OFFLINE'
  if (!offline) {
    if (edge.computed) {
      cls = 'driving'
      label = 'COMPUTING & DRIVING'
    } else {
      cls = 'paused'
      label = 'OFFLOADED'
    }
  }
  const beating = !offline && age < 250

  return (
    <div className={`card-wrapper ${cls}`}>
      <div className="card">
        <div className="card-header">
          <div className="title-area">
            <span className={`heart${beating ? ' beat' : ''}`} />
            <h2>Robot #{edge.id}</h2>
          </div>
          <span className="badge-runtime">edge node</span>
        </div>

        <div className="status-badge-container">
          <span className={`state-badge ${cls}`}>{label}</span>
        </div>

        <div className="metrics-grid">
          <div className="metric-item">
            <span className="k">
              <svg className="metric-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                <path d="M16 18l6-6-6-6M8 6l-6 6 6 6" />
              </svg>
              Program
            </span>
            <span className="v code-style">{program}</span>
          </div>

          <div className="metric-item">
            <span className="k">
              <svg className="metric-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                <path d="M12 2l3.09 6.26L22 9.27l-5 4.87 1.18 6.88L12 17.77l-6.18 3.25L7 14.14 2 9.27l6.91-1.01L12 2z" />
              </svg>
              Leader
            </span>
            <span className="v leader-badge">{leader}</span>
          </div>

          <div className="metric-item full-width">
            <span className="k">
              <svg className="metric-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                <path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v-2M9 7a4 4 0 1 0 0-8 4 4 0 0 0 0 8z" />
              </svg>
              Neighbours used
            </span>
            <div className="v">
              {neighbors.length ? (
                <div className="chips-container">
                  {neighbors.map((n) => (
                    <span className="neighbor-chip" key={n}>
                      #{n}
                    </span>
                  ))}
                </div>
              ) : (
                <span className="neighbor-none">none</span>
              )}
            </div>
          </div>

          <div className="metric-item full-width">
            <span className="k">
              <svg className="metric-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                <path d="M13 2L3 14h9l-1 8 10-12h-9l1-8z" />
              </svg>
              Actuation
            </span>
            <span className="v">
              <span className={`act-pill ${actuation !== '—' && actuation !== 'Stop' ? 'active' : ''}`}>
                {actuation}
              </span>
            </span>
          </div>

          <div className="metric-item">
            <span className="k">Ticks computed</span>
            <span className="v highlight-number">{tick.toLocaleString()}</span>
          </div>

          <div className="metric-item">
            <span className="k">Compute rate</span>
            <span className="v highlight-number hz-value">
              {edge.hz ? `${edge.hz.toFixed(1)} Hz` : '—'}
            </span>
          </div>

          <div className="metric-item">
            <span className="k">Export size</span>
            <span className="v">{exportPaths} paths</span>
          </div>

          <div className="metric-item">
            <span className="k">World seen</span>
            <span className="v">{worldSize} robots</span>
          </div>
        </div>

        <div className="card-footer">
          <span className="footer-label">Last update</span>
          <span className={`footer-value ${offline ? 'stale' : 'fresh'}`}>
            {offline ? '> 3s ago' : `${age} ms ago`}
          </span>
        </div>
      </div>
    </div>
  )
}
