# Fieldmark frontend

React + TypeScript + Vite interface for the Visual Defect Inspector API.

## Run locally

From the repository root, start the API:

```bash
uv run uvicorn app.main:app --reload
```

In another terminal:

```bash
cd frontend
npm install
npm run dev
```

Open the URL printed by Vite (normally `http://localhost:5173`). The dev server
proxies `/health` and `/inspections` to the API at `http://127.0.0.1:8000`.
Set `VITE_API_BASE_URL` when the API is hosted at a different origin.

## Checks

```bash
npm run lint
npm run build
```

The inspection form accepts JPEG, PNG, and WebP files up to 10 MB. It submits
photos to `POST /inspections/produce` and displays the returned grade, confidence,
defects, bounding box, and recommended action.
