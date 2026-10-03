# Arquitectura de Crisvis

## Objetivo

Unir dos proyectos en **una** aplicación local:

- **La cara**: la interfaz holográfica, la voz y la UX de
  [adewaskar/jarvis](https://github.com/adewaskar/jarvis) (React + Three.js).
  Originalmente hablaba con Claude a través de un puente Node.
- **El cerebro**: [OpenJarvis](https://github.com/open-jarvis/OpenJarvis)
  (Python): motores locales, agentes, registro de herramientas, memoria, MCP,
  seguridad y telemetría.

El puente Node desaparece. En su lugar hay un núcleo Python que usa OpenJarvis
como **biblioteca** (no como servicio aparte) y sirve la interfaz compilada.

## Estructura

```
apps/face/            La cara (Vite + React + TS). Se compila a src/crisvis/static.
packages/protocol/    Contrato del WebSocket: frames.json + tipos TypeScript.
src/crisvis/
  settings.py         crisvis.toml + variables de entorno. No importa OpenJarvis.
  app.py              FastAPI: rutas, estáticos, middleware de origen, ciclo de vida.
  cli.py              crisvis [iniciar|init|doctor|preguntar].
  brain/              El cerebro: todo lo que toca OpenJarvis vive aquí.
    openjarvis_brain.py  Arranque, motor, modelo, herramientas, MCP, memoria, prompt.
    agent.py             Agente en streaming (registrado en OpenJarvis como crisvis_voz).
    memory.py            Backend de memoria FTS5 propio (ver «extensión nativa»).
    persona.py           Personalidad JARVIS en castellano, por secciones.
    events.py            Eventos del agente hacia la pasarela.
  gateway/            La frontera con la cara.
    ws.py                /ws: tramas, sesiones, reconexión.
    session.py           Sesión de conversación, cola de escritura, puerta de permisos.
    origin.py            Qué orígenes pueden abrir el socket.
    protocol.py          Lista de tramas (comprobada contra frames.json).
  body/               Herramientas que actúan sobre la cara: display, blade, ui_*,
                      probe_url, look/watch (cámara + modelo de visión).
  security/           Política de permisos y auditoría JSONL.
  media/              Proxy /img, /media, /page, /file con guarda SSRF.
  voice/              /health, /tts (ElevenLabs), /stt (ElevenLabs o faster-whisper).
tests/                pytest: política, red, memoria, agente, origen, protocolo.
scripts/              setup.mjs (instalación) y dev.mjs (desarrollo).
```

### Límites internos

- `brain` no sabe nada de WebSockets ni de la interfaz. Emite eventos
  (`TextDelta`, `ToolStarted`, `ToolFinished`, `Finished`) y pide permiso a
  través de una función `gate` inyectada.
- `gateway` no conoce OpenJarvis: traduce eventos a tramas y tramas a turnos.
- `body` habla con la cara solo a través de la interfaz `Surface` (empujar una
  trama, pedir algo y esperar respuesta), que implementa `Session`.
- La cara solo conoce el protocolo (`@crisvis/protocol`) y URLs relativas.
  Nunca ve una clave.

## Flujo de un turno

1. La cara reconoce la voz (Web Speech, o `/stt` si el núcleo tiene
   ElevenLabs/faster-whisper) o recibe texto en la consola.
2. Envía `{type:"ask", id, text}` por `/ws`.
3. `Session` construye el contexto (historial de la sesión + memoria de
   OpenJarvis vía `inject_context`) y crea un `CrisvisVoiceAgent`.
4. El agente llama a `engine.stream_full` (Ollama por defecto) con las
   herramientas visibles. El texto se emite según llega, filtrando `<think>`.
5. Cada llamada a herramienta pasa por: herramienta conocida → `check_taint`
   de OpenJarvis → **puerta de permisos** de Crisvis → `ToolExecutor` de
   OpenJarvis (en un hilo; conserva RBAC, timeouts y eventos del bus).
6. La cara recibe `text`/`tool`/`done` etiquetados con el `id` de la pregunta,
   lee la respuesta frase a frase y muestra blades, paneles o efectos que el
   cerebro empuje a mitad de turno.

### Sesiones y reconexión

La conversación vive en el núcleo, no en el socket. La cara guarda el
identificador de sesión en `sessionStorage` y, al reconectar, envía `hello`; el
núcleo responde `ready{resumed:true}` y la charla sigue. Las sesiones sin socket
caducan a los 15 minutos. Interrumpir (`interrupt`, o hablar encima) cancela el
turno y guarda la respuesta parcial con la marca «[interrumpido]».

## Permisos

Orden de evaluación: `denegar` → `permitir` → `confirmar` (listas con comodines
de `crisvis.toml`) → matriz modo × nivel. Si OpenJarvis marca una herramienta con
`requires_confirmation` (p. ej. `shell_exec`), un «permitir» se eleva a
«confirmar». Las herramientas denegadas ni siquiera se ofrecen al modelo.

Las herramientas MCP desconocidas se clasifican por el verbo de su nombre
(`list_`, `get_` → lectura; `send`, `delete` → escritura; `exec`, `shell` →
peligroso; en caso de duda, escritura). `memory_store` cuenta como lectura: solo
escribe en la memoria propia del asistente.

La confirmación viaja a la cara como trama `confirm`; la contesta un clic,
`Intro`/`Esc` o la voz («sí»/«no», respuestas de seis palabras como mucho para
que el eco de su propia pregunta no cuente). Sin respuesta en
`segundos_confirmacion`, es un no. Todo queda en `auditoria.jsonl`.

## Red

- El servidor escucha en `127.0.0.1`. El WebSocket y las rutas HTTP rechazan
  orígenes ajenos (403 / cierre 4403): solo la propia app y los puertos de
  desarrollo de Vite en localhost.
- La cara y el núcleo comparten origen, así que la CSP de la página es `'self'`
  para imágenes, medios y marcos: el navegador nunca pide bytes a un tercero.
- El proxy de medios resuelve el nombre **una vez**, rechaza cualquier IP
  privada, reservada, CGNAT o de metadatos, conecta a la IP ya validada
  (conservando Host y SNI) y revalida cada redirección.
- `/file` sirve solo imágenes de la carpeta personal, la temporal o las
  carpetas configuradas.

## Motor y modelo

- **Solo motores locales en autodetección.** El fallback de OpenJarvis podía
  elegir un motor remoto; Crisvis prueba en paralelo los motores locales
  (`ollama`, `lmstudio`, `llamacpp`, `vllm`…) y comprueba que el host sea local.
  La nube solo se usa si se configura explícitamente `cerebro.motor`.
- Si no hay motor, la aplicación arranca igual y lo dice en el HUD; reintenta
  cada 10 segundos.
- **Razonamiento.** Con `qwen3:8b` sin razonar, el modelo responde «lo he
  guardado» sin llamar a `memory_store`, e inventa en lugar de usar
  `memory_search`. Con razonamiento usa bien las herramientas, a cambio de 1–4
  segundos. `razonar = "auto"` lo activa solo en el primer paso de cada turno
  (cuando decide qué herramienta usar) y lo desactiva al redactar con los
  resultados delante. Solo se envía a modelos que lo admiten.

## La extensión nativa de OpenJarvis

OpenJarvis 1.0.x delega parte de su funcionalidad en `openjarvis_rust`, una
extensión que **no se publica en PyPI**. Sin ella:

- No hay backends de memoria (`sqlite` y `bm25` la necesitan). Crisvis registra
  su propio backend `crisvis_sqlite` (SQLite FTS5 con eliminación de tildes y
  palabras vacías en castellano), que OpenJarvis usa a través de su
  `MemoryRegistry` como cualquier otro.
- No hay escáneres de secretos/PII ni limitador de frecuencia. Siguen activos
  el registro de auditoría, el control de taint, la guarda SSRF y la política de
  permisos de Crisvis.

Si se compila la extensión (ver el repositorio de OpenJarvis; requiere Rust y
`maturin`) e instala en el entorno, Crisvis la detecta y usa el backend nativo
configurado. `crisvis doctor` informa de su estado.

## Voz

- **Escuchar**: Web Speech API del navegador por defecto (Chrome/Edge). Con
  `uv sync --extra voz-local`, transcripción local con faster-whisper. Con clave
  de ElevenLabs, Scribe.
- **Hablar**: voz del sistema, preferentemente las neuronales de Microsoft
  («Álvaro», «Jorge», «Pablo»…) en castellano; con clave de ElevenLabs, su
  modelo multilingüe vía `/tts`. Kokoro (del proyecto original) solo se usa si
  el idioma es inglés.
- Nombre, idioma, tratamiento y palabras de activación vienen de
  `[asistente]` en `crisvis.toml` y llegan a la cara por `/health`.

## Particularidades de Windows

- **Control de aplicaciones** (Smart App Control / WDAC) puede bloquear los
  ejecutables lanzadores que generan `uv`/`pip` (`crisvis.exe`, `pytest.exe`).
  Por eso todos los scripts usan `uv run python -m crisvis` y
  `uv run python -m pytest`.
- Conectar a un puerto cerrado de localhost tarda unos dos segundos en Windows;
  por eso la detección de motores es en paralelo.
- `%A`/`%B` de `strftime` salen en inglés: la fecha del prompt usa nombres en
  castellano propios.

## Protocolo

`packages/protocol/frames.json` es la fuente única de los tipos de trama.
`npm test` comprueba que los tipos TypeScript coinciden y `pytest` que la lista
Python también. Cambiar una trama exige tocar los tres sitios a la vez.

## Informe diario y OpenClaw

`src/crisvis/openclaw/` contiene el `OpenClawAdapter`: el informe diario
(«Buenos días», «Informe del día», botón INFORME), la política de acciones con
aprobación exacta y el cliente del Gateway de OpenClaw en loopback. Los
documentos de referencia:

- `CRISVIS_OPENCLAW_ARCHITECTURE.md`: módulos, flujos, tramas, procesos y puertos.
- `SECURITY_MODEL.md`: fronteras, niveles de riesgo, secretos y la checklist
  previa a conectar cuentas.
- `CONNECTOR_MATRIX.md`, `INTEGRATION_PLAN.md`, `RISK_REGISTER.md` y
  `OPENCLAW_AUDIT.md`.
