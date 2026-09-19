# 🎵 YouTube Music to Spotify Sync (Serverless & Free)

Automatización completa para sincronizar tus canciones con **"Me gusta"** de **YouTube Music** a una playlist de **Spotify**, de forma periódica (cada 3 horas) y bajo demanda, ejecutándose a **coste cero** mediante **GitHub Actions**.

> **⚠️ ¿Vienes de un fallo con `twoColumnBrowseResultsRenderer`?**
> Es la sesión de YouTube Music caducada. Salta directo a [La sesión de YouTube Music ha caducado](#-la-sesión-de-youtube-music-ha-caducado).

---

## 🚀 Características Principales

- 🔐 **OAuth + YouTube Data API v3 (oficial)**: los "Me gusta" se leen con un *refresh token* de tu propio cliente de Google, a través de la API pública y documentada de YouTube. **No caduca solo** (con la app publicada) y no depende de la API privada de music.youtube.com, que rechaza tokens OAuth externos. Coste: ~15 de las 10.000 unidades diarias de cuota.
- 🔔 **Aviso automático cuando algo caduca**: si una credencial deja de funcionar, el workflow **abre un issue en el repositorio** (y te llega por email). Cuando la sincronización vuelve a funcionar, el issue **se cierra solo**.
- 🔄 **Sincronización periódica** cada 3 horas vía cron, y **manual** desde la pestaña *Actions* (con opciones de *solo verificar credenciales* y *simulacro*).
- 🎯 **Búsqueda jerárquica e inteligente**:
  1. Coincidencia exacta por código **ISRC** (cuando existe).
  2. Búsqueda estricta por `track:"TÍTULO"` + `artist:"ARTISTA"`.
  3. Búsqueda relajada sobre el título completo del vídeo, con limpieza de ruido (`(Official Video)`, `[Lyrics]`, notas entre paréntesis…).
- 🛡️ **Validación de coincidencias**: descarta resultados cuya duración se desvía más de 30 s o cuyo título/artista no se parecen, para no meter en tu playlist un *cover*, un directo o un mix de una hora.
- ⚡ **Altas por lotes**: las canciones se añaden de 100 en 100 (antes: una petición por canción).
- 💾 **Estado a prueba de cortes**: `synced_tracks.json` se guarda incluso si la ejecución falla a mitad, así que nunca se repite trabajo ya hecho.
- ✅ **Tests offline** (`test_sync.py`) que se ejecutan en cada run antes de tocar ninguna API.
- 🔒 **Seguridad**: credenciales solo en **GitHub Secrets**; permiso de YouTube de **solo lectura**; ningún token se escribe en disco durante la ejecución.
- 🧯 **Fallback**: las cookies del navegador (`YTM_HEADERS_JSON`, vía `ytmusicapi`) siguen funcionando si no hay OAuth, pero están en desuso.

---

## 📁 Estructura del Proyecto

```text
.
├── .github/
│   └── workflows/
│       └── sync.yml          # Cron cada 3h + commit de estado + aviso por issue
├── docs/
│   ├── index.html            # Página pública (GitHub Pages) exigida por Google para publicar la app
│   └── privacy.html          # Política de privacidad (ídem)
├── .env.example              # Plantilla de variables de entorno
├── config.py                 # Lectura y saneado de variables/secretos
├── models.py                 # Modelo TrackInfo compartido
├── youtube_data.py           # Cliente de la YouTube Data API v3 (Me gusta, duración, artista)
├── ytm_auth.py               # Fuentes de "Me gusta": OAuth (Data API) o cookies (ytmusicapi) + errores accionables
├── main.py                   # Pipeline de sincronización
├── setup_ytm_oauth.py        # Genera el refresh token de YouTube (ejecutar una vez)
├── get_spotify_token.py      # Genera el refresh token de Spotify (ejecutar una vez)
├── test_sync.py              # Tests offline (sin red ni credenciales)
├── requirements.txt          # Dependencias Python
└── synced_tracks.json        # Estado: IDs ya procesados + temas sin coincidencia
```

---

## 🔑 Configuración de Credenciales

| Secreto | Descripción |
| :--- | :--- |
| `YTM_OAUTH_CLIENT_ID` | Client ID de tu cliente OAuth de Google |
| `YTM_OAUTH_CLIENT_SECRET` | Client Secret de ese mismo cliente |
| `YTM_OAUTH_REFRESH_TOKEN` | Refresh token de YouTube (no caduca solo con la app publicada) |
| `SPOTIPY_CLIENT_ID` | Client ID de tu app en Spotify Developer Dashboard |
| `SPOTIPY_CLIENT_SECRET` | Client Secret de esa app |
| `SPOTIPY_REFRESH_TOKEN` | Refresh token de OAuth de Spotify |
| `SPOTIFY_PLAYLIST_ID` | ID de la playlist destino |

> El antiguo `YTM_HEADERS_JSON` (cookies) **sigue funcionando como fallback**, pero está en desuso: es exactamente lo que provoca el fallo que estás viendo. Cuando configures OAuth puedes borrar ese secreto.

---

### Paso 1: Crear el cliente OAuth de Google (para YouTube)

Solo se hace una vez y es gratis. Son varios pasos porque Google exige una página pública y una política de privacidad para **publicar** la app, y sin publicarla el token caduca a los 7 días.

#### 1a. Proyecto y API

1. Entra en [Google Cloud Console](https://console.cloud.google.com/) y crea un proyecto (por ejemplo `ytm-sync`).
2. **APIs y servicios → Biblioteca** → busca **YouTube Data API v3** → **Habilitar**.

#### 1b. Marca (pantalla de consentimiento)

**Google Auth Platform → Información de la marca**:
- **Nombre de la aplicación** y **correo de asistencia al usuario** (tu Gmail).
- **Información de contacto del desarrollador**: tu Gmail (escríbelo y pulsa Enter para que quede como etiqueta).
- **No subas logotipo**: dispara la verificación de marca de Google, que no necesitas.
- Los enlaces de página principal / privacidad y el dominio autorizado los rellenas en el paso 1c.
- **Tipo de usuario: Externo**.

#### 1c. Página pública + política de privacidad (requisito para publicar)

Google no activa el botón **Publicar app** hasta que la marca tenga una **página principal**, una **política de privacidad** y un **dominio autorizado verificado en Search Console**. Este repo ya trae ambas páginas en `docs/`, así que se resuelve con GitHub Pages:

1. En tu repo: **Settings → Pages → Source: Deploy from a branch → Branch `main`, carpeta `/docs` → Save**. En 1-2 minutos tendrás `https://TU_USUARIO.github.io/ytm-spotify-sync/`.
2. [Search Console](https://search.google.com/search-console) → **Añadir propiedad → Prefijo de URL** → esa URL → método **Etiqueta HTML**. Copia la etiqueta `<meta name="google-site-verification" …>` dentro del `<head>` de `docs/index.html`, haz push, y pulsa **Verificar**.
3. Vuelve a **Información de la marca**:
   - **Página principal**: `https://TU_USUARIO.github.io/ytm-spotify-sync/`
   - **Política de privacidad**: `https://TU_USUARIO.github.io/ytm-spotify-sync/privacy.html`
   - **Dominios autorizados**: `TU_USUARIO.github.io`
   - **Guardar**.
4. **Google Auth Platform → Público → Publicar app** → confirmar. El estado pasa a **En producción**.

> **Atajo temporal**: si quieres probar antes de publicar, en **Público → Usuarios de prueba** añade tu Gmail. Podrás autorizar, pero Google caducará el refresh token **a los 7 días** (el propio `setup_ytm_oauth.py` te lo avisa). Cuando publiques, vuelve a ejecutar el script para obtener un token permanente.

#### 1d. Cliente OAuth y token

1. **Google Auth Platform → Clientes → Crear cliente**: tipo **Televisores y dispositivos de entrada limitada** (*TVs and Limited Input devices*). Copia el **Client ID** y el **Client Secret** (el secreto solo se muestra una vez).
2. En tu ordenador, con las dependencias instaladas (`pip install -r requirements.txt`):

   ```bash
   python setup_ytm_oauth.py
   ```

   - Pega el Client ID y el Client Secret cuando te los pida.
   - Se abrirá el navegador con un código: inicia sesión **con la misma cuenta de Google que usas en YouTube Music** y acepta el permiso de YouTube (solo lectura).
   - Si sale *"Google no ha verificado esta aplicación"* → **Configuración avanzada → Ir a (tu app)**. Es cosmético: es tu propia app.
   - El script espera solo, verifica que llega a tus "Me gusta" e imprime los **3 valores** listos para copiar.

---

### Paso 2: Crear la app en Spotify Developer Dashboard

1. Ve a [Spotify Developer Dashboard](https://developer.spotify.com/dashboard) → **Create app**:
   - **App name**: `YTM Sync`
   - **Redirect URIs**: exactamente `http://127.0.0.1:9090`
2. En **Settings** copia el **Client ID** y el **Client Secret**.

---

### Paso 3: Obtener el `SPOTIPY_REFRESH_TOKEN`

1. Crea un archivo `.env` con tu client id/secret de Spotify (ver `.env.example`).
2. Ejecuta:

   ```bash
   python get_spotify_token.py
   ```

3. Acepta en el navegador. Serás redirigido a `http://127.0.0.1:9090` (es normal que la página diga "no se puede acceder") y la consola imprimirá tu refresh token.

---

### Paso 4: Obtener el `SPOTIFY_PLAYLIST_ID`

Comparte la playlist → *Copiar enlace* → el ID es lo que va entre `/playlist/` y `?`:
`https://open.spotify.com/playlist/**37i9dQZF1DXcBWIGoYBM5M**?si=…`

*(El script también acepta la URL completa o el URI `spotify:playlist:…`; ya extrae el ID por su cuenta.)*

---

### Paso 5: Configurar los GitHub Secrets

En tu repositorio: **Settings → Secrets and variables → Actions → New repository secret**, y añade los 7 secretos de la tabla anterior.

Después, comprueba que todo está bien **sin sincronizar nada**:

**Actions → Sync YouTube Music to Spotify → Run workflow → marca *Solo verificar credenciales* → Run**.

---

## ⚙️ Permisos de GitHub Actions

**Settings → Actions → General → Workflow permissions → Read and write permissions**.

El workflow necesita `contents: write` (para commitear `synced_tracks.json`) e `issues: write` (para avisarte cuando caduque una credencial). Ambos están declarados en `sync.yml`.

---

## 🆘 La sesión de YouTube Music ha caducado

Síntoma en los logs:

```text
KeyError: "Unable to find 'twoColumnBrowseResultsRenderer' ... on
{'singleColumnBrowseResultsRenderer': ... 'text': 'Sign in' ...}"
```

**Qué significa**: YouTube Music ha respondido con la página de *usuario no identificado*. Las cookies de `YTM_HEADERS_JSON` ya no valen. Google las invalida con el tiempo, y mucho antes cuando se reutilizan desde una IP de datacenter (los runners de GitHub Actions).

**Solución definitiva**: migrar a OAuth siguiendo el [Paso 1](#paso-1-crear-el-cliente-oauth-de-google-para-youtube). Un refresh token propio, con la app publicada, no caduca por antigüedad; solo dejará de valer si lo revocas o cambias la contraseña de Google.

A partir de ahora, si vuelve a pasar **no lo descubrirás dos semanas tarde**: el workflow abre un issue titulado *"🔐 La sesión de YouTube Music ha caducado"* con los pasos exactos, y lo cierra solo cuando la sincronización se recupera.

### Otros errores frecuentes

| Mensaje | Causa | Solución |
| :--- | :--- | :--- |
| `CONFIGURATION ERROR: Missing required ... secret(s)` | Falta un secreto | Añádelo en *Settings → Secrets* |
| `Error 403: access_denied` / *"no ha completado el proceso de verificación"* al autorizar | App en modo Prueba y tu cuenta no es usuario de prueba | Publica la app (Paso 1c) o añádete en *Público → Usuarios de prueba* |
| `invalid_grant: Token has been expired or revoked` | App en modo *Prueba* (7 días) o token revocado | Publica la app y regenera el token |
| `accessNotConfigured` / `YouTube Data API v3 has not been used` | API no habilitada en el proyecto | Paso 1a, punto 2 |
| `quotaExceeded` | Cuota diaria de la Data API agotada | Espera al reinicio (medianoche hora del Pacífico); un run gasta ~15 de 10.000 |
| `SPOTIFY AUTHENTICATION FAILED` | Refresh token de Spotify revocado | `python get_spotify_token.py` |

---

## 🧪 Ejecución Local

```bash
pip install -r requirements.txt
cp .env.example .env      # y rellena tus valores

python main.py                 # sincronización completa
python main.py --check-auth    # solo comprueba que ambas credenciales funcionan
python main.py --dry-run       # busca en Spotify pero no añade nada ni toca el estado
python main.py --limit 20      # procesa solo los 20 "me gusta" más recientes
python main.py --verbose       # logs de depuración (incluye por qué se descarta cada candidato)
python test_sync.py            # tests offline (sin red ni credenciales)
```

### Códigos de salida

| Código | Significado |
| :---: | :--- |
| `0` | Éxito |
| `1` | Fallo inesperado |
| `2` | Credenciales de YouTube ausentes o caducadas |
| `3` | Spotify ha rechazado las credenciales |
| `4` | Falta configuración (algún secreto sin definir) |

---

## 📊 Ejemplo de Salida en Logs

```text
2026-09-19 15:30:00 [INFO] ============================================================
2026-09-19 15:30:00 [INFO] Starting YouTube Music -> Spotify Playlist Sync Pipeline
2026-09-19 15:30:00 [INFO] ============================================================
2026-09-19 15:30:00 [INFO] Loading YouTube OAuth token from YTM_OAUTH_REFRESH_TOKEN.
2026-09-19 15:30:01 [INFO] YouTube client initialised using OAuth (refresh token) + Data API v3.
2026-09-19 15:30:01 [INFO] Authenticated with YouTube as 'Alfred'.
2026-09-19 15:30:02 [INFO] Refreshing Spotify access token with SPOTIPY_REFRESH_TOKEN...
2026-09-19 15:30:02 [INFO] Loaded 434 previously synced YouTube track ID(s).
2026-09-19 15:30:04 [INFO] Retrieved 868 existing track identifier(s) from target Spotify playlist.
2026-09-19 15:30:06 [INFO] Retrieved 520 liked music video(s) from the YouTube Data API (149 non-music like(s) ignored).
2026-09-19 15:30:06 [INFO] Processing liked songs...
2026-09-19 15:30:06 [INFO] [1/520] Processing: 'Con la Misma Piedra' by 'Julio Iglesias' (YT ID: BoD6NCXMKuk)
2026-09-19 15:30:07 [INFO]   -> Match found via strict search: 'Con La Misma Piedra' by Julio Iglesias
2026-09-19 15:30:08 [INFO] [ADDED] 3 track(s) added to the Spotify playlist.
2026-09-19 15:30:08 [INFO] State successfully saved to 'synced_tracks.json'.
2026-09-19 15:30:08 [INFO] ============================================================
2026-09-19 15:30:08 [INFO] SYNCHRONIZATION COMPLETED - SUMMARY REPORT
2026-09-19 15:30:08 [INFO] ============================================================
2026-09-19 15:30:08 [INFO]   Total YouTube Liked Songs Checked: 520
2026-09-19 15:30:08 [INFO]   [+] Newly Added to Spotify:        3
2026-09-19 15:30:08 [INFO]   [=] Already in Spotify Playlist:   0
2026-09-19 15:30:08 [INFO]   [-] Skipped (Previously Synced):   434
2026-09-19 15:30:08 [INFO]   [!] Not Found on Spotify:          0
2026-09-19 15:30:08 [INFO] ============================================================
```

---

## 🛠️ Tecnologías Empleadas

- **Python 3.11+**
- **YouTube Data API v3** (vía `requests`): lectura oficial de los "Me gusta" con OAuth.
- **ytmusicapi**: solo para el fallback de cookies (en desuso).
- **spotipy**: cliente de la Spotify Web API con refresh automático del access token y reintentos ante `429`/`5xx`.
- **GitHub Actions**: orquestación en cron sin coste de servidor.
