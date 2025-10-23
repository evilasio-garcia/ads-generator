# Ads Generator — Multi-Marketplace Ad Generator

## Overview

This Python FastAPI application generates optimized marketplace advertisements (title, description, FAQ, and visual cards) for multiple platforms including Amazon, Mercado Livre, Shopee, Magalu, and Shein. Its purpose is to streamline the ad creation process for various e-commerce platforms. Key capabilities include multi-marketplace support, dual LLM integration (OpenAI and Google Gemini), mock mode for testing, version history for generated content, text-to-speech functionality, customizable templates, and an optional Tiny ERP integration for automated product data retrieval. The project aims to provide a comprehensive solution for efficient and effective online advertisement generation.

## User Preferences

None specified yet.

## System Architecture

The application uses a Python 3.11 FastAPI backend and a static HTML frontend with Tailwind CSS (CDN). It integrates with OpenAI and Google Gemini for LLM capabilities and runs on a Uvicorn ASGI server.

**UI/UX Decisions:**
- **File Upload System:** Supports up to 10 files (images and text) with client-side validation (file type, size limits). Features dynamic slot allocation, color-coded status indicators (Green: will be used; Blue: awaiting selection; Gray: no slot; Red: invalid), and drag-and-drop reordering with real-time slot recalculation. Visual feedback is provided for file processing and validation.
- **Tiny ERP Integration (UI/UX Layer):** Provides a UI foundation for configuring multiple Tiny ERP instances and retrieving product data. It includes sections for "Origem de dados" (data source selection), "Informações cadastrais" (registration info like GTIN, SKU, dimensions, weight), and "Informações de preço" (pricing details including wholesale tiers). The system operates in "Manual Mode" (editable fields) or "Tiny Mode" (read-only, auto-filled mock data when configured). All fields have "Copiar conteúdo" (Copy content) buttons with visual feedback. Wholesale price table includes copy buttons for each row (quantity and price), reading values directly from inputs to capture uncommitted edits.
- **Text-to-Speech (TTS):** Includes real-time word highlighting during playback, auto-scrolling to keep the current word visible, and fixed controls that remain at the top during scrolling. The stop button resets playback to the beginning.
- **Content Generation & Regeneration:** "Gerar conteúdo" resets all history and interface elements. Regeneration for FAQ and Cards now properly combines current content with new prompt information. Automatic regeneration forces significantly different content variations.
- **Version History:** FAQ and Card regenerations always create new version entries, tracked with navigation buttons.
- **Visual Feedback:** Regenerate buttons use sparkle icons. Copy description icons differentiate between "with FAQ" and "without FAQ." FAQ approval icons show check-circle for enabled and x-circle for disabled. Card copy buttons provide visual feedback (outline to filled icon) and border color changes (yellow for one item copied, green for both).

**Technical Implementations:**
- **API Endpoints:** `GET /` serves the frontend; `POST /api/generate` generates ad content; `POST /api/regen` regenerates specific fields.
- **Configuration:** Stores API keys, prompt templates, and marketplace rules in browser localStorage.
- **Mock Mode:** Activates when no API keys are provided, using predefined content examples.
- **LLM Integration with Files:** Backend accepts FormData with files, encoding them to base64 for LLM processing (GPT-4o for OpenAI, inline_data for Gemini). Text files are appended to prompts with clear labeling. Strict instructions ensure product characteristics come *only* from uploaded files.
- **State Management:** `integrationMode` variable tracks "manual" or "tiny" mode, switching automatically.
- **Replit Environment:** Uses `uvicorn app:app --host 0.0.0.0 --port 5000 --reload`. Optional environment variables (`OPENAI_API_KEY`, `GEMINI_API_KEY`, etc.) can be set for LLM features.

## External Dependencies

- **LLM Providers:** OpenAI, Google Gemini
- **Frontend Framework:** Tailwind CSS (via CDN)
- **ASGI Server:** Uvicorn
- **Python Libraries:** FastAPI, python-multipart (for file uploads), and other dependencies listed in `requirements.txt`.
- **Optional Integration:** Tiny ERP (future API integration planned)