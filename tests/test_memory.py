from pathlib import Path

from openjarvis.core.registry import MemoryRegistry

from crisvis.brain.memory import BACKEND_ID, fts_query


def make(tmp_path: Path):
    return MemoryRegistry.get(BACKEND_ID)(db_path=tmp_path / "m.db")


def test_fts_query_drops_stopwords_and_dedupes() -> None:
    assert fts_query("¿Qué te dije de la reunión con Ana, la reunión?") == (
        '"dije"* OR "reunión"* OR "ana"*'
    )
    assert fts_query("de la que") == ""


def test_store_and_retrieve_ignores_accents(tmp_path: Path) -> None:
    mem = make(tmp_path)
    try:
        mem.store("La reunión con Ana es el jueves a las diez", source="usuario")
        mem.store("Me gusta el café solo", source="usuario")
        hits = mem.retrieve("reunion ana")
        assert hits and "Ana" in hits[0].content
        assert hits[0].source == "usuario"
        assert mem.count() == 2
    finally:
        mem.close()


def test_prefix_match_and_delete(tmp_path: Path) -> None:
    mem = make(tmp_path)
    try:
        doc = mem.store("Contraseña del wifi: está en la nevera")
        assert mem.retrieve("contra")
        assert mem.retrieve("contrasena")
        assert mem.delete(doc)
        assert mem.retrieve("contra") == []
        assert mem.count() == 0
    finally:
        mem.close()


def test_persists_across_instances(tmp_path: Path) -> None:
    mem = make(tmp_path)
    mem.store("El coche está en la plaza 42")
    mem.close()
    again = make(tmp_path)
    try:
        assert again.retrieve("coche")
    finally:
        again.close()
