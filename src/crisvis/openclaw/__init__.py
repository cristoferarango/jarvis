"""Integración con OpenClaw a través de un adaptador local y tipado.

Ver docs/CRISVIS_OPENCLAW_ARCHITECTURE.md y docs/SECURITY_MODEL.md.
"""

from crisvis.openclaw.adapter import AdapterError, OpenClawAdapter

__all__ = ["AdapterError", "OpenClawAdapter"]
