import { createServer } from "node:http";
import { existsSync, readFileSync, statSync } from "node:fs";
import { join, normalize } from "node:path";
import { randomUUID } from "node:crypto";
import mqtt from "mqtt";
import { WebSocketServer, WebSocket } from "ws";
import {
  ClientPublishMessageSchema,
  MQTT_SUBSCRIPTIONS,
  type GatewayMqttMessage,
  type GatewayServerMessage,
  type GatewayStatus,
  validateConfigurationPublication,
} from "../shared/protocol.js";

try {
  process.loadEnvFile(".env");
} catch (error) {
  if ((error as NodeJS.ErrnoException).code !== "ENOENT") throw error;
}

const port = Number(process.env.HTTP_PORT ?? 8787);
const mqttUrl = process.env.MQTT_URL ?? "mqtt://192.168.8.1:1883";
const distDirectory = join(process.cwd(), "dist");
const snapshots = new Map<string, GatewayMqttMessage>();
let brokerStatus: GatewayStatus = "connecting";

const mqttClient = mqtt.connect(mqttUrl, {
  clientId: `emerge-dashboard-${randomUUID().slice(0, 8)}`,
  username: process.env.MQTT_USERNAME,
  password: process.env.MQTT_PASSWORD,
  reconnectPeriod: 1_000,
  connectTimeout: 5_000,
  queueQoSZero: false,
});

const server = createServer((request, response) => {
  const requestPath = request.url?.split("?")[0] ?? "/";
  const safePath = normalize(requestPath).replace(/^\.\.(?:\/|\\|$)+/, "");
  const requestedFile = join(distDirectory, safePath === "/" ? "index.html" : safePath);
  const file = existsSync(requestedFile) && statSync(requestedFile).isFile()
    ? requestedFile
    : join(distDirectory, "index.html");

  if (!existsSync(file)) {
    response.writeHead(503, { "Content-Type": "text/plain; charset=utf-8" });
    response.end("Build not found. Run npm run build or use npm run dev.");
    return;
  }

  const extension = file.split(".").pop();
  const contentType = extension === "html"
    ? "text/html; charset=utf-8"
    : extension === "js"
      ? "text/javascript; charset=utf-8"
      : extension === "css"
        ? "text/css; charset=utf-8"
        : "application/octet-stream";
  response.writeHead(200, { "Content-Type": contentType });
  response.end(readFileSync(file));
});

const webSocketServer = new WebSocketServer({ server, path: "/ws" });

function send(socket: WebSocket, message: GatewayServerMessage): void {
  if (socket.readyState === WebSocket.OPEN) {
    socket.send(JSON.stringify(message));
  }
}

function broadcast(message: GatewayServerMessage): void {
  for (const socket of webSocketServer.clients) {
    send(socket, message);
  }
}

function setBrokerStatus(status: GatewayStatus): void {
  brokerStatus = status;
  broadcast({ type: "connection", status });
}

mqttClient.on("connect", () => {
  setBrokerStatus("connected");
  mqttClient.subscribe([...MQTT_SUBSCRIPTIONS], { qos: 0 }, (error) => {
    if (error) {
      console.error("Unable to subscribe to MQTT topics", error.message);
    }
  });
});

mqttClient.on("reconnect", () => setBrokerStatus("connecting"));
mqttClient.on("offline", () => setBrokerStatus("offline"));
mqttClient.on("close", () => setBrokerStatus("offline"));
mqttClient.on("error", (error) => console.error("MQTT error", error.message));

mqttClient.on("message", (topic, rawPayload) => {
  let payload: unknown = rawPayload.toString("utf8");
  try {
    payload = JSON.parse(rawPayload.toString("utf8"));
  } catch {
    // Keep malformed payloads observable without trusting them as telemetry.
  }
  const message: GatewayMqttMessage = { topic, payload, receivedAt: Date.now() };
  snapshots.set(topic, message);
  broadcast({ type: "mqtt", ...message });
});

webSocketServer.on("connection", (socket) => {
  send(socket, { type: "connection", status: brokerStatus });
  send(socket, { type: "snapshot", messages: [...snapshots.values()] });

  socket.on("message", (buffer) => {
    let input: unknown;
    try {
      input = JSON.parse(buffer.toString());
    } catch {
      send(socket, { type: "publish-result", requestId: "unknown", ok: false, error: "Messaggio WebSocket non valido" });
      return;
    }

    const parsed = ClientPublishMessageSchema.safeParse(input);
    if (!parsed.success) {
      const requestId = typeof input === "object" && input !== null && "requestId" in input && typeof input.requestId === "string"
        ? input.requestId
        : "unknown";
      send(socket, { type: "publish-result", requestId, ok: false, error: "Richiesta di pubblicazione non valida" });
      return;
    }

    const { requestId, topic, payload } = parsed.data;
    const validationError = validateConfigurationPublication(topic, payload);
    if (validationError) {
      send(socket, { type: "publish-result", requestId, ok: false, error: validationError });
      return;
    }
    if (!mqttClient.connected) {
      send(socket, { type: "publish-result", requestId, ok: false, error: "MQTT broker is not connected" });
      return;
    }

    mqttClient.publish(topic, JSON.stringify(payload), { qos: 1, retain: true }, (error) => {
      if (error) {
        send(socket, { type: "publish-result", requestId, ok: false, error: error.message });
      } else {
        send(socket, { type: "publish-result", requestId, ok: true });
      }
    });
  });
});

server.listen(port, () => {
  console.log(`Gateway dashboard in ascolto su http://localhost:${port} (MQTT: ${mqttUrl})`);
});
