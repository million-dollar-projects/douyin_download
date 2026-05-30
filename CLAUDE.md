# CLAUDE.md: Developer Instructions

## Commands
- **Install Dependencies**: `pip install -r requirements.txt`
- **Run Locally (Dev)**: `uvicorn main:app --reload --port 8000`
- **Run Locally (Production)**: `uvicorn main:app --host 0.0.0.0 --port 8000`
- **Update yt-dlp**: `pip install -U yt-dlp`
- **Test the API**:
  ```bash
  curl -X POST "http://localhost:8000/parse" \
       -H "Content-Type: application/json" \
       -d '{"url": "https://v.douyin.com/xxxxx/"}'
  ```

## Code Style
- **Python Type Hints**: Enforce type hints on all function parameters and return types.
- **Pydantic Validation**: Use Pydantic models for validated request and response mapping.
- **Error Handling**: Use `HTTPException` with appropriate status codes (400 for client errors, 500 for server issues) for all HTTP-level error handling.
- **Single File Design**: Keep the backend simple. All code lives in `main.py`.
- **Logging**: Use the standard library `logging` module to log incoming requests and parser activity.
