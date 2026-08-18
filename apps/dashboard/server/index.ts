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
  isMotorCommandTopic,
  isTransientCommandTopic,
  validateClientPublication,
} from "../shared/protocol.js";
import { FirmwareStore } from "./firmware-store.js";

try {
  process.loadEnvFile(join(process.cwd(), "../../.env"));
} catch (error) {
  if ((error as NodeJS.ErrnoException).code !== "ENOENT") throw error;
}

const port = Number(process.env.HTTP_PORT ?? 8787);
const mqttUrl = process.env.MQTT_URL ?? "mqtt://192.168.8.1:1883";
const distDirectory = join(process.cwd(), "dist");
const firmwareDirectory = process.env.FIRMWARE_DIRECTORY ?? join(process.cwd(), "firmware");
const firmwareStore = new FirmwareStore(firmwareDirectory);
// The Dropbot OTA slots are 0x1e0000 bytes each (see the firmware's partitions.csv).
const maximumFirmwareSize = 0x1e0000;
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
  void handleHttpRequest(request, response);
});

async function handleHttpRequest(request: import("node:http").IncomingMessage, response: import("node:http").ServerResponse): Promise<void> {
  const requestPath = request.url?.split("?")[0] ?? "/";
  if (request.method === "GET" && requestPath === "/api/firmware/latest") {
    const manifest = await firmwareStore.latest();
    if (!manifest) {
      sendJson(response, 404, { error: "No firmware has been uploaded." });
      return;
    }
    sendJson(response, 200, manifest);
    return;
  }

  if (request.method === "POST" && requestPath === "/api/firmware/latest") {
    if (!request.headers["content-type"]?.startsWith("application/octet-stream")) {
      sendJson(response, 415, { error: "Upload a .bin file as application/octet-stream." });
      return;
    }
    const version = request.headers["x-firmware-version"];
    if (typeof version !== "string") {
      sendJson(response, 400, { error: "A firmware version is required." });
      return;
    }
    const declaredLength = Number(request.headers["content-length"]);
    if (Number.isFinite(declaredLength) && declaredLength > maximumFirmwareSize) {
      request.resume();
      sendJson(response, 413, { error: "Firmware image exceeds the 1.875 MiB Dropbot OTA slot limit." });
      return;
    }
    try {
      const image = await readRequestBody(request, maximumFirmwareSize);
      const manifest = await firmwareStore.save(version, image);
      sendJson(response, 201, manifest);
    } catch (error) {
      sendJson(response, error instanceof RequestTooLargeError ? 413 : 400, {
        error: error instanceof Error ? error.message : "Firmware upload failed.",
      });
    }
    return;
  }

  if (request.method === "GET" && requestPath.startsWith("/firmware/")) {
    const manifest = await firmwareStore.latest();
    if (!manifest || requestPath !== manifest.url) {
      response.writeHead(404, { "Content-Type": "text/plain; charset=utf-8" });
      response.end("Firmware not found.");
      return;
    }
    response.writeHead(200, { "Content-Type": "application/octet-stream", "Content-Length": manifest.size });
    response.end(readFileSync(join(firmwareDirectory, requestPath.slice("/firmware/".length))));
    return;
  }

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
}

class RequestTooLargeError extends Error {
  constructor() {
    super("Firmware image exceeds the 1.875 MiB Dropbot OTA slot limit.");
  }
}

function readRequestBody(request: import("node:http").IncomingMessage, limit: number): Promise<Buffer> {
  return new Promise((resolve, reject) => {
    const chunks: Buffer[] = [];
    let size = 0;
    let exceededLimit = false;
    request.on("data", (chunk: Buffer) => {
      if (exceededLimit) return;
      size += chunk.length;
      if (size > limit) {
        exceededLimit = true;
        reject(new RequestTooLargeError());
        request.resume();
        return;
      }
      chunks.push(chunk);
    });
    request.on("end", () => resolve(Buffer.concat(chunks)));
    request.on("error", reject);
  });
}

function sendJson(response: import("node:http").ServerResponse, status: number, body: object): void {
  response.writeHead(status, { "Content-Type": "application/json; charset=utf-8" });
  response.end(JSON.stringify(body));
}

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
    const validationError = validateClientPublication(topic, payload);
    if (validationError) {
      send(socket, { type: "publish-result", requestId, ok: false, error: validationError });
      return;
    }
    if (!mqttClient.connected) {
      send(socket, { type: "publish-result", requestId, ok: false, error: "MQTT broker is not connected" });
      return;
    }

    const transient = isTransientCommandTopic(topic);
    const isLiveMotion = isMotorCommandTopic(topic) && payload !== "Stop";
    mqttClient.publish(topic, JSON.stringify(payload), { qos: isLiveMotion ? 0 : 1, retain: !transient }, (error) => {
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
