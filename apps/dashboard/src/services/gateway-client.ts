import type {
  GatewayMqttMessage,
  GatewayServerMessage,
  GatewayStatus,
} from "../../shared/protocol";

type PendingRequest = {
  resolve: () => void;
  reject: (error: Error) => void;
};

type GatewayCallbacks = {
  onConnection: (status: GatewayStatus) => void;
  onMqttMessage: (message: GatewayMqttMessage) => void;
};

export class GatewayClient {
  private socket: WebSocket | null = null;
  private reconnectTimer: number | null = null;
  private reconnectAttempt = 0;
  private readonly pending = new Map<string, PendingRequest>();

  constructor(private readonly callbacks: GatewayCallbacks) {}

  connect(): void {
    if (this.socket?.readyState === WebSocket.OPEN || this.socket?.readyState === WebSocket.CONNECTING) return;
    this.callbacks.onConnection("connecting");
    const configured = import.meta.env.VITE_GATEWAY_URL as string | undefined;
    const url = configured ?? `${window.location.protocol === "https:" ? "wss" : "ws"}://${window.location.host}/ws`;
    this.socket = new WebSocket(url);
    this.socket.addEventListener("message", (event) => this.handleMessage(event.data));
    this.socket.addEventListener("close", () => this.scheduleReconnect());
    this.socket.addEventListener("error", () => this.socket?.close());
  }

  disconnect(): void {
    if (this.reconnectTimer !== null) window.clearTimeout(this.reconnectTimer);
    this.reconnectTimer = null;
    this.socket?.close();
    this.socket = null;
    for (const request of this.pending.values()) request.reject(new Error("Gateway disconnected"));
    this.pending.clear();
  }

  publish(topic: string, payload: unknown): Promise<void> {
    if (this.socket?.readyState !== WebSocket.OPEN) {
      return Promise.reject(new Error("Gateway is not connected"));
    }
    const requestId = crypto.randomUUID();
    this.socket.send(JSON.stringify({ type: "publish", requestId, topic, payload }));
    return new Promise<void>((resolve, reject) => {
      const timeout = window.setTimeout(() => {
        this.pending.delete(requestId);
        reject(new Error("The broker did not confirm the save"));
      }, 8_000);
      this.pending.set(requestId, {
        resolve: () => {
          window.clearTimeout(timeout);
          resolve();
        },
        reject: (error) => {
          window.clearTimeout(timeout);
          reject(error);
        },
      });
    });
  }

  private handleMessage(raw: unknown): void {
    if (typeof raw !== "string") return;
    let message: GatewayServerMessage;
    try {
      message = JSON.parse(raw) as GatewayServerMessage;
    } catch {
      return;
    }
    if (message.type === "connection") {
      this.reconnectAttempt = 0;
      this.callbacks.onConnection(message.status);
    } else if (message.type === "mqtt") {
      this.callbacks.onMqttMessage(message);
    } else if (message.type === "snapshot") {
      message.messages.forEach((snapshot) => this.callbacks.onMqttMessage(snapshot));
    } else if (message.type === "publish-result") {
      const request = this.pending.get(message.requestId);
      if (!request) return;
      this.pending.delete(message.requestId);
      if (message.ok) request.resolve();
      else request.reject(new Error(message.error));
    }
  }

  private scheduleReconnect(): void {
    this.callbacks.onConnection("offline");
    if (this.reconnectTimer !== null) return;
    const delay = Math.min(10_000, 750 * 2 ** this.reconnectAttempt);
    this.reconnectAttempt += 1;
    this.reconnectTimer = window.setTimeout(() => {
      this.reconnectTimer = null;
      this.connect();
    }, delay);
  }
}
