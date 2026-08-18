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
| `FIRMWARE_DIRECTORY` | `firmware` | Directory persistente per l'immagine OTA e il manifest pubblicato. |
| `VITE_OTA_SERVER` | Host e porta della dashboard corrente | Default opzionale per il campo OTA, ad esempio `192.168.8.1:8787`. |

## Topic supportati

Il gateway sottoscrive `/pose/+`, `/telemetry/+`, `/imu/+`, `/config/anchors`, `/config/estimation` e `/config/robots/+`.

La configurazione ancore viene pubblicata con QoS 1 e retention su `/config/anchors`. La modalità di stima viene pubblicata con QoS 1 e retention su `/config/estimation` usando `{ "fusion_enabled": true | false }`: `false` espone la sola trilaterazione UWB. I parametri motore vengono pubblicati con QoS 1 e retention su `/config/robots/{id}`. Quest'ultimo è già disponibile nella dashboard ma non viene ancora applicato dal firmware corrente.

## Aggiornamenti OTA

In **Settings → OTA update**, indica l'indirizzo `host:port` del dashboard raggiungibile dai robot, la versione presente nel `Cargo.toml` del firmware e il relativo file `.bin`. Il gateway conserva l'immagine, pubblica `{ "server": "host:port" }` con retention su `/config/ota` e invia un comando non retained su `/ota/check/{id}` a tutti i robot rilevati. Ogni Dropbot scarica e installa solo una versione diversa dalla sua.

Usare l'indirizzo LAN della dashboard, non `localhost`; con la porta predefinita del gateway, ad esempio `192.168.8.1:8787`. La directory `firmware/` non è versionata: impostare `FIRMWARE_DIRECTORY` su uno storage persistente in produzione.
