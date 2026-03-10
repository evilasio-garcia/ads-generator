# ML Category Sanity Check — Plano de Implementação

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Detectar mudanças nos atributos obrigatórios de categorias ML antes de publicar, auto-injetar atributos hidden-writable (SELLER_PACKAGE_*), e alertar o time quando campos obrigatórios não podem ser preenchidos.

**Architecture:** Nova tabela `ml_category_baseline` armazena snapshot dos atributos required/conditional/hidden-writable por categoria. No fluxo de publicação, um novo step `validate_category` (entre token_refresh e montagem de payload) consulta a API ML, compara com o baseline, e decide se prossegue, auto-resolve, ou aborta. Frontend exibe resultado no painel SSE com botão de notificação WhatsApp para erros.

**Tech Stack:** SQLAlchemy + PostgreSQL (JSONB), Alembic migration, FastAPI SSE, vanilla JS frontend.

**Design doc:** `docs/plans/2026-03-10-ml-category-sanity-check-design.md`

---

## Task 1: Migração Alembic — tabela `ml_category_baseline`

**Files:**
- Create: `alembic/versions/rev_007_ml_category_baseline.py`

**Step 1: Criar migração**

```python
# alembic/versions/rev_007_ml_category_baseline.py
"""add ml_category_baseline table

Revision ID: rev_007_ml_category_baseline
Revises: rev_006_tiny_kit_resolution_cache
Create Date: 2026-03-10 10:00:00
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "rev_007_ml_category_baseline"
down_revision = "rev_006_tiny_kit_resolution_cache"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ml_category_baseline",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.String(), nullable=False),
        sa.Column("category_id", sa.String(), nullable=False),
        sa.Column("required_attr_ids", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("conditional_attr_ids", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("hidden_writable_attr_ids", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("full_snapshot", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.text("now()")),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "category_id", name="ux_ml_category_baseline_user_cat"),
    )
    op.create_index(op.f("ix_ml_category_baseline_user_id"), "ml_category_baseline", ["user_id"], unique=False)
    op.create_index(op.f("ix_ml_category_baseline_category_id"), "ml_category_baseline", ["category_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_ml_category_baseline_category_id"), table_name="ml_category_baseline")
    op.drop_index(op.f("ix_ml_category_baseline_user_id"), table_name="ml_category_baseline")
    op.drop_table("ml_category_baseline")
```

**Step 2: Rodar migração**

Run: `cd c:\Users\evila\PycharmProjects\adsGenerator && alembic upgrade head`
Expected: `rev_007_ml_category_baseline` aplicada com sucesso.

**Step 3: Commit**

```bash
git add alembic/versions/rev_007_ml_category_baseline.py
git commit -m "feat(db): add ml_category_baseline table for category sanity check"
```

---

## Task 2: Model SQLAlchemy — `MlCategoryBaseline`

**Files:**
- Modify: `app.py:118` (antes da classe `TinyKitResolution`)
- Modify: `app.py:150` (adicionar tabela à lista de `_ensure_schema_ready`)

**Step 1: Escrever teste de modelo**

Não é necessário teste unitário isolado para modelos SQLAlchemy declarativos — a validação vem pela migração (Task 1) e pelos testes de integração (Tasks 4-5).

**Step 2: Adicionar modelo em `app.py`**

Inserir ANTES da classe `TinyKitResolution` (linha 118):

```python
class MlCategoryBaseline(Base):
    __tablename__ = "ml_category_baseline"
    __table_args__ = (
        UniqueConstraint("user_id", "category_id", name="ux_ml_category_baseline_user_cat"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(String, index=True, nullable=False)
    category_id = Column(String, index=True, nullable=False)
    required_attr_ids = Column(JSONB, nullable=False, default=list)
    conditional_attr_ids = Column(JSONB, nullable=False, default=list)
    hidden_writable_attr_ids = Column(JSONB, nullable=False, default=list)
    full_snapshot = Column(JSONB, nullable=False, default=list)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )
```

**Step 3: Atualizar `_ensure_schema_ready`**

Na linha 150, adicionar `"ml_category_baseline"` ao set `required`:

```python
required = {"alembic_version", "user_config", "sku_workspace", "sku_workspace_history", "tiny_kit_resolution", "ml_category_baseline"}
```

**Step 4: Rodar testes para verificar que nada quebrou**

Run: `python -m pytest tests/ -x -q`
Expected: 79 passed

**Step 5: Commit**

```bash
git add app.py
git commit -m "feat(model): add MlCategoryBaseline SQLAlchemy model"
```

---

## Task 3: Função pura `_validate_category_attributes` em `app.py`

Esta é a lógica central do sanity check — uma função pura (sem DB, sem I/O) que recebe os dados e retorna o resultado da validação.

**Files:**
- Create: `tests/test_ml_category_sanity.py`
- Modify: `app.py` (adicionar função após `_build_pricing_ctx_for_ml`, ~linha 4012)

**Step 1: Escrever testes**

Criar `tests/test_ml_category_sanity.py`:

```python
# tests/test_ml_category_sanity.py
import os
import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import pytest
from app import _validate_category_attributes


# ── Fixtures ──────────────────────────────────────────────────────────────────

def _make_attrs(ids_and_tags: list[tuple]) -> list:
    """Helper: cria lista de atributos ML a partir de [(id, tags_dict), ...]"""
    return [
        {"id": aid, "name": aid.replace("_", " ").title(), "tags": tags}
        for aid, tags in ids_and_tags
    ]


def _make_baseline(required=None, conditional=None, hidden_writable=None):
    """Helper: simula um registro de baseline."""
    return {
        "required_attr_ids": required or [],
        "conditional_attr_ids": conditional or [],
        "hidden_writable_attr_ids": hidden_writable or [],
    }


# ── Cenário A: Primeira publicação, todos os required preenchidos ─────────

def test_first_publish_all_required_filled():
    ml_api_attrs = _make_attrs([
        ("BRAND", {"required": True}),
        ("MODEL", {"required": True}),
        ("COLOR", {}),
    ])
    ui_ml_attributes = [
        {"id": "BRAND", "value_name": "Nike"},
        {"id": "MODEL", "value_name": "Air Max"},
    ]
    result = _validate_category_attributes(
        ml_api_attrs=ml_api_attrs,
        baseline=None,
        ui_ml_attributes=ui_ml_attributes,
        ui_dimensions={"height_cm": 10, "width_cm": 20, "length_cm": 30, "weight_kg": 0.5},
    )
    assert result["status"] == "ok"
    assert result["is_first_publish"] is True
    assert set(result["new_baseline"]["required_attr_ids"]) == {"BRAND", "MODEL"}


# ── Cenário B: Baseline existe, sem mudanças ──────────────────────────────

def test_baseline_exists_no_change():
    ml_api_attrs = _make_attrs([
        ("BRAND", {"required": True}),
        ("MODEL", {"required": True}),
    ])
    baseline = _make_baseline(required=["BRAND", "MODEL"])
    result = _validate_category_attributes(
        ml_api_attrs=ml_api_attrs,
        baseline=baseline,
        ui_ml_attributes=[{"id": "BRAND", "value_name": "X"}, {"id": "MODEL", "value_name": "Y"}],
        ui_dimensions={"height_cm": 10, "width_cm": 20, "length_cm": 30, "weight_kg": 0.5},
    )
    assert result["status"] == "ok"
    assert result["is_first_publish"] is False
    assert result["added"] == []
    assert result["removed"] == []


# ── Cenário C: Mudança detectada, auto-resolvível (SELLER_PACKAGE_*) ─────

def test_change_detected_auto_resolvable_seller_package():
    ml_api_attrs = _make_attrs([
        ("BRAND", {"required": True}),
        ("MODEL", {"required": True}),
        ("SELLER_PACKAGE_HEIGHT", {"hidden": True}),
        ("SELLER_PACKAGE_WIDTH", {"hidden": True}),
        ("SELLER_PACKAGE_LENGTH", {"hidden": True}),
        ("SELLER_PACKAGE_WEIGHT", {"hidden": True}),
    ])
    baseline = _make_baseline(
        required=["BRAND", "MODEL"],
        hidden_writable=[],  # antes não tinha SELLER_PACKAGE_*
    )
    result = _validate_category_attributes(
        ml_api_attrs=ml_api_attrs,
        baseline=baseline,
        ui_ml_attributes=[{"id": "BRAND", "value_name": "X"}, {"id": "MODEL", "value_name": "Y"}],
        ui_dimensions={"height_cm": 10, "width_cm": 20, "length_cm": 30, "weight_kg": 0.5},
    )
    assert result["status"] == "ok"
    assert len(result["auto_injected"]) == 4
    injected_ids = {a["id"] for a in result["auto_injected"]}
    assert injected_ids == {"SELLER_PACKAGE_HEIGHT", "SELLER_PACKAGE_WIDTH", "SELLER_PACKAGE_LENGTH", "SELLER_PACKAGE_WEIGHT"}


# ── Cenário D: Mudança detectada, NÃO resolvível (dimensões ausentes) ────

def test_change_detected_not_resolvable_missing_dimensions():
    ml_api_attrs = _make_attrs([
        ("BRAND", {"required": True}),
        ("MODEL", {"required": True}),
        ("SELLER_PACKAGE_HEIGHT", {"hidden": True}),
        ("SELLER_PACKAGE_WIDTH", {"hidden": True}),
        ("SELLER_PACKAGE_LENGTH", {"hidden": True}),
        ("SELLER_PACKAGE_WEIGHT", {"hidden": True}),
    ])
    baseline = _make_baseline(
        required=["BRAND", "MODEL"],
        hidden_writable=[],
    )
    result = _validate_category_attributes(
        ml_api_attrs=ml_api_attrs,
        baseline=baseline,
        ui_ml_attributes=[{"id": "BRAND", "value_name": "X"}, {"id": "MODEL", "value_name": "Y"}],
        ui_dimensions={"height_cm": 0, "width_cm": 0, "length_cm": 0, "weight_kg": 0},
    )
    assert result["status"] == "error"
    assert len(result["missing_attrs"]) == 4


# ── Cenário E: Required removido do ML ────────────────────────────────────

def test_required_removed_from_category():
    ml_api_attrs = _make_attrs([
        ("BRAND", {"required": True}),
        # MODEL não é mais required
    ])
    baseline = _make_baseline(required=["BRAND", "MODEL"])
    result = _validate_category_attributes(
        ml_api_attrs=ml_api_attrs,
        baseline=baseline,
        ui_ml_attributes=[{"id": "BRAND", "value_name": "X"}],
        ui_dimensions={"height_cm": 10, "width_cm": 20, "length_cm": 30, "weight_kg": 0.5},
    )
    assert result["status"] == "ok"
    assert "MODEL" in result["removed"]


# ── Cenário F: Novo required adicionado, UI já tem o valor ───────────────

def test_new_required_already_in_ui():
    ml_api_attrs = _make_attrs([
        ("BRAND", {"required": True}),
        ("MODEL", {"required": True}),
        ("GTIN", {"required": True}),  # novo required
    ])
    baseline = _make_baseline(required=["BRAND", "MODEL"])
    result = _validate_category_attributes(
        ml_api_attrs=ml_api_attrs,
        baseline=baseline,
        ui_ml_attributes=[
            {"id": "BRAND", "value_name": "X"},
            {"id": "MODEL", "value_name": "Y"},
            {"id": "GTIN", "value_name": "1234567890123"},
        ],
        ui_dimensions={"height_cm": 10, "width_cm": 20, "length_cm": 30, "weight_kg": 0.5},
    )
    assert result["status"] == "ok"
    assert "GTIN" in result["added"]


# ── Cenário G: Novo required adicionado, UI NÃO tem o valor ──────────────

def test_new_required_missing_from_ui():
    ml_api_attrs = _make_attrs([
        ("BRAND", {"required": True}),
        ("MODEL", {"required": True}),
        ("GTIN", {"required": True}),
    ])
    baseline = _make_baseline(required=["BRAND", "MODEL"])
    result = _validate_category_attributes(
        ml_api_attrs=ml_api_attrs,
        baseline=baseline,
        ui_ml_attributes=[
            {"id": "BRAND", "value_name": "X"},
            {"id": "MODEL", "value_name": "Y"},
        ],
        ui_dimensions={"height_cm": 10, "width_cm": 20, "length_cm": 30, "weight_kg": 0.5},
    )
    assert result["status"] == "error"
    assert any(a["id"] == "GTIN" for a in result["missing_attrs"])


# ── Auto-injeção de SELLER_PACKAGE_* com valores corretos ────────────────

def test_auto_inject_seller_package_values():
    ml_api_attrs = _make_attrs([
        ("SELLER_PACKAGE_HEIGHT", {"hidden": True}),
        ("SELLER_PACKAGE_WIDTH", {"hidden": True}),
        ("SELLER_PACKAGE_LENGTH", {"hidden": True}),
        ("SELLER_PACKAGE_WEIGHT", {"hidden": True}),
    ])
    result = _validate_category_attributes(
        ml_api_attrs=ml_api_attrs,
        baseline=None,
        ui_ml_attributes=[],
        ui_dimensions={"height_cm": 15, "width_cm": 25, "length_cm": 35, "weight_kg": 1.2},
    )
    assert result["status"] == "ok"
    injected = {a["id"]: a["value_name"] for a in result["auto_injected"]}
    assert injected["SELLER_PACKAGE_HEIGHT"] == "15"
    assert injected["SELLER_PACKAGE_WIDTH"] == "25"
    assert injected["SELLER_PACKAGE_LENGTH"] == "35"
    assert injected["SELLER_PACKAGE_WEIGHT"] == "1200"  # kg * 1000 = gramas
```

**Step 2: Rodar testes para verificar que falham**

Run: `python -m pytest tests/test_ml_category_sanity.py -v`
Expected: FAIL com `ImportError: cannot import name '_validate_category_attributes' from 'app'`

**Step 3: Implementar `_validate_category_attributes` em `app.py`**

Inserir após `_build_pricing_ctx_for_ml` (~linha 4012, antes de `_run_ml_publish_job`):

```python
# ── Category sanity check (pure function) ─────────────────────────────────

# Mapeamento de atributos hidden-writable → campo da UI que os preenche
_HIDDEN_WRITABLE_AUTO_FILL = {
    "SELLER_PACKAGE_HEIGHT": ("height_cm", 1),      # cm → cm (str)
    "SELLER_PACKAGE_WIDTH":  ("width_cm",  1),       # cm → cm (str)
    "SELLER_PACKAGE_LENGTH": ("length_cm", 1),       # cm → cm (str)
    "SELLER_PACKAGE_WEIGHT": ("weight_kg", 1000),    # kg → g (str)
}


def _validate_category_attributes(
    *,
    ml_api_attrs: list,
    baseline: Optional[dict],
    ui_ml_attributes: list,
    ui_dimensions: dict,
) -> dict:
    """Compare current ML category attributes against saved baseline.

    Returns dict with:
      status: "ok" | "error"
      is_first_publish: bool
      added: list of attr IDs added as required since baseline
      removed: list of attr IDs removed from required since baseline
      auto_injected: list of {"id": ..., "value_name": ...} attrs auto-filled
      missing_attrs: list of {"id": ..., "name": ...} attrs that cannot be filled
      new_baseline: dict with updated baseline data (only if status == "ok")
    """
    # 1. Classify current ML attributes
    current_required = []
    current_conditional = []
    current_hidden_writable = []
    for attr in ml_api_attrs:
        tags = attr.get("tags") or {}
        aid = attr.get("id", "")
        if tags.get("required") or tags.get("catalog_required"):
            current_required.append(aid)
        if tags.get("conditional_required"):
            current_conditional.append(aid)
        if tags.get("hidden") and not tags.get("read_only"):
            current_hidden_writable.append(aid)

    # 2. Build set of IDs the UI already provides
    ui_attr_ids = {a["id"] for a in ui_ml_attributes if a.get("value_name")}

    # 3. Calculate diff against baseline
    is_first = baseline is None
    if is_first:
        prev_required = set()
        prev_hidden_writable = set()
    else:
        prev_required = set(baseline.get("required_attr_ids") or [])
        prev_hidden_writable = set(baseline.get("hidden_writable_attr_ids") or [])

    current_required_set = set(current_required)
    current_hw_set = set(current_hidden_writable)

    added = sorted(current_required_set - prev_required)
    removed = sorted(prev_required - current_required_set)

    # 4. Auto-inject hidden-writable attributes from UI dimensions
    auto_injected = []
    for hw_id in current_hidden_writable:
        if hw_id in ui_attr_ids:
            continue  # already provided by the user
        fill = _HIDDEN_WRITABLE_AUTO_FILL.get(hw_id)
        if fill:
            field_name, multiplier = fill
            raw_val = float(ui_dimensions.get(field_name) or 0)
            if raw_val > 0:
                auto_injected.append({
                    "id": hw_id,
                    "value_name": str(int(raw_val * multiplier)),
                })

    auto_injected_ids = {a["id"] for a in auto_injected}

    # 5. Check which required attrs are missing (not in UI AND not auto-injected)
    all_provided = ui_attr_ids | auto_injected_ids
    missing_attrs = []
    # Check explicit required
    for attr in ml_api_attrs:
        tags = attr.get("tags") or {}
        aid = attr.get("id", "")
        if (tags.get("required") or tags.get("catalog_required")) and aid not in all_provided:
            missing_attrs.append({"id": aid, "name": attr.get("name", aid)})

    # Check hidden-writable that couldn't be auto-filled
    for hw_id in current_hidden_writable:
        if hw_id not in all_provided:
            attr_name = next((a.get("name", hw_id) for a in ml_api_attrs if a.get("id") == hw_id), hw_id)
            missing_attrs.append({"id": hw_id, "name": attr_name})

    if missing_attrs:
        return {
            "status": "error",
            "is_first_publish": is_first,
            "added": added,
            "removed": removed,
            "auto_injected": auto_injected,
            "missing_attrs": missing_attrs,
            "new_baseline": None,
        }

    # 6. Build updated baseline snapshot
    full_snapshot = [
        {"id": a.get("id"), "name": a.get("name"), "tags": a.get("tags") or {}}
        for a in ml_api_attrs
    ]
    new_baseline = {
        "required_attr_ids": current_required,
        "conditional_attr_ids": current_conditional,
        "hidden_writable_attr_ids": current_hidden_writable,
        "full_snapshot": full_snapshot,
    }

    return {
        "status": "ok",
        "is_first_publish": is_first,
        "added": added,
        "removed": removed,
        "auto_injected": auto_injected,
        "missing_attrs": [],
        "new_baseline": new_baseline,
    }
```

**Step 4: Rodar testes**

Run: `python -m pytest tests/test_ml_category_sanity.py -v`
Expected: 8 passed

**Step 5: Rodar todos os testes**

Run: `python -m pytest tests/ -x -q`
Expected: 87 passed (79 existentes + 8 novos)

**Step 6: Commit**

```bash
git add tests/test_ml_category_sanity.py app.py
git commit -m "feat(ml-publish): add _validate_category_attributes pure function with tests"
```

---

## Task 4: Integrar step `validate_category` no fluxo de publicação

**Files:**
- Modify: `app.py` — dentro de `_run_ml_publish_job` (entre step 1 e step "2. Montar payload")

**Step 1: Adicionar step `validate_category` no job**

Inserir APÓS o step 1 (token_refresh, ~linha 4071) e ANTES do `# ── 2. Montar payload` (~linha 4072):

```python
        # ── 1b. Validar estrutura da categoria ML ────────────────────────
        category_id_raw = str((workspace.get("base_state") or {}).get("product_fields", {}).get("ml_category_id") or "")
        if category_id_raw:
            _emit_ml_event(job_id, "validate_category", "Validando atributos obrigatórios da categoria...")
            try:
                ml_api_attrs = await mercadolivre_service.get_category_attributes(access_token, category_id_raw)
            except mercadolivre_service.MLAPIError as exc:
                _emit_ml_event(job_id, "warning", f"Não foi possível validar categoria: {exc}")
                ml_api_attrs = None

            if ml_api_attrs is not None:
                # Load baseline from DB
                def _load_baseline():
                    db_s = SessionLocal()
                    try:
                        row = db_s.query(MlCategoryBaseline).filter(
                            MlCategoryBaseline.user_id == db_user_id,
                            MlCategoryBaseline.category_id == category_id_raw,
                        ).first()
                        if row:
                            return {
                                "required_attr_ids": row.required_attr_ids or [],
                                "conditional_attr_ids": row.conditional_attr_ids or [],
                                "hidden_writable_attr_ids": row.hidden_writable_attr_ids or [],
                            }
                        return None
                    finally:
                        db_s.close()

                baseline = await asyncio.to_thread(_load_baseline)

                fields_pre = (workspace.get("base_state") or {}).get("product_fields", {})
                ui_ml_attributes_pre = list(fields_pre.get("ml_attributes") or [])
                ui_dimensions = {
                    "height_cm": float(fields_pre.get("height_cm") or 0),
                    "width_cm": float(fields_pre.get("width_cm") or 0),
                    "length_cm": float(fields_pre.get("length_cm") or 0),
                    "weight_kg": float(fields_pre.get("weight_kg") or 0),
                }

                validation = _validate_category_attributes(
                    ml_api_attrs=ml_api_attrs,
                    baseline=baseline,
                    ui_ml_attributes=ui_ml_attributes_pre,
                    ui_dimensions=ui_dimensions,
                )

                if validation["status"] == "error":
                    missing_names = ", ".join(a["name"] for a in validation["missing_attrs"])
                    missing_ids = ", ".join(a["id"] for a in validation["missing_attrs"])
                    _emit_ml_event(
                        job_id, "category_validation_failed",
                        f"Atributos obrigatórios sem valor: {missing_names}",
                        failed_at="validate_category",
                        missing_attrs=validation["missing_attrs"],
                        added=validation["added"],
                        removed=validation["removed"],
                        category_id=category_id_raw,
                        sku=sku_normalized,
                    )
                    return

                # Auto-inject hidden-writable attrs into workspace ml_attributes
                if validation["auto_injected"]:
                    existing_ml_attrs = list(fields_pre.get("ml_attributes") or [])
                    injected_ids = {a["id"] for a in validation["auto_injected"]}
                    existing_ml_attrs = [a for a in existing_ml_attrs if a.get("id") not in injected_ids]
                    existing_ml_attrs.extend(validation["auto_injected"])
                    # Mutate workspace so downstream code sees injected attrs
                    base_mut = workspace.get("base_state") or {}
                    pf_mut = dict(base_mut.get("product_fields") or {})
                    pf_mut["ml_attributes"] = existing_ml_attrs
                    base_mut["product_fields"] = pf_mut
                    workspace["base_state"] = base_mut

                # Save/update baseline
                if validation["new_baseline"]:
                    nb = validation["new_baseline"]
                    def _save_baseline():
                        db_s = SessionLocal()
                        try:
                            row = db_s.query(MlCategoryBaseline).filter(
                                MlCategoryBaseline.user_id == db_user_id,
                                MlCategoryBaseline.category_id == category_id_raw,
                            ).first()
                            if row:
                                row.required_attr_ids = nb["required_attr_ids"]
                                row.conditional_attr_ids = nb["conditional_attr_ids"]
                                row.hidden_writable_attr_ids = nb["hidden_writable_attr_ids"]
                                row.full_snapshot = nb["full_snapshot"]
                            else:
                                row = MlCategoryBaseline(
                                    user_id=db_user_id,
                                    category_id=category_id_raw,
                                    required_attr_ids=nb["required_attr_ids"],
                                    conditional_attr_ids=nb["conditional_attr_ids"],
                                    hidden_writable_attr_ids=nb["hidden_writable_attr_ids"],
                                    full_snapshot=nb["full_snapshot"],
                                )
                                db_s.add(row)
                            db_s.commit()
                        finally:
                            db_s.close()
                    await asyncio.to_thread(_save_baseline)

                # Emit appropriate SSE message
                if validation["is_first_publish"]:
                    n_req = len(validation["new_baseline"]["required_attr_ids"])
                    _emit_ml_event(
                        job_id, "validate_category",
                        f"Primeira publicação nesta categoria — estrutura salva ({n_req} obrigatórios)",
                    )
                elif validation["added"] or validation["removed"]:
                    parts = []
                    if validation["added"]:
                        parts.append(f"+{len(validation['added'])} obrigatórios ({', '.join(validation['added'])})")
                    if validation["removed"]:
                        parts.append(f"-{len(validation['removed'])} removidos ({', '.join(validation['removed'])})")
                    auto_msg = ""
                    if validation["auto_injected"]:
                        auto_msg = f" — {len(validation['auto_injected'])} preenchidos automaticamente"
                    _emit_ml_event(
                        job_id, "validate_category",
                        f"Estrutura da categoria mudou! {'; '.join(parts)}{auto_msg}",
                    )
                else:
                    n_req = len(validation["new_baseline"]["required_attr_ids"])
                    _emit_ml_event(
                        job_id, "validate_category",
                        f"Categoria validada — {n_req} atributos obrigatórios verificados",
                    )
```

**Step 2: Rodar todos os testes**

Run: `python -m pytest tests/ -x -q`
Expected: 87 passed

**Step 3: Commit**

```bash
git add app.py
git commit -m "feat(ml-publish): integrate validate_category step in publish flow"
```

---

## Task 5: Frontend — step `validate_category` e erro no painel SSE

**Files:**
- Modify: `static/main.html` — `ML_STEP_ORDER`, `ML_STEP_LABELS`, `handleEvent`

**Step 1: Adicionar step à lista e labels**

Em `ML_STEP_LABELS` (~linha 9706), adicionar após `token_refresh`:

```javascript
validate_category: 'Validando atributos da categoria...',
```

Em `ML_STEP_ORDER` (~linha 9720), adicionar `'validate_category'` após `'token_refresh'`:

```javascript
const ML_STEP_ORDER = [
    'token_refresh', 'validate_category', 'downloading_images', 'uploading_images',
    'creating_listing', 'checking_freight',
    'adjusting_price', 'updating_listing', 'notifying_whatsapp',
    'activating',
];
```

**Step 2: Adicionar handler para `category_validation_failed` no `handleEvent`**

Inserir logo após o handler `freight_updated` e antes do `if (step === 'done')` (~linha 9940):

```javascript
if (step === 'category_validation_failed') {
  // Mark validate_category as failed
  mlPanelStepStates['validate_category'] = 'failed';
  mlRenderSteps('validate_category', true);

  const header = document.getElementById('publishPanelHeader');
  if (header) {
    header.className = 'publish-panel-header error';
    const loader = document.getElementById('publishPanelLoader');
    if (loader) loader.style.display = 'none';
    document.getElementById('publishPanelTitle').textContent = 'Validação de categoria falhou';
  }

  const missingList = (data.missing_attrs || [])
    .map(a => `<li><strong>${a.id}</strong> — ${a.name}</li>`)
    .join('');
  const addedList = (data.added || []).join(', ') || 'nenhum';
  const removedList = (data.removed || []).join(', ') || 'nenhum';

  const footer = document.getElementById('publishPanelFooter');
  if (footer) {
    footer.style.display = '';
    footer.innerHTML = `
      <div style="text-align:left; font-size:0.82rem; line-height:1.5; margin-bottom:0.75rem;">
        <p style="margin:0 0 0.5rem;"><strong>Atributos obrigatórios sem valor:</strong></p>
        <ul style="margin:0 0 0.5rem; padding-left:1.2rem;">${missingList}</ul>
        <p style="margin:0; font-size:0.75rem; opacity:0.7;">
          Novos: ${addedList} | Removidos: ${removedList}
        </p>
      </div>
      <div style="display:flex; gap:0.5rem;">
        <button id="btnMlNotifyDevs" class="btn text-xs" style="flex:1;
          background:var(--color-warning, #f59e0b); color:#000; font-weight:600;">
          Notificar Time de Dev
        </button>
        <button id="btnMlPanelClose" class="btn text-xs" style="flex:1;">Fechar</button>
      </div>
    `;

    const closeBtn = footer.querySelector('#btnMlPanelClose');
    if (closeBtn) closeBtn.addEventListener('click', () => {
      mlHidePanel();
      if (btnPublishMl) btnPublishMl.disabled = false;
    });

    const notifyBtn = footer.querySelector('#btnMlNotifyDevs');
    if (notifyBtn) notifyBtn.addEventListener('click', async () => {
      notifyBtn.disabled = true;
      notifyBtn.textContent = 'Enviando...';
      try {
        const resp = await fetchWithAuth('/api/ml/notify-category-change', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({
            category_id: data.category_id,
            sku: data.sku,
            missing_attrs: data.missing_attrs,
            added: data.added,
            removed: data.removed,
          }),
        });
        if (resp.ok) {
          notifyBtn.textContent = 'Enviado!';
          showToast('Notificação enviada ao time de dev', 'success');
        } else {
          notifyBtn.textContent = 'Erro ao enviar';
          notifyBtn.disabled = false;
        }
      } catch {
        notifyBtn.textContent = 'Erro ao enviar';
        notifyBtn.disabled = false;
      }
    });
  }

  localStorage.removeItem('ml_active_job');
  return true;  // Finaliza o stream SSE
}
```

**Step 3: Rodar todos os testes**

Run: `python -m pytest tests/ -x -q`
Expected: 87 passed

**Step 4: Commit**

```bash
git add static/main.html
git commit -m "feat(ui): add validate_category step and error panel in SSE publish flow"
```

---

## Task 6: Endpoint `/api/ml/notify-category-change` (WhatsApp)

**Files:**
- Modify: `app.py` (adicionar endpoint após `/api/ml/publish/{job_id}/cancel`, ~linha 4670)

**Step 1: Adicionar endpoint**

```python
@app.post("/api/ml/notify-category-change")
async def ml_notify_category_change(
    request: Request,
    current_user: CurrentUser = Depends(get_current_user_master),
):
    """Send WhatsApp notification about ML category structure change."""
    body = await request.json()
    category_id = body.get("category_id", "?")
    sku = body.get("sku", "?")
    missing = body.get("missing_attrs") or []
    added = body.get("added") or []
    removed = body.get("removed") or []

    missing_names = ", ".join(a.get("id", "?") for a in missing) if missing else "nenhum"
    added_names = ", ".join(added) if added else "nenhum"
    removed_names = ", ".join(removed) if removed else "nenhum"

    message = (
        f"⚠️ [Ads Gen] Mudança de categoria ML detectada\n"
        f"Categoria: {category_id}\n"
        f"SKU: {sku}\n"
        f"Campos faltantes: {missing_names}\n"
        f"Novos obrigatórios: {added_names}\n"
        f"Removidos: {removed_names}\n"
        f"Data: {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}"
    )

    if not settings.whatsapp_service_url or not settings.whatsapp_notify_phone:
        return JSONResponse({"status": "skipped", "reason": "WhatsApp not configured"})

    try:
        async with httpx.AsyncClient() as client:
            await client.post(
                settings.whatsapp_service_url,
                json={
                    "phone": settings.whatsapp_notify_phone,
                    "message": message,
                },
                headers={"Authorization": f"Bearer {settings.whatsapp_service_token}"},
                timeout=10.0,
            )
    except Exception as exc:
        logger.warning("Failed to send category change notification: %s", exc)
        return JSONResponse({"status": "error", "detail": str(exc)}, status_code=502)

    return JSONResponse({"status": "sent"})
```

**Step 2: Rodar todos os testes**

Run: `python -m pytest tests/ -x -q`
Expected: 87 passed

**Step 3: Commit**

```bash
git add app.py
git commit -m "feat(ml-publish): add /api/ml/notify-category-change WhatsApp endpoint"
```

---

## Task 7: Teste de regressão do payload de criação

Este teste captura o payload exato enviado ao `create_listing` e previne mutações não intencionais.

**Files:**
- Modify: `tests/test_ml_category_sanity.py`

**Step 1: Adicionar teste de snapshot de payload**

Adicionar ao final de `tests/test_ml_category_sanity.py`:

```python
# ── Teste de regressão: snapshot do payload de publicação ─────────────────

import json
from unittest.mock import AsyncMock, MagicMock, patch
import asyncio


def _make_publish_workspace(category_id="MLB1051", catalog_product_id="", ml_attributes=None):
    """Helper: workspace completo para simular publicação."""
    return {
        "base_state": {
            "product_fields": {
                "cost_price": 50.0,
                "weight_kg": 0.5,
                "length_cm": 15.0,
                "width_cm": 10.0,
                "height_cm": 5.0,
                "ml_category_id": category_id,
                "ml_catalog_product_id": catalog_product_id,
                "ml_listing_type_id": "gold_special",
                "ml_attributes": ml_attributes or [
                    {"id": "BRAND", "value_name": "TestBrand"},
                    {"id": "MODEL", "value_name": "TestModel"},
                ],
                "image_urls": ["https://drive.google.com/img1.png"],
            },
            "shipping_cost_cache": {"value": 10.0},
        },
        "versioned_state": {
            "variants": {
                "simple": {
                    "title": {"versions": ["Produto Teste XYZ"], "current_index": 0},
                    "description": {"versions": ["Descrição do produto."], "current_index": 0},
                }
            },
            "prices": {"listing": 149.99},
        },
    }


def test_payload_freeform_listing_structure():
    """Snapshot test: free-form listing payload must contain exactly these keys."""
    from app import _run_ml_publish_job, ML_PUBLISH_JOBS

    captured_payload = {}

    async def _fake_create_listing(access_token, payload):
        captured_payload.update(payload)
        return ("MLB_TEST_001", "https://example.com/item")

    job_id = "test-payload-snapshot"
    ML_PUBLISH_JOBS[job_id] = {
        "user_id": "test-user",
        "status": "starting",
        "events": [],
        "created_at": 999999999,
        "listing_id": None,
        "error": None,
        "resume_event": None,
        "resume_action": None,
        "paused_at_step": None,
    }

    ws = _make_publish_workspace()

    with patch("mercadolivre_service.get_valid_access_token", new_callable=AsyncMock, return_value=("TOKEN", None)), \
         patch("mercadolivre_service.get_category_settings", new_callable=AsyncMock, return_value={}), \
         patch("mercadolivre_service.get_category_attributes", new_callable=AsyncMock, return_value=[
             {"id": "BRAND", "name": "Marca", "tags": {"required": True}},
             {"id": "MODEL", "name": "Modelo", "tags": {"required": True}},
         ]), \
         patch("mercadolivre_service.upload_image", new_callable=AsyncMock, return_value="PIC123"), \
         patch("mercadolivre_service.create_listing", new_callable=AsyncMock, side_effect=_fake_create_listing) as mock_create, \
         patch("mercadolivre_service.update_description", new_callable=AsyncMock), \
         patch("mercadolivre_service.update_listing_attributes", new_callable=AsyncMock), \
         patch("mercadolivre_service.update_listing_sale_terms", new_callable=AsyncMock), \
         patch("mercadolivre_service.get_seller_shipping_cost", new_callable=AsyncMock, return_value=10.0), \
         patch("mercadolivre_service.activate_listing", new_callable=AsyncMock), \
         patch("app.SessionLocal") as mock_session_cls, \
         patch("app._build_drive_service"), \
         patch("app._list_drive_images_for_sku", return_value=["fake_file_id"]):

        mock_db = MagicMock()
        mock_db.query.return_value.filter.return_value.first.return_value = MagicMock(
            data={"google_drive": {"credentials_json": "{}", "folder_id": "root123"}}
        )
        mock_session_cls.return_value = mock_db
        mock_db.close = MagicMock()

        # Mock Drive file download
        with patch("app._run_ml_publish_job.__code__", create=True):
            pass  # placeholder — actual run below

        asyncio.get_event_loop().run_until_complete(
            _run_ml_publish_job(
                job_id=job_id,
                user_id="test-user",
                workspace=ws,
                account={"ml_user_id": "123456", "access_token": "TOKEN"},
                ml_accounts=[],
                pricing_config=[{"channel": "mercadolivre", "lucro": 0.10, "impostos": 0.05, "tacos": 0.02, "margem_contribuicao": 0.03}],
                variant="simple",
                db_user_id="test-db-user",
                sku_normalized="TESTSKU",
                display_sku="TEST-SKU-001",
            )
        )

    # Verify payload structure for free-form listing
    assert "title" in captured_payload, "Free-form listing must have 'title'"
    assert "shipping" in captured_payload, "Free-form listing must have 'shipping'"
    assert "dimensions" in captured_payload["shipping"], "shipping must have 'dimensions'"
    dims = captured_payload["shipping"]["dimensions"]
    assert set(dims.keys()) == {"width", "height", "length", "weight"}
    assert captured_payload["status"] == "paused"
    assert captured_payload["listing_type_id"] == "gold_special"
    # Ensure SELLER_PACKAGE_* are NOT in the payload attributes for free-form
    # (they go in shipping.dimensions instead)
    attrs_in_payload = captured_payload.get("attributes") or []
    attr_ids = {a["id"] for a in attrs_in_payload}
    assert "SELLER_PACKAGE_HEIGHT" not in attr_ids, "Free-form should not have SELLER_PACKAGE in attributes"

    # Cleanup
    ML_PUBLISH_JOBS.pop(job_id, None)
```

> **Nota:** Este teste é complexo por natureza (end-to-end com muitos mocks). Se a execução mostrar que o setup do Drive ou DB precisa de ajustes, adaptar os mocks conforme necessário. O objetivo principal é capturar o `listing_payload` passado a `create_listing` e validar sua estrutura.

**Step 2: Rodar teste**

Run: `python -m pytest tests/test_ml_category_sanity.py::test_payload_freeform_listing_structure -v`
Expected: PASS (pode precisar de ajustes nos mocks durante a implementação)

**Step 3: Rodar todos os testes**

Run: `python -m pytest tests/ -x -q`
Expected: 88 passed

**Step 4: Commit**

```bash
git add tests/test_ml_category_sanity.py
git commit -m "test: add payload snapshot regression test for ML listing creation"
```

---

## Resumo de arquivos alterados

| Arquivo | Ação | Task |
|---|---|---|
| `alembic/versions/rev_007_ml_category_baseline.py` | Criar | 1 |
| `app.py` | Modificar (modelo + função + step + endpoint) | 2, 3, 4, 6 |
| `static/main.html` | Modificar (step labels + handleEvent) | 5 |
| `tests/test_ml_category_sanity.py` | Criar | 3, 7 |

## Ordem de execução

```
Task 1 (migração) → Task 2 (modelo) → Task 3 (função pura + testes)
    → Task 4 (integração no fluxo) → Task 5 (frontend)
    → Task 6 (endpoint WhatsApp) → Task 7 (teste regressão payload)
```

---

Plano salvo em `docs/plans/2026-03-10-ml-category-sanity-check-plan.md`.

Duas opções de execução:

**1. Subagent-Driven (nesta sessão)** — Executo task por task com revisão entre cada uma. Iteração rápida.

**2. Sessão paralela (separada)** — Abrir nova sessão com o plano e executar em batch com checkpoints.

Qual abordagem?