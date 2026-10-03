# Registro de riesgos

Probabilidad e impacto: B (bajo), M (medio), A (alto). Estado: **Corregido**
(la causa se eliminó en el código y hay pruebas), **Mitigado** (el riesgo se
redujo), **Aceptado** (documentado, sin acción), **Abierto** (requiere trabajo
o una decisión tuya). La probabilidad y el impacto son los del riesgo original.

## Riesgos de la integración con OpenClaw

| ID | Riesgo | Prob. | Impacto | Mitigación | Estado |
|---|---|---|---|---|---|
| R-01 | OpenClaw expuesto fuera del equipo | B | A | `bind: loopback`, mDNS off, Tailscale off, sin túneles; el adaptador rechaza URLs no loopback; verificado: solo `127.0.0.1:18789` | Mitigado |
| R-02 | Inyección de prompt desde correos, issues o mensajes | M | A | El informe no pasa por el modelo; el contenido externo solo se muestra; perfil `minimal` + deny de todos los grupos de acción en OpenClaw; qwen3:8b sin web ni navegador | Mitigado |
| R-03 | Skill o plugin de terceros malicioso (OpenClaw no bloquea código peligroso) | M | A | No se ha instalado ninguno; 7 plugins incluidos desactivados; cualquier instalación futura requiere revisión de código y tu aprobación | Mitigado |
| R-04 | Envío automático de mensajes o correos | B | A | `correo.send` y `mensajeria.send` son HIGH (aprobación exacta siempre); `DRY_RUN=true`; sin canales configurados en OpenClaw | Mitigado |
| R-05 | Tareas de fondo de OpenClaw (latido cada 30 min, dreaming a las 3:00) usan la GPU o actúan solas | A (por defecto) | M | `heartbeat.every: "0m"`, dreaming apagado, `cron.enabled: false`; el log confirmó la retirada de la tarea | Mitigado |
| R-06 | Gmail oficial necesita un endpoint HTTPS público | — | A | No se usa; alternativa por consulta con `gmail.metadata` vía MCP o custom | Mitigado |
| R-07 | El Control inteligente de aplicaciones bloquea binarios nativos (node-pty, cua-driver, sqlite-vec) | M | M | Hoy funciona; cua-computer desactivado; alternativa: WSL2 o Windows Hub firmado. **No desactivar el SAC** | Abierto (vigilar) |
| R-08 | Windows nativo menos probado que WSL2 (lo dice el propio onboarding) | M | B | Uso mínimo (health/status); el informe no depende de OpenClaw | Aceptado |
| R-09 | Cadena de suministro npm (343 paquetes, scripts de instalación) | B | A | Versión fijada 2026.9.8, integridad sha512 de npm, scripts de `preinstall`/`postinstall` revisados antes de instalar; nada dentro del repo | Mitigado |
| R-10 | El token HTTP del Gateway da acceso de operador completo | B | A | CRISVIS no lo lee ni lo usa; solo lo lee la CLI oficial | Mitigado |
| R-11 | Datos de prueba confundidos con reales | M | M | Insignia «DATOS DE PRUEBA», modo por fuente en cada pestaña y aviso en el resumen hablado | Mitigado |
| R-12 | Aprobación accidental o reutilizada | B | A | Un solo uso, caducidad de 120 s, huella sha256 exacta, solo con el botón (no Intro ni voz), Esc deniega. Fase 3.5: lo mismo para el control del PC (grant con nonce, 60 s, ligado a parámetros, turno y ventana) | Mitigado |
| R-15 | Token del Gateway en texto plano en `~/.openclaw/openclaw.json` (`openclaw secrets audit`) | B | M | ACL: solo tu usuario, SYSTEM y administradores; fuera del repo. Pasarlo a variable de entorno lo expondría al proceso de CRISVIS | Aceptado |
| R-24 | La sonda profunda de `security audit` falla por falta de `operator.read` | — | B | El adaptador no necesita ese permiso; no se amplían permisos | Aceptado |
| R-25 | La instancia de CRISVIS que está corriendo (puerto 8787) usa el código anterior | A | B | Reinicio en 8787 tras aplicar el hardening y pasar todas las pruebas (Fase 3.5) | Mitigado |
| R-26 | OpenClaw no arranca solo tras reiniciar Windows | A | B | A propósito (sin Tarea programada); el informe funciona sin él y la UI lo muestra «detenido» | Aceptado |

## Riesgos que ya existían en CRISVIS (inventario de la Fase 0)

R-13, R-14, R-16, R-17 y R-27 a R-31 se corrigieron en la Fase 3.5 (detalle,
pruebas y rollback en `HARDENING_REPORT.md`). El resto sigue pendiente de tu
decisión.

| ID | Riesgo | Prob. | Impacto | Medida | Estado |
|---|---|---|---|---|---|
| R-13 | Un proceso local puede falsificar la cabecera Origin y abrir `/ws` (el control de origen solo frena a navegadores). Afecta también a `propose`/`reply` | B | A | Ticket de un solo uso (30 s) emitido con token de sesión, ligado a Origin y cliente; Host y Origin obligatorios; esquema estricto; límites. Queda R-32 | Corregido |
| R-14 | La trama WS `permissions` cambia el modo global sin validación en el servidor | B | A | La trama se rechaza y se audita; el modo solo baja por HTTP con token; LIBRE solo por CLI de administración | Corregido |
| R-16 | Las herramientas `pc_*` pueden saltarse la puerta de `shell_exec`: un solo «sí» autoriza todas las `pc_*` de la orden (`pc_keys win+r` + `pc_type`), y en LIBRE no hace falta ninguno; `pc_open` ejecuta cualquier archivo con `os.startfile` | B | A | `guard.py` (riesgo por acción y por secuencia; shells, atajos y ejecutables CRITICAL), aprobación individual con nonce, CONFIRMAR persistente, LIBRE temporal y solo por administración. Queda R-35 | Corregido |
| R-17 | Las peticiones HTTP sin cabecera Origin pasan; la API no tiene autenticación | B | M | Cookie de arranque + token de sesión en `/api/*`, `/tts`, `/stt`; Host 421; Origin ajeno 403; límites 429. Queda R-33 | Corregido |
| R-18 | `file_read`/`file_write` sin `allowed_dirs` | M | A | Lista de carpetas permitidas | Abierto |
| R-19 | Los servidores MCP heredan todo el entorno del proceso | M | M | Pasar solo las variables declaradas | Abierto |
| R-20 | `public_dict` no enmascara la URL ni los argumentos de MCP | M | M | Aplicar `redact()` también ahí | Abierto |
| R-21 | `[servidor].host` admite `0.0.0.0` sin aviso | B | A | Avisar o rechazar en `settings.py` | Abierto |
| R-22 | `docs/ARQUITECTURA.md` desactualizado | A | B | Actualizar | Abierto |
| R-23 | OpenJarvis trae scheduler y Telegram que no se usan | B | M | Confirmar que siguen desactivados | Abierto |
| R-27 | `pc_system` lee el portapapeles como LECTURA, sin confirmación | M | M | `read_clipboard` aparte, HIGH, aprobación cada vez (también en LIBRE), enmascarado, fuera de logs, desactivable | Corregido |
| R-28 | `interrupt` cancela el turno pero no la herramienta que ya está ejecutándose en su hilo (un `shell_exec` sigue hasta su timeout); la auditoría registra decisiones, no resultados | M | M | Job object / árbol de procesos; ciclo completo auditado con id de correlación; Cancelando / Cancelado / No se pudo cancelar; aviso previo si no es cancelable. Queda R-36 | Corregido |
| R-29 | La UI dice que en LIBRE «ejecutará órdenes sin pedir permiso», pero `shell_exec` sigue confirmando | B | B | La interfaz ya no ofrece LIBRE; explica cómo habilitarlo y qué sigue preguntando | Corregido |
| R-30 | `POST /tts` y `POST /api/mcp/recargar` sin autenticación (las peticiones sin Origin pasan): gasto de ElevenLabs y reconexiones forzadas | B | M | Token + límites; recarga MCP con confirmación «RECARGAR»; ElevenLabs desactivado salvo `elevenlabs_habilitado = true` | Corregido |
| R-31 | La CSP de la cara permite `'unsafe-inline' 'unsafe-eval'` en `script-src` y `connect-src https:` | B | M | CSP por respuesta con nonce, sin `unsafe-*` en scripts, `connect-src` cerrado, cabeceras de seguridad. Queda R-37 | Corregido |

## Riesgos residuales tras la Fase 3.5

| ID | Riesgo | Prob. | Impacto | Medida actual | Estado |
|---|---|---|---|---|---|
| R-32 | Un proceso local con el mismo usuario puede imitar el navegador entero (pedir la página, leer la cookie, fijar Origin) o leer `admin.token` y activar LIBRE | B | A | LIBRE caduca (≤ 30 min, o sin caducidad con `--minutos 0` por decisión del usuario, hasta reiniciar), queda auditado y lo peligroso sigue preguntando; ese proceso ya puede actuar como el usuario | Aceptado |
| R-33 | `/health` y las rutas de medios (`/img`, `/media`, `/page`, `/file`) no llevan token | B | B | Solo lectura; Host y Origin se siguen comprobando; las cargan etiquetas que no pueden mandar cabeceras | Aceptado |
| R-34 | En desarrollo (`CRISVIS_DEV=1`) no hay cookie de arranque | B | M | Solo lo pone `npm run dev`; documentado en `LOCAL_OPERATIONS.md` | Aceptado |
| R-35 | Falsos positivos del detector de órdenes (`&&`, `x.exe` en texto normal) | M | B | Bloquea de más, nunca de menos; `apps_permitidas` para programas concretos | Aceptado |
| R-36 | Las herramientas `pc_*` y `read_clipboard` no se pueden cancelar a mitad; pequeña ventana antes de asignar el proceso al job | B | B | Aviso en la confirmación; Stop responde «No se pudo cancelar»; limpieza del árbol con psutil | Aceptado |
| R-37 | Excepciones de la CSP: `style-src 'unsafe-inline'`, `'wasm-unsafe-eval'`, `storage.googleapis.com`; Kokoro (desactivado) abriría `unsafe-eval` | B | B | Documentadas en `HARDENING_REPORT.md` §E | Aceptado |
| R-38 | El escáner de guardrails de OpenJarvis podría registrar fragmentos de datos personales (`matched_text`); el modelo podría repetir lo leído del portapapeles | B | M | Lectura solo con aprobación y enmascarada | Abierto (vigilar) |
| R-39 | Entradas de voz falsas de dos o más palabras (eco, ruido, reconocedor) | M | B | Telemetría de entradas, filtro de palabra suelta, aprobación por acción y «sí» por voz que no aprueba HIGH | Mitigado |
| R-40 | Recargar la página ya no retoma la sesión anterior | A | B | Comportamiento buscado (sesiones no transferibles) | Aceptado |
| R-41 | `src/crisvis/static/mediapipe` no existe: los gestos no cargan su runtime (anterior a esta fase) | A | B | Sin impacto de seguridad | Abierto |
| R-42 | Abrir un sitio lo muestra con la sesión que el usuario ya tenga iniciada en su navegador | M | B | Solo cuando se pide, con confirmación; siempre por https en el navegador; CRISVIS no lee ni actúa en esas páginas; las pestañas automáticas del informe están desactivadas por defecto | Aceptado |
| R-45 | `pc_youtube` y la reutilización de pestañas escriben una URL en la barra de direcciones de una ventana de navegador ya abierta | B | B | Solo URLs https validadas (sin espacios, control ni credenciales) construidas por CRISVIS; la pestaña del sitio se busca en la tira de pestañas (UI Automation) y se selecciona; solo se escribe si esa ventana de navegador quedó en primer plano con la pestaña del sitio activa; si no, pestaña nueva | Aceptado |
| R-46 | LIBRE sin caducidad (`--minutos 0`) deja abrir y escribir sin preguntar hasta reiniciar | M | M | Decisión explícita del usuario; acción de administración con frase; auditado; no persiste; HIGH, teclado, ratón y lo bloqueado siguen igual | Aceptado |
| R-44 | Audio de fondo (vídeo, música) tomado como orden | M | B | Tras cada respuesta vuelve a reposo y solo despierta con su nombre; el ruido no alarga la ventana de escucha | Mitigado |
| R-43 | El modelo cambia de idioma tras leer resultados en inglés | M | B | Bloque IDIOMA en el prompt, recordatorio en cada resultado de herramienta, filtro de caracteres CJK | Mitigado |

## Correspondencia con la numeración de la petición de la Fase 3.5

| Petición | Este registro |
|---|---|
| R-16 | R-16 |
| R-27 | R-27 |
| R-28 | R-28 |
| R-29 | R-30 y R-17 |
| R-30 | R-31 |
| R-31 | R-13 y R-14 |
