# Ads Generator — Multi-Marketplace Ad Generator

## Overview

This Python FastAPI application streamlines the creation of optimized marketplace advertisements (title, description, FAQ, and visual cards) for platforms like Amazon, Mercado Livre, Shopee, Magalu, and Shein. It features multi-marketplace support, dual LLM integration (OpenAI and Google Gemini), a mock mode for testing, version history for generated content, text-to-speech functionality, customizable templates, and optional Tiny ERP integration for automated product data retrieval. The project aims to provide a comprehensive solution for efficient and effective online advertisement generation, enhancing market potential for e-commerce sellers.

## User Preferences

None specified yet.

## System Architecture

The application utilizes a Python 3.11 FastAPI backend and a static HTML frontend with Tailwind CSS (CDN), running on a Uvicorn ASGI server. It integrates with OpenAI and Google Gemini for LLM capabilities.

**UI/UX Decisions:**
- **File Upload System:** Supports up to 10 files (images/text) with client-side validation, dynamic slot allocation, color-coded status, and drag-and-drop reordering.
- **Tiny ERP Integration:** Complete integration for automatic product data retrieval via SKU, with visual feedback, concurrency protection, auto-filling of product data (GTIN, dimensions, weight, pricing), and read-only "Tiny Mode" vs. editable "Manual Mode."
- **Text-to-Speech (TTS):** Features real-time word highlighting, auto-scrolling, and fixed playback controls.
- **Content Generation & Regeneration:** Offers reset functionality, proper combination of current and new prompt information for regeneration, and version history for FAQ and Cards.
- **Visual Feedback:** Standardized sparkle icons for regeneration, document icons for copying (with state changes), and checkmark feedback for all copy actions, including text feedback for Tiny fields.
- **Pricing UI:** Displays "Preço do Anúncio" (promo price / 0.85) with blue highlighting and three detailed analysis metrics (% Margem, Múltiplo de Valor, Valor Monetário) per price field (announcement, aggressive, promo). Prices are editable, with manual edits triggering metric recalculation. Visual warnings (red highlighting) for negative margins or low value multiples. In "Manual Pricing Mode," calculations are triggered by a "Calcular Preços" button, with specific alerts for Mercado Livre shipping costs.

**Technical Implementations:**
- **API Endpoints:** Includes endpoints for content generation (`/api/generate`, `/api/regen`), Tiny ERP integration (`/api/tiny/product`, `/api/tiny/validate-token`), and comprehensive pricing calculations (`/pricing/quote`, `/pricing/policies`, `/pricing/validate`).
- **Tiny ERP Backend (`tiny_service.py`):** Features an async architecture using `httpx.AsyncClient`, exponential backoff retry logic, typed error handling (401, 404, 408), token redaction in logs for security, and mapping of Tiny API responses to internal data formats.
- **Configuration:** API keys, prompt templates, marketplace rules, and Tiny tokens are stored in browser localStorage.
- **Mock Mode:** Provides predefined content for testing when no API keys are available.
- **LLM Integration:** Handles file uploads by encoding to base64 for LLM processing (GPT-4o, Gemini `inline_data`). Text files are appended to prompts, and strict instructions ensure product characteristics are derived solely from uploaded files. Tiny ERP data (dimensions, weight) is explicitly injected into LLM prompts with instructions for exact usage.
- **Pricing Module (Strategy + Factory Pattern):** Implemented with an `IPriceCalculator` interface, `BasePriceCalculator`, and 7 marketplace-specific implementations. Supports MercadoLivre, Shopee, Amazon, Shein, Magalu, Ecommerce, and Telemarketing. Calculates deterministic pricing (listing, aggressive, promo, wholesale tiers) based on cost, shipping, and channel-specific markups/taxes/margins. Frontend auto-pricing calls `/pricing/quote` and populates fields, with backend-driven metrics for margin, value multiple, value amount, taxes, and commissions. The module also handles Mercado Livre's custom commission structure and configurable pricing parameters per marketplace.
- **State Management:** `integrationMode` dynamically switches between "manual" and "tiny" based on token availability and SKU input.
- **Replit Environment:** Configured to use `uvicorn app:app --host 0.0.0.0 --port 5000 --reload` with optional environment variables for LLM API keys.

## External Dependencies

- **LLM Providers:** OpenAI, Google Gemini
- **ERP Integration:** Tiny ERP API
- **Frontend Framework:** Tailwind CSS (via CDN)
- **ASGI Server:** Uvicorn
- **Python Libraries:** FastAPI, python-multipart, httpx, pytest (for testing), and other dependencies specified in `requirements.txt`.