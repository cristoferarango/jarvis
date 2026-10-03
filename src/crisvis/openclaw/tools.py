"""Registro cerrado de operaciones del adaptador.

No existe un "ejecutar cualquier cosa": si una operación no está aquí, la
política la bloquea. El riesgo de cada una lo fija este registro, no quien la
propone (ni el modelo ni una fuente).
"""

from __future__ import annotations

from crisvis.openclaw.contracts import Risk, ToolDefinition


def _t(name: str, connector: str, risk: Risk, description: str, access: str = "") -> ToolDefinition:
    if not access:
        access = {
            Risk.READ_ONLY: "read_only",
            Risk.LOW: "draft",
            Risk.MEDIUM: "draft",
            Risk.HIGH: "write",
            Risk.CRITICAL: "delete",
        }[risk]
    return ToolDefinition(
        name=name, connector=connector, risk=risk, description=description, access=access
    )


TOOLS: dict[str, ToolDefinition] = {
    t.name: t
    for t in (
        # Lectura: automática una vez conectada y permitida.
        _t("correo.list_priority", "correo", Risk.READ_ONLY, "Listar correos recientes"),
        _t("agenda.list_today", "agenda", Risk.READ_ONLY, "Eventos del día"),
        _t("tareas.list_open", "tareas", Risk.READ_ONLY, "Tareas abiertas y vencidas"),
        _t("github.list_activity", "github", Risk.READ_ONLY, "Actividad de repositorios"),
        _t("gitlab.list_activity", "gitlab", Risk.READ_ONLY, "Pipelines, MR e issues"),
        _t("documentos.list_recent", "documentos", Risk.READ_ONLY, "Documentos recientes"),
        _t("sistema.snapshot", "sistema", Risk.READ_ONLY, "Estado del equipo y de CRISVIS"),
        _t("docker.list_containers", "docker", Risk.READ_ONLY, "Contenedores"),
        _t("n8n.list_executions", "n8n", Risk.READ_ONLY, "Ejecuciones recientes"),
        _t("crm.list_conversations", "crm", Risk.READ_ONLY, "Conversaciones abiertas"),
        _t("openclaw.health", "openclaw", Risk.READ_ONLY, "Salud del gateway"),
        _t("openclaw.status", "openclaw", Risk.READ_ONLY, "Estado del gateway"),
        # LOW: solo local, y solo si la configuración lo permite.
        _t("sandbox.save_briefing", "sandbox", Risk.LOW, "Guardar el informe en el sandbox"),
        _t("correo.local_draft", "correo", Risk.LOW, "Borrador local (no sale del equipo)"),
        # MEDIUM: vista previa + aprobación.
        _t("navegador.open_url", "navegador", Risk.MEDIUM, "Abrir una URL"),
        _t("correo.create_draft", "correo", Risk.MEDIUM, "Crear un borrador en el buzón"),
        _t("n8n.dry_run", "n8n", Risk.MEDIUM, "Probar un flujo sin efectos"),
        # HIGH: aprobación exacta por acción, siempre.
        _t("correo.send", "correo", Risk.HIGH, "Enviar un correo"),
        _t("mensajeria.send", "mensajeria", Risk.HIGH, "Enviar un mensaje"),
        _t("agenda.create_event", "agenda", Risk.HIGH, "Crear un evento"),
        _t("agenda.update_event", "agenda", Risk.HIGH, "Modificar un evento"),
        _t("github.create_issue", "github", Risk.HIGH, "Crear un issue"),
        _t("gitlab.create_issue", "gitlab", Risk.HIGH, "Crear un issue"),
        _t("git.push", "git", Risk.HIGH, "Publicar commits"),
        _t("n8n.trigger_workflow", "n8n", Risk.HIGH, "Lanzar un flujo real"),
        _t("crm.update_contact", "crm", Risk.HIGH, "Modificar el CRM"),
        _t("deploy.run", "deploy", Risk.HIGH, "Desplegar"),
        # CRITICAL: bloqueado por defecto.
        _t("correo.delete", "correo", Risk.CRITICAL, "Borrar correos"),
        _t("correo.bulk_send", "correo", Risk.CRITICAL, "Envío masivo"),
        _t("documentos.delete", "documentos", Risk.CRITICAL, "Borrar documentos"),
        _t("pagos.pay", "pagos", Risk.CRITICAL, "Realizar un pago"),
        _t("infra.change", "infra", Risk.CRITICAL, "Cambiar infraestructura o producción"),
        _t("datos.export", "datos", Risk.CRITICAL, "Exportar datos sensibles"),
    )
}
