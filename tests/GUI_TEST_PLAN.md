# GUI Test Instrumentation Plan (Backlog)

This document tracks manual-to-automated GUI scenarios to be implemented in the dedicated test infra plan.

## Variation Tabs + Pricing Volatility

1. `SKU switch with DB hit refreshes UI`
- Load SKU `PTBOCSALMCATCX10` and verify title/description/FAQ/cards are visible.
- Search SKU `NEWGD60C7`.
- Assert that product header fields and Result content change to the NEW SKU data (no stale UI from previous SKU).
- Assert active variation is reset to `Anúncio simples` after workspace hydration.

2. `Shipping decision cost is volatile and never persisted`
- For `Anúncio simples`, set cost base so `cost_base * 2 > 78.99` and trigger auto freight path.
- Assert freight lookup uses a derived in-memory decision cost, while persisted `tiny_cost_price` remains unchanged.
- Reload the same SKU from DB and assert cost base equals the original persisted cost (not doubled).
- Repeat for `Kit com 2..5` and assert the same non-persistence behavior.

3. `Versioning isolation per variation`
- In one SKU, create title/description edits in `Anúncio simples`.
- Switch to `Kit com 2` and create independent edits.
- Assert version counters and content in `simple` and `kit2` are independent.
- Repeat for FAQ/cards regenerate and manual edits to confirm no cross-variant version contamination.

4. `Computed fields sanity with 2 Tiny fixture SKUs`
- Build a deterministic Tiny fixture with at least 2 real SKUs (`scripts/extract_tiny_sku_fixture.py`).
- For SKU A and SKU B, load `simple`, then switch between `kit2..kit5`.
- Assert `cost/width/weight` in kits are derived as `simple * quantity`.
- Assert switching back to `simple` restores original `simple` values (no leakage from kits).
- Assert SKU switch (`A -> B -> A`) restores each SKU own persisted `simple` values.

5. `DB vs UI vs Tiny fake parity on workspace flow`
- Mock `/api/sku/workspace/load` with source `tiny` on first search and source `db` on subsequent search.
- Persist saves in an in-memory fake DB (`/api/sku/workspace/save`) and compare latest `base_state.product_fields`.
- Validate that even while user is on kit tabs, persisted base fields remain the simple values.
- On re-open (DB hit), compare UI simple fields against both fake DB state and Tiny fixture source.

6. `NEWGD60C7 reference parity from screenshots (exact values)`
- Use deterministic Tiny fake fixture for SKU `NEWGD60C7`.
- Validate exact field-by-field parity for:
  - `Anúncio simples`
  - `Kit com 2`
  - `Kit com 3`
  - `Kit com 4`
  - `Kit com 5`
- Execute mandatory tab order:
  - `simple -> kit2 -> kit3 -> kit4 -> kit5 -> kit4 -> kit3 -> kit2 -> simple`
  - repeat once more in the same run.
- Enforce strict numeric equality (no tolerance) for:
  - cadastral fields (`altura/largura/comprimento/peso`)
  - `custo base` and `custo do frete`
  - pricing blocks (`anúncio/agressivo/promocional` + metrics)
  - wholesale rows (`preço/qtd/margem/múltiplo/valor`)
- Press `Enter` on SKU input to trigger search (not button click).
- Repeat the full sequence after `F5` (without server restart) and assert same values with DB hit.

7. `Dual SKU exact parity + price tab toggling (% Max/% Min) with refresh`
- Add a second end-to-end GUI flow keeping the old NEW-only test untouched.
- Execute full strict sequence for `NEWGD60C7` and then `PTBOCSALMCATCX10`:
  - repeated variant tab round-trip (`simple -> kit2 -> kit3 -> kit4 -> kit5 -> ... -> simple`)
  - at each step, click `% Max (Premium)` and then `% Min (Classico)` before assertions.
- Validate exact numeric parity (strict equality) for base fields, pricing blocks, and wholesale rows.
- Validate general UI state without pixel diff:
  - active variant and min price tab state are correct,
  - title/description remain empty,
  - FAQ and Cards stay empty when no edits are made.
- Run the same full dual-SKU sequence again after `F5` (no server restart) and require DB-hit parity.

8. `Variation tabs ear layout + sticky + no scroll jump/no reload`
- Desktop (`>1024px`):
  - Assert tabs render as left-side vertical ear tabs in `#resultCard`.
  - Assert labels are rotated and active/hover states remain in blue palette.
  - Assert rail uses sticky behavior while `#resultCard` is visible.
  - Assert sticky remains bounded by `#resultCard` (rail does not overflow card bottom).
- Mobile/tablet (`<=1024px`):
  - Assert fallback to top horizontal chips.
  - Assert labels are not rotated in mobile fallback.
- Runtime behavior on tab switch:
  - Scroll page into `#resultCard`, capture `scrollY`.
  - Switch variants multiple times (`simple -> kit5 -> kit2 -> simple`).
  - Assert `scrollY` delta remains minimal (no jump).
  - Assert no page reload (runtime boot marker/counter unchanged).

9. `Tiny KIT auto-discovery + auto-create by variant tab`
- Open a SKU with Tiny integration active and switch to `kit2`.
- Backend resolve flow:
  - when no valid kit exists, assert `create_available=true` and UI shows `Cadastrar KIT` button.
  - when valid kit exists, assert UI uses resolved SKU and hides create button.
- Auto-create flow:
  - click create button in a missing-kit tab and assert:
    - POST `/api/tiny/kit/create` is called with `base_sku` + `kit_quantity`,
    - resolved SKU is rendered in cadastral SKU field,
    - button is hidden after success.
- Cache behavior:
  - switch away and back to the same kit tab,
  - assert resolved SKU remains and no fresh full scan is required.
- Collision behavior:
  - force backend `409 kit_sku_collision`,
  - assert red error toast with a clear message containing the colliding code.
- Validation behavior:
  - if resolve returns a false-positive candidate (wrong class/structure), assert UI still exposes create button.

10. `Tiny KIT create payload mapping from active variant`
- On a kit tab (`kit2..kit5`), trigger `Cadastrar KIT`.
- Assert request payload includes:
  - `announcement_price` from active variant announce field (`% Min/% Max` active tab aware),
  - `promotional_price=0`,
  - `base_unit_override` matching simple product unit,
  - `kit_weight_kg`, `kit_height_cm`, `kit_width_cm`, `kit_length_cm`,
  - `kit_volumes=1`,
  - `kit_description` from active variant description.
- Assert `combo_name_override` keeps the expected `COMBO COM {QTD} ...: ...` format.

11. `Tiny include edge cases (cold, mocked)`
- Simulate Tiny include returning validation error (`status=Erro`, e.g. missing price):
  - assert frontend toast shows upstream message clearly (not generic 502-only text).
- Simulate Tiny include returning `OK` with empty `registros` and delayed SKU visibility:
  - assert service retries post-include confirmation lookup before failing,
  - assert success if SKU appears within retry window,
  - assert explicit error if SKU never appears.
- Simulate Tiny requiring promo price when sent as zero:
  - assert service retries once with `preco_promocional = preco` and succeeds.
