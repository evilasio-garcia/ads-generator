import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from mercadolivre_category_tree import (
    _normalize,
    search_categories,
    set_tree,
    get_tree,
    get_tree_status,
    _build_path,
)


# ── Sample tree for tests ────────────────────────────────────────────────────

SAMPLE_TREE = {
    "MLB1000": {
        "id": "MLB1000",
        "name": "Pet Shop",
        "path": "Pet Shop",
        "children": ["MLB1001", "MLB1002"],
        "leaf": False,
    },
    "MLB1001": {
        "id": "MLB1001",
        "name": "Aves e Acessórios",
        "path": "Pet Shop > Aves e Acessórios",
        "children": ["MLB1003"],
        "leaf": False,
    },
    "MLB1002": {
        "id": "MLB1002",
        "name": "Cães",
        "path": "Pet Shop > Cães",
        "children": ["MLB1004", "MLB1005"],
        "leaf": False,
    },
    "MLB1003": {
        "id": "MLB1003",
        "name": "Ração para Aves",
        "path": "Pet Shop > Aves e Acessórios > Ração para Aves",
        "children": [],
        "leaf": True,
    },
    "MLB1004": {
        "id": "MLB1004",
        "name": "Ração para Cães",
        "path": "Pet Shop > Cães > Ração para Cães",
        "children": [],
        "leaf": True,
    },
    "MLB1005": {
        "id": "MLB1005",
        "name": "Tapetes Higiênicos",
        "path": "Pet Shop > Cães > Tapetes Higiênicos",
        "children": [],
        "leaf": True,
    },
    "MLB2000": {
        "id": "MLB2000",
        "name": "Casa e Decoração",
        "path": "Casa e Decoração",
        "children": ["MLB2001"],
        "leaf": False,
    },
    "MLB2001": {
        "id": "MLB2001",
        "name": "Cama Box",
        "path": "Casa e Decoração > Cama Box",
        "children": [],
        "leaf": True,
    },
}


# ── Normalize ─────────────────────────────────────────────────────────────────


def test_normalize_accents():
    assert _normalize("Ração") == "racao"


def test_normalize_case():
    assert _normalize("PET SHOP") == "pet shop"


def test_normalize_combined():
    assert _normalize("Tapetes Higiênicos") == "tapetes higienicos"


# ── Build path ────────────────────────────────────────────────────────────────


def test_build_path_simple():
    path = _build_path([{"name": "Root"}, {"name": "Child"}, {"name": "Leaf"}])
    assert path == "Root > Child > Leaf"


def test_build_path_empty():
    assert _build_path([]) == ""


# ── Tree status ───────────────────────────────────────────────────────────────


def test_tree_status_after_set():
    set_tree(SAMPLE_TREE)
    assert get_tree_status() == "ready"
    assert get_tree() is SAMPLE_TREE


# ── Search — basic ────────────────────────────────────────────────────────────


def test_search_exact_name():
    set_tree(SAMPLE_TREE)
    result = search_categories("Tapetes Higiênicos")
    names = [r["name"] for r in result["results"]]
    assert "Tapetes Higiênicos" in names


def test_search_fuzzy_no_accent():
    """Search without accents should still find results."""
    set_tree(SAMPLE_TREE)
    result = search_categories("racao")
    names = [r["name"] for r in result["results"]]
    assert any("Ração" in n for n in names)


def test_search_fuzzy_partial():
    """Partial term should find matching categories."""
    set_tree(SAMPLE_TREE)
    result = search_categories("aves")
    names = [r["name"] for r in result["results"]]
    assert any("Aves" in n for n in names)


def test_search_by_path_context():
    """Searching 'pet shop racao' should find ração categories via path."""
    set_tree(SAMPLE_TREE)
    result = search_categories("pet shop racao")
    ids = [r["id"] for r in result["results"]]
    assert "MLB1003" in ids or "MLB1004" in ids


def test_search_empty_query():
    set_tree(SAMPLE_TREE)
    result = search_categories("")
    assert result["results"] == []
    assert result["total_found"] == 0


def test_search_no_tree():
    """Search with no tree loaded returns empty."""
    import mercadolivre_category_tree
    mercadolivre_category_tree._tree_cache = None
    mercadolivre_category_tree._tree_status = "unavailable"
    result = search_categories("anything")
    assert result["results"] == []
    # Restore
    set_tree(SAMPLE_TREE)


# ── Search — limit and has_more ───────────────────────────────────────────────


def test_search_limit():
    set_tree(SAMPLE_TREE)
    result = search_categories("Pet", limit=2)
    assert result["showing"] <= 2
    if result["total_found"] > 2:
        assert result["has_more"] is True


def test_search_no_limit():
    """limit=0 returns all results."""
    set_tree(SAMPLE_TREE)
    result = search_categories("Pet", limit=0)
    assert result["showing"] == result["total_found"]
    assert result["has_more"] is False


# ── Search — result structure ─────────────────────────────────────────────────


def test_search_result_has_path():
    set_tree(SAMPLE_TREE)
    result = search_categories("Cama Box")
    assert len(result["results"]) > 0
    first = result["results"][0]
    assert "id" in first
    assert "name" in first
    assert "path" in first
    assert first["path"] == "Casa e Decoração > Cama Box"
