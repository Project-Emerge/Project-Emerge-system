/** Telemetry published by an EdgeRobotRuntime on `edge/{id}/status`. */
export type EdgeStatus = {
  id: number
  tick: number
  ts: number
  offloaded: boolean
  actuating: boolean
  computed: boolean
  actuation: string
  program: string
  leader: string
  neighbors: number[]
  worldSize: number
  exportPaths: number
}

/** Local, derived view kept per edge node. */
export type EdgeView = EdgeStatus & {
  rxAt: number // local timestamp of last message
  hz: number // smoothed compute rate
}
