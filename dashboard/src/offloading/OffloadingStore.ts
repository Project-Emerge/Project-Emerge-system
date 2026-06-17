import { create } from 'zustand'

// Where the offloading-manager REST API lives (PUT /{id}, GET /). Defaults to the host port mapping.
const MANAGER_URL = (import.meta.env.VITE_OFFLOADING_MANAGER_URL || 'http://localhost:8081').replace(/\/$/, '')

type OffloadingStore = {
  /** id -> true if the central runtime computes it (aggregate=true), false if its edge runtime does. */
  central: Record<number, boolean>
  /** ids the manager knows about — i.e. robots that have an edge runtime able to take over. */
  known: number[]
  /** A robot the manager doesn't list is computed by the central by default. */
  isCentral: (id: number) => boolean
  hasEdge: (id: number) => boolean
  setCompute: (id: number, central: boolean) => Promise<void>
}

export const useOffloading = create<OffloadingStore>()((set, get) => {
  const poll = async () => {
    try {
      const res = await fetch(`${MANAGER_URL}/`)
      const body = await res.json()
      const central: Record<number, boolean> = {}
      Object.entries(body.robots || {}).forEach(([id, st]) => {
        central[Number(id)] = !!(st as { aggregate?: boolean }).aggregate
      })
      set({ central, known: Object.keys(body.robots || {}).map(Number) })
    } catch {
      /* manager is optional: leave last known state */
    }
  }
  setInterval(poll, 1500)
  poll()

  return {
    central: {},
    known: [],
    isCentral: (id) => {
      const c = get().central
      return id in c ? c[id] : true
    },
    hasEdge: (id) => get().known.includes(id),
    setCompute: async (id, central) => {
      try {
        await fetch(`${MANAGER_URL}/${id}`, {
          method: 'PUT',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ id, type: { aggregate: central, aruco: false, neighbor: false } }),
        })
        set((s) => ({ central: { ...s.central, [id]: central } }))
      } catch (e) {
        console.error(`Offloading manager request failed for robot ${id}`, e)
        alert(`Offloading manager request failed for robot ${id}.\nIs it reachable at ${MANAGER_URL}?`)
      }
    },
  }
})
