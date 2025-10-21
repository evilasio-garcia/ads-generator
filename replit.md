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

### October 21, 2025 - Complete File Upload System with Slot Management (FINAL)

**System Overview:**
- Maximum 10 files processed per generation (hard limit)
- Dynamic slot allocation based on user selections
- Real-time visual feedback with color-coded status indicators
- Drag-and-drop reordering to change file priority

**Core Algorithm:**
1. Build list of files to process: valid + checked, max 10 in order
2. Calculate available slots: 10 - processed files
3. Distribute slots to next valid unchecked files
4. Render with correct numbering (1/10 through 10/10 only)

**Visual Status Indicators:**
- 🟢 **Green** "✓ Será usado (X/10)": File will be processed (numbered 1-10)
  - Checkbox: enabled + checked
  - First column: green background
  
- 🔵 **Blue** "○ Aguardando seleção": Slot available, awaiting user selection
  - Checkbox: enabled + unchecked
  - First column: blue background
  - Message: "Marque o checkbox para usar"
  
- ⚪ **Gray** "Não será usado": No slot available (limit reached)
  - Checkbox: disabled + unchecked
  - First column: gray background
  - Message: "Limite de 10 arquivos atingido"
  
- 🔴 **Red** "❌ Inválido": File validation failed
  - Checkbox: disabled + unchecked
  - First column: red background
  - Error message displayed (e.g., "Arquivo muito grande", "Tipo não suportado")

**Slot Management Behavior:**

*Scenario 1: Upload 15 valid files*
- Files 1-10: Green, checkbox checked, "Será usado (1/10)" through "(10/10)"
- Files 11-15: Gray, checkbox disabled, "Não será usado"

*Scenario 2: Uncheck file #3*
- File #3: Blue, checkbox unchecked (but enabled), "Aguardando seleção"
- File #11: Automatically gains slot → Blue, checkbox enabled, "Aguardando seleção"
- Files 1-2, 4-10: Remain green, renumbered (1/10) through (9/10)
- Files 12-15: Still gray (no slots)

*Scenario 3: Check file #11*
- File #11: Green, "Será usado (10/10)"
- File #12: Loses slot → Gray, checkbox disabled, "Não será usado"
- File #3: Remains blue (still has slot available)

*Scenario 4: Uncheck 3 files (#2, #5, #8)*
- Files #2, #5, #8: Blue, "Aguardando seleção"
- Files #11, #12, #13: Automatically gain slots → Blue
- Files 14-15: Still gray
- 7 files will be processed, numbered (1/10) through (7/10)

*Scenario 5: Drag file #15 to position #1*
- System recalculates entire slot allocation
- File priority changes based on new order
- Slot distribution updates automatically

**Client-Side Validation:**
- File type: Images (PNG, JPG, JPEG, GIF, WEBP) and text files (.txt)
- File size: Maximum 5MB per file
- Total size: Maximum 20MB aggregate
- Immediate validation before server upload
- Revalidation when LLM changes (different models support different file types)

**Drag & Drop Reordering:**
- Drag handle icon (☰) in first column
- Color-coded to match file status (green/blue/gray/red)
- Visual feedback during drag (opacity, border highlight)
- Instant recalculation of slots after reorder

**General Warnings Section:**
- Real-time counter: "X de Y arquivo(s) válido(s) será(ão) usado(s)"
- Warning when >10 files uploaded
- Alert when total size exceeds 20MB
- Individual file error messages for invalid files

**Key Implementation Details:**
- Deterministic slot allocation: recalculated on every render
- No mutation of user intent (enabled state preserved unless forced by limits)
- Processing number strictly limited to 1-10 range
- Slot availability calculated as: 10 - (checked valid files)
- Next available slots filled by unchecked valid files in order

**Testing:**
See TESTE_FILE_UPLOAD.md for comprehensive test scenarios and expected behaviors

### October 21, 2025 - File Upload for Product Data
- **Upload Multiple Files:**
  - Added file upload component after LLM selection
  - Supports images (PNG, JPEG, GIF, WEBP) and text files (.txt)
  - Maximum file size: 5MB per file
  - Files are kept in memory during the session (not persisted)
  - Table display with checkboxes to enable/disable files
  - Remove button for each file

- **File Validation:**
  - Server-side validation of file types and sizes
  - Maximum 10 files per upload
  - Maximum 5MB per file
  - Maximum 20MB total aggregate size
  - Rejects unsupported file types with clear error messages
  - Text files limited to 10k characters to avoid token limits
  - Returns detailed warnings about rejected files

- **LLM Integration with Files:**
  - Backend modified to accept FormData with files
  - Files are encoded to base64 and sent to LLM
  - OpenAI uses GPT-4o (vision model) when files are present
  - Gemini uses inline_data for images
  - Text files appended to prompt with clear labeling

- **Strict Instructions for LLM:**
  - Product characteristics MUST come ONLY from uploaded files
  - No invention or assumptions about specs, dimensions, materials, etc.
  - Creative freedom allowed for copy, marketing, and sales techniques
  - But always based on real characteristics from files

- **Backward Compatibility:**
  - Endpoint supports both FormData (with files) and JSON (without files)
  - Existing functionality unchanged when no files are uploaded
  - python-multipart dependency already present in requirements.txt

### October 19, 2025 - Regeneration Improvements
- **Fixed Regenerate with Prompt:**
  - Fixed regenerate with prompt functionality for FAQ and Cards
  - Previously: System would ignore current content and just create variation
  - Now: System properly **combines** current content with new prompt information
  - Example: Adding "Peso: 41g, C:10 x L:4 x A:25cm" now enriches the existing FAQ answer instead of replacing it
  - Backend now distinguishes between: regenerate (variation) vs regenerate with prompt (improve & complete)

- **Improved Automatic Regeneration:**
  - Strengthened backend instructions to force significantly different content on regeneration
  - Added explicit "DO NOT repeat" instructions to the AI model
  - Now generates truly different variations instead of similar/identical content

- **Version History Always Created:**
  - FAQ and Card regenerations now ALWAYS create new version entries
  - Even if AI returns identical content, it's tracked for transparency
  - Navigation buttons (◀ ▶) and version counter now properly appear after regeneration

### October 20, 2025 - TTS Playlist Fixes
- **Fixed FAQ Enable/Disable During Playback:**
  - Fixed critical bug where disabling a FAQ during playback would cause the system to skip items
  - Problem: FAQ labels change when items are disabled (FAQ 2 becomes FAQ 1 if FAQ 1 is disabled)
  - Solution: Each FAQ/Card now tracks its `originalIndex` in the history array
  - Playlist intelligently maintains the current reading position by comparing originalIndex instead of labels
  - Highlighting now correctly targets the right FAQ/Card element using originalIndex
  - Works correctly even when multiple FAQs are enabled/disabled during playback

- **Improved Description Scroll:**
  - Fixed auto-scroll issues in long descriptions with multiple paragraphs
  - Now uses mirror element technique to measure real text height including word-wrap
  - Accurately handles line breaks, word wrapping, and paragraph spacing

### October 19, 2025 - Text-to-Speech Word Highlighting
- **Real-time Word Highlighting:**
  - Words are highlighted in real-time as they're being read by TTS
  - Uses native text selection for textarea/input fields (blue highlight)
  - Auto-scroll within text fields to keep current word visible
  - Auto-scroll page to keep current section visible
  - Highlight clears automatically when Stop is pressed

- **Improved Stop Button:**
  - Stop button now resets playback to the beginning (Título)
  - Clears all highlights and selections

### October 19, 2025 - Interface Reset on New Generation
- **Automatic History Reset:**
  - When clicking "Gerar conteúdo", the system now resets all history and interface
  - This ensures data coherence when switching products
  - Clears: titles, descriptions, FAQs, cards, and all version history

### October 18, 2025 - UI Enhancements and Visual Feedback
- **Icon Updates:**
  - Changed all regenerate buttons to use sparkles (✨) icon instead of circular arrow
  - Swapped copy description icons: filled for "with FAQ", outline for "without FAQ"
  - Updated FAQ approval icon: check-circle when enabled, x-circle when disabled
  
- **Card Copy Tracking:**
  - Added visual feedback for card copy buttons (outline → filled when clicked)
  - Cards show yellow border when 1 item copied, green border when both copied
  - Copy state resets when navigating between versions or regenerating
  
- **TTS Improvements:**
  - Floating TTS controls remain at top when scrolling
  - Active Play/Pause buttons show blue background with white icon
  - Fixed pause/resume to maintain position instead of restarting

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
