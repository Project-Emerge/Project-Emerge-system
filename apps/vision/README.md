# VisionSystem

Sistema di localizzazione indoor per marker ArUco basato su camere fisse (da una a quattro), calibrazione ChArUco guidata, fusione multi-camera e pubblicazione real-time su MQTT di posizione e orientamento.

---

## Indice

1. [Requisiti e Installazione](#1-requisiti-e-installazione)
2. [Pannello grafico di calibrazione](#2-pannello-grafico-di-calibrazione)
3. [Configurazione delle Camere](#3-configurazione-delle-camere)
   - [3.1 Selezione visiva delle sorgenti](#31-selezione-visiva-delle-sorgenti)
   - [3.2 Regolazione di campo visivo e zoom](#32-regolazione-di-campo-visivo-e-zoom)
4. [Calibrazione Intrinseca (ChArUco)](#4-calibrazione-intrinseca-charuco)
   - [4.1 Generazione e stampa della board](#41-generazione-e-stampa-della-board)
   - [4.2 Verifica delle sorgenti video (probe)](#42-verifica-delle-sorgenti-video-probe)
   - [4.3 Wizard interattivo intrinseche](#43-wizard-interattivo-intrinseche)
   - [4.4 Calibrazione da cartella di foto](#44-calibrazione-da-cartella-di-foto)
5. [Mappa dei Reference Marker](#5-mappa-dei-reference-marker)
   - [5.1 Mappa da singola camera o foto 2D](#51-mappa-da-singola-camera-o-foto-2d)
   - [5.2 Selezione degli Anchor Marker (`--mode anchors`)](#52-selezione-degli-anchor-marker---mode-anchors)
   - [5.3 Arena grande: Stitching multi-camera](#53-arena-grande-stitching-multi-camera)
   - [5.4 Selezione e rotazione dell'origine del frame](#54-selezione-e-rotazione-dellorigine-del-frame)
6. [Calibrazione Estrinseca](#6-calibrazione-estrinseca)
7. [Esecuzione del Runtime](#7-esecuzione-del-runtime)
   - [7.0 Esecuzione con Docker Compose](#70-esecuzione-con-docker-compose)
   - [7.1 Esecuzione locale (singolo PC)](#71-esecuzione-locale-singolo-pc)
   - [7.2 Modalità distribuita (un PC per camera)](#72-modalità-distribuita-un-pc-per-camera)
   - [7.3 GUI di avvio e debug del server](#73-gui-di-avvio-e-debug-del-server)
   - [7.4 Docker: server e nodi su PC diversi](#74-docker-server-e-nodi-su-pc-diversi)
8. [Simulatore](#8-simulatore)
   - [8.1 Simulazione sintetica](#81-simulazione-sintetica)
   - [8.2 Simulazione live con webcam reali](#82-simulazione-live-con-webcam-reali)
9. [Protocollo MQTT e Configurazione](#9-protocollo-mqtt-e-configurazione)
   - [9.1 Tabella dei topic](#91-tabella-dei-topic)
   - [9.2 Esempio di configurazione completa](#92-esempio-di-configurazione-completa)
   - [9.3 Target automatici e frame anchor](#93-target-automatici-e-frame-anchor)
   - [9.4 Coerenza fra camere](#94-coerenza-fra-camere)
   - [9.5 Filtro del tracker](#95-filtro-del-tracker)
10. [Diagnostica e Convenzioni Geometriche](#10-diagnostica-e-convenzioni-geometriche)
11. [Collaudo Fisico](#11-collaudo-fisico)
---

## 1. Requisiti e Installazione

Il progetto richiede **Python >= 3.12** e gestisce dipendenze e lockfile tramite `uv`.

```bash
UV_CACHE_DIR=/tmp/visionsystem-uv-cache uv sync --all-groups
UV_CACHE_DIR=/tmp/visionsystem-uv-cache uv run pytest
```

---

## 2. Pannello grafico di calibrazione

L'intero flusso delle sezioni 3-7 è disponibile anche come pannello Tkinter: un'unica finestra
che mostra quali camere sono già calibrate e con che errore, e che lancia ogni fase con gli
argomenti giusti.

```bash
uv run vision-calibrate-gui --config config.local.json --board-format a3
# oppure
make calibrate-gui CONFIG=config.local.json BOARD_FORMAT=a3
```

Richiede `tkinter` (Debian/Ubuntu: `sudo apt install python3-tk`). **Le etichette del pannello
sono in inglese**; le finestre OpenCV dei wizard restano in italiano, e la console del pannello
mostra l'output grezzo del comando lanciato.

Il menu di sinistra segue l'ordine di questo documento. Ogni voce ha un pallino pieno quando la
fase è eseguibile e vuoto quando manca un prerequisito:

| Voce del menu | Sezione di riferimento | Comandi lanciati |
| --- | --- | --- |
| `1 Cameras` | [3. Configurazione delle Camere](#3-configurazione-delle-camere) | `vision-select-cameras`, `vision-configure-cameras`, probe |
| `2 Board` | [4.1 Generazione e stampa della board](#41-generazione-e-stampa-della-board) | `vision-calibrate board` |
| `3 Intrinsics` | [4.3](#43-wizard-interattivo-intrinseche) e [4.4](#44-calibrazione-da-cartella-di-foto) | `vision-calibrate intrinsics`, calibrazione da cartella |
| `4 Reference markers` | [5. Mappa dei Reference Marker](#5-mappa-dei-reference-marker) | `vision-reference-map`, `vision-reference-stitch`, `vision-select-origin` |
| `5 Extrinsics` | [6. Calibrazione Estrinseca](#6-calibrazione-estrinseca) | `vision-calibrate extrinsics` |
| `6 Runtime` | [7. Esecuzione del Runtime](#7-esecuzione-del-runtime) | `vision-localizer`, `vision-server-gui` |

Selezionando una fase bloccata il pannello scrive **perché** lo è, distinguendo casi che
richiedono interventi diversi: una camera senza intrinseche (manca il file), una camera le cui
impostazioni sono cambiate dopo la calibrazione (il file c'è ma non è più valido: rifare le
intrinseche), una calibrazione che non ha superato le soglie di qualità, e la mancanza dei 3
reference marker minimi per le estrinseche.

**Cosa gira dove.** Le fasi interattive — selezione sorgenti, campo visivo, wizard intrinseche
ed estrinseche, mappa reference, stitching, origine — vengono eseguite **come processi figli**:
aprono la loro finestra OpenCV con i controlli da tastiera di sempre (`SPAZIO`, `BACKSPACE`,
`R`, `ENTER`, `ESC`) e il pannello ne mostra l'output nella console. `Cancel` li termina. Le
fasi non interattive — generazione board, probe delle sorgenti, calibrazione da cartella di
foto — girano invece dentro al pannello, con avanzamento per camera e risultati per immagine.

> **Il codice di uscita non è un verdetto.** Annullare un wizard con `ESC` esce con codice
> diverso da zero, e una sessione multi-camera fallita sulla terza camera ha comunque scritto
> le prime due. Il pannello rilegge `calibrations/` ogni volta che un processo termina:
> **fa fede la tabella**, non il codice di uscita.

La colonna `Board` confronta il `board_checksum` dell'artefatto con il formato selezionato: se
si calibra con la board A3 e si apre il pannello in A4, ogni riga risulta `stale` con nota
`differs (a4)`. Usare `--board-format` coerente con la board realmente stampata.

Il pannello **non scrive mai** `config.local.json`: restano `vision-select-cameras` e
`vision-configure-cameras` gli unici a modificarlo, e il pannello si limita a lanciarli. Tutti
i comandi delle sezioni seguenti continuano a funzionare identici — il pannello è un client di
quei comandi, e la CLI resta la via supportata su macchine senza display.

---

## 3. Configurazione delle Camere

> Questa fase corrisponde alla voce `1 Cameras` del [pannello grafico](#2-pannello-grafico-di-calibrazione).

### 3.1 Selezione visiva delle sorgenti

Il sistema supporta arene con **2, 3 o 4 camere**: il file base definisce al massimo quattro slot logici (`cam_0`..`cam_3`), ma la configurazione salvata contiene solo le camere effettivamente presenti. Le sorgenti OpenCV iniziali sono `5`, `1`, `2`, `4` a 1920×1080 @ 30 FPS. Per associarle visivamente:

```bash
uv run vision-select-cameras \
  --base config.example.json \
  --output config.local.json
```

- Cliccare su un riquadro video e premere `1`, `2`, `3` o `4` per assegnarlo alla camera logica corrispondente (`cam_0`..`cam_3`).
- `C` cancella le assegnazioni, `R` ripete la scansione delle periferiche, `ENTER` salva il file, `ESC` annulla.
- **Meno di quattro camere:** basta assegnare solo le camere disponibili; gli slot non assegnati non finiscono nel file. Per dichiarare esplicitamente il roster (consigliato, così i tasti e gli ID corrispondono alle camere reali):
  ```bash
  uv run vision-select-cameras --base config.example.json \
    --cameras cam_0 cam_1 --output config.local.json
  ```
  Con due camere la finestra mostra due slot e si assegnano con i tasti `1` e `2`.
- Per limitare la scansione a indici noti:
  ```bash
  uv run vision-select-cameras --sources 5 1 2 4 --output config.local.json
  ```
- Disponibile anche come sottocomando: `uv run vision-calibrate select-cameras`.
- Su Linux, dopo l'identificazione, è consigliato sostituire gli indici numerici con i percorsi stabili `/dev/v4l/by-id/...`.

### 3.2 Regolazione di campo visivo e zoom

```bash
uv run vision-configure-cameras --config config.local.json
```

- Mostra contemporaneamente tutte le camere configurate (due, tre o quattro). Cliccare su una vista e usare `+`/`-` per regolare lo zoom digitale (`digital_zoom`).
- `N` imposta il preset normale `1.75x`; `W` ripristina il grandangolo completo `1.00x`.
- `A` applica lo zoom selezionato a tutte le camere configurate; `ENTER` salva la configurazione (incrementando `revision`), `ESC` annulla.
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

## 4. Calibrazione Intrinseca (ChArUco)

> Questa fase corrisponde alle voci `2 Board` e `3 Intrinsics` del [pannello grafico](#2-pannello-grafico-di-calibrazione).

### 4.1 Generazione e stampa della board

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

### 4.2 Verifica delle sorgenti video (probe)

Verifica che tutte le sorgenti video rispondano alla risoluzione configurata, misurando FPS effettivi, formato e duplicati:

```bash
uv run vision-calibrate --config config.local.json probe
```

### 4.3 Wizard interattivo intrinseche

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

### 4.4 Calibrazione da cartella di foto

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

## 5. Mappa dei Reference Marker

> Questa fase corrisponde alla voce `4 Reference markers` del [pannello grafico](#2-pannello-grafico-di-calibrazione).

Per calcolare le estrinseche delle camere, il sistema ha bisogno delle coordinate 3D dei marker di riferimento fissi posti sul piano di lavoro/pavimento.

### 5.1 Mappa da singola camera o foto 2D

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

### 5.2 Selezione degli Anchor Marker (`--mode anchors`)

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

### 5.3 Arena grande: Stitching multi-camera

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

### 5.4 Selezione e rotazione dell'origine del frame

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

## 6. Calibrazione Estrinseca

> Questa fase corrisponde alla voce `5 Extrinsics` del [pannello grafico](#2-pannello-grafico-di-calibrazione), che la blocca
> finché mancano le intrinseche valide o i 3 reference marker minimi.

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

## 7. Esecuzione del Runtime

> Questa fase corrisponde alla voce `6 Runtime` del [pannello grafico](#2-pannello-grafico-di-calibrazione).

### 7.0 Esecuzione con Docker Compose

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

Questo servizio esegue il **monolite** `vision-localizer` (tutte le camere su un
solo PC); da questa cartella lo stesso stack si avvia con `make all`. Per il
deployment distribuito in Docker — `make server` sul PC di fusione, `make client`
su ogni PC con una camera — vedere [7.4](#74-docker-server-e-nodi-su-pc-diversi).

### 7.1 Esecuzione locale (singolo PC)

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

### 7.2 Modalità distribuita (un PC per camera)

Architettura scalabile per arene ampie: da 2 a 4 PC periferici (ciascuno con una sola camera) inviano osservazioni leggere (~8 KB/s) via MQTT a un server di fusione centrale. Il numero di nodi è libero: conta solo che **tutti i nodi e il server condividano lo stesso roster di camere** (`--cameras`), altrimenti il coordinatore continua a segnalare offline gli slot che nessuno pubblica.

#### Requisiti di sincronizzazione temporale:
Tutti i nodi e il server devono avere gli orologi sincronizzati via **NTP/Chrony** con scarto inferiore a 2-3 ms (la fusione usa timestamp UTC in nanosecondi).

```bash
# Installazione chrony (Debian/Ubuntu)
sudo apt install chrony && sudo systemctl enable --now chronyd
```

#### Su ogni PC nodo (`cam_X`):
1. Associare la camera dichiarando il roster completo del deployment (qui un'arena a due camere, `cam_0` e `cam_1`; su questo PC si assegna solo `cam_X`):
   ```bash
   uv run vision-select-cameras --base config.example.json \
     --cameras cam_0 cam_1 --camera cam_X --output config.local.json
   ```
   Senza `--cameras` il file conserva tutti e quattro gli slot del file base: usarlo su un'arena a 2 o 3 camere lascerebbe nel roster camere inesistenti.
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

Il server usa lo stesso roster dei nodi: con 2 camere la fusione lavora su due osservazioni per tag, con 3 o 4 il residuo migliora ma il flusso resta identico.

### 7.3 GUI di avvio e debug del server

Nel deployment distribuito i processi sono indipendenti e la domanda tipica non è *cosa calcola la fusione* ma *chi sta parlando con chi*. Il pannello grafico avvia il server sul PC locale e mostra, nella stessa finestra, la vista delle due estremità della catena più la mappa 2D dei robot tracciati:

```bash
# Server sulla stessa macchina del broker
uv run vision-server-gui --config config.local.json

# Broker (e nodi) su un'altra macchina
uv run vision-server-gui --config config.local.json --mqtt-host 192.168.1.10
```

Richiede `tkinter` (Debian/Ubuntu: `sudo apt install python3-tk`). **Le etichette del pannello sono in inglese.**

Il pannello è diviso in tre zone:

1. **Server launch** — host e porta MQTT, `config`, `calibrations`, cache di stato e i flag `--debug`, `--no-mqtt`, `--verbose`. `Start server` lancia `vision-server` come processo figlio (in una sessione separata: chiudere il terminale non lo uccide), `Stop server` gli invia `SIGTERM` e, se non risponde entro 10 s, `SIGKILL`; il pulsante non blocca la finestra mentre aspetta. Le impostazioni MQTT diventano `VISION_MQTT_HOST`/`VISION_MQTT_PORT` del processo figlio.
2. **Deployment cameras** — una riga per camera del roster, con le due viste affiancate: *Node* (il `vision-node` sta pubblicando le sue metriche) e *Server* (il coordinatore sta effettivamente ricevendo osservazioni da quella camera), più osservazioni pubblicate/ricevute, età dell'ultima osservazione e stato di calibrazione.
3. **World view / Console** — due schede: la mappa 2D dell'arena (camere calibrate in arancio, reference marker in viola, robot tracciati con scia e freccia di heading) e la console del server con gli eventi MQTT (`MISSING_CALIBRATION`, `CALIBRATION_DRIFT`, `CAMERA_DISAGREEMENT`, …).

La vista world disegna le pose fuse pubblicate su `<base_topic>/pose/<tag_id>`, **non** le ricalcola: è quindi un controllo indipendente di ciò che il server sta realmente mandando al resto del sistema. Un tag disegnato vuoto è una posa predetta o ferma da più di 1,5 s.

Il pannello si limita ad ascoltare il broker (non pubblica nulla): se il server gira su un altro PC basta puntarlo allo stesso broker e usarlo come monitor, senza premere `Start server`.

Diagnosi rapida della tabella:

| Sintomo | Causa tipica |
| --- | --- |
| Node `●`, Server `○` | broker o `base_topic` diversi (`site`/`system_id` nel config), oppure firewall sulla porta 1883 |
| Node `○`, Server `●` | il nodo pubblica osservazioni ma non metriche: processo in avvio o log-level alterato |
| Entrambi `●`, età alta | orologi non sincronizzati (NTP/Chrony) o rete satura |
| `Calibrated: no` | manca `calibrations/cam_X.json` **sul PC del server** |
| Nota `drift: recalibrate` | la camera si è spostata: ripetere la calibrazione estrinseca |
| World view vuota con camere online | nessun marker mobile visibile, oppure `size_m` dei marker errato (le osservazioni vengono scartate per reprojection error) |

### 7.4 Docker: server e nodi su PC diversi

Tutti i comandi Docker di questo sottoprogetto sono nel `Makefile` di questa
cartella. Le variabili disponibili sono `CAMERA` (camera gestita dal nodo),
`MQTT_HOST`/`MQTT_PORT` (broker visto dai container del nodo), `GUI_MQTT_HOST` e
`CONFIG`.

| Comando | Cosa fa |
| --- | --- |
| `make all` | stack completo su un solo PC: broker, dashboard e il monolite `vision-localizer`, che apre tutte le camere del roster e fonde da solo |
| `make server` | deployment distribuito, lato server: ferma il monolite e avvia broker + `vision-server` (nessuna camera aperta) |
| `make client CAMERA=cam_0` | un nodo camera; il broker è quello dello stesso PC |
| `make client CAMERA=cam_1 MQTT_HOST=192.168.1.10` | un nodo camera su un PC remoto, verso il broker del PC server |
| `make gui` | pannello grafico di avvio e debug (gira sull'host, non in container) |
| `make logs` / `make logs-client CAMERA=cam_1` | log del server / di un nodo |
| `make ps`, `make down`, `make down-client CAMERA=cam_1` | stato e arresto |

Dalla root del repository esistono le scorciatoie `make vision-all`,
`make vision-server`, `make vision-client CAMERA=... MQTT_HOST=...`, `make vision-gui`.

`make all` e `make server` sono alternativi: il monolite e il server di fusione
pubblicherebbero le stesse pose, perciò `make server` ferma il servizio `vision`
prima di partire.

#### Prerequisiti su tutti i PC

```bash
# 1. Orologi sincronizzati: il container eredita l'orologio dell'host
sudo apt install chrony && sudo systemctl enable --now chronyd
chronyc tracking          # lo scarto deve restare sotto 2-3 ms

# 2. Stesso roster e stesso base_topic in config.local.json su ogni PC
#    (site + system_id identici, lista `cameras` identica)
jq '{site, system_id, cameras: [.cameras[].id]}' config.local.json
```

#### PC A — broker, server e nodo locale

```bash
cd apps/vision

make server                 # broker + server di fusione
make client CAMERA=cam_0    # la camera collegata a questo PC
make logs                   # oppure: make logs-client CAMERA=cam_0

sudo ufw allow 1883/tcp     # il broker deve essere raggiungibile dagli altri PC
ip -4 addr show | grep inet # IP da passare ai nodi remoti
```

Il servizio `vision-server` è definito in `compose.yaml` nella root sotto il
profilo `distributed`: non parte con `docker compose up`, solo con `make server`
(o `docker compose --profile distributed up -d mosquitto vision-server`). Il nodo
usa invece `compose.node.yaml` di questa cartella, con un progetto Compose per
camera (`vision-node-<camera>`): sullo stesso PC possono convivere più nodi.

#### PC B — solo il nodo camera

Su ogni PC remoto serve una copia di `apps/vision` con il **proprio**
`config.local.json` (stesso roster del PC A) e le proprie calibrazioni; il broker
e il server restano sul PC A.

```bash
cd apps/vision

make client CAMERA=cam_1 MQTT_HOST=192.168.1.10
make logs-client CAMERA=cam_1 MQTT_HOST=192.168.1.10
make down-client CAMERA=cam_1 MQTT_HOST=192.168.1.10
```

`MQTT_HOST` serve in ogni comando perché identifica il broker del container; il
default `host.docker.internal` vale solo quando broker e nodo stanno sullo stesso
PC. Se sul PC B non c'è il repository, trasferire l'immagine invece di
ricostruirla:

```bash
# sul PC A
docker save vision-node-cam_1-vision-node | ssh utente@pc-b docker load
```

#### Calibrazioni: il server le vuole tutte

Il coordinatore ricostruisce le osservazioni usando le estrinseche presenti
nella **sua** cartella `calibrations/`: il PC A deve quindi avere il file di ogni
camera del roster, comprese quelle collegate ai PC remoti.

```bash
# Opzione 1: copia diretta dal PC del nodo al PC del server
scp calibrations/cam_1.json utente@pc-a:~/Project-Emerge-system/apps/vision/calibrations/

# Opzione 2: distribuzione via MQTT (il bridge salva il file su disco da solo)
mosquitto_pub -h 192.168.1.10 -t 'vision/<site>/<system_id>/calibration/cam_1/set' \
  -q 1 -f calibrations/cam_1.json
```

#### Verifica del deployment

```bash
# Dal PC A: pose fuse pubblicate dal server
mosquitto_sub -h localhost -t 'vision/+/+/pose/+' -v | head

# Metriche del coordinatore (camere online, osservazioni ricevute)
mosquitto_sub -h localhost -t 'vision/+/+/metrics' -v | head

# Pannello grafico: con il server già attivo in Docker si usa come monitor,
# senza premere "Avvia server"
make gui
```

Arresto completo:

```bash
make down                                  # broker + server (PC A)
make down-client CAMERA=cam_0              # nodo locale
make down-client CAMERA=cam_1 MQTT_HOST=192.168.1.10   # nodo remoto (dal PC B)
```

---

## 8. Simulatore

### 8.1 Simulazione sintetica

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

### 8.2 Simulazione live con webcam reali

Avvia 4 processi `vision-node` e il `vision-server` sulla stessa macchina collegandosi a webcam fisiche reali:

```bash
uv run vision-simulate --live --config config.local.json --node-debug
```

- `--cameras cam_0 cam_1`: avvia solo un sottoinsieme di camere.
- `--allow-low-quality`: impedisce l'esclusione automatica delle camere durante i collaudi di drift.

---

## 9. Protocollo MQTT e Configurazione

Base topic predefinito: `vision/<site>/<system_id>`.

### 9.1 Tabella dei topic

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

### 9.2 Esempio di configurazione completa

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
      "max_camera_disagreement_m": 0.25,
      "max_fused_reprojection_error_px": 8.0,
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

### 9.3 Target automatici e frame anchor

- **Target automatici (`auto_mobile_markers`)**: se abilitato, qualsiasi marker rilevato che non appartenga a `reference_markers` né a `ignored_ids` viene tracciato come marker mobile con dimensione `default_size_m`.
- **Frame anchor (`anchor_frame`)**: vincola le posizioni dei 4 marker di riferimento chiave esattamente sui vertici del rettangolo specificato, garantendo un sistema di coordinate world ortogonale e stabile.

### 9.4 Coerenza fra camere

Ogni camera che vede il tag produce una propria stima in coordinate world.
Prima di risolvere la posa congiunta la fusione confronta quelle stime fra loro
e scarta le camere che contraddicono il consenso:

- `max_camera_disagreement_m`: distanza massima fra la stima di una singola
  camera e la mediana delle altre. Oltre questa soglia la camera viene esclusa
  da quel ciclo di fusione e riportata in `rejected_by` nel payload della posa.
- `max_fused_reprojection_error_px`: errore RMS massimo della soluzione
  congiunta. Se le camere rimaste non sono comunque spiegabili da un'unica posa
  rigida la posa viene scartata e il tag prosegue in dead-reckoning.

Se una camera compare di continuo fra quelle scartate il problema **non** è il
rumore: viene emesso l'evento `CAMERA_DISAGREEMENT` e le cause tipiche sono un
`size_m` sbagliato per quel marker (anche via `auto_mobile_markers.default_size_m`)
oppure estrinseci non più validi. Una dimensione errata sposta la stima di ogni
camera lungo il proprio raggio visivo: presa singolarmente ogni camera sembra
perfettamente stabile, ma le stime non si intersecano e la posa fusa oscilla.

### 9.5 Filtro del tracker

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

## 10. Diagnostica e Convenzioni Geometriche

- **Convenzione assi `world`**: Metri, frame destrorso con piano **XY sul pavimento** e asse **Z rivolto verso l'alto**.
- **Orientamento**: Quaternioni espressi come `(x, y, z, w)` normalizzati.
- **Log diagnostico strutturato**: Scritto automaticamente in `diagnostics/vision-system.jsonl` (formato JSON Lines ruotato a 20 MB). Contiene telemetria completa, controlli UVC accettati/rifiutati, condizioni delle matrici, metriche di stabilità e motivi degli scarti.
- **Monitoraggio del drift**: Durante il runtime, se i reference marker di una camera mostrano una discrepanza superiore a 2 cm o 2° per più di 2 secondi consecutivi, la camera viene automaticamente esclusa dalla fusione e viene emesso un evento `CALIBRATION_DRIFT`.

---

## 11. Collaudo Fisico

La suite di test verifica la correttezza algoritmica, le matrici geometriche e i protocolli di rete:

```bash
uv run pytest
uv run ruff check
```

Per il collaudo in opera, verificare l'accuratezza posizionando un tag a distanze note (2, 3 e 4 metri) lungo gli assi della griglia fisica. Un errore di riproiezione pixel ridotto non sostituisce la verifica metrica a terra.
