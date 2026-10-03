# Evidencia de pruebas de seguridad — Fase 3.5

Fecha de ejecución: 2026-10-03, en este equipo (Windows 11, Python 3.12 en
`.venv`, Node con npm workspaces). Todas las órdenes se lanzan desde la raíz
del repositorio.

## Resultado de la suite completa

| Orden | Resultado |
|---|---|
| `uv run --no-sync python -m pytest -q` | **315 passed** (194 anteriores + 121 nuevas) |
| `uv run --no-sync python -m pytest -q tests -k hardening` | **121 passed**, 194 deselected |
| `uv run --no-sync python -m ruff check src tests` | All checks passed! |
| `npm test -w @crisvis/protocol` | «TypeScript y frames.json coinciden.» |
| `npm test -w @crisvis/face` | **17 pass**, 0 fail (11 anteriores + 6 nuevas) |
| `npm run typecheck` (`tsc -b`) | Sin errores |
| `npm run lint -w @crisvis/face` (oxlint) | Sin errores; solo avisos de estilo de React del mismo tipo que los que ya existían |
| `npm run build` | Correcto |
| `rg "new Function\(\|eval\(" src/crisvis/static/assets` | Ninguna coincidencia |
| `index.html` compilado | Sin `<meta http-equiv="Content-Security-Policy">` y sin scripts en línea |

## Pruebas nuevas por corrección

### A. R-16 — Control del PC y modos

`tests/test_hardening_actions.py` (18 funciones):

| Prueba | Qué demuestra |
|---|---|
| `test_win_r_cmd_enter_never_reaches_a_shell` | La cadena Win+R → «cmd» → Intro se bloquea en el primer paso |
| `test_typing_shell_commands_is_blocked` | Escribir `powershell`, `cmd`, `regedit`, `mshta`, `rundll32`, `wscript`, `cscript`, `taskkill`, `shutdown`… es CRITICAL |
| `test_launcher_and_task_manager_shortcuts_are_blocked` | Win+R, Ctrl+Shift+Esc y similares bloqueados |
| `test_opening_shells_or_executables_is_blocked` | `pc_open` no abre shells, intérpretes ni ejecutables fuera de la lista |
| `test_start_menu_shells_are_refused_even_by_app_id` | Tampoco por AppID del menú Inicio |
| `test_commands_split_across_steps_are_caught` | «pow» + «ershell» se detecta como orden |
| `test_enter_or_paste_after_command_like_text_is_blocked` | Intro o pegar tras texto con forma de orden |
| `test_shell_windows_are_recognised` | Se reconocen las ventanas de consola |
| `test_keyboard_and_mouse_refuse_a_shell_window` | Teclado y ratón no actúan sobre una consola |
| `test_one_yes_does_not_cover_a_chain` | Cada paso necesita su propia aprobación |
| `test_libre_still_asks_for_keyboard_and_dangerous_actions` | LIBRE sigue preguntando lo peligroso |
| `test_reused_approval_fails` | Una aprobación no se reutiliza |
| `test_approval_without_grant_or_with_wrong_nonce_fails` | Sin grant o con nonce falso, no se ejecuta |
| `test_approval_is_void_if_the_target_window_changes` | Cambiar de ventana anula la aprobación |
| `test_grants_are_bound_to_parameters_turn_and_time` | Grant ligado a parámetros, turno, uso único y caducidad |
| `test_benign_allowlist_still_works` | Las acciones inocuas siguen funcionando |
| `test_nothing_that_modifies_the_pc_is_assessed_as_low` | Nada que modifique el equipo es LOW |
| `test_command_detector_does_not_flag_ordinary_text` | El texto normal no se marca como orden |

`tests/test_hardening_modes.py` (8 funciones): CONFIRMAR por defecto y LIBRE
no disponible; migración de `modo = "libre"`; el entorno no fuerza LIBRE;
LECTURA y CONFIRMAR persisten tras reiniciar; la interfaz no sube a LIBRE;
LIBRE exige la frase y una duración corta; LIBRE se audita, caduca y no
sobrevive a un reinicio; desactivar LIBRE vuelve a CONFIRMAR.

`tests/test_pc.py::test_each_pc_step_needs_its_own_yes` (sustituye a una
prueba anterior que daba por buena la vulnerabilidad): cuatro pasos, cuatro
preguntas y cuatro aprobaciones distintas.

### B. R-27 — Portapapeles

`tests/test_hardening_clipboard.py` (9): `pc_system` ya no lee el
portapapeles; `read_clipboard` es PELIGROSO y se confirma; sin aprobación se
bloquea; pregunta cada vez, también en LIBRE; el contenido sale enmascarado y
no aparece en los logs; la auditoría no lo contiene; enmascarado de secretos
habituales; se puede deshabilitar por configuración; un valor inválido impide
arrancar.

### C. R-28 — Cancelación

`tests/test_hardening_cancel.py` (9): Stop mata el proceso y sus hijos (sin
supervivientes); el timeout mata el árbol y se informa `timed_out`; un proceso
completado devuelve su salida; `shell_exec` cancelado nunca dice
«completado»; el tracker audita el ciclo completo con un solo id de
correlación; nunca «completado» para timeouts o cancelaciones tardías; Stop
sobre una acción no cancelable responde que no pudo; Stop cancela un
`shell_exec` en marcha; la confirmación avisa de que no se puede cancelar.

### D. R-29 (registro R-30/R-17) — HTTP

`tests/test_hardening_http.py` (16):

| Prueba | Resultado esperado |
|---|---|
| `test_tts_without_token_is_401` | 401 |
| `test_valid_client_can_speak` | 200 con token válido |
| `test_expired_token_is_rejected` | 401 |
| `test_token_is_bound_to_its_origin` | 401 con otro Origin |
| `test_foreign_origin_is_403` | 403 |
| `test_cross_site_fetch_is_403` | 403 |
| `test_dns_rebinding_host_is_421` | 421 |
| `test_session_needs_the_boot_cookie` | 401 con `reload: true` |
| `test_boot_cookie_is_httponly_and_strict` | Cookie HttpOnly, SameSite=Strict |
| `test_rate_limit_returns_429` | 429 al superar el límite |
| `test_mcp_reload_needs_token_and_confirmation` | 401 sin token, 400 sin «RECARGAR» |
| `test_read_endpoints_need_a_token` | Las lecturas de `/api/*` exigen token |
| `test_ui_can_lower_but_never_raise_to_libre` | 403 al pedir LIBRE |
| `test_admin_libre_only_from_the_cli_with_the_admin_token` | 403 con Origin o sin `admin.token` |
| `test_elevenlabs_stays_off_unless_explicitly_enabled` | Con clave pero sin habilitar, no se usa |
| `test_tokens_and_tickets_never_reach_logs` | Ni tokens ni tickets en los logs |

### E. R-30 (registro R-31) — CSP

`tests/test_hardening_csp.py` (6): CSP con nonce y sin `unsafe-*` en scripts;
nonce nuevo en cada carga; cabeceras de seguridad en todas las respuestas;
Kokoro solo si se activa; `index.html` fuente sin CSP en `meta` ni scripts en
línea; el `index.html` compilado, también limpio.

### F. R-31 (registro R-13/R-14) — WebSocket

`tests/test_hardening_ws.py` (13 funciones, 21 casos): sin ticket, ticket
inválido, caducado o reutilizado → 4401; Origin falso → 4403/4401; cliente
válido funciona y su entrada queda en la telemetría sin texto; la trama
`permissions` se rechaza y se audita; nueve tramas que intentan decidir por
el núcleo (modo, riesgo, aprobación, identidad…) se rechazan; la primera
trama debe ser `hello`; carga sobredimensionada rechazada; un cambio de modo
cierra las conexiones (4001); límite de conexiones (4429); no se puede
retomar la sesión de otro cliente.

### G. Incidente «como»

- `tests/test_hardening_telemetry.py` (3): entradas trazables sin su texto;
  fuentes desconocidas y rechazos registrados; la clave del HMAC sobrevive a
  reinicios.
- `apps/face/test/guardrails.test.ts`: `isNoiseUtterance` descarta «como»,
  «Cómo.», «¿qué?», «eh» o «y» sueltos y deja pasar «sí», «no», «para»,
  «hora», «como estás» o «qué hora es».

### Interfaz

`apps/face/test/guardrails.test.ts` (6 pruebas nuevas): un «sí» de viva voz no
aprueba riesgo alto; la interfaz solo ofrece LECTURA o CONFIRMAR; cuenta atrás
de LIBRE; Stop nunca se presenta como completado; renovación del token antes
de caducar; filtro de palabra suelta.

## Verificación en ejecución

La verificación sobre la instancia real reiniciada en `127.0.0.1:8787` (modo,
voz clonada, WebSocket con ticket, CSP que bloquea un script inyectado y
puertos en loopback) se recoge en el informe de cierre de la Fase 3.5.
