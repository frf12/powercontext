# Add a Server-owned web page

PowerContext serves a small multi-page web UI from the same FastAPI process as its HTTP API. Use this structure for
Server-owned pages that read PowerContext APIs. Do not introduce a separate frontend build or client-side router unless
the product requires an independently built application.

## Directory layout

The web UI is organized by responsibility:

```text
src/powercontext/server/
├── web.py
├── static/
│   ├── auth.js
│   ├── dashboard.js
│   ├── topics.js
│   └── site.css
└── templates/
    ├── base.html
    ├── components/
    └── pages/
        ├── dashboard.html
        └── topics.html
```

`web.py` owns the Jinja environment, page router, static mount, and UI support endpoints. `base.html` owns the document
head, global header and footer, and asset slots. `auth.js` owns bearer-token session storage and authenticated requests.
Page templates provide page content. Components contain complete, reusable fragments such as the login form, activity
heatmap, and recall trend.

Templates and static files are package resources. Keep them below `powercontext.server` so both editable installs and
built wheels expose the same files.

## Add a page

Create a template below `templates/pages/` and extend the shared layout:

```html
{% extends "base.html" %}

{% block title %}Page title{% endblock %}

{% block content %}
<section>
  <h1>Page heading</h1>
</section>
{% endblock %}
```

Register an explicit FastAPI route in `mount_web_ui()`. Pass the incoming `Request` to `TemplateResponse` so Jinja can
generate application URLs correctly:

```python
async def page(request: Request) -> Response:
    return _templates().TemplateResponse(
        request=request,
        name="pages/page.html",
        headers=_PAGE_HEADERS,
    )


router.add_api_route(
    "/page",
    page,
    methods=["GET"],
    response_class=HTMLResponse,
    name="page",
)
```

The root path is the Dashboard entry point. Add later pages at explicit paths and keep API routes under their existing
versioned prefixes.

## Understand Dashboard data

The browser authenticates against `/dashboard/scopes`, then requests `/v1/stats` with the selected `scope_id` and a
`30d` period. The Server reads one scoped snapshot and returns inventory, model usage, and recall statistics.

| Dashboard value | Source |
| --- | --- |
| Sources | Current scoped Source journal position |
| Memory entries | Entries in the current Memory Artifact |
| Artifacts | Current Artifact heads grouped by family |
| Pending review | Current Candidate heads grouped by family and status |
| Model usage | Persisted daily generation and embedding usage |
| Recall hits, token reduction, and savings trend | Persisted daily recall measurements for the configured estimator |

The Runtime performs these reads in one database transaction and calculates totals, pending Sources, family counts,
daily buckets, and token reduction on the Server. The browser presents `ready_preparations` as recall hits and plots the
signed daily `token_reduction` as the savings trend. Each heatmap cell combines those two fields for its date. Its fixed
bands are no hit, hit without a positive reduction, 1–255, 256–1023, and 1024 or more estimated tokens reduced. The
fixed thresholds keep sparse activity and outliers from changing the meaning of every other cell.

## Share only stable page structure

Put document-level structure in `base.html`. Put a fragment in `templates/components/` when it is reused or represents
a self-contained UI unit. Import `auth.js` instead of implementing token storage or bearer headers in each page. Keep
page-specific sign-in errors, data loading, and rendering in that page's static script.

Do not create a generic chart abstraction from one chart type. Share markup and styles first. Extract a JavaScript data
or rendering contract only after a second page needs the same behavior.

## Add the Handoff Report page

When Handoff Report is enabled, the Server hosts the scope Handoff page at `/handoff-reports` without requiring the scoped-statistics Dashboard or its configured scope list. The optional Dashboard remains at `/` when separately enabled. The pages share only `base.html`, the header and footer, `auth.js`, theme state, and locale state; their statistics and report calculations remain independent.

The Handoff Report page obtains exact `scope_id` values with committed Handoffs from `POST /v1/handoff-reports/scopes/list-known` and uses them in a searchable scope combobox. Selecting a scope sends its required `scope_id` to `POST /v1/handoff-reports/get`; neither the Project catalog nor `project_id` participates in report selection. The page presents the exact current Handoff snapshot at full width.

The current snapshot displays objective, current state, disposition, next action, and known omissions as one Handoff document. One Edit action opens all five fields, and one Save Revision action prepares and commits the complete document as a new immutable Handoff Revision. Scope switching and background refresh pause while the editor is open. Receiver-side decisions are not part of this page; existing continuity records remain available in the read-only Continuity timeline. Apart from the explicit revision write, the browser formats returned `summary`, `coverage`, Workstream state, and digests without recalculating report semantics.

When known-scope discovery succeeds but no scope has a committed Handoff, the page replaces report controls with a clearly labeled, data-free template preview. Retry enumerates Handoff heads again; the first committed scope replaces the preview. The preview neither creates a Handoff nor requests fabricated report data.

The page requests the current day in UTC by default and provides current-day, ISO-week, calendar-month, and custom date-range inputs. The custom end date is inclusive in the UI and is converted to the exclusive start of the next day for the API. The current scope application normalizes this input but supplies no Activity events, reports `activity_coverage=not_configured`, and returns no period comparison. Handoff status comes from the current exact selection and must not be presented as a historical period-end state.

The overview request may disable evidence checks for lower latency. A Markdown download makes a separate request with `format=markdown`, `download=true`, and evidence checks enabled by default. The browser never reconstructs Markdown from rendered DOM or canonical JSON. Both background refresh and browser download currently require a stored bearer token even when Server authentication is disabled; initial and manual report loads still work without one. Disabling Handoff Report removes the `/handoff-reports` page and its API while leaving the original Dashboard route, scope selection, and statistics request unchanged.

## Browse Topic Memory

When the Dashboard is enabled, `/topics` provides a read-only management projection over the scopes returned by
`/dashboard/scopes`. With an empty query, the browser calls the private
`POST /dashboard/topic-memories/list` support route and follows its opaque current-head keyset cursor in recently
published order. With a focused query, it calls the public `POST /v1/topic-memory/search` operation with a limit of 20;
there is no search pagination or caller-selected retrieval mode. These are the only two ordering semantics: recent for
browse and relevance for search.

Selecting a result sends the same exact `ArtifactRef` to the private `POST /dashboard/topic-memories/get` support
route. The response adds publication time, current/historical state, the current exact ref, and direct SourceRef
identifiers to the full Topic detail already selected by the application. The page never fetches Source content or
metadata and exposes no create, edit, review, retire, publish, delete, or flush action. All generated fields are inserted
as text nodes; Topic detail is displayed as text rather than interpreted as HTML or Markdown.

Both support routes are hidden from OpenAPI and MCP and delegate to the same `TopicMemoryApplication` used by the
public operations. They exist only while the Dashboard is enabled and reject scopes absent from the configured
Dashboard list with a uniform 404.

## Preserve the security boundary

The Dashboard shell and static assets are public so a browser can render the sign-in form. They must not contain bearer
tokens, configured scope names, statistics, or other private data. UI support endpoints and `/v1/` data endpoints remain
behind `StaticBearerMiddleware`.

`DashboardConfig.scopes` controls UI discovery and is not a per-user or per-scope authorization list. The current
Bearer credential is one Server-wide token: anyone holding it can call protected Server operations for arbitrary valid
scope IDs, independent of whether those IDs appear in the Dashboard list. The two private Topic support routes add an
extra configured-scope check for the UI projection, but that check must not be represented as an ACL. Deployments that
disable authentication deliberately expose data routes according to the existing Server policy. Per-user/per-scope
authorization requires a separate authentication design.

Return Server-owned pages with the shared Content Security Policy and `Cache-Control: no-store`. Prefer external CSS
and JavaScript. The short inline script in `base.html` exists only to apply the saved theme before first paint.

## Validate behavior

Test through the public HTTP surface. Cover page routing, protected data requests, scope isolation, and data obtained
from a real database-backed Server. Assert user-visible behavior or preserve a concrete regression. Do not assert DOM
IDs, static asset paths, JavaScript source text, Jinja internals, or private function call order.

Run:

```bash
uv run pytest tests/test_dashboard.py -q
make check
make test
make build
```

After building, confirm the wheel contains `powercontext/server/templates/` and `powercontext/server/static/`.
