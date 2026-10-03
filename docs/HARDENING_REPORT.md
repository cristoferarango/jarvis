# Informe de hardening — Fase 3.5

Fecha: 2026-10-03. Todo el trabajo está **sin commit**. Ninguna cuenta real
conectada, ningún token OAuth pedido ni guardado, OpenClaw sin cambios
(solo `127.0.0.1:18789`, sin autoarranque, canales, skills, hooks ni webhooks).

## Correspondencia de identificadores

La petición de la Fase 3.5 numeró los riesgos de forma distinta a
`RISK_REGISTER.md`. Esta tabla evita confusiones:

| ID en la petición | ID en `RISK_REGISTER.md` | Tema |
|---|---|---|
| R-16 | R-16 | Control del PC (`pc_*`) sin puerta propia; modos de permisos |
| R-27 | R-27 | Portapapeles leído como LECTURA |
| R-28 | R-28 | Stop no cancelaba la herramienta en curso |
| R-29 | R-30 (+ R-17) | `/tts`, `/api/mcp/recargar` y la API HTTP sin autenticación |
| R-30 | R-31 | CSP con `unsafe-inline` y `unsafe-eval` |
| R-31 | R-13 + R-14 | WebSocket falsificable; trama `permissions` sin validar |
| — | R-29 | Texto de LIBRE engañoso en la interfaz (queda corregido: la interfaz ya no ofrece LIBRE) |
| «como» | — | Incidente en la instancia de prueba (`INCIDENT_COMO_ANALYSIS.md`) |

## Resultado global

| Comprobación | Antes | Ahora |
|---|---|---|
| Pruebas Python (`pytest`) | 194 | **315** (194 + 121 nuevas) |
| Pruebas de interfaz (`npm test -w @crisvis/face`) | 11 | **17** (11 + 6 nuevas) |
| Protocolo (`npm test -w @crisvis/protocol`) | OK | OK (protocolo v2) |
| Ruff | OK | OK |
| TypeScript (`tsc -b`) | OK | OK |
| Build (`npm run build`) | OK | OK; sin `eval` ni `new Function` en el bundle; `index.html` sin CSP en `meta` ni scripts en línea |

Detalle por archivo en `SECURITY_TEST_EVIDENCE.md`.

---

## A. R-16 — Modos de permisos y control del PC

**Riesgo original.** Un solo «sí» autorizaba todas las `pc_*` de una orden
(`pc_keys win+r` + `pc_type "cmd"` + `pc_keys enter` = shell arbitrario sin
pasar por la puerta de `shell_exec`). En LIBRE no hacía falta ni ese «sí».
`pc_open` ejecutaba cualquier archivo con `os.startfile`. El modo se podía
fijar a LIBRE desde la configuración, el entorno o la interfaz.

**Impacto.** Alto: ejecución de órdenes arbitrarias guiada por el modelo, es
decir, por cualquier texto que llegue al modelo (inyección de prompt).

**Cambio.**

- *Modo de permisos* (`security/modes.py`, `settings.py`, `cli.py`, `app.py`):
  - CONFIRMAR por defecto. LECTURA y CONFIRMAR persisten en `<home>/permisos.json`.
  - LIBRE nunca persiste. Solo se habilita con `crisvis permisos libre --minutos N`
    desde una terminal del usuario: lee `<home>/admin.token` (nuevo en cada
    arranque), exige escribir «ACTIVAR LIBRE», dura como máximo
    `seguridad.libre_max_minutos` (30) y caduca solo. Al caducar, al
    desactivarlo o al reiniciar vuelve a CONFIRMAR. Todo queda auditado
    (`libre_habilitado`, `libre_caducado`, `libre_deshabilitado`, `libre_rechazado`).
  - La interfaz puede bajar a LECTURA o CONFIRMAR (`POST /api/permisos` con
    token), nunca subir. Las rutas `/api/admin/*` rechazan cualquier petición
    con Origin (un navegador siempre lo manda).
  - `modo = "libre"` en `crisvis.toml` o `CRISVIS_PERMISOS=libre` se migran a
    CONFIRMAR con aviso.
- *Riesgo por acción* (`security/guard.py`): cada llamada se evalúa con la
  herramienta, sus parámetros y lo ocurrido antes en el turno. Niveles LOW,
  MEDIUM, HIGH y CRITICAL (bloqueado, ni se pregunta). Ninguna regla depende
  del modelo.
  - CRITICAL: Win+R, Ctrl+Shift+Esc, Ctrl+Alt+Supr y similares; escribir o
    pegar texto con forma de orden (`cmd`, `powershell`, `pwsh`, `wt`,
    `regedit`, `mshta`, `rundll32`, `wscript`, `cscript`, `taskkill`,
    `shutdown`, `reg`, `certutil`, `bitsadmin`, intérpretes…); abrir esos
    programas o cualquier ejecutable/script fuera de
    `seguridad.apps_permitidas`; Intro o pegar tras texto con forma de orden;
    teclado o ratón sobre una ventana de shell.
  - Órdenes partidas en varios pasos (`pc_type "pow"` + `pc_type "ershell"`) y
    AppID del menú Inicio se detectan igual.
- *Aprobación individual con nonce* (`security/grants.py`, `gateway/session.py`):
  cada acción MEDIUM o HIGH pide su propia aprobación. El núcleo emite un
  grant de un solo uso ligado a herramienta, parámetros exactos (huella
  sha256), turno y ventana objetivo, con nonce aleatorio y caducidad de 60 s.
  Reutilizarlo, cambiar un parámetro, cambiar de turno o de ventana lo anula.
- *En LIBRE* siguen preguntando: teclado, ratón, portapapeles, órdenes y
  acciones de sistema. Lo CRITICAL sigue bloqueado.

**Limitaciones.** El detector de órdenes es por reglas: puede dar falsos
positivos (`&&` o `x.exe` en un texto normal) y bloquear algo legítimo. Un
proceso local con el usuario de Windows puede leer `admin.token`.

**Pruebas.** `test_hardening_actions.py` (18), `test_hardening_modes.py` (8),
`test_hardening_http.py` (`test_ui_can_lower_but_never_raise_to_libre`,
`test_admin_libre_only_from_the_cli_with_the_admin_token`),
`test_pc.py::test_each_pc_step_needs_its_own_yes`, interfaz
(`userSelectable`, `libreLeft`).

**Resultado.** Todas pasan. La cadena Win+R → `cmd` → Intro no llega nunca a
una shell, ni en LIBRE ni con aprobaciones.

**Rollback.** Bajar a LECTURA o volver a CONFIRMAR: `crisvis permisos confirmar`.
Revertir el código: `git checkout -- src/crisvis/{settings,cli,app}.py
src/crisvis/body src/crisvis/gateway/session.py` y borrar
`src/crisvis/security/{modes,guard,grants}.py`. Borrar `<home>/permisos.json`
devuelve al modo de la configuración. **No recomendado**: reabre la cadena de
shell por teclado.

**Riesgo residual.** Bajo. Falsos positivos del detector; malware local con el
mismo usuario.

## B. R-27 — Portapapeles

**Riesgo original.** `pc_system` devolvía el portapapeles como acción de
LECTURA, sin confirmación, y su contenido podía acabar en logs o auditoría.

**Impacto.** Medio: fuga de contraseñas, tokens o datos personales copiados.

**Cambio** (`body/pc.py`, `security/policy.py`, `settings.py`).

- `pc_system` ya no lee el portapapeles.
- Nueva herramienta `read_clipboard`, nivel PELIGROSO/HIGH: se aprueba cada
  lectura, también en LIBRE, y un «sí» por voz no basta.
- El contenido se enmascara con `mask_secrets` (tokens `ghp_`, `sk-`, JWT,
  `password=`, claves privadas, tarjetas…) y se recorta a 1500 caracteres.
- Nunca se escribe en logs ni en auditoría: solo `tool=read_clipboard`.
- `[seguridad] portapapeles = "deshabilitado"` hace que la herramienta ni se
  ofrezca. Cualquier otro valor distinto de `confirmar` impide arrancar.
- La cabecera `Permissions-Policy` niega `clipboard-read` a la página.

**Limitaciones.** El modelo ve el texto enmascarado que el usuario aprobó y
podría repetirlo o guardarlo con herramientas de memoria. El escáner de
guardrails de OpenJarvis podría registrar fragmentos de datos personales
(`matched_text`).

**Pruebas.** `test_hardening_clipboard.py` (9).

**Resultado.** Todas pasan.

**Rollback.** `git checkout -- src/crisvis/body/pc.py src/crisvis/security/policy.py`.
Para apagar solo esta función: `portapapeles = "deshabilitado"`.

**Riesgo residual.** Bajo.

## C. R-28 — Cancelación real

**Riesgo original.** Stop cancelaba el turno, pero la herramienta seguía en su
hilo: un `shell_exec` corría hasta su timeout. La auditoría registraba
decisiones, no resultados.

**Impacto.** Medio: una orden aprobada por error no se podía parar.

**Cambio** (`execution.py`, `body/shell.py`, `gateway/session.py`, interfaz).

- `run_process`: cada proceso de `shell_exec` se lanza dentro de un *job
  object* de Windows con `KILL_ON_JOB_CLOSE` (grupo de procesos en POSIX). Al
  cancelar se termina el job y, como respaldo, el árbol completo con psutil.
- `CancellationToken` por turno y `ExecutionTracker` con un id de correlación
  por ejecución. La auditoría registra el ciclo completo: `iniciada`,
  `completada`, `cancelada`, `timed_out`, `fallida`, `completada_tras_cancelar`.
  Se escribe antes de avisar a quien espera, así que Stop nunca se adelanta.
- Stop envía tramas `cancel` con fase `requested`, `cancelled` o `failed`. La
  interfaz muestra «Cancelando…», «Cancelado.» o «No se pudo cancelar…», y
  nunca «completado» para algo cancelado.
- Las acciones no cancelables (`pc_*`, `read_clipboard`…) lo avisan en la
  ventana de confirmación: «Una vez iniciada, esta acción no se puede
  cancelar». Si Stop llega con una en marcha, espera hasta 6 s y responde
  `failed` + auditoría `cancel_failed`.

**Limitaciones.** Hay una ventana muy pequeña entre crear el proceso y
asignarlo al job (mitigada con la limpieza del árbol con psutil). Las
herramientas `pc_*` no se pueden interrumpir a mitad. Todavía no hay rutinas
programadas, así que no aplica excluir de ellas lo no cancelable.

**Pruebas.** `test_hardening_cancel.py` (9), interfaz (`cancelNotice`).

**Resultado.** Todas pasan; la prueba del árbol padre-hijo confirma que no
queda ningún proceso vivo.

**Rollback.** `git checkout -- src/crisvis/body/__init__.py src/crisvis/gateway/session.py`
y borrar `src/crisvis/execution.py` y `src/crisvis/body/shell.py`.

**Riesgo residual.** Bajo.

## D. R-29 (registro R-30 + R-17) — Autenticación HTTP y límites

**Riesgo original.** `POST /tts`, `POST /stt` y `POST /api/mcp/recargar`
aceptaban peticiones sin Origin y sin autenticación: cualquier proceso o web
podía gastar ElevenLabs o forzar reconexiones de MCP.

**Impacto.** Medio: coste económico, denegación de servicio, fuga de texto a
un tercero.

**Cambio** (`security/auth.py`, `security/ratelimit.py`, `app.py`, `voice/speech.py`, interfaz).

1. Al servir la página, el núcleo fija la cookie `crisvis_arranque`
   (HttpOnly, SameSite=Strict, nueva en cada arranque).
2. `POST /api/sesion` exige Host local, Origin permitido y esa cookie, y
   devuelve un token de 15 min (`seguridad.sesion_minutos`) ligado a ese Origin.
3. `/tts`, `/stt`, `/api/mcp/recargar`, `/api/permisos`, `/api/estado`,
   `/api/mcp` y `/api/openclaw/*` exigen `X-Crisvis-Token`.
4. `/api/mcp/recargar` exige además `{"confirmacion": "RECARGAR"}` y queda
   auditado.
5. Host que no sea de loopback → 421 (DNS rebinding). Origin ajeno → 403.
6. Límites por minuto: `tts_por_minuto` (40), `stt_por_minuto` (30); también
   sesión, ticket y administración → 429.
7. ElevenLabs **desactivado** aunque haya clave, salvo
   `[voz] elevenlabs_habilitado = true`.
8. Los tokens y tickets se eliminan de los logs de acceso (`_RedactTickets`).

**Limitaciones.** Un proceso local del mismo usuario puede imitar el navegador
entero (pedir la página, leer la cookie, fijar Origin). Las rutas de medios
(`/img`, `/media`, `/page`, `/file`) y `/health` siguen sin token: son de
lectura y las cargan etiquetas `<img>`/`<video>`, que no pueden mandar
cabeceras. En desarrollo (`CRISVIS_DEV=1`) no hay cookie de arranque.

**Pruebas.** `test_hardening_http.py` (16).

**Resultado.** Todas pasan.

**Rollback.** `git checkout -- src/crisvis/app.py src/crisvis/voice/speech.py
apps/face/src/lib/{tts,voice}.ts`, borrar `security/{auth,ratelimit}.py` y
`apps/face/src/lib/auth.ts`, y recompilar (`npm run build`). Para volver a
usar ElevenLabs sin revertir nada: `elevenlabs_habilitado = true`.

**Riesgo residual.** Bajo frente a webs y clientes sin sesión; aceptado frente
a malware local.

## E. R-30 (registro R-31) — CSP de producción

**Riesgo original.** La CSP iba en un `<meta>` con `script-src 'unsafe-inline'
'unsafe-eval'` y `connect-src https:`.

**Impacto.** Medio: un XSS habría podido ejecutar código y sacar datos a
cualquier dominio.

**Cambio** (`security/headers.py`, `app.py`, `apps/face/index.html`, `vite.config.ts`).

- CSP por respuesta, en cabecera, con un nonce nuevo en cada carga de página:
  `script-src 'self' 'wasm-unsafe-eval' 'nonce-…'`, `script-src-attr 'none'`,
  `object-src 'none'`, `base-uri 'self'`, `frame-ancestors 'none'`,
  `form-action 'self'`, `connect-src` limitado al propio puerto (HTTP y WS) y a
  `storage.googleapis.com`.
- Cabeceras en todas las respuestas: `X-Content-Type-Options: nosniff`,
  `X-Frame-Options: DENY`, `Referrer-Policy: no-referrer`,
  `Cross-Origin-Opener-Policy` y `Cross-Origin-Resource-Policy: same-origin`, y
  `Permissions-Policy` (cámara y micrófono solo para la propia página;
  geolocalización, pagos, USB, serie, Bluetooth, lectura del portapapeles y
  captura de pantalla denegados).
- `index.html` sin CSP en `meta` y sin scripts en línea. Desarrollo con su
  propia política en `vite.config.ts`.

**Excepciones documentadas.**

| Excepción | Motivo | Alcance |
|---|---|---|
| `'wasm-unsafe-eval'` | Compilar WebAssembly (palabra de activación) | No permite `eval` de JavaScript |
| `style-src 'unsafe-inline'` | Los paneles insertan HTML saneado con atributos `style` | Solo estilos |
| `connect-src https://storage.googleapis.com` | Pesos del modelo de gestos de MediaPipe | Datos, no código |
| `seguridad.csp_kokoro = true` (desactivado) | Kokoro usa `eval` y descarga modelos de Hugging Face | Solo si se activa explícitamente |

**Limitaciones.** `style-src 'unsafe-inline'` permite inyección de estilos (no
de scripts). La carpeta `src/crisvis/static/mediapipe` no existe en esta
máquina (problema anterior a esta fase): el reconocimiento de gestos no carga
su runtime, y la CSP no es la causa.

**Pruebas.** `test_hardening_csp.py` (6).

**Resultado.** Todas pasan; comprobado también en el `index.html` compilado.

**Rollback.** `git checkout -- apps/face/index.html apps/face/vite.config.ts
src/crisvis/app.py`, borrar `security/headers.py` y recompilar.

**Riesgo residual.** Bajo.

## F. R-31 (registro R-13 + R-14) — WebSocket

**Riesgo original.** Cualquier proceso local podía abrir `/ws` imitando un
Origin local. La trama `permissions` cambiaba el modo global sin validación.
El cliente podía mandar campos que el núcleo debía decidir.

**Impacto.** Alto: control total del asistente desde cualquier proceso local
o página que imitara el Origin.

**Cambio** (`gateway/ws.py`, `gateway/frames.py`, `security/auth.py`, `gateway/session.py`, protocolo v2).

- El WebSocket se abre con un ticket de un solo uso de 30 s, pedido con el
  token de sesión y ligado al mismo Origin y cliente. Host y Origin
  obligatorios. Rechazos: 4403 (Host/Origin), 4401 (ticket).
- Esquema estricto por tipo (`frames.py`): campos obligatorios, tamaños
  (`ask` ≤ 8000 caracteres), tipos. Se rechazan los campos que decide el
  núcleo (modo, riesgo, aprobación, identidad, sesión…). La primera trama
  debe ser `hello`.
- La trama `permissions` se rechaza siempre y queda auditada. El modo solo
  cambia por HTTP con token (bajar) o por la CLI de administración (LIBRE).
- Límites: 60 tramas cada 10 s, 20 `ask` por minuto, 20 tramas inválidas
  (después se cierra), `seguridad.max_conexiones` (6) → 4429.
- Al cambiar el modo, el núcleo cierra todas las conexiones (código 4001) y
  revoca los grants pendientes; la interfaz se reconecta con ticket nuevo.
- Una sesión no puede retomar la de otro cliente.
- Trama `input` de aceptación y telemetría de entradas (ver G).

**Limitaciones.** Recargar la página ya no retoma la sesión anterior: es una
sesión nueva. Mismo límite que D frente a malware local.

**Pruebas.** `test_hardening_ws.py` (13 funciones, 21 casos con parámetros).

**Resultado.** Todas pasan.

**Rollback.** Requiere revertir núcleo e interfaz a la vez (protocolo v2):
`git checkout -- src/crisvis/gateway packages/protocol apps/face/src/lib/brain.ts`,
borrar `gateway/frames.py` y recompilar.

**Riesgo residual.** Bajo.

## G. Incidente «como» — trazabilidad de entradas

**Riesgo original.** Las entradas de texto no dejaban rastro y la interfaz
mostraba el turno antes de que el núcleo lo aceptara. Una palabra suelta oída
por el micrófono se enviaba como orden.

**Cambio.**

- `security/telemetry.py`: cada entrada se registra en
  `<home>/telemetria-entradas.jsonl` con hora, `event_id`, sesión (8
  caracteres), fuente declarada, canal, Origin, HMAC-SHA256 del texto con una
  clave local (`telemetria.key`, 32 bytes, 0600), longitud, autenticación,
  aceptada y motivo. **Nunca el texto.**
- Entradas sin sesión válida: rechazadas y registradas, nunca mostradas.
- La interfaz muestra el turno solo tras la trama `input` de aceptación.
- `isNoiseUtterance()` descarta enunciados de voz de una sola palabra funcional.

**Pruebas.** `test_hardening_telemetry.py` (3), `test_hardening_ws.py`, interfaz
(`isNoiseUtterance`).

**Rollback.** Borrar `security/telemetry.py`, revertir `gateway/ws.py` y
`App.tsx`. Se pueden borrar `telemetria-entradas.jsonl` y `telemetria.key`.

**Riesgo residual.** Bajo. Análisis completo en `INCIDENT_COMO_ANALYSIS.md`.

---

## Migraciones

| Migración | Qué pasa al arrancar | Vuelta atrás |
|---|---|---|
| `modo = "libre"` en `crisvis.toml` o `CRISVIS_PERMISOS=libre` | Se arranca en CONFIRMAR, con aviso en el log y `modo_arranque` en la auditoría | Ninguna: LIBRE solo por la CLI |
| `<home>/permisos.json` (nuevo) | Guarda LECTURA/CONFIRMAR elegido desde la interfaz | Borrarlo |
| `<home>/admin.token` (nuevo) | Se reescribe en cada arranque | Se puede borrar con el núcleo parado |
| `[voz] elevenlabs_habilitado` (nuevo, `false`) | ElevenLabs no se usa aunque haya clave | Poner `true` |
| `[seguridad]` (nueva sección) | Valores por defecto seguros; `portapapeles` inválido impide arrancar | Quitar la sección = valores por defecto |
| Protocolo v1 → v2 | Una cara antigua no se conecta (falta ticket) | Recompilar la cara (`npm run build`) |
| `<home>/telemetria-entradas.jsonl` y `telemetria.key` (nuevos) | Se crean al recibir la primera entrada | Borrarlos |
| Auditoría (`auditoria.jsonl`) | Eventos nuevos, mismo formato JSONL | Compatible |

## Rollback general

Todo está sin commit. Lista completa de archivos en `INTEGRATION_PLAN.md`
(«Vuelta atrás»). Revertir **siempre** núcleo e interfaz juntos y recompilar,
porque el protocolo cambió a v2.
