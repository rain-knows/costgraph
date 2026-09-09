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
  or status regions.
- Agent progress is driven by the canonical Runtime lifecycle and ordered SSE
  events. Animation may clarify active progress but must stop for terminal
  states and respect `prefers-reduced-motion`.
- Charts use the same server values and Decimal-derived display strings as
  tables and reports. Color is never the only carrier of meaning.

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
