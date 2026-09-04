# VisionSystem

Sistema di localizzazione indoor per marker ArUco basato su camere fisse (da una a quattro), calibrazione ChArUco guidata, fusione multi-camera e pubblicazione real-time su MQTT di posizione e orientamento.

---

## Indice

1. [Requisiti e Installazione](#1-requisiti-e-installazione)
2. [Configurazione delle Camere](#2-configurazione-delle-camere)
   - [2.1 Selezione visiva delle sorgenti](#21-selezione-visiva-delle-sorgenti)
   - [2.2 Regolazione di campo visivo e zoom](#22-regolazione-di-campo-visivo-e-zoom)
3. [Calibrazione Intrinseca (ChArUco)](#3-calibrazione-intrinseca-charuco)
   - [3.1 Generazione e stampa della board](#31-generazione-e-stampa-della-board)
   - [3.2 Verifica delle sorgenti video (probe)](#32-verifica-delle-sorgenti-video-probe)
   - [3.3 Wizard interattivo intrinseche](#33-wizard-interattivo-intrinseche)
   - [3.4 Calibrazione da cartella di foto](#34-calibrazione-da-cartella-di-foto)
4. [Mappa dei Reference Marker](#4-mappa-dei-reference-marker)
   - [4.1 Mappa da singola camera o foto 2D](#41-mappa-da-singola-camera-o-foto-2d)
   - [4.2 Selezione degli Anchor Marker (`--mode anchors`)](#42-selezione-degli-anchor-marker---mode-anchors)
   - [4.3 Arena grande: Stitching multi-camera](#43-arena-grande-stitching-multi-camera)
   - [4.4 Selezione e rotazione dell'origine del frame](#44-selezione-e-rotazione-dellorigine-del-frame)
5. [Calibrazione Estrinseca](#5-calibrazione-estrinseca)
6. [Esecuzione del Runtime](#6-esecuzione-del-runtime)
   - [6.1 Esecuzione locale (singolo PC)](#61-esecuzione-locale-singolo-pc)
   - [6.2 Modalità distribuita (un PC per camera)](#62-modalità-distribuita-un-pc-per-camera)
7. [Simulatore](#7-simulatore)
   - [7.1 Simulazione sintetica](#71-simulazione-sintetica)
   - [7.2 Simulazione live con webcam reali](#72-simulazione-live-con-webcam-reali)
8. [Protocollo MQTT e Configurazione](#8-protocollo-mqtt-e-configurazione)
   - [8.1 Tabella dei topic](#81-tabella-dei-topic)
   - [8.2 Esempio di configurazione completa](#82-esempio-di-configurazione-completa)
   - [8.3 Target automatici e frame anchor](#83-target-automatici-e-frame-anchor)
9. [Diagnostica e Convenzioni Geometriche](#9-diagnostica-e-convenzioni-geometriche)
10. [Collaudo Fisico](#10-collaudo-fisico)

---

## 1. Requisiti e Installazione

Il progetto richiede **Python >= 3.12** e gestisce dipendenze e lockfile tramite `uv`.

```bash
UV_CACHE_DIR=/tmp/visionsystem-uv-cache uv sync --all-groups
UV_CACHE_DIR=/tmp/visionsystem-uv-cache uv run pytest
```

---

## 2. Configurazione delle Camere

### 2.1 Selezione visiva delle sorgenti

Il sistema usa inizialmente le sorgenti OpenCV `5`, `1`, `2`, `4` a 1920×1080 @ 30 FPS. Per associarle visivamente:

```bash
uv run vision-select-cameras \
  --base config.example.json \
  --output config.local.json
```

- Cliccare su un riquadro video e premere `1`, `2`, `3` o `4` per assegnarlo alla camera logica corrispondente (`cam_0`..`cam_3`).
- `C` cancella le assegnazioni, `R` ripete la scansione delle periferiche, `ENTER` salva il file, `ESC` annulla.
- Per limitare la scansione a indici noti:
  ```bash
  uv run vision-select-cameras --sources 5 1 2 4 --output config.local.json
  ```
- Disponibile anche come sottocomando: `uv run vision-calibrate select-cameras`.
- Su Linux, dopo l'identificazione, è consigliato sostituire gli indici numerici con i percorsi stabili `/dev/v4l/by-id/...`.

### 2.2 Regolazione di campo visivo e zoom

```bash
uv run vision-configure-cameras --config config.local.json
```

- Mostra contemporaneamente le quattro camere. Cliccare su una vista e usare `+`/`-` per regolare lo zoom digitale (`digital_zoom`).
- `N` imposta il preset normale `1.75x`; `W` ripristina il grandangolo completo `1.00x`.
- `A` applica lo zoom selezionato a tutte le quattro camere; `ENTER` salva la configurazione (incrementando `revision`), `ESC` annulla.
- Per salvare su un file diverso:
  ```bash
  uv run vision-configure-cameras --config config.local.json --output config.con-fov.json
  ```
- Per pubblicare direttamente sul broker MQTT (`config/set`):
  ```bash
  uv run vision-configure-cameras --config config.local.json --publish-mqtt
  ```

> **Nota:** Qualsiasi variazione di FOV o risoluzione modifica le matrici intrinseche. Dopo aver modificato lo zoom occorre ripetere la calibrazione intrinseca ed estrinseca.

---

## 3. Calibrazione Intrinseca (ChArUco)

### 3.1 Generazione e stampa della board

Genera il PDF stampabile ad alta risoluzione (con barra millimetrica di controllo da 100 mm) e il PNG sorgente:

```bash
# Formato A4 (board 6x8, quadrati 30 mm, marker 22 mm)
uv run vision-calibrate board --format a4 --output calibration-assets

# Formato A3 (board 7x9, quadrati 40 mm, marker 30 mm, ideale per arene ampie)
uv run vision-calibrate board --format a3 --output calibration-assets

# Entrambi i formati
uv run vision-calibrate board --format both --output calibration-assets
```

Stampare il PDF generato al **100% (senza adattamento)**, verificare con un righello la barra da 100 mm e fissare il foglio su un supporto rigido e perfettamente piano.

### 3.2 Verifica delle sorgenti video (probe)

Verifica che tutte le sorgenti video rispondano alla risoluzione configurata, misurando FPS effettivi, formato e duplicati:

```bash
uv run vision-calibrate --config config.local.json probe
```

### 3.3 Wizard interattivo intrinseche

Esegue l'assistente a schermo con guida in tempo reale (movimento, orientamento, scala e stabilità):

```bash
# Calibra tutte le camere in sequenza (board A4 di default)
uv run vision-calibrate --config config.local.json intrinsics --camera all

# Con board A3
uv run vision-calibrate --config config.local.json intrinsics --camera all --board-format a3

# Singola camera
uv run vision-calibrate --config config.local.json intrinsics --camera cam_0 --board-format a3
```

- La cattura delle pose è automatica quando la board è ferma e in una posizione valida/inedita.
- Controlli tastiera: `SPAZIO` attiva/disattiva cattura automatica, `BACKSPACE` rimuove l'ultimo campione, `R` resetta i campioni, `ENTER` conferma e salva, `ESC` annulla.

### 3.4 Calibrazione da cartella di foto

Se le immagini ChArUco sono già state acquisite come file:

```bash
# Singola cartella
uv run vision-calibrate-folder \
  --config config.local.json \
  --input foto/cam_0 \
  --camera cam_0 \
  --board-format a3 \
  --output calibrations

# Struttura multi-camera (sottocartelle cam_0/, cam_1/, cam_2/, cam_3/)
uv run vision-calibrate-folder \
  --config config.local.json \
  --input foto \
  --board-format a3 \
  --output calibrations
```

Disponibile anche come sottocomando:
```bash
uv run vision-calibrate --config config.local.json from-folder --input foto --board-format a3
```

---

## 4. Mappa dei Reference Marker

Per calcolare le estrinseche delle camere, il sistema ha bisogno delle coordinate 3D dei marker di riferimento fissi posti sul piano di lavoro/pavimento.

### 4.1 Mappa da singola camera o foto 2D

Se una singola camera inquadra l'intera area dei reference:

```bash
uv run vision-reference-map \
  --config config.local.json \
  --width-m 5.40 \
  --height-m 3.80 \
  --marker-size-m 0.15 \
  --plane-z-m 0.0 \
  --output reference-markers.json
```

- Nel mosaico live, selezionare la camera con `1`–`4` e premere `SPAZIO` per catturare.
- Nella finestra interattiva, cliccare in ordine: **origine (0,0)**, **punto +X**, **angolo opposto (+X,+Y)** e **punto +Y**.
- Se si usa un'immagine statica o una piantina:
  ```bash
  uv run vision-reference-map \
    --image stanza.jpg \
    --config config.local.json \
    --width-m 5.40 \
    --height-m 3.80 \
    --marker-size-m 0.15 \
    --output reference-markers.json
  ```

### 4.2 Selezione degli Anchor Marker (`--mode anchors`)

Invece di cliccare coordinate arbitrarie, i 4 click possono agganciarsi direttamente al centro di 4 marker ArUco "anchor":

```bash
uv run vision-reference-map \
  --config config.local.json \
  --mode anchors \
  --width-m 0.60 \
  --height-m 0.60 \
  --marker-size-m 0.07 \
  --output reference-markers.json
```

- Con `--auto-capture`, lo scatto avviene automaticamente non appena i marker sono stabili:
  ```bash
  uv run vision-reference-map \
    --config config.local.json \
    --mode anchors \
    --auto-capture \
    --marker-size-m 0.07 \
    --output reference-markers.json
  ```
- Con `--anchor-ids` espliciti (origine, +X, +X+Y, +Y), la mappa viene calcolata senza richiedere click manuali:
  ```bash
  uv run vision-reference-map \
    --config config.local.json \
    --mode anchors \
    --anchor-ids 13 15 19 18 \
    --width-m 0.60 \
    --height-m 0.60 \
    --marker-size-m 0.07 \
    --output reference-markers.json
  ```

### 4.3 Arena grande: Stitching multi-camera

In arene ampie dove nessuna camera vede tutti i reference, `vision-reference-stitch` combina le viste parziali in un unico piano world mediante bundle adjustment globale (minimi quadrati non lineari):

**Requisiti geometrici:**
- 4 anchor marker distribuiti ai vertici dell'arena con distanze note;
- Almeno due reference visibili per ogni camera;
- Camere adiacenti con almeno un reference in comune (catena interamente connessa);
- Marker complanari con lato nero identico (`--marker-size-m`).

```bash
# Acquisizione live coordinata con selezione visiva del rettangolo anchor
uv run vision-reference-stitch \
  --config config.local.json \
  --camera all \
  --marker-size-m 0.09 \
  --output reference-markers.json \
  --force
```

- Il sistema scatta automaticamente quando tutte le camere sono stabili e connesse.
- Sull'omografia top-down visualizzata, cliccare in sequenza vicino ai 4 marker: `origine`, `+X`, `+X+Y`, `+Y`, quindi premere `ENTER`. Il terminale richiederà le distanze reali X e Y in metri (evitabili con `--width-m` e `--height-m`).
- Se gli anchor sono già noti:
  ```bash
  uv run vision-reference-stitch \
    --config config.local.json \
    --camera all \
    --anchor-ids 100 101 102 103 \
    --width-m 12.0 \
    --height-m 8.0 \
    --marker-size-m 0.09 \
    --output reference-markers.json \
    --force
  ```
- È possibile elaborare anche foto pre-acquisite:
  ```bash
  uv run vision-reference-stitch \
    --config config.local.json \
    --images photo/cam_0.jpg photo/cam_1.jpg photo/cam_2.jpg photo/cam_3.jpg \
    --select-frame \
    --marker-size-m 0.09 \
    --output reference-markers.json \
    --force
  ```

### 4.4 Selezione e rotazione dell'origine del frame

Per ridefinire quale dei 4 anchor sia l'origine `(0,0,0)` ruotando rigidamente l'intero sistema di riferimento:

```bash
# Da vista live
uv run vision-select-origin --config config.local.json

# Da foto esistente
uv run vision-select-origin \
  --image reference-markers-capture.jpg \
  --config config.local.json
```

Cliccare sull'ancora desiderata e premere `ENTER`. La configurazione viene aggiornata mantenendo un frame destrorso coerente.

> **Importante:** La modifica dell'origine world rende necessarie nuove calibrazioni estrinseche per tutte le camere (le intrinseche restano invece invariate).

---

## 5. Calibrazione Estrinseca

Determina la posa 3D di ciascuna camera rispetto al frame world (`world_from_camera`):

```bash
# Calibra tutte le camere usando i reference nel file config/MQTT
uv run vision-calibrate --config config.local.json extrinsics --camera all

# Con file reference separato
uv run vision-calibrate --config config.local.json extrinsics --camera all \
  --reference-markers reference-markers.json
```

- La finestra live mostra gli ID visti, i reference utili, gli scarti e l'errore di riproiezione.
- Raccoglie 100 campioni stabili ed esegue il calcolo con RANSAC + LM refinement.
- Per collaudo rapido con tolleranze RANSAC allargate (da 3 a 30 px):
  ```bash
  uv run vision-calibrate --config config.local.json extrinsics --camera all \
    --reference-markers reference-markers.json --allow-low-quality
  ```

---

## 6. Esecuzione del Runtime

### 6.0 Esecuzione con Docker Compose

Dalla root del repository, lo stack hardware completo include automaticamente
VisionSystem:

```bash
docker compose up --build -d
docker compose logs --follow vision
```

Il container usa `config.local.json` e la directory `calibrations/` presenti in
questa applicazione, accede alle camere USB V4L2 del sistema Linux e comunica con
il broker Compose tramite `mosquitto:1883`. Stato e log diagnostici sono
conservati nei volumi Docker `vision-state` e `vision-diagnostics`.

Per usare il simulatore robot senza avviare VisionSystem eseguire invece
`make up-simulator` dalla root. Il target arresta anche un eventuale container
`vision` già attivo.

### 6.1 Esecuzione locale (singolo PC)

Avvia il localizzatore aprendo tutte le camere configurate, eseguendo rilevamento, controllo drift e fusione:

```bash
# Modalità di produzione (connesso al broker MQTT)
export VISION_MQTT_HOST=localhost
export VISION_MQTT_PORT=1883
uv run vision-localizer --config config.local.json

# Modalità offline con debug visivo (mosaico camere + mappa world 2D)
uv run vision-localizer --config config.local.json --no-mqtt --debug

# Stampa posa JSON su stdout ad ogni ciclo
uv run vision-localizer --config config.local.json --no-mqtt --print-poses
```

### 6.2 Modalità distribuita (un PC per camera)

Architettura scalabile per arene ampie: 4 PC periferici (ciascuno con una sola camera) inviano osservazioni leggere (~8 KB/s) via MQTT a un server di fusione centrale.

#### Requisiti di sincronizzazione temporale:
Tutti i nodi e il server devono avere gli orologi sincronizzati via **NTP/Chrony** con scarto inferiore a 2-3 ms (la fusione usa timestamp UTC in nanosecondi).

```bash
# Installazione chrony (Debian/Ubuntu)
sudo apt install chrony && sudo systemctl enable --now chronyd
```

#### Su ogni PC nodo (`cam_X`):
1. Associare la camera:
   ```bash
   uv run vision-select-cameras --base config.example.json --camera cam_X --output config.local.json
   ```
2. Calibrare intrinseche ed estrinseche per la propria camera:
   ```bash
   uv run vision-calibrate --config config.local.json intrinsics --camera cam_X --board-format a3
   uv run vision-calibrate --config config.local.json extrinsics --camera cam_X
   ```
3. Avviare il nodo camera:
   ```bash
   export VISION_MQTT_HOST=192.168.1.10
   uv run vision-node --camera cam_X
   ```

#### Sul PC server (nessuna camera collegata):
```bash
export VISION_MQTT_HOST=192.168.1.10
uv run vision-server --debug
```

---

## 7. Simulatore

### 7.1 Simulazione sintetica

Permette di testare l'intera catena distribuita (4 nodi sintetici + broker MQTT + fusion server) su una singola macchina senza telecamere collegate:

```bash
uv run vision-simulate --config config.local.json
```

- Se il broker MQTT non è attivo su `localhost:1883`, avvia automaticamente un container Docker `eclipse-mosquitto:2`.
- Apre la finestra 2D *VisionSystem - world* mostrando la fusione del target sintetico (ID 23 di default).
- Opzioni utili:
  - `--tag-id 23`: ID del tag mobile simulato.
  - `--hz 25.0`: frequenza di pubblicazione delle osservazioni.
  - `--noise-px 0.5`: deviazione standard del rumore gaussiano sui pixel.
  - `--broker none`: usa un broker esterno già attivo.
  - `--keep-broker`: non ferma il container Docker all'uscita.

### 7.2 Simulazione live con webcam reali

Avvia 4 processi `vision-node` e il `vision-server` sulla stessa macchina collegandosi a webcam fisiche reali:

```bash
uv run vision-simulate --live --config config.local.json --node-debug
```

- `--cameras cam_0 cam_1`: avvia solo un sottoinsieme di camere.
- `--allow-low-quality`: impedisce l'esclusione automatica delle camere durante i collaudi di drift.

---

## 8. Protocollo MQTT e Configurazione

Base topic predefinito: `vision/<site>/<system_id>`.

### 8.1 Tabella dei topic

| Topic | QoS | Retained | Direzione | Descrizione |
|---|:---:|:---:|:---:|---|
| `config/set` | 1 | Sì | Inbound | Invio di una nuova configurazione completa |
| `config/state` | 1 | Sì | Outbound | Configurazione attualmente attiva |
| `config/result` | 1 | No | Outbound | Esito validazione (`accepted: true/false`) |
| `calibration/<cam>/set` | 1 | Sì | Inbound | Invio di un artefatto di calibrazione |
| `calibration/<cam>/state` | 1 | Sì | Outbound | Artefatto di calibrazione applicato |
| `observations/<cam>` | 0 | No | Outbound (Node) | Rilevamenti ArUco grezzi del nodo |
| `pose/<tag_id>` | 0 | No | Outbound (Server) | Posa 3D fusa del tag nel frame `world` |
| `camera/<cam>/status` | 1 | No | Outbound | Stato connessione, FPS e calibrazione |
| `metrics` | 0 | No | Outbound | Metriche aggregate di sistema |
| `event` | 1 | No | Outbound | Notifiche di drift, errori e allarmi |
| `status` | 1 | Sì | Outbound | Stato online del sistema e Last Will |
| `/config/aruco-map` | 1 | Sì | Inbound | Mappa globale marker ArUco → device ID robot |
| `/pose/<device_id>` | 0 | No | Outbound | Posa adattata al protocollo dashboard per i marker mappati |

I topic che iniziano con `/` sono globali e non usano il base topic Vision. Per
ogni marker mobile, `pose/<tag_id>` continua a pubblicare la posa 3D dettagliata.
Quando `/config/aruco-map` associa quel marker a un robot, Vision pubblica anche
`/pose/<device_id>` con i campi richiesti dalla dashboard (`x_m`, `y_m`,
`heading_rad`, `speed_m_s`, `timestamp_us`) e con gli altri dati effettivamente
disponibili: quota, orientamento, velocità lineare e angolare, camere coinvolte,
errore di riproiezione e qualità. `position_variance_m2` non viene sintetizzato,
perché VisionSystem non calcola una covarianza della posizione.

### 8.2 Esempio di configurazione completa

```json
{
  "request_id": "config-init-01",
  "config": {
    "schema_version": 1,
    "revision": 1,
    "site": "default",
    "system_id": "indoor-01",
    "cameras": [
      {"id": "cam_0", "source": 5, "width": 1920, "height": 1080, "fps": 30.0, "digital_zoom": 1.0},
      {"id": "cam_1", "source": 1, "width": 1920, "height": 1080, "fps": 30.0, "digital_zoom": 1.0},
      {"id": "cam_2", "source": 2, "width": 1920, "height": 1080, "fps": 30.0, "digital_zoom": 1.0},
      {"id": "cam_3", "source": 4, "width": 1920, "height": 1080, "fps": 30.0, "digital_zoom": 1.0}
    ],
    "aruco": {
      "dictionary": "DICT_4X4_50",
      "mobile_markers": [{"id": 23, "size_m": 0.12, "name": "robot"}],
      "auto_mobile_markers": {"enabled": false, "default_size_m": 0.12, "ignored_ids": []},
      "reference_markers": [
        {"id": 13, "size_m": 0.07, "position_m": [0.6, 0.0, 0.0], "orientation_xyzw": [0.0, 0.0, 0.0, 1.0]},
        {"id": 15, "size_m": 0.07, "position_m": [0.0, 0.5, 0.0], "orientation_xyzw": [0.0, 0.0, 0.0, 1.0]},
        {"id": 18, "size_m": 0.07, "position_m": [0.0, 0.0, 0.0], "orientation_xyzw": [0.0, 0.0, 0.0, 1.0]},
        {"id": 19, "size_m": 0.07, "position_m": [0.6, 0.5, 0.0], "orientation_xyzw": [0.0, 0.0, 0.0, 1.0]}
      ],
      "anchor_frame": {
        "origin_id": 18,
        "x_axis_id": 13,
        "y_axis_id": 15,
        "opposite_id": 19,
        "x_distance_m": 0.6,
        "y_distance_m": 0.5,
        "plane_z_m": 0.0
      }
    },
    "fusion": {
      "window_ms": 40.0,
      "publish_hz": 20.0,
      "max_reprojection_error_px": 4.0,
      "huber_scale_px": 1.5,
      "tracker_filter": "one_euro",
      "one_euro_min_cutoff_hz": 2.0,
      "one_euro_beta": 5.0,
      "one_euro_derivative_cutoff_hz": 1.0,
      "tracker_position_gain": 0.65,
      "tracker_velocity_gain": 0.12,
      "tracker_orientation_gain": 0.55,
      "tracker_max_innovation_m": 0.15,
      "stale_after_ms": 250.0
    },
    "debug": {
      "mosaic": false,
      "world_view": false,
      "trail_seconds": 3.0
    }
  }
}
```

### 8.3 Target automatici e frame anchor

- **Target automatici (`auto_mobile_markers`)**: se abilitato, qualsiasi marker rilevato che non appartenga a `reference_markers` né a `ignored_ids` viene tracciato come marker mobile con dimensione `default_size_m`.
- **Frame anchor (`anchor_frame`)**: vincola le posizioni dei 4 marker di riferimento chiave esattamente sui vertici del rettangolo specificato, garantendo un sistema di coordinate world ortogonale e stabile.

### 8.4 Filtro del tracker

Il tracker usa di default un **One Euro Filter** sulla posizione. A target quasi
fermo attenua il jitter, mentre durante un movimento rapido aumenta
automaticamente la frequenza di taglio e riduce il ritardo:

- `one_euro_min_cutoff_hz`: stabilità a riposo; aumentarlo rende il tracker più
  reattivo ma lascia passare più rumore.
- `one_euro_beta`: adattamento alla velocità; aumentarlo riduce il ritardo nei
  movimenti rapidi.
- `one_euro_derivative_cutoff_hz`: filtraggio della velocità usata sia per
  adattare il filtro sia per le brevi predizioni quando il marker non è visibile.

I valori iniziali `2.0`, `5.0`, `1.0` sono un profilo reattivo per acquisizioni a
20–30 FPS. Il precedente tracker è ancora selezionabile con
`"tracker_filter": "alpha_beta"`; in quel caso si usano
`tracker_position_gain` e `tracker_velocity_gain`.

---

## 9. Diagnostica e Convenzioni Geometriche

- **Convenzione assi `world`**: Metri, frame destrorso con piano **XY sul pavimento** e asse **Z rivolto verso l'alto**.
- **Orientamento**: Quaternioni espressi come `(x, y, z, w)` normalizzati.
- **Log diagnostico strutturato**: Scritto automaticamente in `diagnostics/vision-system.jsonl` (formato JSON Lines ruotato a 20 MB). Contiene telemetria completa, controlli UVC accettati/rifiutati, condizioni delle matrici, metriche di stabilità e motivi degli scarti.
- **Monitoraggio del drift**: Durante il runtime, se i reference marker di una camera mostrano una discrepanza superiore a 2 cm o 2° per più di 2 secondi consecutivi, la camera viene automaticamente esclusa dalla fusione e viene emesso un evento `CALIBRATION_DRIFT`.

---

## 10. Collaudo Fisico

La suite di test verifica la correttezza algoritmica, le matrici geometriche e i protocolli di rete:

```bash
uv run pytest
uv run ruff check
```

Per il collaudo in opera, verificare l'accuratezza posizionando un tag a distanze note (2, 3 e 4 metri) lungo gli assi della griglia fisica. Un errore di riproiezione pixel ridotto non sostituisce la verifica metrica a terra.
