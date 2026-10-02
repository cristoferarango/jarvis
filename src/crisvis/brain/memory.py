"""Memoria persistente cuando falta la extensión nativa de OpenJarvis.

Los backends de memoria de OpenJarvis 1.0.x delegan en ``openjarvis_rust``, una
extensión que no se publica en PyPI y hay que compilar con Rust. Para que la
memoria funcione desde la instalación, este backend implementa la misma
interfaz (``MemoryBackend``) con SQLite FTS5 de la biblioteca estándar y se
registra en el ``MemoryRegistry`` de OpenJarvis. Las herramientas
``memory_store`` / ``memory_search`` y la inyección de contexto lo usan sin
saber la diferencia. Si la extensión nativa está instalada, se prefiere esa.
"""

from __future__ import annotations

import json
import re
import sqlite3
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from openjarvis.core.events import EventType, get_event_bus
from openjarvis.core.registry import MemoryRegistry
from openjarvis.tools.storage._stubs import MemoryBackend, RetrievalResult

BACKEND_ID = "crisvis_sqlite"

_STOP = frozenset(
    """
    a al algo como con de del el ella ellos en es esa ese eso esta este esto fue ha han hay la las
    le les lo los me mi mis muy nos o para pero por que qué se si sí sin su sus te tu tus un una
    uno unos y ya yo the and for are was you your what with this that have from
    """.split()
)


def fts_query(text: str) -> str:
    terms = [t for t in re.findall(r"\w+", text.lower()) if len(t) > 1 and t not in _STOP]
    return " OR ".join(f'"{t}"*' for t in dict.fromkeys(terms))


if not MemoryRegistry.contains(BACKEND_ID):

    @MemoryRegistry.register(BACKEND_ID)
    class CrisvisMemory(MemoryBackend):
        backend_id = BACKEND_ID

        def __init__(self, db_path: str | Path = "") -> None:
            if not db_path:
                from openjarvis.core.config import DEFAULT_CONFIG_DIR

                db_path = DEFAULT_CONFIG_DIR / "memoria_crisvis.db"
            self._path = Path(db_path)
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._lock = threading.Lock()
            self._conn = sqlite3.connect(str(self._path), check_same_thread=False)
            self._conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS documents (
                    id TEXT PRIMARY KEY,
                    content TEXT NOT NULL,
                    source TEXT NOT NULL DEFAULT '',
                    metadata TEXT NOT NULL DEFAULT '{}',
                    created_at REAL NOT NULL
                );
                CREATE VIRTUAL TABLE IF NOT EXISTS documents_fts USING fts5(
                    id UNINDEXED, content, source,
                    tokenize = 'unicode61 remove_diacritics 2'
                );
                """
            )
            self._conn.commit()

        def store(
            self, content: str, *, source: str = "", metadata: dict[str, Any] | None = None
        ) -> str:
            doc_id = uuid.uuid4().hex
            with self._lock:
                self._conn.execute(
                    "INSERT INTO documents VALUES (?, ?, ?, ?, ?)",
                    (doc_id, content, source, json.dumps(metadata or {}), time.time()),
                )
                self._conn.execute(
                    "INSERT INTO documents_fts (id, content, source) VALUES (?, ?, ?)",
                    (doc_id, content, source),
                )
                self._conn.commit()
            get_event_bus().publish(
                EventType.MEMORY_STORE, {"backend": BACKEND_ID, "doc_id": doc_id, "source": source}
            )
            return doc_id

        def retrieve(self, query: str, *, top_k: int = 5, **kwargs: Any) -> list[RetrievalResult]:
            match = fts_query(query)
            if not match:
                return []
            with self._lock:
                rows = self._conn.execute(
                    """
                    SELECT d.content, d.source, d.metadata, bm25(documents_fts) AS rank
                    FROM documents_fts JOIN documents d ON d.id = documents_fts.id
                    WHERE documents_fts MATCH ? ORDER BY rank LIMIT ?
                    """,
                    (match, max(1, int(top_k))),
                ).fetchall()
            results = [
                RetrievalResult(
                    content=content,
                    score=round(-rank, 4),
                    source=source,
                    metadata=json.loads(meta or "{}"),
                )
                for content, source, meta, rank in rows
            ]
            get_event_bus().publish(
                EventType.MEMORY_RETRIEVE,
                {"backend": BACKEND_ID, "query": query, "num_results": len(results)},
            )
            return results

        def delete(self, doc_id: str) -> bool:
            with self._lock:
                cur = self._conn.execute("DELETE FROM documents WHERE id = ?", (doc_id,))
                self._conn.execute("DELETE FROM documents_fts WHERE id = ?", (doc_id,))
                self._conn.commit()
            return cur.rowcount > 0

        def clear(self) -> None:
            with self._lock:
                self._conn.execute("DELETE FROM documents")
                self._conn.execute("DELETE FROM documents_fts")
                self._conn.commit()

        def count(self) -> int:
            with self._lock:
                return int(self._conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0])

        def close(self) -> None:
            with self._lock:
                self._conn.close()


def native_available() -> bool:
    try:
        from openjarvis._rust_bridge import get_rust_module

        get_rust_module()
        return True
    except Exception:  # noqa: BLE001
        return False
