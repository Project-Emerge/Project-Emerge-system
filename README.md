# Project Emerge Fleet View

Dashboard React per osservare una flotta Dropbot in 2D/3D e configurare la geometria UWB tramite MQTT.

## Avvio

```bash
cp .env.example .env
npm install
npm run dev
```

Apri `http://localhost:5173`. Il gateway MQTT/WebSocket ascolta sulla porta `8787`; Vite inoltra `/ws` automaticamente durante lo sviluppo.

Per una build servita dal gateway:

```bash
npm run build
npm run start
```

## Variabili d'ambiente

| Variabile | Default | Descrizione |
| --- | --- | --- |
| `MQTT_URL` | `mqtt://192.168.8.1:1883` | Broker MQTT TCP raggiungibile dai robot. |
| `MQTT_USERNAME` / `MQTT_PASSWORD` | — | Credenziali opzionali del broker. |
| `HTTP_PORT` | `8787` | Porta del gateway in produzione. |

## Topic supportati

Il gateway sottoscrive `/pose/+`, `/telemetry/+`, `/imu/+`, `/config/anchors`, `/config/estimation` e `/config/robots/+`.

La configurazione ancore viene pubblicata con QoS 1 e retention su `/config/anchors`. La modalità di stima viene pubblicata con QoS 1 e retention su `/config/estimation` usando `{ "fusion_enabled": true | false }`: `false` espone la sola trilaterazione UWB. I parametri motore vengono pubblicati con QoS 1 e retention su `/config/robots/{id}`. Quest'ultimo è già disponibile nella dashboard ma non viene ancora applicato dal firmware corrente.
