# Matriz de conectores

Estado a 2026-10-03. Ninguna cuenta real está conectada.

**Integración**: *oficial* = soportada por OpenClaw según su documentación;
*plugin* = plugin incluido en OpenClaw; *MCP* = servidor MCP (vía
`mcp.servers` de OpenClaw o el MCP propio de CRISVIS); *custom* = proveedor
propio de CRISVIS en `src/crisvis/openclaw/providers/`.

**Ámbitos OAuth mínimos**: son los del proveedor para **solo lectura**. Hay
que volver a verificarlos en la documentación del proveedor el día que se
conecte la cuenta. No son de OpenClaw.

| Conector | Uso en informe diario | Integración (oficial/plugin/skill/MCP/custom) | Acceso | Ámbitos OAuth mínimos | Datos consultados | Riesgo | Estado | Aprobación requerida | Pruebas necesarias |
|---|---|---|---|---|---|---|---|---|---|
| Sistema local | Pestaña Sistema: CRISVIS, Windows, RAM, disco, GPU/VRAM, Ollama, git | custom (`LocalSystemProvider`: psutil, `nvidia-smi`, `/api/tags` y `/api/ps` de Ollama, `git status`) | read-only | — (sin cuenta) | Métricas locales; nombres de modelos; rama y nº de cambios | Bajo | **real** | No (READ_ONLY) | Hecho: snapshot real, timeout de comandos, `shell=False`, sin rutas en errores |
| Docker | Pestaña Sistema: contenedores caídos | custom (`docker ps -a --format {{json .}}`) | read-only | — | Nombre, imagen, estado | Bajo | **real** (aquí `unavailable`: Docker no instalado) | No | Hecho: no instalado → `unavailable` sin romper el informe |
| OpenClaw Gateway | Pestaña Integraciones: estado del gateway | oficial (`/healthz`, `/readyz`, CLI `gateway call health\|status`) | read-only | — (token local que solo lee la CLI) | Vivo/listo, plugins cargados | Bajo | **real** (listo cuando el gateway corre) | No | Hecho: loopback obligatorio, métodos cerrados, `config.get` rechazado |
| GitHub | Pestaña Proyectos: PR, issues, CI fallida | Plugin oficial `github` = solo repos **públicos**, sin cuenta (<https://docs.openclaw.ai/plugins/github>). Repos privados: MCP oficial de GitHub o custom con API REST | read-only | Token *fine-grained*: Metadata R, Contents R, Issues R, Pull requests R, Actions R; solo repos elegidos | Títulos de PR/issue, estado de workflows | Medio (código privado) | mock | Conectar: sí. Leer: no. `github.create_issue`: HIGH | Token no aparece en logs; 401 → `unauthorized`; límite de API; repos fuera de lista bloqueados |
| GitLab | Pestaña Proyectos: pipelines, MR | Sin integración oficial en la documentación de OpenClaw (NO CONFIRMADO). MCP de GitLab o custom con API REST | read-only | `read_api` (token personal o de proyecto, con caducidad) | Pipelines, MR, issues | Medio | mock | Conectar: sí. `gitlab.create_issue`: HIGH | Igual que GitHub; instancia self-hosted solo por HTTPS |
| Correo (Gmail) | Pestaña Correo: no leídos prioritarios | Oficial = Gmail Pub/Sub + `gog gmail watch serve` + **endpoint HTTPS público** (Tailscale Funnel) (<https://docs.openclaw.ai/automation/cron-jobs/gmail>). **Incompatible** con «sin túneles públicos». Alternativa: MCP o custom con Gmail API por *polling* | read-only | `gmail.metadata` (cabeceras, sin cuerpo) o `gmail.readonly` si hace falta el texto | Remitente, asunto, fecha, etiquetas, no leído | Alto (datos personales) | mock | Conectar: sí. `correo.create_draft`: MEDIUM. `correo.send`: HIGH. `correo.delete`/`bulk_send`: CRITICAL (bloqueado) | Sin cuerpo por defecto; redacción; nunca enviar; prompt injection en asuntos |
| Correo (IMAP) | Igual | Plugin oficial IMAP (<https://docs.openclaw.ai/automation/imap>): sin webhook público, pero **disparado por entrada** (no consulta) y requiere sandbox (Docker por defecto) + agente lector restringido | read-only | Contraseña de aplicación IMAP (sin OAuth); idealmente cuenta o carpeta dedicada | Igual | Alto | no configurado | Igual | Requiere Docker; revisar que el agente lector no tenga herramientas |
| Calendario (Google) | Pestaña Agenda: eventos de hoy, conflictos | Sin integración oficial (NO CONFIRMADO; solo existe el plugin Google **Meet**). MCP o custom con Calendar API | read-only | `calendar.events.readonly` | Título, hora, asistentes (sin descripción por defecto) | Medio | mock | Conectar: sí. `agenda.create_event`/`update_event`: HIGH | Zona America/Lima; eventos de día completo; 401 |
| Drive / documentos | Pestaña Documentos: recientes, pendientes de firma | Sin integración oficial (NO CONFIRMADO). MCP o custom con Drive API | read-only | `drive.metadata.readonly` (sin contenido) | Nombre, fecha, propietario | Medio | mock | Conectar: sí. `documentos.delete`: CRITICAL | Nunca descargar contenido por defecto |
| Tareas | Pestaña Tareas: vencidas y de hoy | Por decidir (Notion, Google Tasks, Todoist…) vía MCP o custom | read-only | Según servicio, solo lectura | Título, vencimiento, prioridad | Bajo | mock | Conectar: sí | Fechas en zona Lima |
| n8n | Pestaña Automatizaciones: ejecuciones fallidas | Sin integración oficial (NO CONFIRMADO). Custom con la API REST de n8n | read-only | API key de n8n (en la edición community no tiene ámbitos: usar instancia o usuario de solo lectura si existe) | Nombre del flujo, estado, hora | Medio (la key permite lanzar flujos) | mock (timeout simulado a propósito) | `n8n.dry_run`: MEDIUM. `n8n.trigger_workflow`: HIGH | La key no aparece en logs; timeout; nunca `activate`/`execute` |
| Botwoot / CRM (Chatwoot) | Pestaña Negocio: conversaciones sin responder, SLA | Sin integración oficial (NO CONFIRMADO). Custom con la API de Chatwoot | read-only | Token de acceso de un **agente** (Chatwoot no tiene ámbitos finos); nunca de administrador | Conversaciones abiertas, espera, etiqueta | Alto (datos de clientes) | mock | `crm.update_contact`: HIGH | Redacción de datos de clientes; 401 |
| Mensajería (WhatsApp/Telegram) | Solo borradores sugeridos | Canales oficiales de OpenClaw (<https://docs.openclaw.ai/channels/whatsapp>, `/channels/telegram`) | draft | Token de bot / emparejamiento; número separado | — | Alto (envío automático) | no configurado | `mensajeria.send`: HIGH, siempre | Nunca autoenvío; `dmPolicy: pairing` |
| Sandbox local | Guardar el informe | custom (`<home>/sandbox`) | draft | — | — | Bajo | no configurado (LOW deshabilitado) | LOW: solo si `permitir_bajo = true` | Ruta confinada al sandbox |

## Notas

- **Gmail oficial no se usa**: necesita un endpoint HTTPS público. Viola la
  regla de no exponer nada a Internet.
- **Ningún conector real se conecta sin aprobación explícita**, uno a uno, en
  el orden de `INTEGRATION_PLAN.md` y tras la checklist de `SECURITY_MODEL.md`.
- Los proveedores mock se marcan en la UI con «DATOS DE PRUEBA» y el resumen
  hablado lo dice en voz alta. Nunca se presentan como datos reales.
