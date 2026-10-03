# Operación local de CRISVIS

Guía de uso diario tras la Fase 3.5. Todo corre en este equipo y solo escucha
en loopback.

## Procesos y puertos

| Proceso | Dirección | Arranque | Notas |
|---|---|---|---|
| Núcleo CRISVIS (FastAPI + interfaz) | `127.0.0.1:8787` | `npm start` | Abre el navegador salvo `--no-browser` |
| Voz clonada XTTS-v2 («Adam», `adam.wav`) | `127.0.0.1:8788` | La arranca el núcleo si está instalada | `/health` debe decir `ttsEngine: clonada` |
| Ollama (qwen3:8b) | `127.0.0.1:11434` | Servicio de Ollama | — |
| OpenClaw Gateway | `127.0.0.1:18789` | **Manual** (ver abajo) | Sin autoarranque, canales, skills, hooks ni webhooks |
| Vite (solo desarrollo) | `localhost:5173` | `npm run dev` | Pone `CRISVIS_DEV=1` en el núcleo |

Ninguno debe aparecer en `0.0.0.0` ni en `::`. Comprobación:

```powershell
Get-NetTCPConnection -State Listen |
  Where-Object LocalPort -in 8787,8788,11434,18789 |
  Select-Object LocalAddress, LocalPort, OwningProcess
```

## Arrancar

```powershell
cd C:\Users\USUARIO\Desktop\CRISVIS
npm run build      # solo si cambió la interfaz
npm start
```

Al arrancar, el núcleo:

1. Arranca en CONFIRMAR, o en el modo guardado en `<home>/permisos.json`
   (LECTURA o CONFIRMAR). LIBRE nunca se restaura.
2. Escribe un token de administración nuevo en `<home>/admin.token`.
3. Genera un secreto de arranque nuevo: las pestañas abiertas antes del
   reinicio piden recargar la página.

`<home>` es `%USERPROFILE%\.crisvis` salvo que `CRISVIS_HOME` diga otra cosa.

Comprobación rápida (sin token, `/health` es público y de solo lectura):

```powershell
Invoke-RestMethod http://127.0.0.1:8787/health
```

## Modo de permisos

| Modo | Qué hace | Cómo se activa |
|---|---|---|
| LECTURA | Solo consultas | Botón «Solo lectura» de la interfaz |
| CONFIRMAR (por defecto) | Cada acción con efecto pide aprobación individual | Botón «Confirmar» o `crisvis permisos confirmar` |
| LIBRE | Escribe archivos y abre aplicaciones permitidas sin preguntar. Teclado, ratón, portapapeles, órdenes y sistema **siguen preguntando**; lo bloqueado sigue bloqueado | Solo desde una terminal: `crisvis permisos libre` |

```powershell
# Ver el modo guardado
uv run --no-sync python -m crisvis permisos estado

# Habilitar LIBRE temporalmente (pide escribir «ACTIVAR LIBRE»; máximo 30 min)
uv run --no-sync python -m crisvis permisos libre --minutos 10

# LIBRE sin caducidad: hasta `permisos confirmar` o reiniciar el núcleo
uv run --no-sync python -m crisvis permisos libre --minutos 0

# Volver a CONFIRMAR ya
uv run --no-sync python -m crisvis permisos confirmar
```

LIBRE caduca solo (salvo `--minutos 0`), vuelve a CONFIRMAR al reiniciar y queda en
`<home>/auditoria.jsonl` (`libre_habilitado`, `libre_caducado`,
`libre_deshabilitado`). Cada cambio de modo cierra las conexiones y anula las
aprobaciones pendientes; la interfaz se reconecta sola.

## Archivos de seguridad en `<home>`

| Archivo | Contenido | Se puede borrar |
|---|---|---|
| `admin.token` | Token de administración del arranque actual | Sí, con el núcleo parado |
| `permisos.json` | Último modo persistente elegido | Sí: se vuelve al de `crisvis.toml` |
| `auditoria.jsonl` | Decisiones y ciclo de vida de las acciones | No borrar sin motivo: es la evidencia |
| `auditoria-adaptador.jsonl` | Informes y acciones del adaptador de OpenClaw | Ídem |
| `telemetria-entradas.jsonl` | Una línea por entrada: fuente, sesión, HMAC, aceptada (sin texto) | Sí |
| `telemetria.key` | Clave del HMAC de la telemetría | Sí (las huellas antiguas dejan de ser comparables) |

Ninguno debe copiarse al repositorio, a capturas ni a documentos.

## Configuración relevante (`crisvis.toml`, fuera de git)

```toml
[permisos]
modo = "confirmar"          # "libre" aquí se ignora y se arranca en CONFIRMAR

[voz]
elevenlabs_habilitado = false   # ElevenLabs no se usa aunque haya clave

[seguridad]
portapapeles = "confirmar"      # o "deshabilitado"
libre_max_minutos = 30
sesion_minutos = 15
apps_permitidas = []            # rutas exactas de programas que pc_open puede abrir
tts_por_minuto = 40
stt_por_minuto = 30
max_conexiones = 6
csp_kokoro = false
```

## Desarrollo

`npm run dev` arranca Vite y el núcleo con `CRISVIS_DEV=1`. En ese modo no hay
cookie de arranque y se acepta el Origin de Vite en localhost. **No usar
`CRISVIS_DEV=1` fuera del desarrollo.**

## Instancias de prueba

- Se arrancan con un `CRISVIS_HOME` y un puerto propios (por ejemplo `8790`) y
  **se cierran al terminar**. No deben quedar encendidas sin uso.
- Antes de arrancar la instancia normal, limpiar el entorno de la terminal:

```powershell
Remove-Item Env:CRISVIS_HOME, Env:CRISVIS_VOZ_DIR, Env:CRISVIS_CONFIG, `
  Env:OPENJARVIS_HOME, Env:CRISVIS_PORT, Env:CRISVIS_HOST, Env:CRISVIS_PERMISOS `
  -ErrorAction SilentlyContinue
```

- Cerrar una instancia por puerto:

```powershell
$p = (Get-NetTCPConnection -LocalPort 8790 -State Listen -ErrorAction SilentlyContinue).OwningProcess
if ($p) { Stop-Process -Id $p }
```

## OpenClaw (manual)

```powershell
openclaw gateway run --port 18789 --bind loopback    # arrancar (en su propia terminal)
openclaw gateway status --json --require-rpc         # estado
openclaw security audit --deep                       # auditoría oficial
```

Para pararlo: Ctrl+C en su terminal. No hay servicio ni Tarea programada. No
se arranca con Windows (decisión vigente). El informe del día funciona sin él.

## Pruebas

```powershell
npm test                                    # protocolo + interfaz + pytest
npm run typecheck
npm run lint
uv run --no-sync python -m pytest -q tests -k hardening   # solo las de seguridad
```

## Investigar una entrada sospechosa

Ver `INCIDENT_COMO_ANALYSIS.md` §9.
