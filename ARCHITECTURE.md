# Architecture — Fieldmark

Software-only product grading for farmers. A photo goes in, a validated AI finding and
deterministic grading rules come out as a verdict, quality grade, and recommended action, and any
inspection can be turned into an AI-written PDF report. This document describes the system as it
is implemented, not as it is planned; the README keeps the short version for first-time readers.

## 1. Purpose and scope

**Goals.** Let a farmer upload or capture a product photo, receive a pass/defect verdict with a
confidence score and a plain-language explanation, keep a local inspection history, and download
a professional PDF report that folds in an optional voice note.

**Non-goals (out of scope per the project SOW).** Hardware and sensors, user accounts,
multi-tenancy, payments, production deployment and scaling, and training models from scratch.
Inference uses a hosted API model (Groq) with an optional local Ollama provider behind the same
interface.

**Chosen user type.** Farmer (product grading after harvest): bruises, rot, mold, and deformities
on tomato, apple, banana, and orange photos, with quality grades (A / B / Reject) and
sell-or-discard advice.

## 2. System context

```mermaid
flowchart LR
    Farmer([Farmer]) --> UI["React SPA (Vite)"]
    UI --> API["FastAPI /inspections"]
    API --> SVC["Inspection service"]
    API --> RPT["Report service"]
    SVC --> DET["VisionDetector"]
    DET --> Groq["Groq API"]
    DET --> Ollama["Ollama (optional)"]
    RPT --> ReportAI["Groq report + Whisper"]
    SVC --> DB[("SQLite")]
    RPT --> DB
    RPT --> PDF["PDF renderer (reportlab)"]
```

The browser talks only to the FastAPI process, which in turn talks to exactly one configured
vision provider. Groq is the default and the only provider used by default configuration; Ollama
exists for the optional local-model path and is never selected automatically.

## 3. Components

| Path | Responsibility |
|---|---|
| `app/api/routes_inspection.py` | HTTP boundary: multipart parsing, size/format guards, domain-to-HTTP error mapping, pagination limits |
| `app/services/inspection_service.py` | Image validation (PIL), deterministic grading rules, verdict notes, persistence |
| `app/services/report_service.py` | Orchestrates transcription, AI narrative, PDF rendering, and report persistence |
| `app/ai/provider_factory.py` | Builds the configured `VisionDetector` from settings (no network at construction) |
| `app/ai/produce_groq.py` | Groq vision provider: base64 image prompt, JSON-mode response, schema validation |
| `app/ai/produce_ollama.py` | Local Ollama provider: stdlib HTTP client, response size cap, schema validation |
| `app/ai/groq_report.py` | Whisper transcription and one-shot report writing with validated JSON output |
| `app/ai/schemas.py` | Pydantic contracts: `Finding`, `InspectionResult`, `InspectionReportText`, history models |
| `app/ai/prompts.py` | System/user prompts for the vision model, including injection-hardening rules |
| `app/reports/pdf_renderer.py` | One-page reportlab PDF: metadata, evidence photos, quick-scan card, narrative |
| `app/storage/inspection_repository.py` | Parameterized SQLite access, thumbnail encoding, schema migration |
| `app/core/config.py` | Environment-driven `Settings` with URL validation |
| `frontend/src/services/api.ts` | Single frontend API boundary: typed calls, `ApiError`, URL building |
| `frontend/src/App.tsx` | UI: upload/camera, result panel, report controls, history, clear |

## 4. Data flow

### 4.1 Inspection

```mermaid
sequenceDiagram
    actor F as Farmer
    participant UI as React SPA
    participant API as FastAPI route
    participant S as Inspection service
    participant D as VisionDetector
    participant DB as SQLite
    F->>UI: upload / capture photo
    UI->>API: POST /inspections/product (multipart)
    API->>API: read capped at max_image_bytes + 1
    API->>S: inspect_produce(bytes) in threadpool
    S->>S: load_and_validate_image (format, verify, pixels)
    S->>D: detect(image)
    D->>D: prompt + JSON-mode call, Finding validation
    D-->>S: Finding
    S->>S: deterministic grade + verdict + notes
    S->>DB: save result_json, thumbnail, original image
    S-->>API: InspectionResult
    API-->>UI: verdict, grade, defects, severity, action
```

### 4.2 Report generation with an optional voice note

```mermaid
sequenceDiagram
    actor F as Farmer
    participant UI as React SPA
    participant API as FastAPI route
    participant R as Report service
    participant T as Whisper transcription
    participant W as Report writer
    participant DB as SQLite
    F->>UI: request PDF (+ optional voice note)
    UI->>API: POST /inspections/{id}/report
    API->>API: validate audio suffix, media type, size
    API->>R: generate_inspection_report(id, user_type, audio)
    R->>DB: load original image + stored result
    R->>T: transcribe (if audio supplied)
    R->>W: write narrative from result + transcript
    W->>W: validate InspectionReportText, check conflicts
    R->>R: reportlab render with annotated photo
    R->>DB: upsert report row (pdf + validated narrative)
    R-->>API: PDF bytes
    API-->>UI: application/pdf download
```

### 4.3 History and clearing

`GET /inspections/history` returns reverse-chronological summaries with `report_available` and
`report_source_available` flags. `DELETE /inspections/history` deletes reports first, then
inspections, inside one transaction. `GET .../thumbnail` streams the 192×192 JPEG preview.

## 5. Data model

Single SQLite file (default `data/inspections.sqlite3`), created and migrated lazily on first
connection. All queries are parameterized.

```sql
CREATE TABLE inspections (
    inspection_id  TEXT PRIMARY KEY,
    created_at     TEXT NOT NULL,        -- ISO-8601 UTC
    produce_type   TEXT NOT NULL,
    verdict        TEXT NOT NULL,        -- pass | defect | needs_review
    grade          TEXT NOT NULL,        -- A | B | Reject | Needs manual review
    thumbnail      BLOB NOT NULL,        -- 192x192 JPEG, quality 75
    original_image BLOB,                 -- retained for report evidence
    result_json    TEXT                  -- serialized InspectionResult
);

CREATE TABLE inspection_reports (
    inspection_id   TEXT PRIMARY KEY,    -- one report per inspection (upserted)
    report_id       TEXT NOT NULL UNIQUE,
    created_at      TEXT NOT NULL,
    user_type       TEXT NOT NULL,       -- farmer
    transcript      TEXT NOT NULL,       -- "" when no voice note
    report_text_json TEXT NOT NULL,      -- validated InspectionReportText
    pdf             BLOB NOT NULL
);
```

A legacy migration adds `original_image` and `result_json` to databases created before reports
stored source photos; legacy rows without those columns are rejected for reporting with HTTP 409
rather than fabricated.

**Retention rules.** Every successful inspection stores the full result, a small thumbnail, and
the original photo (required for report evidence). Clearing history deletes reports and
inspections; originals are never written anywhere outside this database.

## 6. API surface

| Method | Path | Success | Client errors | Provider/server errors |
|---|---|---|---|---|
| POST | `/inspections/product` | 200 `InspectionResult` | 400 invalid image, 413 too large | 500 storage, 502 detection |
| POST | `/inspections/produce` (legacy alias) | 200 `InspectionResult` | same | same |
| POST | `/inspections/tomato` (legacy alias) | 200 `InspectionResult` | same | same |
| GET | `/inspections/history?limit&offset` | 200 page (limit 1–100, default 20) | 422 invalid query | 500 |
| DELETE | `/inspections/history` | 204 | — | 500 |
| GET | `/inspections/history/{id}/thumbnail` | 200 JPEG | 404 | 500 |
| POST | `/inspections/{id}/report` | 200 PDF attachment | 400 invalid audio / transcript too long, 413 audio too large, 409 no source photo | 500 storage/render, 502 AI |
| GET | `/inspections/{id}/report` | 200 PDF attachment | 404 | 500 |
| GET | `/health` | 200 `{"status":"ok"}` | — | — |

Route parameters are typed as `UUID`, so malformed identifiers are rejected by FastAPI before
reaching the service layer.

## 7. AI abstraction and model swap

```mermaid
flowchart LR
    Settings["Settings (env)"] --> Factory["provider_factory.create_vision_detector()"]
    Factory -->|"VISION_PROVIDER=groq"| G["GroqVisionDetector"]
    Factory -->|"VISION_PROVIDER=ollama"| O["OllamaVisionDetector"]
    G --> Finding["Finding (validated)"]
    O --> Finding
```

- `VisionDetector` is a callable protocol (`__call__(image) -> Finding`) in `app/ai/providers.py`;
  both providers implement it and raise `DetectionError` on failure.
- **Groq** (`meta-llama/llama-4-scout-17b-16e-instruct` by default) receives a base64 JPEG with
  `response_format={"type": "json_object"}` and temperature 0 for determinism.
- **Ollama** posts to `{base_url}/api/chat` with `format=json`, a 120 s timeout, and a 64 KB
  response cap. It is opt-in (`VISION_PROVIDER=ollama`) and never a silent fallback.
- **Report path.** `GROQ_TRANSCRIPTION_MODEL` (Whisper) transcribes audio; the report writer
  defaults to the vision model when `GROQ_REPORT_MODEL` is unset.
- **Validation.** Model output is never trusted: `Finding` bounds confidence to [0, 1], discards
  non-normalized or unordered bounding boxes, and caps `visual_observation` at 240 characters.
  `InspectionReportText` forbids extra fields and cross-checks the voice-note relation: a
  `conflicts` relation requires a non-empty explanation, and an explanation without a conflict is
  rejected.

## 8. Deterministic grading rules

All thresholds are constants in `inspection_service.py`; the model only reports observations.

| Condition | Result |
|---|---|
| Product is not an edible item | No quality grade; shown as an unsupported item |
| Edible product outside the categories (for example guava) | Normal fresh/rotten rules, flagged as outside the categories |
| Product type unknown, or model unsure | Needs manual review |
| Rotten, confidence ≥ 0.60 | Reject + separate/discard advice |
| Rotten, confidence < 0.60 | Needs manual review |
| Fresh, confidence < 0.40 | Needs manual review |
| Fresh, severity high, or mold reported | Needs manual review |
| Fresh, confidence ≥ 0.70, severity none | Grade A + premium advice |
| Everything else fresh | Grade B + standard advice |

Verdicts follow the grade: review → `needs_review`, Reject or any reported defect → `defect`,
otherwise `pass`. `verdict_notes` explain the decision without exposing thresholds or repeating
model-reported confidence percentages. The vision model reports `detected_item` and
`item_is_fruit`; the service sets `in_inspection_categories` so the interface can show a normal
result for any edible product and a plain "Unsupported item" result for everything else.

## 9. Error handling

| Layer | Failure | Handling |
|---|---|---|
| Route | Oversized image/voice note | Read capped at limit + 1 byte → 413 |
| Service | Bad image (format, verify, decompression bomb, OS) | `InvalidImageError` → 400, logged |
| Provider | Groq API error, invalid JSON, missing key | `DetectionError` → 502, logged |
| Provider | Ollama network/timeout/oversize | `DetectionError` → 502 |
| Report AI | Model unavailable, rate-limited, invalid JSON, no response | Distinct actionable messages → 502 |
| Report AI | Contradiction between transcript and visual result | Required conflict explanation, validated |
| Service | Transcript longer than 800 characters | 400 with a clear message |
| Storage | Any `sqlite3.Error` / `OSError` | `HistoryStorageError` → 500, logged |
| PDF | Layout/render errors (`LayoutError`, `OSError`) | `InspectionReportError` → 500 |

Rules applied throughout: no bare excepts, no swallowed exceptions, `raise ... from exc` chaining,
module loggers (never `print`), exception *types* logged but never keys or personal data.

## 10. Security boundaries

- **Secrets** live only in environment variables / the local `.env`; `.env` is gitignored and
  excluded from the Docker build context. The Docker image contains no key material.
- **Input validation at the boundary.** Pydantic validates every model response; routes enforce
  file-type allowlists (JPEG/PNG/WEBP images; flac/m4a/mp3/mpga/ogg/wav/webm audio), size limits
  (10 MB image, 25 MB voice note), and a 25-megapixel decompression-bomb guard.
- **Prompt injection.** Voice-note transcripts are treated as untrusted data: the report prompt
  instructs the model to treat transcript text as observations, never instructions, and to
  describe both sources when they conflict instead of obeying the transcript.
- **LLM output as untrusted input.** Both the vision `Finding` and the report narrative are
  schema-validated before use; unexpected fields are rejected (`extra="forbid"`).
- **SQL injection.** All statements are parameterized; identifiers come from code, not input.
- **Path traversal.** No user-supplied path is ever opened: images and audio are processed in
  memory, database paths come from configuration, and route parameters are UUIDs.
- **No `eval` / `pickle`** on user data; JSON only.

## 11. Configuration

| Variable | Default | Purpose |
|---|---|---|
| `VISION_PROVIDER` | `groq` | `groq` or `ollama` |
| `GROQ_API_KEY` | *(empty)* | Required for the default provider |
| `GROQ_VISION_MODEL` | `meta-llama/llama-4-scout-17b-16e-instruct` | Vision model |
| `GROQ_REPORT_MODEL` | *(empty → vision model)* | Report narrative model |
| `GROQ_TRANSCRIPTION_MODEL` | `whisper-large-v3-turbo` | Voice-note transcription |
| `OLLAMA_BASE_URL` | `http://localhost:11434` | Validated absolute HTTP(S) URL |
| `OLLAMA_VISION_MODEL` | `qwen2.5vl:7b` | Local vision model |
| `INSPECTION_DB_PATH` | `data/inspections.sqlite3` | Absolute in Docker (`/app/data/...`) |

## 12. Deployment

Single container, single origin. A multi-stage `Dockerfile` builds the Vite bundle with Node,
installs locked Python dependencies with `uv`, and runs Uvicorn as non-root user `inspector`
(uid 10001). FastAPI serves the API routes first and mounts `frontend/dist` at `/` for
everything else, so the SPA and the API share one origin and no CORS configuration is needed.

- Docker Compose publishes `${APP_PORT:-8000}:8000`, injects `.env` at runtime, and mounts the
  named volume `inspection-data` at `/app/data`, so inspection history survives restarts,
  rebuilds, and `docker compose down` (only `down -v` deletes it).
- A `HEALTHCHECK` polls `/health` from inside the container.
- SQLite requires a single instance; do not scale to replicas or run on multi-node hosts.

## 13. Testing

`uv run pytest --cov=app --cov-report=term-missing` — 60 tests, 90% line coverage.

- `tests/test_api.py` — route behavior, status-code mapping, pagination, oversized/invalid
  uploads, report routes, an end-to-end happy path through the API with a mocked detector.
- `tests/test_inspection.py` — grading rules, verdict notes, invalid images.
- `tests/test_providers.py` — factory selection, key handling, provider request/response
  handling, network failure translation.
- `tests/test_reports.py` — transcription, narrative validation, conflict handling, annotated
  photo rendering, one-page layout constraints, actionable AI error mapping.
- `tests/test_config.py` — settings defaults and Ollama URL validation.

The AI model is always mocked (`monkeypatch`) in unit tests, so the suite is deterministic and
network-free; no test calls Groq.

## 14. Limitations and known gaps

- The bounding box is returned by the API and rendered in the PDF's annotated photo, but the web
  UI does not yet draw the defect overlay, and the result panel does not yet display the
  confidence score.
- No audio-duration limit is enforced (size and post-transcription length are).
- The Ollama provider is implemented and unit-tested but has no documented live run or
  performance numbers; the local-model bonus (quantization/batching metrics) is not done.
- The optional multi-inspection summary report from the SOW is not implemented.
- SQLite limits the system to one writer process; concurrent-heavy or multi-node deployments
  would need a different store (explicitly out of scope for the MVP).
