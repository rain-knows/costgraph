# CostGraph design contract

This file binds the upstream IBM-inspired OpenDesign package to the CostGraph
product. It is the repository-level source of truth for product-specific
decisions and takes precedence when the bundled guidance conflicts with an
operational cost workspace.

## Source and identity

- Read [`design-systems/ibm/USAGE.md`](design-systems/ibm/USAGE.md), then the
  Simplified Chinese [`DESIGN-zh.md`](design-systems/ibm/DESIGN-zh.md) or the
  canonical [`DESIGN.md`](design-systems/ibm/DESIGN.md).
- Import [`tokens.css`](design-systems/ibm/tokens.css) as the single upstream
  token source. Product CSS may bind semantic aliases to these tokens but must
  not duplicate the root token block.
- The bundled [`tailwind-v4.css`](design-systems/ibm/tailwind-v4.css) is a
  reference only. CostGraph remains on Tailwind CSS 3 and does not install
  `@carbon/react` merely to reproduce the visual language.
- CostGraph is independently developed. References to IBM, IBM Plex, Carbon,
  and OpenDesign describe design provenance only; the application must never
  imply IBM ownership, endorsement, certification, or partnership.
- The bundled package is IBM-inspired OpenDesign guidance, not a Carbon React
  implementation or a promise of `--cds-*` token compatibility. Its actual
  portable token names come from the checked-in `tokens.css`; CostGraph binds
  product semantics to those names without inventing a second token namespace.

## Product direction

CostGraph is a high-density manufacturing cost operations workspace, not a
marketing site. The first viewport must prioritize navigation, period context,
cost facts, run state, and the next concrete action. Do not add a hero section,
decorative slogan, oversized editorial heading, ornamental illustration, or
floating section card.

- Use IBM Plex Sans for interface text and IBM Plex Mono only for identifiers,
  hashes, timestamps, and diagnostic values.
- Set `letter-spacing: 0` for all CostGraph interface text, including display,
  labels, buttons, tables, and monospaced diagnostics.
- Preserve Carbon's white/gray surfaces, Blue 60 interaction signal, square
  geometry, 1 px structural borders, restrained elevation, and visible focus
  rings. Product containers and controls use zero radius unless a native
  semantic shape, such as a status dot, requires a circle.
- Keep the palette functional: blue for interaction, green for success, yellow
  for warning, red for failure, and neutral grays for structure. Do not build a
  one-hue decorative composition or use gradients, glow, blur, or bokeh.
- Favor tables, split panes, compact toolbars, inline filters, disclosure rows,
  and persistent status regions. Do not nest cards or use cards as page-section
  wrappers.
- Keep route context in the global 48 px toolbar. The period selector combines
  previous/next month shortcuts with a year-scoped 12-month grid and does not
  accept free-form year input. It is followed by a single-line semantic
  breadcrumb whose parent items navigate to real routes
  while preserving the active period; the current item is plain text with
  `aria-current="page"`. Below the small breakpoint, the breadcrumb occupies a
  second toolbar row so the current context remains visible. Page headers contain
  only the page title and necessary actions, without category eyebrows or
  explanatory introductions.
- Use Lucide icons for recognizable actions. Icon-only controls require an
  accessible name and tooltip; text buttons are reserved for clear commands.

## Data and interaction truth

- The UI displays only server-returned costs, status, permissions, artifacts,
  and lineage. It must not calculate authoritative money or invent alerts,
  approvals, export jobs, batches, BOM nodes, or generation states.
- The cost workspace presents the v2 finished-batch table with frozen identity
  columns, grouped six-category manufacturing-cost columns, and the three
  server-provided views (料工费, 六类制造成本, 变动/固定) inside a horizontally
  scrollable dense work surface. Trace trees and source-record details remain
  auditable and use the same IBM-inspired surfaces and focus states.
- Every data surface implements loading, empty, error, unavailable, and
  unauthorized states without shifting fixed toolbars, table columns, charts,
  or status regions. The data governance surface is opened only to principals
  holding `data_management.read`, stays read-only for batch, quality, and
  snapshot data, uses dense horizontally scrollable tables, and never renders
  rollback, delete, edit, or raw-payload controls.
- The governance workspace is split by permission, not by convenience: import
  submission (`/data-admin/imports/new`, `data_import.submit`), the job list and
  job detail (`/data-admin/imports`, `data_management.read`), identity and scope
  management (`/iam`, `iam.manage`), and audit search (`/audit`, `audit.read`).
  Job detail actions follow the server-returned `status`; approval and
  cancellation controls appear only where the state machine permits them and
  only for principals holding the matching permission.
- A failed publish is never presented as a recoverable client action. The
  surface states that the previously published snapshot is unchanged and that
  the Worker retries under its own lease, and the retry control is disabled
  rather than pointing at an endpoint that does not exist.
- Authentication is part of the interaction truth. Staging and production obtain
  an access token only through OIDC Authorization Code + PKCE; local `demo` may
  use the explicitly enabled fixed development token so the workspace can start
  without an IdP. Both paths keep the token in memory and never persist it to
  `localStorage`, `sessionStorage`, cookies, or the URL.
- `401`, `403`, and `404` are three distinct surfaces, never a shared empty
  state: `401` clears the in-memory token and returns to sign-in, `403` renders
  the unauthorized panel while keeping the session, and `404` renders the
  not-found panel. Hiding an entry the principal cannot use is navigation
  hygiene, not authorization; the server remains the authority.
- Navigation entries are derived from the server-returned `permissions` of
  `GET /api/me`. Direct navigation to a route the principal cannot access
  renders the unauthorized panel instead of an empty table. While permissions
  are loading the surface shows loading, and when they cannot be read it shows
  unavailable; neither may be shown as "no data".
- Agent progress is driven by the canonical Runtime lifecycle and ordered SSE
  events. Animation may clarify active progress but must stop for terminal
  states and respect `prefers-reduced-motion`.
- Charts use the same server values and Decimal-derived display strings as
  tables and reports. Color is never the only carrier of meaning.
- Report presentation is intent-specific. `presentation` leads with one-period
  batch metrics and stacked material/labor/overhead composition;
  `period_comparison` leads with labeled baseline/current KPIs, grouped
  six-class unit-cost bars, signed deltas, percentage change, and an exact data
  table. Comparison colors always pair with arrows, signs, labels, or patterns.
- A report must not expose an arbitrary chart picker. Chart selection follows
  the server-declared report style and measure semantics; every comparison
  chart keeps the baseline and target periods visible in its title or legend.
- `presentation` reports lead with the server-provided period identity and
  summary cards, then show stacked material/labor/overhead composition,
  analysis insights, cost views, batch details, and audit metadata. The
  presentation layout collapses to one reading order on narrow screens while
  preserving exact tables and chart labels.
- Quantity metrics use integer, half-up display formatting in the frontend;
  API values, persisted `report_json`, Decimal calculations, amounts,
  percentages, and allocation ratios retain their existing precision.

## Responsive and accessibility baseline

- Desktop favors dense side-by-side inspection; narrow screens collapse to a
  single reading order while keeping the primary action and current context
  visible.
- Fixed-format elements use explicit grid tracks, minimum sizes, or aspect
  ratios so content and state changes do not move surrounding controls.
- Support keyboard operation, logical focus order, visible focus, semantic
  landmarks, reduced motion, and WCAG AA contrast. Never truncate the only copy
  of a product name, amount, error, or action label.
- Validate both light and dark themes at desktop and mobile widths with browser
  screenshots before changing the design contract or shared tokens.
