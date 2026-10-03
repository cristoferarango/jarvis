# Plan de integración

## Estado por fase

| Fase | Contenido | Estado |
|---|---|---|
| 0 | Auditoría y documentos (`OPENCLAW_AUDIT`, arquitectura, matriz, seguridad, este plan, riesgos) | Hecho |
| 1 | `OpenClawAdapter`: contratos tipados, esquemas, proveedores mock, auditoría redactada, `DRY_RUN` global, UI de conectores y aprobaciones | Hecho |
| 2 | Flujo `daily-briefing`: disparadores, botón INFORME, 10 pestañas, alertas, resumen hablado, timeouts, cancelación | Hecho |
| 3 | OpenClaw 2026.9.8 instalado, solo loopback, token local, endurecido, auditoría oficial, health/status sin conectores | Hecho |
| 4.1 | Estado local (sistema, Ollama, git, Docker) en modo real de solo lectura | Hecho |
| **3.5** | **Hardening obligatorio**: CONFIRMAR persistente y LIBRE solo por administración, riesgo por acción y secuencia con aprobación con nonce, portapapeles protegido, cancelación real, autenticación HTTP y WebSocket, CSP de producción, trazabilidad de entradas (ver `HARDENING_REPORT.md`) | Hecho, sin commit; **espera tu aprobación** |
| 4.2 | GitLab / GitHub (lectura) | **Pausada** hasta aprobar la Fase 3.5. Requiere tu aprobación y tu cuenta |
| 4.3 | Correo (lectura, sin cuerpo) | Pendiente: aprobación + cuenta |
| 4.4 | Calendario (lectura) | Pendiente: aprobación + cuenta |
| 4.5 | Drive (metadatos) | Pendiente: aprobación + cuenta |
| 4.6 | n8n / Botwoot / CRM (lectura) | Pendiente: aprobación + credenciales |
| 4.7 | Borradores de mensajería (sin envío) | Pendiente: aprobación |
| 4.8 | Escrituras en sandbox (con `DRY_RUN` desactivado solo para ese conector) | Pendiente: aprobación explícita por conector |

## Cómo conectar un conector (Fase 4, cada paso)

1. Pasar la checklist de `SECURITY_MODEL.md` (incluye comprobar que el
   hardening de la Fase 3.5 sigue en vigor).
2. Elegir la integración de `CONNECTOR_MATRIX.md` (oficial > MCP revisado > custom).
3. Escribir el proveedor real en `src/crisvis/openclaw/providers/` con la
   misma interfaz `Provider` (`mode = ConnectorMode.REAL`, solo lectura).
4. Pruebas: éxito, 401, timeout, no disponible, redacción del secreto.
5. Registrar el proveedor en `adapter.default_providers` detrás de una opción
   de configuración (desactivada por defecto).
6. Comprobar en la UI (Integraciones: modo «Real») y en la auditoría.
7. Pedir tu visto bueno antes de pasar al siguiente.

## Puesta en marcha diaria

```powershell
# OpenClaw (opcional para el informe; solo loopback)
openclaw gateway run --port 18789 --bind loopback

# CRISVIS
npm start
```

Comprobación rápida:

```powershell
openclaw gateway status --json --require-rpc
Invoke-WebRequest http://127.0.0.1:18789/readyz -UseBasicParsing
Invoke-RestMethod http://127.0.0.1:8787/health
```

Desde la Fase 3.5, `/api/openclaw/estado` exige el token de sesión de la
interfaz: se consulta desde la pestaña Integraciones, no con
`Invoke-WebRequest`. Guía completa en `LOCAL_OPERATIONS.md`.

## Vuelta atrás (rollback)

De menor a mayor alcance:

1. **Desactivar el adaptador sin tocar nada más:** en `crisvis.toml`,
   `[openclaw] habilitado = false`. CRISVIS funciona como antes (el informe se
   desactiva).
2. **Parar OpenClaw:** cerrar el proceso de `openclaw gateway run` (Ctrl+C en
   su terminal). No hay servicio ni Tarea programada que lo relance.
3. **Deshacer el endurecimiento o la configuración:** OpenClaw guarda
   `~/.openclaw/openclaw.json.bak` al reescribir la configuración. Los dos
   parches aplicados están descritos en `OPENCLAW_AUDIT.md` §7. Tras restaurar,
   ejecutar `openclaw config validate`.
4. **Desinstalar OpenClaw** (<https://docs.openclaw.ai/install/uninstall>):

   ```powershell
   openclaw uninstall --dry-run --all      # vista previa
   openclaw uninstall --all --yes --non-interactive
   npm rm -g openclaw
   Get-Command openclaw -ErrorAction SilentlyContinue   # debe no devolver nada
   ```

   `--all` borra `~/.openclaw` (config, token, workspace, sesiones). Si
   quedara una Tarea programada: `schtasks /Delete /F /TN "OpenClaw Gateway"`.
5. **Revertir el código de CRISVIS:** todo el trabajo está sin commit. Archivos
   nuevos: `src/crisvis/openclaw/`, `tests/test_openclaw_*.py`,
   `apps/face/src/lib/briefing*.ts`, `apps/face/src/ui/Briefing.tsx`,
   `apps/face/src/ui/Approval.tsx`, `apps/face/test/`, `docs/*.md` nuevos.
   Modificados: `settings.py`, `app.py`, `gateway/{session,ws,protocol}.py`,
   `packages/protocol/{frames.json,src/index.ts}`, `apps/face/src/{App.tsx,
   index.css,lib/brain.ts}`, `package.json`, `apps/face/package.json`.
   `git diff` / `git checkout -- <archivo>` sobre cada uno, **con tu
   autorización**.
6. **Revertir el hardening de la Fase 3.5** (no recomendado; reabre R-13,
   R-14, R-16, R-17 y R-27 a R-31). Revertir núcleo e interfaz **a la vez** y
   recompilar con `npm run build`, porque el protocolo pasó a la versión 2.
   - Archivos nuevos: `src/crisvis/execution.py`, `src/crisvis/body/shell.py`,
     `src/crisvis/gateway/frames.py`,
     `src/crisvis/security/{auth,grants,guard,headers,modes,ratelimit,telemetry}.py`,
     `apps/face/src/lib/{auth,guardrails}.ts`, `apps/face/test/guardrails.test.ts`,
     `tests/{app_helpers,gate_helpers}.py`, `tests/test_hardening_*.py` y los
     documentos `HARDENING_REPORT`, `THREAT_MODEL`, `LOCAL_OPERATIONS`,
     `INCIDENT_COMO_ANALYSIS` y `SECURITY_TEST_EVIDENCE`.
   - Modificados: `settings.py`, `app.py`, `cli.py`, `body/{__init__,base,desktop,pc}.py`,
     `brain/{agent,openjarvis_brain}.py`, `gateway/{protocol,session,ws}.py`,
     `security/{audit,policy}.py`, `voice/speech.py`,
     `packages/protocol/{frames.json,src/index.ts}`, `scripts/dev.mjs`,
     `apps/face/{index.html,vite.config.ts,package.json}`,
     `apps/face/src/{App.tsx,store.ts,index.css}`,
     `apps/face/src/lib/{brain,confirm,tts,voice}.ts`,
     `apps/face/src/ui/{Confirm,Console}.tsx`, `tests/test_pc.py`.
   - Datos en `<home>` que se pueden borrar: `permisos.json`, `admin.token`,
     `telemetria-entradas.jsonl`, `telemetria.key`.
   - Rollbacks parciales sin tocar código: `crisvis permisos confirmar`,
     `[seguridad] portapapeles = "deshabilitado"`,
     `[voz] elevenlabs_habilitado = true`, `[seguridad] csp_kokoro = true`.
   Detalle por corrección en `HARDENING_REPORT.md`.

## Dependencias nuevas

- CRISVIS: **ninguna** (httpx, pydantic y psutil ya estaban en `pyproject.toml`).
- Sistema: OpenClaw 2026.9.8 global en `%APPDATA%\npm\node_modules\openclaw`
  (343 paquetes npm). Estado en `~/.openclaw`. Logs en
  `%TEMP%\openclaw\openclaw-AAAA-MM-DD.log`.
