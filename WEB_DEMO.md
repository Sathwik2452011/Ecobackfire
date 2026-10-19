# EcoBackfire web demo

Thin layer around the existing engine. Core (`src/`) is unchanged.

## Workflow (research → review → confirm → engine)

1. **Question** — the home page takes a research question.
2. **Parameters needed** — the inputs the engine really uses (`E0 EM0 EM1 L epsilon eta f phi`, read from the model).
3. **Web research** — `web/research.py` searches the web (provider behind an interface) and extracts candidate values/ranges, each with
   source title, URL, the verbatim sentence the number came from, and a status. Nothing is invented: no source ⇒
   *"Evidence insufficient — no reliable source found."* Web research unavailable ⇒ reported as such, nothing searched.
4. **Review** — a table (value, unit, evidence, source, status, edit). Conflicting sources are all shown; you choose. You can accept a
   candidate, override with your own value/range, use the engine's illustrative default (not for `f`), or mark unavailable (blocks the run).
5. **Confirm** — summary + explicit confirmation. The engine does not run before this.
6. **Engine** — `web/review.py` converts the decisions into the existing contracts and calls the same `run_mission()` as the CLI/demo.
7. **Results** — existing results rendering plus a provenance table (value used, source, range, alternatives, override).

How reviewed values reach the engine (engine's own vocabulary; no new scientific code):

| User decision | Engine representation | Engine status |
|---|---|---|
| accept web candidate | `sourced` record in a run-scoped dataset release + pinned evidence requirement; all other candidates for the input are in the same release so the engine's evidence challenges see them | `sourced_unreviewed` |
| accept registry candidate (`f`, OWID/Ember) | evidence requirement on the existing pinned release | `sourced_unreviewed` |
| override | `user_supplied` binding; note carries the researched value it replaced | assumption (never evidence) |
| illustrative default | the demo mission's own `illustrative` binding | illustrative |
| unavailable | run refused; no value is invented | — |

The engine's rule that at least one input must resolve from evidence is **not** weakened: overriding/illustrating everything is refused.

## Routes
- `GET /` page · `GET /api/web/status` · `GET /api/research/parameters`
- `POST /api/research` `{question}` → `{report, signature}` (no engine run)
- `POST /api/run-reviewed` `{report, signature, decisions[], confirm:true}` → result + provenance (no code/paths accepted)
- `GET /api/runs/<key>/artifacts/<name>` (whitelist = files the engine wrote for that run, served from the in-memory snapshot taken when the run finished; see "Artifacts" below)
- demo routes: `POST /api/mission/run`, `GET /api/artifacts/<name>` (same snapshot mechanism), `GET /api/health`

## Environment variables
- `ECOBACKFIRE_SEARCH_API_KEY` — key for the search provider (without it the UI says web research is unavailable; registry evidence still shown)
- `ECOBACKFIRE_SEARCH_PROVIDER` — `tavily` (default) or `brave`
- `ECOBACKFIRE_SESSION_SECRET` — HMAC secret that signs the research report so a client cannot invent "sourced" candidates.
  Optional locally; **required on Vercel** (instances do not share memory).

## Run / test
- Local: `pip install -e ".[dev,web]" && uvicorn web.app:app --port 8000` → http://localhost:8000
- Tests: `pytest -q` (web tests in `tests/web`; offline, fake providers with synthetic data)
- UI smoke (needs node + jsdom): see `tests/web/serve_fake_provider.py` and `tests/web/ui_smoke.js`
- Deploy: `vercel --prod`; set the three variables above in the project settings.
  See "Artifacts" below for what is and is not persistent.

## Artifacts (view / download)
- Both run endpoints (`POST /api/mission/run`, `POST /api/run-reviewed`) return the artifact files' text in the response as
  `artifact_contents` (`{name: text}`), next to the existing `artifacts` listing (name, bytes, sha256). The page's **View** /
  **Download** links are built from that copy (a local `blob:` URL), so they do not depend on a later request reaching the
  instance that ran the engine. Files: `result.json verification.json manifest.json report.md config.json` plus the mission files.
- The same files are also kept, read once when the run finished, in a bounded in-memory store of that process (16 runs, 2 MB per
  file) and served by the GET endpoints. Lookup is by whitelisted name inside the run's snapshot; no path is ever built from the
  request, and symlinks/directories are never read. The GET endpoints never run the engine.
- **This is not persistent storage.** Neither `/tmp` nor process memory survives a cold start or is shared between Vercel
  instances. A GET that reaches an instance that did not run the mission (or after a restart) returns a JSON `404`
  (`no such artifact …`), not a 500. The copy in the run response (and anything you downloaded) is the durable one;
  re-running the mission regenerates identical files (content-derived key, deterministic engine).
- Truly durable, shareable run links would need external storage (e.g. Vercel Blob/KV); that is not implemented.

## Limits (read before trusting numbers)
Extraction is a keyword + unit rule over search-result text, not reading comprehension. It can pick the wrong device, country, year or
definition. Every web candidate is `sourced_unreviewed`; the verbatim sentence is shown so you can check it. Results remain model-derived
and conditional; `evidence_insufficient` is not `false`.
