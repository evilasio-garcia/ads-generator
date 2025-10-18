# Ads Generator — Multi-Marketplace Ad Generator

## Overview

This is a Python FastAPI application that generates optimized marketplace advertisements (title, description, FAQ, and visual cards) for multiple platforms including Amazon, Mercado Livre, Shopee, Magalu, and Shein.

**Current Status:** Fully functional MVP running on Replit
**Last Updated:** October 18, 2025

## Project Architecture

### Technology Stack
- **Backend:** Python 3.11 + FastAPI
- **Frontend:** Static HTML with Tailwind CSS (CDN)
- **LLM Integration:** OpenAI and Google Gemini support
- **Server:** Uvicorn ASGI server

### File Structure
```
.
├── app.py                 # Main FastAPI application
├── static/
│   └── index.html        # Single-page frontend application
├── requirements.txt      # Python dependencies
├── README.md            # Original project README
└── replit.md           # This file
```

### Key Features
1. **Multi-Marketplace Support:** Generates content optimized for different platforms
2. **Dual LLM Support:** Works with OpenAI or Google Gemini
3. **Mock Mode:** Falls back to example content when no API keys are provided
4. **Version History:** Allows navigation through generated content versions
5. **Text-to-Speech:** Built-in reading feature for generated content
6. **Customizable Templates:** User-configurable prompts and marketplace rules

## How It Works

### API Endpoints
- `GET /` - Serves the frontend HTML
- `POST /api/generate` - Generates complete ad content (title, description, FAQ, cards)
- `POST /api/regen` - Regenerates specific fields with optional custom prompts

### Configuration
The application stores configuration in browser localStorage:
- API keys for OpenAI and Gemini
- Custom prompt templates
- Marketplace-specific rules (character limits, etc.)

### Mock Mode
When no API keys are configured, the application runs in mock mode using predefined content examples. This is useful for testing and demonstration purposes.

## Replit Environment Setup

### Port Configuration
- **Frontend Port:** 5000 (bound to 0.0.0.0 for Replit proxy compatibility)
- **Server Type:** Uvicorn with auto-reload enabled

### Workflow
The project uses a single workflow named "Server" that runs:
```bash
uvicorn app:app --host 0.0.0.0 --port 5000 --reload
```

### Environment Variables (Optional)
To enable LLM features, users can configure:
- `OPENAI_API_KEY` - OpenAI API key
- `OPENAI_BASE_URL` - Custom OpenAI endpoint (optional)
- `GEMINI_API_KEY` - Google Gemini API key
- `GEMINI_BASE_URL` - Custom Gemini endpoint (optional)

Note: These can also be configured through the UI settings modal.

## Recent Changes

### October 18, 2025 - Replit Environment Setup
- Installed Python 3.11 and all required dependencies
- Updated app.py to bind to 0.0.0.0:5000 for Replit compatibility
- Configured workflow to run FastAPI server with auto-reload
- Verified application runs successfully in mock mode
- Created project documentation

## User Preferences

None specified yet.

## Development Notes

### Running Locally
To run this project locally (outside Replit):
```bash
python -m venv .venv
source .venv/bin/activate  # On Windows: .venv\Scripts\activate
pip install -r requirements.txt
uvicorn app:app --reload --port 8000
```

### Adding API Keys
1. Click the settings gear icon in the UI
2. Enter your OpenAI or Gemini API key
3. Optionally configure custom base URLs
4. Click "Salvar" (Save)

### Production Considerations
- Replace Tailwind CDN with PostCSS build
- Add rate limiting for API endpoints
- Consider adding authentication for API key protection
- Add persistent storage (e.g., Supabase) for content history

## Future Enhancements

Potential improvements mentioned in original README:
- Persistent storage using Supabase (schema `adgen`)
- Rate limiting and authentication
- Production-ready Tailwind CSS setup
- Advanced content enrichment with SerpAPI
