# Modelo de seguridad CRISVIS + OpenClaw

## Fronteras de confianza

| Zona | Confianza | Qué puede hacer |
|---|---|---|
| Usuario frente al equipo | Total | Aprobar o denegar acciones con el botón Aprobar |
| Núcleo CRISVIS (Python) | Alta | Única pieza que habla con OpenClaw y con los proveedores |
| Cara (navegador en 127.0.0.1:8787) | Media | Pide informes y propone acciones con token de sesión y ticket de WebSocket; no ejecuta nada, no ve secretos, no decide modo, riesgo ni aprobaciones |
| CLI de administración (terminal del usuario) | Alta | Única vía para habilitar LIBRE (con `admin.token` y frase) |
| Modelo (qwen3:8b) | Baja | No participa en el informe; no tiene ruta hacia OpenClaw ni hacia credenciales |
| Contenido externo (correos, issues, mensajes) | Nula | Solo se muestra; se redacta; nunca se interpreta como orden |
| OpenClaw Gateway | Media | Proceso local en loopback, herramientas denegadas; solo se le consulta salud y estado |

## Reglas que el código hace cumplir

1. **Solo loopback.** `require_loopback()` rechaza cualquier `gateway_url` que
   no sea 127.0.0.1, localhost o ::1. Si la config apunta fuera, el adaptador
   desactiva el gateway y lo explica en Integraciones. OpenClaw está con
   `gateway.bind: "loopback"`, mDNS apagado y Tailscale apagado.
2. **Sin acceso directo de la UI.** La cara solo habla con `/ws` y
   `/api/openclaw/*` (lectura) del núcleo. Hay un middleware de origen (403
   para orígenes ajenos) y OpenClaw exige un token que la cara no tiene.
3. **Sin shell arbitrario.** No hay ejecutor genérico. Los únicos procesos que
   lanza el adaptador son argv fijos (`nvidia-smi`, `git status`, `docker ps`,
   `node openclaw.mjs gateway call health|status`), con `shell=False`, timeout y
   ventana oculta. Los shims `.cmd` de npm se evitan para no pasar por `cmd.exe`.
4. **Sin prompts crudos a OpenClaw.** El adaptador no tiene ningún método que
   envíe texto libre al agente de OpenClaw.
5. **Registro cerrado + política.** Toda acción es una `ProposedAction`
   validada contra `tools.py`. El riesgo lo fija el registro: quien propone
   puede subirlo, nunca bajarlo.
6. **DRY_RUN global.** `dry_run = true` por defecto; `DRY_RUN=true` en el
   entorno solo puede activarlo. Con DRY_RUN, o si el conector no es `real`,
   la ejecución es siempre `simulated`.
7. **Aprobación exacta.** Cada aprobación vale para una sola acción. Está
   ligada a la huella sha256 de herramienta, servicio, cuenta, destino,
   parámetros y contenido; caduca (120 s) y se consume al usarse. Solo se
   aprueba con el botón (ni Intro ni voz); Esc deniega.
8. **Redacción.** Auditoría, errores y salidas de la CLI pasan por `redact()`:
   Bearer/Basic, `ghp_`, `github_pat_`, `glpat-`, `sk-`, `xox*`, `AIza`, `ya29.`,
   JWT, `token=`/`password:` y claves privadas. Las rutas se ocultan en los
   errores (`safe_error`).
9. **Auditoría.** Cada informe, fuente, decisión de política y acción queda en
   `<home>/auditoria-adaptador.jsonl`, con decisión, resultado y `dry_run`.
10. **Límites.** Máximo de informes por minuto; timeouts por fuente y global;
    cancelación con Stop.

### Reglas añadidas en la Fase 3.5 (hardening)

Detalle, pruebas y rollback de cada una en `HARDENING_REPORT.md`.

11. **Modo de permisos en el núcleo.** CONFIRMAR por defecto, persistente en
    `<home>/permisos.json`. LIBRE no persiste (al reiniciar vuelve a
    CONFIRMAR) y solo lo habilita `crisvis permisos libre` con `admin.token` y
    la frase «ACTIVAR LIBRE»: con `--minutos N` caduca (≤ 30 min); con
    `--minutos 0`, por decisión del usuario, dura hasta
    `crisvis permisos confirmar` o reiniciar. La interfaz solo puede bajar de modo. Cada cambio cierra las
    conexiones y anula las aprobaciones pendientes.
12. **Riesgo por acción y por secuencia** (`security/guard.py`). Shells,
    atajos de ejecución (Win+R, Ctrl+Shift+Esc), órdenes escritas o pegadas,
    ejecutables fuera de `apps_permitidas` e Intro tras texto con forma de
    orden son CRITICAL: se bloquean sin preguntar, también en LIBRE.
13. **Aprobación individual con nonce** para cada acción MEDIUM o HIGH del
    control del PC: un solo uso, 60 s, ligada a parámetros, turno y ventana.
    Un «sí» por voz no aprueba nada de riesgo alto.
14. **Portapapeles.** Solo `read_clipboard`, HIGH, aprobación cada vez,
    enmascarado, nunca en logs; `portapapeles = "deshabilitado"` lo retira.
15. **Cancelación real.** `shell_exec` corre en un job object; Stop mata el
    árbol de procesos. Ciclo completo auditado con id de correlación. Lo no
    cancelable se avisa antes de aprobar.
16. **Autenticación local.** Cookie de arranque HttpOnly/SameSite=Strict →
    token de sesión de 15 min ligado al Origin → ticket de WebSocket de un
    solo uso (30 s). Host de loopback obligatorio (421), Origin ajeno 403,
    límites por minuto (429).
17. **WebSocket estricto.** Esquema por tipo de trama; el cliente no puede
    fijar modo, riesgo, aprobación ni identidad; `permissions` rechazada y
    auditada; límites de tramas, `ask` y conexiones.
18. **CSP de producción** por respuesta con nonce, sin `unsafe-inline` ni
    `unsafe-eval` en scripts, `connect-src` cerrado, `frame-ancestors 'none'`
    y cabeceras de seguridad. Excepciones documentadas.
19. **Trazabilidad de entradas.** Cada entrada queda en
    `<home>/telemetria-entradas.jsonl` con fuente, sesión, autenticación y un
    HMAC del texto (nunca el texto). El turno solo se muestra cuando el
    núcleo lo acepta.
20. **ElevenLabs** desactivado aunque haya clave, salvo
    `elevenlabs_habilitado = true`.
21. **Abrir sitios web.** Cada sitio se abre cuando el usuario lo pide, con
    `pc_open` (riesgo MEDIUM: confirmación en CONFIRMAR). Un dominio suelto
    (`youtube.com`) o un nombre conocido (YouTube, Gmail, Notion…) se trata
    como página web y `Desktop.open` lo manda al navegador como `https://`;
    nunca pasa por `os.startfile`, así que el `.com` de un dominio no se
    confunde con un ejecutable. El resto de extensiones de ejecutable y los
    nombres de consolas siguen bloqueados aunque parezcan dominios.
    Opcional y desactivado por defecto: `[openclaw] abrir_al_informe` abre
    sitios solos al pedir el informe (`openclaw/tabs.py`; solo `https`, sin
    credenciales, máximo 8, auditado como `briefing.tab`, Stop cancela).
22. **Idioma.** El prompt exige responder solo en el idioma configurado, al
    principio y al final; cada resultado de herramienta lleva un recordatorio
    y los caracteres chinos, japoneses o coreanos se eliminan antes de llegar
    a la voz.

## Niveles de riesgo

| Nivel | Comportamiento | Ejemplos |
|---|---|---|
| READ_ONLY | Automático si el conector está conectado y permitido | `correo.list_priority`, `sistema.snapshot` |
| LOW | Solo si `permitir_bajo = true` (por defecto **no**) | `sandbox.save_briefing`, `correo.local_draft` |
| MEDIUM | Vista previa + aprobación | `correo.create_draft`, `navegador.open_url`, `n8n.dry_run` |
| HIGH | Aprobación exacta por acción, siempre | `correo.send`, `mensajeria.send`, `git.push`, `deploy.run` |
| CRITICAL | Bloqueado; solo con `critico_habilitado = true` y, aun así, aprobación exacta | `correo.delete`, `pagos.pay`, `datos.export` |

Herramienta desconocida o conector `no_configurado` → BLOCK.

## Secretos

| Secreto | Dónde vive | Quién lo lee |
|---|---|---|
| Token del Gateway de OpenClaw | `~/.openclaw/openclaw.json` (texto plano, ACL: usuario, SYSTEM, administradores) | Solo la CLI oficial de OpenClaw |
| Futuros tokens OAuth / API | Fuera del repo: almacén del proveedor MCP, `~/.openclaw` con SecretRef, o el gestor de credenciales de Windows. **Nunca** en `crisvis.toml` versionado, frontend, logs, capturas ni docs | El proveedor correspondiente |
| `.env`, `crisvis.toml`, `mcp.json` | Ignorados por git | Núcleo |
| `admin.token` (nuevo en cada arranque) | `<home>` del usuario | Núcleo y CLI `crisvis permisos` |
| Secreto de arranque, tokens de sesión, tickets | Memoria del núcleo; cookie HttpOnly en el navegador; nunca en logs | Núcleo y la página servida por él |
| `telemetria.key` (clave del HMAC) | `<home>`, permisos 0600 | Núcleo |

## Control inteligente de aplicaciones (SAC)

Está activo en modo «aplicar». Bloquea binarios sin firma o sin reputación.
Hasta ahora no ha bloqueado node, esbuild ni koffi de OpenClaw. Si algún día
bloquea `@lydell/node-pty`, `@trycua/cua-driver` o `sqlite-vec`, **no** hay que
desactivar el SAC (no se puede volver a activar sin reinstalar Windows). Las
alternativas son WSL2 o el instalador firmado de Windows Hub.

## Checklist antes de conectar CUALQUIER cuenta real

- [ ] El usuario ha aprobado este conector concreto, por escrito, en esta sesión.
- [ ] Integración confirmada en la documentación oficial (OpenClaw o proveedor);
      origen, versión y permisos revisados. Si es de terceros: código revisado,
      riesgo documentado y aprobación explícita.
- [ ] Cuenta de prueba o con el menor privilegio posible; ámbitos de **solo
      lectura** (ver `CONNECTOR_MATRIX.md`).
- [ ] El secreto queda fuera del repo (`git status` limpio de secretos;
      `openclaw secrets audit --check` si vive en OpenClaw).
- [ ] Sin endpoints públicos, túneles ni webhooks entrantes.
- [ ] `dry_run = true` sigue activo; las escrituras siguen en mock/sandbox.
- [ ] Pruebas del conector: 401 → `unauthorized`, timeout, no disponible,
      redacción del token en logs y auditoría.
- [ ] El conector aparece en Integraciones con modo `real` y su fuente.
- [ ] Vuelta atrás escrita: cómo revocar el token en el proveedor y cómo
      volver al proveedor mock.
- [ ] `openclaw security audit --deep` sin hallazgos críticos nuevos.
- [ ] Hardening de la Fase 3.5 en vigor: CONFIRMAR activo, LIBRE apagado,
      `npm test` y `pytest -k hardening` en verde, instancias de prueba cerradas.
