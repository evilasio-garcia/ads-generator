# Ads Generator — Multi-Marketplace Ad Generator

## Overview

This Python FastAPI application generates optimized marketplace advertisements (title, description, FAQ, and visual cards) for multiple platforms including Amazon, Mercado Livre, Shopee, Magalu, and Shein. Its purpose is to streamline the ad creation process for various e-commerce platforms. Key capabilities include multi-marketplace support, dual LLM integration (OpenAI and Google Gemini), mock mode for testing, version history for generated content, text-to-speech functionality, customizable templates, and an optional Tiny ERP integration for automated product data retrieval. The project aims to provide a comprehensive solution for efficient and effective online advertisement generation.

## User Preferences

None specified yet.

## System Architecture

The application uses a Python 3.11 FastAPI backend and a static HTML frontend with Tailwind CSS (CDN). It integrates with OpenAI and Google Gemini for LLM capabilities and runs on a Uvicorn ASGI server.

**UI/UX Decisions:**
- **File Upload System:** Supports up to 10 files (images and text) with client-side validation (file type, size limits). Features dynamic slot allocation, color-coded status indicators (Green: will be used; Blue: awaiting selection; Gray: no slot; Red: invalid), and drag-and-drop reordering with real-time slot recalculation. Visual feedback is provided for file processing and validation.
- **Tiny ERP Integration (FULLY IMPLEMENTED - Etapa 2):** Complete integration with Tiny ERP API for automatic product data retrieval. When user provides Tiny tokens and enters a SKU:
  - **Automatic API Calls:** Frontend asynchronously calls POST /api/tiny/product to fetch real product data
  - **Visual Feedback:** Loading state (blue pulsing), success (green flash), error (red flash) with descriptive alerts
  - **Concurrency Protection:** Guards against stale data when SKU changes mid-fetch by validating SKU/instance after async operations
  - **Data Auto-fill:** Populates GTIN, SKU display, dimensions (height/width/length), weight, and pricing fields with real Tiny data
  - **LLM Integration:** Automatically injects Tiny dimensions and weight into LLM prompts with explicit instructions to use exact values
  - **Dual Modes:** "Manual Mode" (editable fields) when no Tiny data available, "Tiny Mode" (read-only, auto-filled) when API returns data
  - **Copy Functionality:** All fields have "Copiar conteúdo" buttons with standardized visual feedback
- **Text-to-Speech (TTS):** Includes real-time word highlighting during playback, auto-scrolling to keep the current word visible, and fixed controls that remain at the top during scrolling. The stop button resets playback to the beginning.
- **Content Generation & Regeneration:** "Gerar conteúdo" resets all history and interface elements. Regeneration for FAQ and Cards now properly combines current content with new prompt information. Automatic regeneration forces significantly different content variations.
- **Version History:** FAQ and Card regenerations always create new version entries, tracked with navigation buttons.
- **Visual Feedback:** Regenerate buttons use sparkle icons. Copy buttons throughout the system use a standardized document-with-arrow icon (outline for default state, filled for copied state). Copy description icons differentiate between "with FAQ" (filled, using legacy dual-square icon) and "without FAQ" (outline, using new document icon). FAQ approval icons show check-circle for enabled and x-circle for disabled. Card copy buttons provide visual feedback (outline to filled icon) and border color changes (yellow for one item copied, green for both). All copy buttons now display a green checkmark feedback when clicked, with Tiny field buttons also showing "Copiado!" text. Feedback handles rapid clicks gracefully by preserving original button state and canceling previous timeouts.

**Technical Implementations:**
- **API Endpoints:** 
  - `GET /` serves the frontend
  - `POST /api/generate` generates ad content
  - `POST /api/regen` regenerates specific fields
  - `POST /api/tiny/product` fetches product data from Tiny ERP by SKU
  - `POST /api/tiny/validate-token` validates Tiny ERP tokens
  - `POST /pricing/quote` calculates all prices from cost_price + channel (NEW - Etapa 3)
  - `GET /pricing/policies` lists pricing policies by channel (NEW - Etapa 3)
  - `POST /pricing/validate` validates pricing inputs (NEW - Etapa 3)
- **Tiny ERP Backend (tiny_service.py):**
  - **Async Architecture:** Uses httpx.AsyncClient for non-blocking HTTP calls to Tiny API
  - **Retry Logic:** Exponential backoff with max 2 total attempts (initial + 1 retry) using asyncio.sleep
  - **Error Handling:** Typed exceptions (TinyAuthError, TinyNotFoundError, TinyTimeoutError) mapped to appropriate HTTP status codes (401, 404, 408)
  - **Security:** Token redaction in all logs - tokens never exposed in log output
  - **API Endpoints:** produtos.pesquisa.php (search by SKU), produto.obter.php (get full product details)
  - **Data Mapping:** Converts Tiny API response to internal format (height_cm, width_cm, length_cm, weight_kg, prices, GTIN)
- **Configuration:** Stores API keys, prompt templates, marketplace rules, and Tiny tokens in browser localStorage.
- **Mock Mode:** Activates when no API keys are provided, using predefined content examples.
- **LLM Integration with Files:** Backend accepts FormData with files, encoding them to base64 for LLM processing (GPT-4o for OpenAI, inline_data for Gemini). Text files are appended to prompts with clear labeling. Strict instructions ensure product characteristics come *only* from uploaded files.
- **LLM Integration with Tiny Data:** When tiny_product_data is provided in Options, backend automatically injects dimensions and weight into prompts with explicit instructions: "📦 DADOS OFICIAIS DO TINY ERP (USE ESTES DADOS REAIS): Dimensões: X cm, Peso: Y kg. ⚠️ IMPORTANTE: Use EXATAMENTE estas dimensões e peso nas descrições e cards."
- **Pricing Module (NEW - Etapa 3 - Strategy + Factory Pattern):**
  - **Architecture:** Interface `IPriceCalculator` defines contract, `BasePriceCalculator` provides shared logic, 7 marketplace-specific implementations
  - **Supported Channels:** MercadoLivre, Shopee, Amazon, Shein, Magalu, Ecommerce, Telemarketing
  - **Factory:** `PriceCalculatorFactory.get(channel)` returns correct calculator, validates channel, handles errors (422 for unsupported)
  - **Calculations:** Deterministic pricing (listing, aggressive, promo, wholesale tiers) based on cost_price + channel-specific markup/tax/margins
  - **Frontend Integration:** Auto-pricing function calls `/pricing/quote` when cost_price changes, populates price fields automatically (manual mode only)
  - **Tests:** 15 unit tests covering factory, calculators, and endpoints (pytest)
- **State Management:** `integrationMode` variable tracks "manual" or "tiny" mode, switching automatically based on token availability and SKU input.
- **Replit Environment:** Uses `uvicorn app:app --host 0.0.0.0 --port 5000 --reload`. Optional environment variables (`OPENAI_API_KEY`, `GEMINI_API_KEY`, etc.) can be set for LLM features.

## External Dependencies

- **LLM Providers:** OpenAI, Google Gemini
- **ERP Integration:** Tiny ERP API (fully integrated - read-only access)
- **Frontend Framework:** Tailwind CSS (via CDN)
- **ASGI Server:** Uvicorn
- **Python Libraries:** FastAPI, python-multipart (file uploads), httpx (async HTTP client), pytest (testing), and other dependencies listed in `requirements.txt`.

## Recent Changes

**October 24, 2025 - Etapa 3: Pricing Module**
- Implemented complete pricing module using Strategy + Factory patterns
- Created 7 marketplace-specific calculators with distinct markup/tax configurations
- Added 3 REST endpoints for price calculation, policy listing, and validation
- Integrated frontend auto-pricing that populates price fields automatically when cost_price changes
- Created 15 unit tests (all passing) covering factory, calculators, and business logic
- Architecture approved by code review for extensibility, correctness, and best practices