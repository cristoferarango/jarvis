# Auditoría de OpenClaw para CRISVIS

Fecha: 2026-10-03. Versión auditada e instalada: **OpenClaw 2026.9.8 (fc23bc8)**.

Cada dato va marcado como **CONFIRMADO** (leído en la documentación oficial, en
el paquete publicado o comprobado en esta máquina) o **NO CONFIRMADO** (no
encontrado en fuentes oficiales; no se usa en el código).

Fuentes oficiales usadas:

- Documentación: <https://docs.openclaw.ai/> e índice completo
  <https://docs.openclaw.ai/llms.txt>
- Repositorio: <https://github.com/openclaw/openclaw>
- Paquete npm `openclaw@2026.9.8` (descargado con `npm pack` y revisado antes
  de instalar)

## 1. Qué es

| Dato | Estado | Fuente |
|---|---|---|
| Gateway autoalojado que une canales de chat (WhatsApp, Telegram, Slack, Discord…) con agentes de IA; un proceso en tu máquina | CONFIRMADO | llms.txt (cabecera), <https://docs.openclaw.ai/help/faq/what-is-openclaw> |
| Licencia MIT | CONFIRMADO | `npm view openclaw license` |
| Modelo de confianza: «asistente personal, un operador de confianza», no multiusuario hostil | CONFIRMADO | `openclaw security audit` (summary.attack_surface), <https://docs.openclaw.ai/gateway/security/trust-model> |
| Requiere Node `>=24.16.0 <25 \|\| >=26.1.0` | CONFIRMADO | `npm view openclaw engines`; aquí Node 24.21.0 |

## 2. Windows

| Dato | Estado | Fuente |
|---|---|---|
| Tres vías en Windows: Windows Hub (instalador firmado), CLI por PowerShell/npm, o Gateway en WSL2 | CONFIRMADO | <https://docs.openclaw.ai/install>, <https://docs.openclaw.ai/platforms/windows> |
| El propio onboarding avisa: «Native Windows might be trickier» y recomienda WSL2 | CONFIRMADO | salida de `openclaw onboard` en esta máquina |
| Con `--install-daemon` crea una Tarea programada «OpenClaw Gateway» (o un elemento de Inicio si se deniega) | CONFIRMADO | <https://docs.openclaw.ai/cli/onboard>, <https://docs.openclaw.ai/install/uninstall> |
| npm 11.16+ acepta `--allow-scripts=openclaw` para los scripts del paquete | CONFIRMADO | <https://docs.openclaw.ai/install> |
| Funciona con el Control inteligente de aplicaciones activo (modo aplicar) | CONFIRMADO en esta máquina | La CLI y el Gateway arrancan; ver §8 |

Elegido: **npm global con versión fijada** (`npm install -g openclaw@2026.9.8
--allow-scripts=openclaw`). No se usó `iwr … | iex` (ejecuta un script remoto
sin revisar) ni el instalador de Windows Hub (añade bandeja, modo nodo y MCP
local que no necesitamos).

## 3. Gateway, puertos y API

| Dato | Estado | Fuente |
|---|---|---|
| WebSocket + HTTP multiplexados en un puerto, por defecto `18789` | CONFIRMADO | <https://docs.openclaw.ai/gateway/security/network-exposure> |
| `gateway.bind`: `loopback` (por defecto) \| `lan` \| `tailnet` \| `auto` \| `custom` | CONFIRMADO | idem + `openclaw onboard --help` |
| Autenticación obligatoria por defecto (fail-closed); onboarding genera un token aunque sea loopback | CONFIRMADO | idem |
| Sondas HTTP sin autenticación: `/healthz` (vivo) y `/readyz` (listo) | CONFIRMADO | <https://docs.openclaw.ai/gateway/health>; probado: 200 |
| `openclaw gateway status --json --require-rpc` | CONFIRMADO | <https://docs.openclaw.ai/cli/gateway/query>; probado |
| `openclaw gateway call <método> --params <json> --port N --json --timeout ms` | CONFIRMADO | idem; probado con `health` |
| `openclaw health` (instantánea por RPC) | CONFIRMADO | <https://docs.openclaw.ai/cli/health> |
| El token HTTP bearer da acceso de operador completo (`operator.admin`, `operator.write`…) | CONFIRMADO | network-exposure, «Gateway HTTP bearer auth is effectively all-or-nothing» |
| mDNS/Bonjour (`_openclaw-gw._tcp`, UDP 5353) desactivable con `discovery.mdns.mode: "off"` o `OPENCLAW_DISABLE_BONJOUR=1` | CONFIRMADO | network-exposure |
| API REST pública estable para «ejecutar herramienta X» desde fuera | NO CONFIRMADO | Existe `/tools/invoke` pero exige el token de operador completo; **no se usa** |

Decisión: el adaptador **no** usa el token ni la API HTTP autenticada. Usa las
sondas `/healthz` y `/readyz` y la CLI oficial con una lista cerrada de métodos
(`health`, `status`). La CLI lee su propio token de `~/.openclaw/openclaw.json`,
así que CRISVIS nunca lo ve.

## 4. Herramientas, política y sandbox

| Dato | Estado | Fuente |
|---|---|---|
| `tools.profile`: `minimal` \| `coding` \| `messaging` \| `full`; el onboarding local pone `full` | CONFIRMADO | <https://docs.openclaw.ai/gateway/config-tools/tool-policy>; visto en la config generada |
| `minimal` = `session_status` + `gateway` (solo update) | CONFIRMADO | idem |
| `tools.deny` gana sobre `allow`; grupos `group:runtime`, `group:fs`, `group:automation`, `group:nodes`, `group:ui`, `group:web`, `group:messaging`, `group:plugins` | CONFIRMADO | idem |
| Configuración endurecida oficial (loopback + token + deny de runtime/fs/automation, exec deny, elevated off) | CONFIRMADO | <https://docs.openclaw.ai/gateway/security/hardened-baseline> |
| Sandbox por Docker/Podman/SSH/OpenShell; no hay sandbox nativo de Windows | CONFIRMADO | <https://docs.openclaw.ai/gateway/sandboxing> |
| Los modelos pequeños (≤300B) no se recomiendan con entradas no confiables | CONFIRMADO | hallazgo `models.small_params` de la auditoría |

## 5. Skills, plugins, ClawHub y MCP

| Dato | Estado | Fuente |
|---|---|---|
| Skills y plugins de terceros se tratan como código de confianza; no hay bloqueo local de código peligroso | CONFIRMADO | <https://docs.openclaw.ai/help/faq/security-and-access-control> |
| Política de instalación y consentimiento de capacidades al instalar plugins | CONFIRMADO | <https://docs.openclaw.ai/cli/plugins/install> |
| ClawHub publica auditorías de seguridad orientativas antes de instalar | CONFIRMADO | <https://docs.openclaw.ai/clawhub/security-audits> |
| MCP: `mcp.servers`, `openclaw mcp …`, OAuth MCP; las herramientas MCP pasan por la política (`bundle-mcp`) | CONFIRMADO | <https://docs.openclaw.ai/cli/mcp>, <https://docs.openclaw.ai/cli/mcp/transports> |
| 14 plugins incluidos se cargan por defecto (anthropic, browser, canvas, cua-computer, device-pair, file-transfer, geolocation, github, linux-node, memory-core, ollama, openai, talk-voice, xai) | CONFIRMADO | log de arranque del Gateway |

**No se ha instalado ningún skill ni plugin de terceros.** Se desactivaron 7
plugins incluidos que CRISVIS no necesita (ver §7).

## 6. Memoria, cron y tareas de fondo

| Dato | Estado | Fuente |
|---|---|---|
| Heartbeat: turno del agente cada 30 min por defecto; `agents.defaults.heartbeat.every: "0m"` lo apaga | CONFIRMADO | <https://docs.openclaw.ai/gateway/heartbeat> |
| Dreaming (memory-core): consolidación nocturna con el modelo, `0 3 * * *`, activo por defecto | CONFIRMADO | <https://docs.openclaw.ai/concepts/dreaming>; el log mostró «created managed dreaming cron job» |
| `cron.enabled: false` apaga el programador | CONFIRMADO | idem |

Las tres cosas se desactivaron: no queremos que OpenClaw consuma la GPU por su
cuenta ni que actúe sin una petición de CRISVIS.

## 7. Configuración aplicada (`~/.openclaw/openclaw.json`)

Onboarding no interactivo (opciones verificadas en `openclaw onboard --help`):

```
openclaw onboard --non-interactive --accept-risk --mode local
  --auth-choice ollama --custom-base-url http://127.0.0.1:11434 --custom-model-id qwen3:8b
  --gateway-port 18789 --gateway-bind loopback --gateway-auth token
  --skip-daemon --skip-channels --skip-skills --skip-hooks --skip-search --skip-ui
  --skip-health --skip-bootstrap --suppress-gateway-token-output
```

Endurecimiento aplicado con `openclaw config patch` (con `--dry-run` antes y
`openclaw config validate` después):

- `tools.profile: "minimal"`, `tools.deny` con todos los grupos de acción.
- `tools.exec: {security: "deny", ask: "always"}`, `tools.elevated.enabled: false`,
  `tools.fs.workspaceOnly: true`, `tools.agentToAgent.enabled: false`,
  `tools.sessions.visibility: "agent"`, `session.dmScope: "per-channel-peer"`.
- `discovery.mdns.mode: "off"`.
- `cron.enabled: false`, `agents.defaults.heartbeat.every: "0m"`, dreaming apagado.
- Plugins desactivados: browser, canvas, cua-computer, file-transfer,
  geolocation, linux-node, talk-voice.
- Sin canales, sin hooks, sin webhooks, sin Tailscale (`tailscale.mode: "off"`),
  sin servicio de arranque automático.

## 8. Verificación en esta máquina

| Prueba | Resultado |
|---|---|
| `openclaw --version` | `OpenClaw 2026.9.8 (fc23bc8)` |
| Puertos en escucha del Gateway | solo `127.0.0.1:18789` (TCP); ningún UDP |
| `GET /healthz` | 200 `{"ok":true,"status":"live"}` |
| `GET /readyz` | 200 `{"ready":true,"failing":[]}` |
| `openclaw gateway call health --json` | `ok: true`; 7 plugins cargados tras el endurecimiento |
| `openclaw gateway status --json --require-rpc` | `rpc.ok: true` |
| Adaptador CRISVIS (`GatewayClient.status`) | `state: "listo"`, `installed: true` |
| Adaptador pide `config.get` | rechazado por la lista cerrada del adaptador |
| `openclaw security audit --deep --json` | **0 críticos**, 2 avisos, 2 informativos (detalle abajo) |
| `openclaw secrets audit --check` | 1 hallazgo: token del Gateway en texto plano (riesgo R-15) |
| Control inteligente de aplicaciones | activo (estado 1); no bloqueó node, esbuild ni koffi |

Hallazgos de la auditoría oficial, aceptados:

- `gateway.trusted_proxies_missing` (aviso): solo aplica con proxy inverso. No
  hay ninguno; la Control UI queda solo local.
- `gateway.probe_failed: missing scope: operator.read` (aviso): la sonda
  profunda de la auditoría no tiene permiso de lectura de configuración. El
  adaptador no lo necesita (usa `health`/`status`, que sí funcionan). No se
  amplían permisos.
- `models.small_params` (info): qwen3:8b es pequeño. Mitigado: sin web, sin
  navegador, sin herramientas de acción y sin entradas externas.
- `summary.attack_surface` (info): elevated, webhooks, hooks y browser control
  deshabilitados.

## 9. Revisión del paquete antes de instalar

- `preinstall`: comprueba la versión de Node y borra un marcador dentro del
  propio paquete. Sin red.
- `postinstall`: poda archivos obsoletos de `dist/` dentro del paquete. Si
  falta el binario precompilado de `@openclaw/fs-safe`, lo descarga del registro
  npm con `npm` (mismo origen que el resto).
- Dependencias nativas: `koffi`, `esbuild`, `@lydell/node-pty`,
  `@trycua/cua-driver`, `sqlite-vec` (opcional). npm 11.19 avisó de que los
  scripts de `@google/genai`, `esbuild`, `koffi` y `protobufjs` no estaban en
  `allowScripts`. En npm 11.x solo avisa y los ejecuta igual (documentado en
  la página de instalación).
- 343 paquetes en `%APPDATA%\npm\node_modules\openclaw`. Nada dentro del repo.

## 10. Reauditoría tras el hardening de la Fase 3.5

Fecha: 2026-10-03. Política de OpenClaw **sin cambios** (no se modificó
ninguna configuración). No se leyó el token.

| Prueba | Resultado |
|---|---|
| Puertos del proceso del Gateway | solo `127.0.0.1:18789` (TCP); ningún UDP; nada en `0.0.0.0` ni `::` |
| `openclaw security audit --deep --json` | **0 críticos**, 2 avisos, 2 informativos: los mismos hallazgos aceptados del §8 |
| `config get gateway.bind` / `gateway.port` | `loopback` / `18789` |
| `config get gateway.tailscale.mode` / `discovery.mdns.mode` | `off` / `off` |
| `config get channels` | sin definir: ningún canal |
| `config get hooks` | sin definir; la auditoría confirma `hooks.webhooks: disabled` y `hooks.internal: disabled` |
| `config get skills` | sin definir: ninguna skill instalada |
| `config get cron.enabled` / `agents.defaults.heartbeat.every` | `false` / `0m` |
| Autoarranque | ninguna Tarea programada, ningún servicio, nada en `HKCU\...\Run` ni en la carpeta Inicio |
| Acceso desde la cara | imposible: `connect-src` de la CSP no incluye el puerto 18789 y la cara solo habla con el núcleo (token + ticket) |

Cambios de CRISVIS que afectan a la integración: las lecturas
`/api/openclaw/*` exigen ahora el token de sesión de la interfaz, y las
tramas `briefing` y `propose` solo llegan por un WebSocket abierto con
ticket. El adaptador no cambió su lista cerrada de métodos (`health`,
`status`).
