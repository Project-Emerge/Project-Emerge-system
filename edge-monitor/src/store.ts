import { create } from 'zustand'
import mqtt from 'mqtt'
import type { EdgeStatus, EdgeView } from './types'

const BROKER_URL = import.meta.env.VITE_MQTT_BROKER_URL || 'ws://localhost:9001/mqtt'

type Store = {
  connected: boolean
  edges: Record<number, EdgeView>
}

export const useEdges = create<Store>()((set, get) => {
  const client = mqtt.connect(BROKER_URL)

  client.on('connect', () => {
    set({ connected: true })
    client.subscribe('edge/+/status')
  })
  client.on('close', () => set({ connected: false }))
  client.on('error', () => set({ connected: false }))

  client.on('message', (topic, payload) => {
    const m = topic.match(/^edge\/(\d+)\/status$/)
    if (!m) return
    let d: EdgeStatus
    try {
      d = JSON.parse(payload.toString())
    } catch {
      return
    }
    const now = Date.now()
    const prev = get().edges[d.id]
    let hz = prev?.hz ?? 0
    if (prev && d.tick > prev.tick && now > prev.rxAt) {
      const inst = ((d.tick - prev.tick) * 1000) / (now - prev.rxAt)
      hz = prev.hz ? prev.hz * 0.7 + inst * 0.3 : inst
    }
    set((s) => ({ edges: { ...s.edges, [d.id]: { ...d, rxAt: now, hz } } }))
  })

  return { connected: false, edges: {} }
})
