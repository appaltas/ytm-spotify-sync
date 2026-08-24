# 🎵 YouTube Music to Spotify Sync (Serverless & Free)

Automatización completa y optimizada para sincronizar tus canciones con **"Me gusta"** de **YouTube Music** a una playlist específica de **Spotify** de forma periódica (cada 3 horas) y bajo demanda, ejecutándose a **coste cero** mediante **GitHub Actions**.

---

## 🚀 Características Principales

- 🔄 **Sincronización Periódica**: Automatizada cada 3 horas vía cron en GitHub Actions.
- ⚡ **Activación Manual**: Posibilidad de disparar la sincronización en cualquier momento desde la pestaña *Actions* de GitHub (`workflow_dispatch`).
- 🎯 **Búsqueda Jerárquica e Inteligente**:
  1. Coincidencia exacta por código **ISRC** (cuando esté disponible).
  2. Búsqueda por `track:"TÍTULO"` y `artist:"ARTISTA"`.
  3. Búsqueda con limpieza y saneamiento de títulos de YouTube (eliminando `(Official Video)`, `[Lyrics]`, etc.).
- 🛡️ **Control de Estado e Idempotencia**: Archivo `synced_tracks.json` persistido automáticamente en el repositorio para no reconsultar temas ya procesados ni duplicar canciones en Spotify.
- 🔒 **Seguridad Total**: Las credenciales sensibles se gestionan de forma segura mediante **GitHub Secrets**.
- 💸 **Coste Cero**: Utiliza exclusivamente la capa gratuita de GitHub Actions y las APIs públicas de Spotify y YouTube Music.

---

## 📁 Estructura del Proyecto

```text
.
├── .github/
│   └── workflows/
│       └── sync.yml          # Flujo de GitHub Actions (Cron cada 3h + Push de estado)
├── .env.example              # Plantilla de variables de entorno para desarrollo local
├── .gitignore                # Ignora .env, caches y archivos temporales
├── get_spotify_token.py      # Script auxiliar para obtener el SPOTIPY_REFRESH_TOKEN
├── main.py                   # Script principal de sincronización y lógica de negocio
├── README.md                 # Documentación y guía paso a paso
├── requirements.txt          # Dependencias Python
└── synced_tracks.json        # Registro de IDs procesados (auto-persistido por git)
```

---

## 🔑 Guía Paso a Paso para Configurar Credenciales

Para que el script funcione, necesitas configurar 5 variables (en local en un archivo `.env` o en **GitHub Repository Secrets**):

| Variable | Descripción |
| :--- | :--- |
| `YTM_HEADERS_JSON` | Cabeceras de sesión de YouTube Music en formato JSON |
| `SPOTIPY_CLIENT_ID` | Client ID de tu aplicación en Spotify Developer Dashboard |
| `SPOTIPY_CLIENT_SECRET` | Client Secret de tu aplicación en Spotify Developer Dashboard |
| `SPOTIPY_REFRESH_TOKEN` | Refresh Token de OAuth de Spotify con permisos de playlist |
| `SPOTIFY_PLAYLIST_ID` | ID de la playlist destino en Spotify |

---

### Paso 1: Obtener `YTM_HEADERS_JSON` (YouTube Music)

1. Abre tu navegador (Chrome/Firefox/Edge) y entra en [music.youtube.com](https://music.youtube.com) habiendo iniciado sesión con tu cuenta de Google.
2. Abre las **Herramientas de Desarrollador** (`F12` o `Ctrl + Shift + I`) y dirígete a la pestaña **Network** (Red).
3. Filtra por `browse` o interactúa con la página (por ejemplo, haz clic en *Biblioteca* o *Explorar*).
4. Selecciona una petición enviada a `music.youtube.com` (por ejemplo, `browse` o `next`).
5. En la sección **Request Headers** (Cabeceras de la petición):
   - Haz clic derecho sobre las cabeceras y selecciona **Copy as cURL (bash)** o copia las cabeceras principales (`cookie`, `x-goog-authuser`, `authorization`, `user-agent`).
6. Si utilizas la librería `ytmusicapi`, puedes generar el JSON automáticamente ejecutando en tu terminal:
   ```bash
   pip install ytmusicapi
   ytmusicapi browser
   ```
   Pega las cabeceras que te solicite el asistente y se generará un archivo `browser.json`.
7. Abre el contenido de ese archivo `browser.json` (o la cadena JSON con las cabeceras) y cópialo. Ese texto completo será el valor de `YTM_HEADERS_JSON`.

---

### Paso 2: Crear App en Spotify Developer Dashboard

1. Ve a [Spotify Developer Dashboard](https://developer.spotify.com/dashboard) e inicia sesión con tu cuenta.
2. Haz clic en **Create app**:
   - **App name**: `YTM Sync`
   - **App description**: `Sync liked songs from YTM`
   - **Redirect URIs**: Añade exactamente `http://127.0.0.1:9090`
   - Marca la casilla de aceptación de términos y haz clic en **Save**.
3. En la configuración de la app (**Settings**):
   - Copia el **Client ID** (`SPOTIPY_CLIENT_ID`).
   - Haz clic en **View client secret** y copia el **Client Secret** (`SPOTIPY_CLIENT_SECRET`).

---

### Paso 3: Obtener el `SPOTIPY_REFRESH_TOKEN`

Hemos incluido el script auxiliar `get_spotify_token.py` para obtener el refresh token en menos de 1 minuto:

1. Crea un archivo `.env` en la raíz del proyecto con tu Client ID y Secret:
   ```env
   SPOTIPY_CLIENT_ID="tu_client_id"
   SPOTIPY_CLIENT_SECRET="tu_client_secret"
   ```
2. Instala las dependencias y ejecuta el script:
   ```bash
   pip install -r requirements.txt
   python get_spotify_token.py
   ```
3. Se abrirá tu navegador solicitando autorización. Haz clic en **Aceptar**.
4. Serás redirigido a `http://127.0.0.1:9090` y la consola imprimirá tu **`SPOTIPY_REFRESH_TOKEN`**. Cópialo.

---

### Paso 4: Obtener el `SPOTIFY_PLAYLIST_ID`

1. En Spotify, crea una playlist (o usa una existente) donde quieras recibir las canciones.
2. Haz clic en los tres puntos de la playlist -> **Compartir** -> **Copiar enlace a la playlist**.
3. El enlace tendrá este formato:
   `https://open.spotify.com/playlist/37i9dQZF1DXcBWIGoYBM5M?si=...`
4. El ID es la parte entre `/playlist/` y `?`:
   `SPOTIFY_PLAYLIST_ID="37i9dQZF1DXcBWIGoYBM5M"`

---

### Paso 5: Configurar GitHub Secrets

1. Sube este repositorio a tu cuenta de GitHub (puede ser **público o privado**).
2. En tu repositorio de GitHub, ve a **Settings** -> **Secrets and variables** -> **Actions**.
3. Haz clic en **New repository secret** y añade los siguientes 5 secretos:

| Nombre del Secreto | Valor |
| :--- | :--- |
| `YTM_HEADERS_JSON` | El JSON de cabeceras obtenido en el Paso 1 |
| `SPOTIPY_CLIENT_ID` | Tu Client ID de Spotify |
| `SPOTIPY_CLIENT_SECRET` | Tu Client Secret de Spotify |
| `SPOTIPY_REFRESH_TOKEN` | El Refresh Token generado en el Paso 3 |
| `SPOTIFY_PLAYLIST_ID` | El ID de la playlist obtenido en el Paso 4 |

---

## ⚙️ Permisos de GitHub Actions

Para que GitHub Actions pueda guardar y commitear automáticamente el archivo de estado `synced_tracks.json`:

1. En tu repositorio, ve a **Settings** -> **Actions** -> **General**.
2. En la sección **Workflow permissions**, selecciona:
   - **Read and write permissions**
3. Haz clic en **Save**.

*(El workflow `.github/workflows/sync.yml` ya incluye explícitamente `permissions: contents: write`)*.

---

## 🧪 Ejecución Local (Opcional)

Si deseas probar el sincronizador en tu máquina local antes de desplegar:

1. Crea tu archivo `.env` basándote en `.env.example`:
   ```bash
   cp .env.example .env
   ```
2. Rellena las 5 variables con tus credenciales.
3. Instala las dependencias:
   ```bash
   pip install -r requirements.txt
   ```
4. Ejecuta la sincronización:
   ```bash
   python main.py
   ```

---

## 📊 Ejemplo de Salida en Logs

```text
2026-08-24 15:30:00 [INFO] ============================================================
2026-08-24 15:30:00 [INFO] Starting YouTube Music -> Spotify Playlist Sync Pipeline
2026-08-24 15:30:00 [INFO] ============================================================
2026-08-24 15:30:00 [INFO] Loaded 12 previously synced YouTube track ID(s).
2026-08-24 15:30:01 [INFO] Refreshing Spotify access token with SPOTIPY_REFRESH_TOKEN...
2026-08-24 15:30:02 [INFO] Retrieved 12 existing track identifier(s) from target Spotify playlist.
2026-08-24 15:30:02 [INFO] Fetching up to 50 liked songs from YouTube Music...
2026-08-24 15:30:03 [INFO] Successfully retrieved 50 liked track(s) from YouTube Music.
2026-08-24 15:30:03 [INFO] Processing liked songs...
2026-08-24 15:30:03 [INFO] [1/50] Processing: 'Starboy' by 'The Weeknd' (YT ID: dXN4pTq_...)
2026-08-24 15:30:04 [INFO]   -> Match found via ISRC (USUM71607007): 'Starboy' by The Weeknd
2026-08-24 15:30:04 [INFO]   -> [ADDED] Successfully added 'Starboy' to Spotify playlist!
...
2026-08-24 15:30:15 [INFO] ============================================================
2026-08-24 15:30:15 [INFO] SYNCHRONIZATION COMPLETED - SUMMARY REPORT
2026-08-24 15:30:15 [INFO] ============================================================
2026-08-24 15:30:15 [INFO]   Total YouTube Liked Songs Checked: 50
2026-08-24 15:30:15 [INFO]   ✨ Newly Added to Spotify:          4
2026-08-24 15:30:15 [INFO]   🔁 Already in Spotify Playlist:     34
2026-08-24 15:30:15 [INFO]   ⏭️  Skipped (Previously Synced):   12
2026-08-24 15:30:15 [INFO]   ⚠️  Not Found on Spotify:            0
2026-08-24 15:30:15 [INFO] ============================================================
```

---

## 🛠️ Tecnologías Empleadas

- **Python 3.11+**
- **ytmusicapi**: Extracción autenticada y parsing de YouTube Music.
- **spotipy**: Cliente oficial y flujo OAuth2 Refresh Token para Spotify Web API.
- **GitHub Actions**: Orquestación CI/CD y automatización programada en cron sin costes de servidor.
