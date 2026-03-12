"""Tests for config and workspace persistence — guards against data loss."""
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import pytest
import httpx
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker, Session

from app import (
    app,
    Base,
    UserConfig,
    SkuWorkspace,
    get_db,
    get_current_user_master,
    _normalize_base_state,
    ConfigPayload,
    DATABASE_URL,
)
from appgtw_auth import CurrentUser

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

TEST_USER_ID = "test-user-00000000"
TEST_SCHEMA = "test_config_persistence"


@pytest.fixture()
def db_session():
    engine = create_engine(DATABASE_URL, future=True)
    with engine.connect() as conn:
        conn.execute(text(f"CREATE SCHEMA IF NOT EXISTS {TEST_SCHEMA}"))
        conn.commit()

    schema_engine = create_engine(
        DATABASE_URL,
        future=True,
        execution_options={"schema_translate_map": {None: TEST_SCHEMA}},
    )
    Base.metadata.create_all(bind=schema_engine)
    _SessionLocal = sessionmaker(bind=schema_engine, autoflush=False, autocommit=False)
    session = _SessionLocal()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(bind=schema_engine)
        schema_engine.dispose()
        with engine.connect() as conn:
            conn.execute(text(f"DROP SCHEMA IF EXISTS {TEST_SCHEMA} CASCADE"))
            conn.commit()
        engine.dispose()


@pytest.fixture()
def client(db_session: Session):
    def _override_db():
        try:
            yield db_session
        finally:
            pass

    def _override_user():
        return CurrentUser(
            user_id=TEST_USER_ID,
            email="test@test.com",
            name="Test User",
            role="admin",
            raw_claims={},
        )

    app.dependency_overrides[get_db] = _override_db
    app.dependency_overrides[get_current_user_master] = _override_user

    transport = httpx.ASGITransport(app=app)
    c = httpx.AsyncClient(transport=transport, base_url="http://test")
    yield c

    app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

FULL_CONFIG = {
    "openai": "sk-test-openai-key",
    "openai_base": "https://api.openai.com/v1",
    "gemini": "gemini-test-key",
    "gemini_base": "https://generativelanguage.googleapis.com",
    "rules": {"some_rule": True},
    "prompt_template": "Generate an ad for {{product}}",
    "tiny_tokens": [{"name": "Main", "token": "tok_tiny_123"}],
    "pricing_config": [{"marketplace": "mercadolivre", "comissao_min": 11}],
    "image_search": {"api_key": "img-key-abc"},
    "google_drive": {"folder_id": "drive-folder-xyz", "credentials_json": "{}"},
    "canva": {"client_id": "canva-id", "client_secret": "canva-secret"},
    "general": {"kit_name_replacements": [], "tts_rate": 2},
}


def seed_config(db_session: Session):
    """Insert a full config into the DB so subsequent saves can be tested."""
    cfg = UserConfig(user_id=TEST_USER_ID, data=dict(FULL_CONFIG))
    db_session.add(cfg)
    db_session.commit()
    db_session.refresh(cfg)
    return cfg


def seed_workspace(db_session: Session, tiny_product_data: dict):
    """Insert a workspace with tiny_product_data into the DB."""
    from datetime import datetime

    ws = SkuWorkspace(
        sku_normalized="TESTSKU123",
        marketplace_normalized="mercadolivre",
        sku_display="TESTSKU123",
        base_state=_normalize_base_state(
            {
                "integration_mode": "tiny",
                "tiny_product_data": tiny_product_data,
                "selected_marketplace": "mercadolivre",
                "product_fields": {"product_name": "Test Product"},
            },
            default_marketplace="mercadolivre",
        ),
        versioned_state_current={
            "schema_version": 2,
            "variants": {
                "simple": {
                    "title": {"versions": [], "current_index": -1},
                    "description": {"versions": [], "current_index": -1},
                    "faq_lines": [],
                    "card_lines": [],
                }
            },
        },
        state_seq=1,
        created_by_user_id=TEST_USER_ID,
        updated_by_user_id=TEST_USER_ID,
        last_accessed_at=datetime.utcnow(),
    )
    db_session.add(ws)
    db_session.commit()
    db_session.refresh(ws)
    return ws


# ===========================================================================
# save_config: partial save must NOT erase unrelated keys
# ===========================================================================


class TestSaveConfigPartialUpdate:
    """POST /api/config with a subset of fields must preserve the rest."""

    @pytest.mark.asyncio
    async def test_partial_save_general_preserves_api_keys(self, client, db_session):
        seed_config(db_session)

        resp = await client.post(
            "/api/config",
            json={"general": {"tts_rate": 2.5, "kit_name_replacements": []}},
        )
        assert resp.status_code == 200
        data = resp.json()

        assert data["openai"] == FULL_CONFIG["openai"]
        assert data["gemini"] == FULL_CONFIG["gemini"]
        assert data["google_drive"]["folder_id"] == FULL_CONFIG["google_drive"]["folder_id"]
        assert data["canva"]["client_id"] == FULL_CONFIG["canva"]["client_id"]
        assert data["image_search"]["api_key"] == FULL_CONFIG["image_search"]["api_key"]
        assert data["general"]["tts_rate"] == 2.5

    @pytest.mark.asyncio
    async def test_partial_save_openai_preserves_other_fields(self, client, db_session):
        seed_config(db_session)

        resp = await client.post("/api/config", json={"openai": "sk-new-key"})
        assert resp.status_code == 200
        data = resp.json()

        assert data["openai"] == "sk-new-key"
        assert data["gemini"] == FULL_CONFIG["gemini"]
        assert data["google_drive"]["folder_id"] == FULL_CONFIG["google_drive"]["folder_id"]
        assert data["tiny_tokens"] == FULL_CONFIG["tiny_tokens"]

    @pytest.mark.asyncio
    async def test_partial_save_preserves_ml_accounts(self, client, db_session):
        cfg = seed_config(db_session)
        current = dict(cfg.data)
        current["ml_accounts"] = [{"ml_user_id": "123", "access_token": "tok"}]
        cfg.data = current
        db_session.commit()

        resp = await client.post(
            "/api/config",
            json={"general": {"tts_rate": 1.5, "kit_name_replacements": []}},
        )
        assert resp.status_code == 200

        db_session.refresh(cfg)
        assert cfg.data.get("ml_accounts") == [{"ml_user_id": "123", "access_token": "tok"}]

    @pytest.mark.asyncio
    async def test_full_save_writes_all_fields(self, client, db_session):
        resp = await client.post("/api/config", json=FULL_CONFIG)
        assert resp.status_code == 200
        data = resp.json()

        assert data["openai"] == FULL_CONFIG["openai"]
        assert data["gemini"] == FULL_CONFIG["gemini"]
        assert data["canva"]["client_id"] == FULL_CONFIG["canva"]["client_id"]

    @pytest.mark.asyncio
    async def test_empty_payload_preserves_everything(self, client, db_session):
        seed_config(db_session)

        resp = await client.post("/api/config", json={})
        assert resp.status_code == 200
        data = resp.json()

        assert data["openai"] == FULL_CONFIG["openai"]
        assert data["gemini"] == FULL_CONFIG["gemini"]
        assert data["tiny_tokens"] == FULL_CONFIG["tiny_tokens"]


# ===========================================================================
# ConfigPayload.model_dump(exclude_unset=True)
# ===========================================================================


class TestConfigPayloadExcludeUnset:
    """Verify that model_dump(exclude_unset=True) only includes sent fields."""

    def test_only_general_sent(self):
        payload = ConfigPayload(general={"tts_rate": 2})
        dumped = payload.model_dump(exclude_unset=True)
        assert "general" in dumped
        assert "openai" not in dumped
        assert "gemini" not in dumped

    def test_full_payload_includes_all(self):
        payload = ConfigPayload(**FULL_CONFIG)
        dumped = payload.model_dump(exclude_unset=True)
        assert "openai" in dumped
        assert "gemini" in dumped
        assert "general" in dumped

    def test_empty_payload_dumps_nothing(self):
        payload = ConfigPayload()
        dumped = payload.model_dump(exclude_unset=True)
        assert dumped == {}


# ===========================================================================
# workspace save: tiny_product_data must never be lost
# ===========================================================================


class TestWorkspaceSaveTinyProductData:
    """POST /api/sku/workspace/save must not erase tiny_product_data."""

    @pytest.mark.asyncio
    async def test_save_with_null_tiny_product_data_preserves_existing(self, client, db_session):
        tiny_data = {
            "sku": "TESTSKU123",
            "title": "Test Product Title",
            "cost_price": "15.50",
            "height_cm": "10",
            "width_cm": "20",
            "length_cm": "30",
            "weight_kg": "0.5",
        }
        seed_workspace(db_session, tiny_data)

        resp = await client.post(
            "/api/sku/workspace/save",
            json={
                "sku": "TESTSKU123",
                "marketplace": "mercadolivre",
                "base_state": {
                    "integration_mode": "tiny",
                    "tiny_product_data": None,
                    "selected_marketplace": "mercadolivre",
                    "product_fields": {
                        "product_name": "Test Product Title",
                        "ml_category_id": "MLB12345",
                    },
                },
                "versioned_state": {
                    "schema_version": 2,
                    "variants": {
                        "simple": {
                            "title": {"versions": [], "current_index": -1},
                            "description": {"versions": [], "current_index": -1},
                            "faq_lines": [],
                            "card_lines": [],
                        }
                    },
                },
                "action": "field_change",
            },
        )
        assert resp.status_code == 200

        ws = db_session.query(SkuWorkspace).filter(
            SkuWorkspace.sku_normalized == "TESTSKU123"
        ).first()
        db_session.refresh(ws)

        saved_tpd = ws.base_state.get("tiny_product_data")
        assert saved_tpd is not None, "tiny_product_data was erased!"
        assert saved_tpd["sku"] == "TESTSKU123"
        assert saved_tpd["cost_price"] == "15.50"

    @pytest.mark.asyncio
    async def test_save_with_new_tiny_product_data_overwrites(self, client, db_session):
        seed_workspace(db_session, {"sku": "TESTSKU123", "title": "Old"})

        resp = await client.post(
            "/api/sku/workspace/save",
            json={
                "sku": "TESTSKU123",
                "marketplace": "mercadolivre",
                "base_state": {
                    "integration_mode": "tiny",
                    "tiny_product_data": {"sku": "TESTSKU123", "title": "New"},
                    "selected_marketplace": "mercadolivre",
                    "product_fields": {},
                },
                "versioned_state": {
                    "schema_version": 2,
                    "variants": {
                        "simple": {
                            "title": {"versions": [], "current_index": -1},
                            "description": {"versions": [], "current_index": -1},
                            "faq_lines": [],
                            "card_lines": [],
                        }
                    },
                },
                "action": "field_change",
            },
        )
        assert resp.status_code == 200

        ws = db_session.query(SkuWorkspace).filter(
            SkuWorkspace.sku_normalized == "TESTSKU123"
        ).first()
        db_session.refresh(ws)

        assert ws.base_state["tiny_product_data"]["title"] == "New"
