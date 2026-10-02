# Crisvis

Asistente personal **local** con la interfaz holográfica y de voz de
[adewaskar/jarvis](https://github.com/adewaskar/jarvis) y el cerebro de
[OpenJarvis](https://github.com/open-jarvis/OpenJarvis): agentes, herramientas,
memoria y modelos que corren en tu equipo.

Es **una sola aplicación**: un proceso Python sirve la interfaz, el WebSocket y
el cerebro en `http://127.0.0.1:8787`. No hay puente Node ni dos servidores que
mantener sincronizados.

```
Navegador ──(mismo origen: /, /ws, /tts, /stt, /img, /page…)──► núcleo crisvis (FastAPI)
  la cara: escena 3D, HUD, voz,                                 ├─ cerebro: OpenJarvis (agente, herramientas, memoria)
  blades, confirmaciones                                        ├─ permisos + auditoría
                                                                ├─ proxy de medios con guarda SSRF
                                                                └─ voz: clonada local / ElevenLabs / faster-whisper (opcionales)
                                                                     └─ services/voz: XTTS-v2 en la GPU (proceso hijo)
                                                                         │
                                                                         ▼
                                                                Ollama u otro motor local
```

## Requisitos

- Windows 10/11, macOS o Linux.
- [Node.js](https://nodejs.org) 20 o superior.
- [uv](https://docs.astral.sh/uv/) (gestor de Python). En Windows: `winget install --id astral-sh.uv -e`.
- Un motor local. Recomendado: [Ollama](https://ollama.com) (`winget install --id Ollama.Ollama -e`).
- Chrome o Edge para la voz (Web Speech API). Sin ellos, la consola escrita funciona igual.

Modelo recomendado: `qwen3:8b` (≈5 GB, va bien con 8 GB de VRAM o más). Con menos
memoria, `qwen3:4b`.

## Instalación (en cualquier PC)

```bash
git clone <url-del-repositorio> crisvis
cd crisvis
npm run setup
```

Hace todo: `uv sync`, `npm install`, compila la interfaz, crea
`~/.crisvis/crisvis.toml`, instala la voz clonada (PyTorch + XTTS-v2), comprueba
Ollama, descarga `qwen3:8b` y el modelo de visión `qwen3-vl:4b-instruct` si
faltan, y termina con un diagnóstico.

Opciones: `--sin-modelo` (no descarga modelos), `--modelo=qwen3:4b` (otro
modelo de chat), `--sin-voz` (sin voz clonada; habla la voz del navegador),
`--sin-vision` (sin el modelo que ve la pantalla). Por ejemplo:
`npm run setup -- --modelo=qwen3:4b --sin-voz`.

No hay que configurar nada a mano: el cerebro elige solo el modelo de chat y el
de visión entre los instalados en Ollama, y la voz de serie viene en el propio
repositorio. Lo que se descarga (dependencias, modelos, la interfaz compilada)
no está en git.

## Uso

```bash
npm start
```

Abre `http://127.0.0.1:8787`. Pulsa **INICIAR** (o da una palmada) y di
**«Jarvis…»** o **«Crisvis…»** seguido de lo que quieras. También puedes
escribir en la consola de abajo a la izquierda.

| Tecla | Acción |
|---|---|
| `Espacio` | Hablar sin decir el nombre / interrumpirle |
| `Esc` | Ponerle en espera |
| `Intro` / `Esc` | Permitir / denegar una petición de permiso |
| `V` | Probar la siguiente voz del sistema |
| `T` | Prueba de audio |
| `G` | Control por gestos con la cámara |
| `D` | Panel de diagnóstico de voz |

Cosas que puedes pedirle desde el primer momento: «recuerda que…» y «¿qué te
dije de…?» (memoria persistente), cálculos, búsquedas web, el tiempo (con
`OPENWEATHERMAP_API_KEY`), leer y escribir archivos, ejecutar órdenes (siempre
con permiso), abrir artículos, imágenes o vídeos en pantalla, cambiar el color
de la interfaz y, con un modelo de visión, «mira esto».

## Voz clonada (en tu equipo)

Crisvis habla con una voz clonada a partir de un audio y generada en tu GPU con
XTTS-v2: sin cuentas, sin pagar y sin que el audio salga del equipo.

**Voz de serie:** el repositorio trae `services/voz/muestras/adam.wav`. En un
equipo nuevo se copia sola a `~/.crisvis/voz` la primera vez que arranca, así
que habla con esa voz sin hacer nada. Si pones tu propia muestra, la de serie no
la sustituye.

Para usar otra voz:

```bash
npm run voz:instalar                      # una vez (npm run setup ya lo hace)
npm run voz -- "C:/ruta/a/la/voz.mp3" --reemplazar   # wav, mp3, flac u ogg
```

Para cambiar la voz de serie del repositorio, sustituye el archivo de
`services/voz/muestras/`.

¿Muchos clips sueltos (frases de una película, por ejemplo)? Prepáralos juntos:

```bash
npm run voz -- --preparar --nombre jarvis "C:/ruta/a/la/carpeta"
```

Aísla la voz de cada clip con Demucs (quita música y efectos), recorta
silencios, nivela el volumen, descarta los clips que no suenan a la misma voz
o que eran casi todo fondo, y une el resto en una sola muestra.

La muestra: entre 10 segundos y 2 minutos de la voz sola, sin música ni ruido.
Varias muestras se combinan (`npm run voz -- a.wav b.wav`; `--reemplazar` quita
las anteriores). Con Crisvis en marcha, la voz nueva se aplica sola en unos
segundos. La primera vez descarga el modelo (≈1,8 GB). Va mejor con una GPU
NVIDIA (controlador reciente; las RTX 50 incluidas); sin ella funciona en la
CPU, pero lento. Se instala en Windows y Linux; en macOS habla la voz del
sistema.

El servicio vive en `services/voz` con su propio entorno de Python para que
PyTorch no se mezcle con el cerebro; el núcleo lo lanza, solo escucha en
127.0.0.1 y exige una clave aleatoria por arranque. Mientras carga, o si falla,
habla la voz del navegador. El modelo XTTS-v2 se distribuye bajo la
[Coqui Public Model License](https://coqui.ai/cpml): solo uso no comercial.
Clonar la voz de otra persona sin su permiso puede no ser legal según dónde
vivas; úsalo para ti.

## Control del PC

Puede manejar el equipo: abrir aplicaciones y webs, enfocar, mover y cerrar
ventanas, pulsar botones (los encuentra por su nombre con UI Automation o, si
hace falta, mirando la pantalla con el modelo de visión), escribir, usar atajos,
desplazarse, controlar el volumen y la música, buscar y mover archivos (a la
papelera, nunca borrando del todo), cerrar procesos y bloquear, suspender,
reiniciar o apagar.

- Lo que cambia algo pide permiso según el modo (ver abajo). En el modo
  *confirmar*, un sí vale para el resto de pasos de esa misma orden; cerrar
  procesos y apagar o reiniciar preguntan siempre.
- **Parada de emergencia:** lleva el ratón a la esquina superior izquierda de la
  pantalla y se detiene cualquier acción en curso.
- Nunca escribe sobre lo que tengas abierto: abre un documento nuevo antes.

Se desactiva con `herramientas_pc = false` en `[cerebro]`.

## Aplicaciones conectadas (MCP)

Crisvis puede usar cualquier servidor [MCP](https://modelcontextprotocol.io)
(GitHub, Notion, Slack, Home Assistant, Playwright, Zapier…). Se declaran en
`~/.crisvis/mcp.json` con el mismo formato que Claude Desktop y Cursor, así que
puedes copiar la configuración de cualquiera de ellos:

```json
{
  "mcpServers": {
    "notion": {
      "command": "npx",
      "args": ["-y", "@notionhq/notion-mcp-server"],
      "env": { "NOTION_TOKEN": "${NOTION_TOKEN}" }
    },
    "github": {
      "url": "https://api.githubcopilot.com/mcp/",
      "headers": { "Authorization": "Bearer ${GITHUB_TOKEN}" }
    }
  }
}
```

Las claves (`${NOTION_TOKEN}`) se leen de `~/.crisvis/.env`, para no dejarlas en
el archivo. Al guardar `mcp.json` o `.env`, Crisvis reconecta solo, sin
reiniciar. Sus herramientas aparecen como `mcp__servidor__herramienta`, pasan
por los mismos permisos y, en cada orden, el modelo solo ve las que tienen que
ver con lo que pides (`mcp_max_herramientas`, 12 por defecto). El estado de cada
conexión está en `http://127.0.0.1:8787/api/mcp`.

## Permisos

Cada herramienta tiene un nivel (interfaz, lectura, escritura, peligroso) y el
modo elegido en la consola decide qué pasa:

| Modo | Lectura | Escritura | Peligroso |
|---|---|---|---|
| **Solo lectura** | sí | bloqueada | bloqueada |
| **Confirmar** (por defecto) | sí | pregunta | pregunta |
| **Libre** | sí | sí | pregunta |

Las preguntas aparecen en pantalla y se dicen en voz alta; se contestan con un
clic, con `Intro`/`Esc` o diciendo «sí» / «no». Sin respuesta en 30 s, es un no.
Cada decisión queda en `~/.crisvis/auditoria.jsonl`. En `crisvis.toml` puedes
fijar listas `permitir`, `confirmar` y `denegar` con comodines.

## Configuración

`~/.crisvis/crisvis.toml` (se crea con `uv run python -m crisvis init`). Lo
más habitual:

```toml
[asistente]
nombre = "Jarvis"           # también el que aparece en pantalla
tratamiento = "señor"
palabras_activacion = ["jarvis", "crisvis"]

[cerebro]
modelo = ""                      # vacío = el primero instalado (p. ej. "qwen3:8b")
modelo_vision = ""               # vacío = el mejor instalado (qwen3-vl:4b-instruct)
razonar = "auto"                 # auto | siempre | nunca
herramientas_pc = true           # control del PC

[voz]
clonada = true                   # usa la voz clonada si está instalada
estilo = "original"              # voz del navegador: original (británica) | nativa
elevenlabs_api_key = ""          # opcional: voz y transcripción de mayor calidad
```

Variables de entorno: ver [`.env.example`](.env.example).

## Desarrollo

```bash
npm run dev        # núcleo en :8787 + Vite en :5173 con recarga en caliente
npm test           # contrato del protocolo + pytest
npm run typecheck  # TypeScript
npm run lint       # ruff + oxlint
npm run doctor     # diagnóstico de la instalación
uv run python -m crisvis preguntar "¿qué hora es?"   # el cerebro por terminal
```

La arquitectura y las decisiones están en [docs/ARQUITECTURA.md](docs/ARQUITECTURA.md).

## Licencias

Código propio bajo MIT. La interfaz deriva de adewaskar/jarvis (MIT,
[docs/LICENSE-jarvis-ui.txt](docs/LICENSE-jarvis-ui.txt)); OpenJarvis se usa como
dependencia (Apache-2.0, [docs/LICENSE-openjarvis.txt](docs/LICENSE-openjarvis.txt)).
La voz de serie (`services/voz/muestras/adam.wav`) se generó con ElevenLabs
(voz «Adam»); su uso queda sujeto a las condiciones de ElevenLabs.
