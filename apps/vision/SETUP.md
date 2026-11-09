# Setup del vision system

## Calibrazione (dal PC con tutte le camere)

Selezionare le sorgenti:

```bash
uv run vision-select-cameras \
  --base config.example.json \
  --output config.local.json \
  --force
```

In `config.local.json`, inserire in `"reference_ids": []` gli ID dei marker di riferimento da usare.

Posizionare i marker nel mondo. Se esistono già `reference-markers.json` e i file di stitching, eliminarli prima:

```bash
uv run vision-reference-stitch \
  --config config.local.json \
  --camera all \
  --marker-size-m 0.07 \
  --output reference-markers.json \
  --force
```

Lo stitching è automatico: se i tag nella foto non risultano corretti, ripetere.

Calibrazione estrinseca (posizione delle camere rispetto al mondo):

```bash
uv run vision-calibrate --config config.local.json extrinsics --camera all --allow-low-quality
```

Verifica:

```bash
uv run vision-localizer --config config.local.json --no-mqtt --debug
```

Se non rileva nulla, probabilmente gli auto-marker sono disabilitati. In `config.local.json`:

```json
"auto_mobile_markers": {
  "default_size_m": 0.07,
  "enabled": true,
  "ignored_ids": []
}
```

## Nodo centrale

Riselezionare la camera rimasta collegata:

```bash
uv run vision-select-cameras \
  --base config.local.json \
  --output config.local.json \
  --force
```

Dalla root del repository, avviare il server, che raccoglie le osservazioni di tutte le camere:

```bash
make vision-server
```

Sempre sul nodo centrale, avviare il client della camera collegata:

```bash
make vision-client CAMERA=cam_x
```

GUI di monitoraggio del server (opzionale):

```bash
uv run vision-server-gui --config config.local.json
```

## Nodo remoto

Su ogni nodo, riselezionare la camera rimasta collegata (stesso comando del nodo centrale).

Verificare che la camera funzioni:

```bash
uv run vision-localizer --config config.local.json --camera cam_x --no-mqtt
```

Avviare il client (senza Docker), puntando al broker del nodo centrale:

```bash
VISION_MQTT_HOST=<ip-nodo-centrale> \
  uv run vision-client --config config.local.json --camera cam_x --allow-low-quality
```

## Avvio del sistema

Sul nodo centrale, dalla root, avviare il resto dello stack (non usare `make up`: avvierebbe anche il `vision` monolitico, duplicando le pose):

```bash
make up-core
```

Dalla dashboard (`http://localhost:8787`):

1. **Settings → Marker mapping**: associare ogni marker al suo robot.
2. **Formation & parameters**: scegliere formazione e leader, poi applicare.

Per spegnere tutto (stack, server e client Docker sul nodo centrale): `make down`.
