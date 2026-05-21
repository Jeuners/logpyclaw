# AgentClaw v3 — Design System

## Color Tokens

```css
:root {
  --bg:      #0f1117;   /* Page background */
  --surface: #1a1d27;   /* Cards, sidebar, header */
  --border:  #2a2d3a;   /* Dividers, card borders */
  --accent:  #6366f1;   /* Primary: indigo — actions, active states */
  --accent2: #10b981;   /* Secondary: emerald — success, online, ez values */
  --warn:    #ef4444;   /* Danger: red — errors, drift */
  --text:    #e2e8f0;   /* Body text */
  --muted:   #64748b;   /* Labels, metadata, placeholders */
}
```

### CDC Color Semantics
| Color | Hex | Usage |
|---|---|---|
| Indigo | `#6366f1` | Request messages, primary accent |
| Emerald | `#10b981` | Response messages, success, online status |
| Red | `#ef4444` | Errors, **causal drift** (dashed line in spacetime) |
| Amber | `#f59e0b` | Heartbeat messages |
| Violet | `#a78bfa` | Causally ordered edges in spacetime |

---

## Typography

- **Font:** `'JetBrains Mono', 'Fira Code', 'Cascadia Code', monospace`
- All text is monospace — consistent with the terminal/code aesthetic

| Size | Usage |
|---|---|
| 9px | Y-axis labels, tiny metadata |
| 10px | Tags, badges, section labels (uppercase + letter-spacing: .1em) |
| 11px | Table cells, secondary UI text |
| 12px | Chat messages, buttons, primary UI text |
| 13px | Body default |
| 15px | Logo |

Font weights: 400 (normal), 600 (semi-bold for names/keys), 700 (logo)

---

## Spacing Scale

`4 · 6 · 8 · 12 · 14 · 16 · 20px`

- Base unit: 4px
- Card padding: 10px 12px
- Content padding: 20px
- Gap between elements: 6–16px

---

## Layout Grid

```
┌─────────────────────────────────────────┐  48px  Header
├───────────┬─────────────────────────────┤
│  260px    │           1fr               │
│  Sidebar  │           Main              │
│           │   Tabs (48px) + Content     │
└───────────┴─────────────────────────────┘
```

---

## Component Patterns

### Agent Card
```
┌──────────────────────────────┐
│ Name                (12px bold)│
│ agent:id            (10px muted)│
│ ez=4 · ops=12       (10px accent2)│
└──────────────────────────────┘
Border: --border → --accent on hover/active
Background on active: rgba(99,102,241,.08)
```

### Chat Bubble — User
```
align: flex-end
background: --accent
border-radius: 8px 8px 2px 8px  (bottom-right flat)
```

### Chat Bubble — Agent
```
align: flex-start
background: --surface
border: 1px solid --border
border-radius: 8px 8px 8px 2px  (bottom-left flat)
```
Metadata line below: font-size 10px, color --accent2 (shows CDC clock summary)

### Status Dot
```
width/height: 8px, border-radius: 50%
Online: background --accent2, box-shadow: 0 0 6px --accent2
Offline: background --muted
```

### Tab Bar
```
border-bottom: 1px solid --border
Active tab: color --accent, border-bottom: 2px solid --accent
Inactive: color --muted
```

---

## Spacetime Diagram

The spacetime SVG follows special CDC semantics:

```
Y-axis: Eigenzeit (τ) — subjective operation count per agent
X-axis: Agent columns (world lines)

World lines:   vertical, agent color, 30% opacity
Nodes:         circles r=5, color by message type, glow filter
Edges:         Bezier curves between agents
  - ORDERED:        violet #a78bfa, solid
  - DRIFT (any):    red #ef4444, dashed stroke-dasharray="5,4"
```

Arrow markers: `arr-ord` (violet) and `arr-drift` (red)

### SVG Layout Constants
```javascript
const PAD = { top: 50, bottom: 30, left: 40, right: 40 };
const COL_W = PLOT_W / agents.length;  // min 180px per agent
const agentX[a] = PAD.left + i * COL_W + COL_W / 2;
const ezY(ez) = PAD.top + PLOT_H * (1 - ez / maxEZ);
```

---

## Interaction States

| State | Visual |
|---|---|
| Hover | border-color → --accent, subtle bg |
| Active/Selected | border-color --accent, bg rgba(accent,.08) |
| Loading/Thinking | opacity .5, CSS pulse animation 1.2s |
| Disabled | opacity .4, cursor default |
| Error | color --warn |
| Success | color --accent2 |

---

## Vue Migration Notes (Phase 4)

When migrating to Vue 3 + Vite:
- All CSS variables → keep as-is in a `theme.css` file
- Components to create: `AgentCard.vue`, `ChatBubble.vue`, `StatusDot.vue`, `SpacetimeChart.vue`, `TabBar.vue`
- `SpacetimeChart.vue` props: `{ data: SpacetimeData }`, emits: `nodeClick`, `edgeClick`
- i18n: migrate JS `I18N` object → `vue-i18n` locale files (same keys)
- Color semantics from CDC must stay consistent — these are protocol-level, not just design
