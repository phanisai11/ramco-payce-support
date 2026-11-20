# Web frontends

## Dev UI (`dev/`)

Static HTML/CSS/JS served by the API at **`/dev/ui/`** when you run:

```bash
uvicorn payce_support.api.app:app --reload --host 127.0.0.1 --port 8000
```

Open [http://127.0.0.1:8000/](http://127.0.0.1:8000/) (redirects to `/dev/ui/`) or [http://127.0.0.1:8000/dev/ui/](http://127.0.0.1:8000/dev/ui/).

Forms call the same-origin JSON API (`/health`, `/v1/...`). No build step.

CORS defaults include `localhost:8000` so you may also open the HTML file via a simple static server on another port if you point `PAYCE_API_ORIGIN` in `app.js` in the future; for now **prefer serving from uvicorn** so relative paths work.

**Auth:** not implemented — local simulation only. For production, put the UI behind IAP or issue JWTs to the API.
