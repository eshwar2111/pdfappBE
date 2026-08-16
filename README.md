# PDF Intelligence & Collaboration — Backend

FastAPI service for PDF upload, AI summarisation, retrieval-grounded chat,
link sharing and collaborative commenting.

Frontend repository: **`pdf-intelligence-frontend`** (React + TypeScript + Vite).

---

## Contents

- [Stack](#stack)
- [Architecture](#architecture)
- [Running locally](#running-locally)
- [Environment variables](#environment-variables)
- [API surface](#api-surface)
- [The AI implementation](#the-ai-implementation)
- [Security model](#security-model)
- [Guest identity](#guest-identity)
- [Trade-offs and scope decisions](#trade-offs-and-scope-decisions)

---

## Stack

| Concern | Choice |
|---|---|
| API | Python 3.11+, FastAPI (async), Uvicorn |
| Database | PostgreSQL 17 + `pgvector` |
| ORM / migrations | SQLAlchemy 2.0 (async) + Alembic |
| PDF extraction | `pypdf`, with `pdfplumber` fallback |
| LLM + embeddings | Google Gemini (`gemini-3.7-flash`, `gemini-embedding-001`) |
| Passwords | Argon2id (`argon2-cffi`) |
| Tokens | JWT (`pyjwt`) |
| File storage | Azure Blob Storage (private + SAS), local filesystem for dev |
| Email | Resend, with a console backend for local development |
| Search | Hybrid — pgvector (dense) + Postgres full-text (lexical) + pg_trgm (filenames), fused with RRF |

---

## Architecture

```
HTTP
 │
 ▼
Router          api/v1/routers/*     HTTP only — status codes, response models,
 │                                   and the authorization dependency
 ▼
Controller      controllers/*        orchestration; owns the transaction boundary
 │
 ▼
Service         services/*           business rules; provider- and HTTP-agnostic
 │
 ▼
Repository      repositories/*       SQLAlchemy queries; no business logic
 │
 ▼
PostgreSQL + pgvector
```

Two adapter layers hang off the side, each behind an abstract port so the
implementation is swappable without touching the layers above:

```
ai/provider.py       AIProvider  ──▶  ai/gemini_provider.py
storage/base.py      BlobStorage ──▶  storage/azure_blob.py | local_storage.py
jobs/queue.py        JobQueue    ──▶  BackgroundTaskQueue (Service Bus adapter documented below)
```

### Database connections

One process-wide `AsyncEngine` owning a connection pool; one `AsyncSession` per
request, closed when the request ends. There is no global connection and no
shared session — that is what makes concurrent users safe. The engine is
configured with `pool_pre_ping` so a connection dropped by the database (or by
a serverless tier resuming) is detected and replaced rather than handed to a
request.

The transaction boundary lives in the controller, so a request touching three
repositories still commits or rolls back as one unit.

### Document processing pipeline

```
POST /documents ──▶ validate ──▶ blob storage ──▶ DB row (UPLOADED) ──▶ commit ──▶ 201
                                                                          │
                                                          (after response is flushed)
                                                                          ▼
                                          PROCESSING ──▶ extract ──▶ chunk ──▶ embed
                                                                          │
                                                          summarise ──▶ READY | FAILED
```

Upload returns immediately; the client polls the document until it leaves
`PROCESSING`. Every failure path terminates on a typed
`ProcessingFailureReason`, so a document is never stuck in `PROCESSING` and the
UI can explain *what* went wrong (a scanned PDF says so, rather than showing an
empty summary).

---

## Running locally

### 1. Database

`pgvector` is not part of a stock PostgreSQL install, so the compose file uses
an image that ships with it:

```bash
docker compose up -d
```

Using an existing PostgreSQL instead? Install the extension, then point
`DATABASE_URL` at it — the first migration runs `CREATE EXTENSION IF NOT EXISTS
vector`.

### 2. Application

```bash
python -m venv .venv
.venv\Scripts\activate          # Windows
# source .venv/bin/activate     # macOS / Linux

pip install -r requirements.txt

cp .env.example .env            # then edit — see below
alembic upgrade head

uvicorn app.main:app --reload --port 8000
```

Interactive API docs: <http://localhost:8000/docs>

### 3. Minimum configuration

Two values must be set before the app will start:

```bash
# A real secret — the app refuses to boot with the placeholder still in place.
python -c "import secrets; print(secrets.token_urlsafe(64))"
```

- `JWT_SECRET` — the value printed above
- `GEMINI_API_KEY` — from <https://aistudio.google.com/apikey> (free tier is sufficient)

`STORAGE_BACKEND=local` is the default, so no Azure account is needed for
development.

---

## Environment variables

Every variable is documented in [`.env.example`](.env.example). `.env` is
gitignored and no key is ever sent to the browser — the frontend calls this
API, and only this API talks to Gemini.

The ones that matter most:

| Variable | Purpose |
|---|---|
| `DATABASE_URL` | asyncpg URL. Alembic uses the same one. |
| `JWT_SECRET` | Signs user tokens, guest tokens and local file URLs. |
| `GEMINI_API_KEY` | Server-side only. |
| `STORAGE_BACKEND` | `local` or `azure`. |
| `AZURE_STORAGE_CONNECTION_STRING` | Required when `STORAGE_BACKEND=azure`. |
| `CORS_ORIGINS` | Comma-separated. Also the base for generated share links. |
| `GEMINI_EMBEDDING_DIMENSIONS` | Must match the migration's `EMBEDDING_DIM` (768). |
| `EMAIL_BACKEND` | `console` (logs only) or `resend`. |
| `RESEND_API_KEY` | Required when `EMAIL_BACKEND=resend`. |
| `EMAIL_FROM` | Must be a verified Resend sender. |

Two helper scripts exist for the parts that depend on external services:

```bash
python scripts/list_models.py                    # which Gemini models this key can use
python scripts/send_test_email.py you@you.com    # verify email credentials in isolation
```

`EMAIL_BACKEND=console` is the default, so share notifications and the whole
password-reset flow work end to end with no provider configured — the reset
link is written to the application log.

Changing the embedding dimension requires a migration and re-embedding every
document — the vector column width is fixed at the schema level.

---

## API surface

All paths are prefixed with `/api/v1`.

### Auth
| Method | Path | Access |
|---|---|---|
| `POST` | `/auth/signup` | public |
| `POST` | `/auth/login` | public |
| `POST` | `/auth/forgot-password` | public |
| `POST` | `/auth/reset-password` | public (token) |
| `GET` | `/auth/me` | user |

### Documents
| Method | Path | Access |
|---|---|---|
| `POST` | `/documents` | user |
| `GET` | `/documents` | user (own only) |
| `GET` | `/documents/search?q=` | user (own only) |
| `GET` | `/documents/{id}` | `VIEW` |
| `GET` | `/documents/{id}/file` | `VIEW` |
| `DELETE` | `/documents/{id}` | `MANAGE` (owner) |

### Sharing
| Method | Path | Access |
|---|---|---|
| `POST` | `/documents/{id}/shares` | `MANAGE` |
| `GET` | `/documents/{id}/shares` | `MANAGE` |
| `DELETE` | `/documents/{id}/shares/{share_id}` | `MANAGE` |
| `GET` | `/shares/{token}` | **public** — preview |
| `POST` | `/shares/{token}/session` | **public** — start guest session |

### Comments
| Method | Path | Access |
|---|---|---|
| `GET` | `/documents/{id}/comments` | `VIEW` |
| `POST` | `/documents/{id}/comments` | `COMMENT` |
| `PATCH` | `/documents/{id}/comments/{comment_id}` | `COMMENT` + author |
| `DELETE` | `/documents/{id}/comments/{comment_id}` | `COMMENT` + author, or owner |

### Chat
| Method | Path | Access |
|---|---|---|
| `GET` | `/documents/{id}/chat` | `CHAT` |
| `POST` | `/documents/{id}/chat` | `CHAT` |
| `POST` | `/documents/{id}/chat/stream` | `CHAT` — server-sent events |

Guests reach the `VIEW`/`COMMENT`/`CHAT` endpoints with exactly the same
`Authorization: Bearer` header a registered user uses. The token kind differs;
the routes do not.

---

## The AI implementation

**Provider: Google Gemini**, behind the `AIProvider` port in
`app/ai/provider.py`. `gemini-3.7-flash` for generation, `gemini-embedding-001`
at 768 dimensions for vectors. Chosen for a free tier that covers both
generation and embeddings without billing setup — which matters when a reviewer
is exercising a deployed app. Swapping providers means one new adapter and one
line in `app/api/deps.py`.

Model names are configuration, not code — no service, controller or router
mentions a model. If a name is retired (Google closes older models to new API
keys), run `python scripts/list_models.py` to list what your key can use and
change one line in `.env`.

The embedding model is the exception: it cannot be changed freely, because
stored vectors are only comparable with query vectors from the same model. A
switch requires re-embedding every document.

### Extraction

`pypdf` first. Pages that come back under ~40 characters are retried with
`pdfplumber`, whose char-level extraction handles some layouts better. Text is
then normalised: hyphenated line breaks rejoined, whitespace runs collapsed,
blank-line runs squeezed.

If the *entire* document extracts empty it is almost certainly a scan. That is
recorded as `NO_EXTRACTABLE_TEXT` and the LLM is never called — asking a model
to summarise nothing produces a confident, wrong summary, which is worse than
an honest failure. OCR is out of scope.

### Chunking

Paragraph-packing to a ~900-token target with ~150 tokens of overlap, carrying
the page range on each chunk.

- **Paragraphs, not fixed windows** — splitting mid-sentence produces chunks
  that embed poorly and read badly when surfaced as a citation.
- **Overlap** — a fact spanning a boundary would otherwise be retrievable from
  neither side.
- **Oversized paragraphs** (dense contract clauses, tables) are split on
  sentence boundaries so they still fit.
- Token counts are estimated at ~4 chars/token. The estimate only sizes chunks,
  so a few percent of drift is harmless, and it avoids shipping a tokenizer that
  must stay in sync with the provider's.

### Summarisation prompt

In `app/ai/prompts.py`. The design principles, stated there and applied
throughout:

1. **Refusal must be cheaper than invention.** Every prompt names the
   can't-determine escape hatch explicitly. Models hallucinate hardest when the
   instructions imply an answer is mandatory.
2. **Specificity beats adjectives.** "Concise and useful" tells a model
   nothing. Naming what to extract — parties, dates, amounts, obligations,
   findings — is what separates a useful summary from a restatement.

The prompt also bans the openings that produce filler ("This document…"),
requires the first sentence to name what the document *is*, and gives a worked
contrast between a useful sentence and a vacuous one.

### Long documents

Two paths, chosen on estimated tokens rather than page count (a 40-page deck
can be shorter than a 10-page contract):

- **≤ 24k tokens** — single pass over the full text.
- **> 24k tokens** — map-reduce. Chunks are grouped into sections of 12; each
  section is reduced to ≤ 6 factual bullets; the notes are then summarised into
  the final 3–5 sentences. The reduce prompt is told to cover the document as a
  whole rather than dwelling on whichever section produced the most notes.

Sections are processed sequentially, not concurrently — the free tier
rate-limits hard enough that a parallel burst fails more often than it saves
time at this scale.

**Chat never faces this problem**, because it retrieves rather than reading the
whole document: a 500-chunk PDF sends ~8 chunks to the model.

### Chat: retrieval-grounded, conversational

```
question
   ├─▶ embed (RETRIEVAL_QUERY)
   ├─▶ pgvector cosine search, WHERE document_id = ... , top 8
   ├─▶ drop anything below 0.35 similarity
   ├─▶ re-order by position in the document
   └─▶ system instruction + last 5 turns + passages + question ──▶ Gemini
```

Details that matter:

- **Query and document embeddings are asymmetric.** Passages are embedded with
  `RETRIEVAL_DOCUMENT`, questions with `RETRIEVAL_QUERY`. Using one task type
  for both measurably degrades retrieval.
- **The similarity floor is real filtering, not padding.** Weak chunks are
  dropped rather than used to fill the context — irrelevant context invites the
  model to answer from something adjacent to the question.
- **Passages are ordered by document position, not by score.** The model reads
  them as a narrative; showing page 9 before page 2 because it scored
  fractionally higher makes cross-referencing worse.
- **Conversation memory is the last 5 turns** (10 messages), bounded so a long
  conversation cannot crowd out the grounding passages. The system prompt tells
  the model to resolve follow-up references against history but never to treat
  its own earlier answers as a source of new facts.
- **Citations are structured, not just inline.** Each answer persists the
  chunks that grounded it, so the UI can show sources and the answer stays
  auditable afterwards.
- **Temperature 0.15** — this is extraction, not composition.

### Hybrid retrieval

Both chat grounding and dashboard search are **hybrid**, not pure vector.

Dense and lexical retrieval fail in opposite directions:

- **Embeddings** capture meaning — "what if I quit early?" finds a clause
  headed *Termination for convenience* — but they blur rare tokens. A search
  for a company name like "MariApps" returned every document at 58–68%
  similarity, because proper nouns cluster together in embedding space.
- **Full-text ranking** is exact on rare terms and useless for paraphrase.

So three retrievers run and their **rankings** are fused:

| Retriever | Mechanism | Index |
|---|---|---|
| Filename | `pg_trgm` similarity | GIN trigram |
| Lexical | `ts_rank_cd` over a generated `tsvector` | GIN |
| Dense | pgvector cosine distance | HNSW |

`ts_rank_cd` is not literally Okapi BM25, but it is the same family — term
frequency weighted by inverse document frequency, length-normalised — and it
needs no service beyond the database already in use.

**Fusion is by Reciprocal Rank Fusion**, `score = Σ 1 / (60 + rank)`, rather
than by blending scores. Trigram similarity, `ts_rank_cd` and cosine distance
live on unrelated scales; combining them numerically would mean inventing a
conversion factor and retuning it per corpus. RRF consumes only each
retriever's ordering, so there is nothing to calibrate — and a chunk both
retrievers found accumulates two contributions, so cross-retriever agreement
outranks a single retriever's favourite. That is the property that fixes the
rare-proper-noun case.

Each retriever is over-fetched 3× before the cut, so fusion has room to promote
something one side ranked mid-table. The lexical query uses
`websearch_to_tsquery`, which accepts raw user input — quoted phrases,
`-exclusions`, unbalanced quotes — without raising or needing sanitisation.

Retrieval is always scoped: chunk queries carry `WHERE document_id = …` (chat)
or `IN (…)` over the caller's own documents (search), so neither retriever can
reach a document the caller is not authorised for. If the embedding call fails,
search degrades to lexical-only rather than erroring — a keyword-grounded
answer beats no answer.

---

## Security model

**Passwords** — Argon2id via `argon2-cffi`. No column can hold a plaintext
password. Login hashes a dummy password when the account does not exist, so
response timing does not reveal which emails are registered. Hashes written
under older parameters are transparently upgraded on next login.

**Authorization** — enforced entirely server-side, in one dependency:

```python
access = Depends(require_document(Permission.COMMENT))
```

Because it is a FastAPI dependency, it runs before the handler body — there is
no path into a protected handler that skips it. React receives the caller's
effective permissions only so it can render the right affordances; every write
is re-checked on the server.

**Enumeration** — document ids are UUIDs, and a caller with no relationship to
a document gets `404`, not `403`. A 403 would confirm the id exists.

**Data isolation** — every dashboard query starts from an owner-scoped
`SELECT` built in one place (`DocumentRepository._owned`), so the tenant filter
cannot be forgotten at a call site. Vector search carries `WHERE document_id =
…` inside the SQL, so retrieval physically cannot return a chunk the caller is
not authorized to see.

**Files** — blobs are private. Each read mints a fresh, read-only SAS valid for
minutes, and only after authorization has passed. The local backend mirrors the
same shape with a signed, expiring token.

**Share tokens** — 256 bits of entropy; only the SHA-256 digest is stored, so a
leaked database does not yield working links. Revocation is immediate: guest
tokens are re-validated against the share row on every request rather than
trusted until expiry.

**Password reset** — the token is 256 bits of entropy, stored only as a
SHA-256 digest, single-use, and expires in 30 minutes. It is burned the moment
it is redeemed, so a mail client that pre-fetches links cannot consume it, and
requesting a new link voids any outstanding one. `POST /auth/forgot-password`
returns an identical response whether or not the address is registered —
otherwise the endpoint becomes an account-enumeration oracle.

**Secrets** — `.env` is gitignored, `.env.example` carries placeholders only,
and the Gemini and Resend keys are read exclusively on the server. Nothing
reads `os.environ` outside `app/core/config.py`.

---

## Guest identity

The requirement is that invited users can comment without an account. The
design principle: **a guest is a server-issued principal with a database row,
not a display name the client attaches to each write.**

```
GET  /shares/{token}          → preview: filename, owner, granted permissions
POST /shares/{token}/session  → { display_name }
                              → creates guest_sessions row
                              → returns a JWT scoped to ONE document
```

The guest then calls the same endpoints a registered user calls, with the same
`Authorization: Bearer` header. `AuthorizationService.resolve_principal`
branches on the token's `typ` claim and returns a `Principal` either way:

```python
@dataclass(frozen=True)
class Principal:
    kind: PrincipalKind          # USER | GUEST
    id: UUID
    display_name: str
    permissions: frozenset[Permission]
    document_scope: UUID | None  # pinned for guests, None for users
```

Nothing downstream branches on user-vs-guest. That is what keeps guest support
from leaking into every endpoint.

Three properties fall out of the design:

- **`CreateCommentRequest` has no author field.** Authorship is derived from
  the caller's principal, so posting as the document owner is not expressible —
  not merely rejected.
- **`comments` has two nullable author columns and a `CHECK` requiring exactly
  one.** There is no representable state with an unattributed or
  doubly-attributed author, and it is Postgres enforcing it, not convention.
- **Guest chat is keyed to the guest session**, so history survives a refresh
  but is invisible to the next visitor on the same link.

Guests are always rendered with a "Guest" badge, so a visitor who types the
owner's name into the display-name field still cannot visually impersonate them.

---

## Trade-offs and scope decisions

Written plainly, per the brief's request for transparency.

**No dedicated vector database.** The original design used Azure Cosmos DB for
vectors. It was cut in favour of `pgvector` in the same PostgreSQL instance,
because at this corpus size (a few thousand vectors) a separate vector store
buys nothing and costs a distributed-consistency problem: chunk writes and the
document status update now commit in one transaction, and the authorization
filter is part of the similarity query rather than a metadata filter in a
second system. The retrieval interface is abstracted, so a dedicated ANN index
is a one-file change if the corpus grows.

**No Service Bus.** The design called for a queue and a separate worker
process. The queue *abstraction* is kept (`jobs/queue.py`), but the deployed
implementation is in-process via FastAPI `BackgroundTasks`. Reasons: an App
Service tier without Always On kills a polling consumer, and the user-visible
behaviour — upload returns immediately, status polls to READY — is identical.
Adding a Service Bus adapter means implementing `JobQueue` and changing one
factory line; nothing in the pipeline changes. The honest limitation: with
in-process execution, a process restart mid-pipeline leaves a document in
`PROCESSING` (there is no redelivery). A durable queue is the correct fix at
real scale.

**No OCR.** Scanned PDFs are detected and reported as `NO_EXTRACTABLE_TEXT`
rather than silently summarised into nonsense.

**Email sending is limited by the sender domain.** The default
`onboarding@resend.dev` sender requires no DNS setup but only delivers to the
Resend account owner's own address. Sending to arbitrary invitees needs a
verified domain — a Resend account configuration step, not a code change. The
share URL is always returned in the API response, so email is a convenience
and never the only way to obtain a link.

Email delivery is treated as a side effect throughout: it happens *after* the
transaction commits, and a failure is logged rather than raised. A share link
or a reset token remains valid whether or not the message arrives.

**Comment formatting** is a markdown subset stored as written and rendered as
React elements — never as HTML — rather than a rich-text document model.

**Rate limiting** is dependency-ready (`slowapi` is installed) but not applied
per-route. In production, guest `POST /comments` and `/chat` should be limited
per guest session.

---

## Project layout

```
app/
├── main.py                  app assembly, error handlers, health checks
├── api/
│   ├── deps.py              the dependency graph, principals, require_document
│   └── v1/
│       ├── router.py
│       └── routers/         auth, documents, shares, comments, chat, files
├── controllers/             orchestration + transaction boundary
├── services/                business rules
│   ├── authorization_service.py   principal resolution + document authz
│   ├── auth_service.py
│   ├── document_service.py
│   ├── sharing_service.py
│   ├── comment_service.py
│   ├── chat_service.py
│   └── processing_service.py
├── repositories/            SQLAlchemy queries
├── models/                  ORM
├── schemas/                 Pydantic DTOs
├── domain/                  enums, Principal
├── ai/                      provider port, gemini adapter, extraction,
│                            chunking, embeddings, retrieval, summarisation,
│                            prompts
├── storage/                 blob port, azure + local adapters
├── email/                   email port, resend + console adapters, templates
├── jobs/                    queue port, background processor
└── core/                    config, database, security, logging, middleware,
                             exceptions
migrations/                  Alembic (0001 schema, 0002 hybrid search,
                             0003 password reset)
scripts/                     list_models.py, send_test_email.py
```
