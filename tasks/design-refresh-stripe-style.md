# Atlas Dashboard — Stripe Dev Blog Style Refresh

## Reference: https://stripe.dev/blog/

---

## Design Language Summary (Stripe Dev Blog)

### What makes it distinctive
1. **24-column grid** with `+` crosshair markers at intersections — a visible skeleton
2. **Ultra-light font weights** (300) for everything — headings, body, labels
3. **Monospaced uppercase** for all system labels (`sohne-mono`, 12px, uppercase, -0.3px tracking)
4. **Section headers** use a `/SECTION_NAME` prefix with a 1px solid underline
5. **Dotted separators** between metadata rows (`1px dotted rgba(…, 0.27)`)
6. **Pill buttons** (`border-radius: 99px`, 1px solid outline, no fill)
7. **Generous whitespace** — no shadows, no gradients, flat & editorial
8. **Fixed nav** with keyboard shortcut hints `[B] BLOG`, `[E] EVENTS`
9. **Tags** use dotted borders, very small and understated
10. **Image containers** have a subtle 1px solid border, 4px radius, gray tint bg

### Typography Scale
| Element | Font | Size | Weight | Tracking | Transform |
|---------|------|------|--------|----------|-----------|
| Display H1 | sohne-var (sans) | ~100px | 300 | -6% tight | none |
| Subhead H3 | sohne-var | 36px | 300 | -1.8px | none |
| Body | sohne-var | 18px | 300 | -0.18px | none |
| System label | sohne-mono | 12px | 300 | -0.3px | UPPERCASE |
| Tag | sohne-mono | 12px | 300 | — | UPPERCASE |
| Button | sohne-var | 14px | 500 | — | none |

### Color Palette (original — light mode)
| Token | Value | Use |
|-------|-------|-----|
| `--backgroundColor` | `#eaeaea` | Page bg |
| `--fontColor` | `#1e1e1e` | Primary text |
| `--highlightColor` | `#c4e817` | Accent (lime) |
| `--borderColorLight` | `#1e1e1e44` | Dotted borders |
| `--inactiveColor` | `#8d8d8d` | Muted text |
| `--windowFrameBG` | `#dcdcdc` | Image frame bg |
| `--artBackground` | `#e8e8e8` | Art containers |

---

## Adaptation Plan: "Stripe Dev × Dark Terminal"

Keep Atlas dark. Adapt Stripe's *structure, typography, and rhythm* — not its light palette.

### 1. Background & Surface Treatment

**Current**: Multi-layer dark surfaces (`#0d0c0a` → `#151310` → `#1c1914`) with warm amber tinting, CRT vignette, phosphor canvas.

**Proposed**: Flatten to two tones. Kill the warm amber tint. Go cooler & cleaner.

```css
:root {
  --bg: #111111;               /* cool charcoal (was warm #0d0c0a) */
  --surface: transparent;       /* sections are flat, no bg fill */
  --surface-hover: #191919;     /* subtle hover only */
  --border: #1e1e1e;            /* matches Stripe's font color as our border */
  --border-dotted: rgba(232, 228, 218, 0.15); /* dotted separators */
}
```

- **Remove** the `#phosphor-bg` canvas and `.crt-vignette` — too noisy for this aesthetic
- **Keep** the top accent bar but make it a single clean color (or remove)

### 2. Grid Overlay with `+` Markers

Add a CSS grid overlay mirroring Stripe's 24-column grid with `+` markers at key intersections.

```css
.grid-overlay {
  position: fixed; inset: 0; z-index: 0; pointer-events: none;
  display: grid;
  grid-template-columns: repeat(24, 1fr);
  opacity: 0.04;
}
.grid-overlay .col-line {
  border-right: 1px solid currentColor;
  height: 100%;
}
```

Place subtle `+` SVG markers (8×8px) at grid intersections every 4 columns × every ~280px vertically, matching Stripe's pattern:

```html
<svg width="8" height="8" viewBox="0 0 8 8" fill="none">
  <path d="M3.5 4.5V8H4.5V4.5H8V3.5H4.5V0H3.5V3.5H0V4.5H3.5Z" fill="currentColor"/>
</svg>
```

Animate these to fade in staggered on load (Stripe's page has entrance animations).

### 3. Typography Overhaul

**Replace Figtree + Instrument Serif with closer Stripe equivalents:**

```css
:root {
  --font: 'Inter', system-ui, sans-serif;       /* clean sans like sohne-var */
  --font-display: 'Inter', system-ui, sans-serif; /* same family, light weight */
  --mono: 'JetBrains Mono', 'SF Mono', monospace; /* keep — similar role to sohne-mono */
}
```

Or even better, use **Inter Tight** for display headings to get that compressed, editorial feel.

**Key changes:**
- **Logo "Atlas"**: From `Instrument Serif` → `Inter` at 300 weight, bigger (32px), tight tracking (-1px). Or keep serif but go thinner.
- **Section titles**: From serif 16px → **mono 12px uppercase** with `/` prefix: `/PROFIT & LOSS`, `/PERFORMANCE`
- **Card labels**: Already uppercase mono — keep but reduce weight to 400, reduce size to 10px
- **Card values**: Reduce weight from 700 → 500. Keep mono. Tighten tracking.
- **Body text weight**: Drop from 400/500 → 300 wherever possible

### 4. Section Headers — The `/SECTION` Pattern

This is the most distinctive Stripe element. Replace the current collapsible section headers:

**Current:**
```
┌──────────────────────────────────────┐
│ Profit & Loss  [—]              ▾    │
└──────────────────────────────────────┘
```

**Proposed:**
```
/PROFIT & LOSS ─────────────────────────
                                       ▾
```

```css
.section-head {
  padding: 0 0 8px;
  border-bottom: 1px solid var(--border);
  background: none;
}
.section-title {
  font-family: var(--mono);
  font-size: 12px;
  font-weight: 400;
  letter-spacing: -0.3px;
  text-transform: uppercase;
  color: var(--text-secondary);
}
.section-title::before {
  content: '/';
  margin-right: 2px;
  opacity: 0.5;
}
```

- Remove the section background fill
- Remove rounded corners on sections
- Add 1px solid bottom border on the header
- Generous padding below header (16px) before content

### 5. Cards — Flatten & Breathe

**Current**: Dark rounded cards with borders, hover glow, gradient accents.

**Proposed**: Borderless or 1px dotted border. No background. Let the values speak.

```css
.card {
  background: none;
  border: none;
  border-bottom: 1px dotted var(--border-dotted);
  border-radius: 0;
  padding: 20px 0;
}
```

Or alternatively, keep very subtle cards but remove the glow/accent effects:

```css
.card {
  background: var(--bg);
  border: 1px solid #1e1e1e;
  border-radius: 2px;         /* 2px like Stripe buttons, not 10px */
  padding: 20px;
}
.card:hover { border-color: #2a2a2a; }
.card::before, .card::after { display: none; }  /* kill all glow effects */
```

### 6. Navigation — Keyboard Hint Style

Adapt Stripe's `[KEY] LABEL` nav pattern:

**Current**: Tab buttons with icons + text + badge counts

**Proposed**: Flat mono links with keyboard shortcuts

```
[T] TRADING    [R] RESEARCH 3    [M] MONITOR 2
```

```css
.tab-btn {
  font-family: var(--mono);
  font-size: 12px;
  font-weight: 400;
  text-transform: uppercase;
  letter-spacing: -0.3px;
  background: none;
  border: none;
  border-radius: 2px;
  padding: 6px 10px;
  color: var(--text-tertiary);
}
.tab-btn.active {
  background: var(--text);
  color: var(--bg);
}
```

Active tab inverts (white-on-black) like Stripe's `[B] BLOG` active state.

### 7. Tags & Badges — Dotted Borders

**Current**: Colored background fills with matching text colors (`.strat-trend_following`)

**Proposed**: All tags get dotted outlines, monochrome, no fills.

```css
.strat, .market-tag, .order-status {
  background: none;
  border: 1px dotted var(--border-dotted);
  border-radius: 3px;
  color: var(--text-secondary);
  font-family: var(--mono);
  font-size: 11px;
  padding: 2px 6px;
}
```

Keep **color coding** for semantic meaning (green=profit, red=loss) but only on the *text color*, not the background:
```css
.strat-trend_following { color: var(--blue); border-color: rgba(90,147,192,0.3); }
```

### 8. Buttons — Pill Outlines

**Current**: Solid colored buttons (green approve, red reject)

**Proposed**: Pill-shaped outline buttons matching Stripe's share buttons:

```css
.plan-btn {
  border-radius: 99px;
  border: 1px solid var(--text);
  background: none;
  color: var(--text);
  padding: 7px 16px;
  font-size: 13px;
  font-weight: 500;
}
.plan-btn:hover {
  background: var(--text);
  color: var(--bg);
}
/* Semantic color only on the border */
.plan-btn-approve { border-color: var(--green); color: var(--green); }
.plan-btn-reject { border-color: var(--red); color: var(--red); }
```

### 9. Table Styling — Cleaner Rows

**Current**: Alternating hover highlights, uppercase headers, borders between all rows

**Proposed**: Dotted row separators, lighter touch:

```css
th {
  font-family: var(--mono);
  font-size: 11px;
  font-weight: 400;        /* lighter */
  text-transform: uppercase;
  letter-spacing: -0.3px;   /* tighter like Stripe */
  border-bottom: 1px solid var(--border);  /* solid for header */
  padding: 10px 12px;
}
td {
  border-bottom: 1px dotted var(--border-dotted);  /* dotted for rows */
  padding: 12px;  /* more breathing room */
}
th::after { display: none; }  /* remove sort arrows by default */
```

### 10. Animations & Micro-interactions

**Current**: `fadeIn` with `translateY(10px)`, staggered delays, pulse on dots.

**Proposed (matching Stripe's more refined approach):**

```css
/* Smoother, subtler entrance */
@keyframes revealUp {
  from { opacity: 0; transform: translateY(16px); }
  to { opacity: 1; transform: translateY(0); }
}
.animate-in {
  opacity: 0;
  animation: revealUp 0.6s cubic-bezier(0.16, 1, 0.3, 1) forwards;
}

/* Grid markers fade in with stagger */
.grid-cross {
  opacity: 0;
  animation: fadeIn 0.8s ease forwards;
}
.grid-cross:nth-child(1) { animation-delay: 0.1s; }
.grid-cross:nth-child(2) { animation-delay: 0.15s; }
/* ... */

/* Section reveal on scroll (Intersection Observer) */
.section { opacity: 0; transform: translateY(20px); transition: all 0.5s cubic-bezier(0.16, 1, 0.3, 1); }
.section.visible { opacity: 1; transform: none; }
```

Add scroll-triggered reveals (Stripe uses these) via Intersection Observer:
```js
const observer = new IntersectionObserver(entries => {
  entries.forEach(e => { if (e.isIntersecting) e.target.classList.add('visible'); });
}, { threshold: 0.1 });
document.querySelectorAll('.section, .card').forEach(el => observer.observe(el));
```

### 11. Header — Simpler, Fixed

**Current**: Sticky header with backdrop-blur, logo, version pill, market clocks, status pills.

**Proposed**: Thinner fixed bar. No blur. Just the essentials.

```css
.header {
  position: fixed;
  top: 0;
  left: 0;
  right: 0;
  z-index: 100;
  padding: 12px 24px;
  background: var(--bg);           /* solid, no blur */
  backdrop-filter: none;
  border: none;
  border-radius: 0;
}
```

Market clocks and status pills keep their mono treatment but get dotted-border styling.

### 12. Status Pill — Cleaner Pulse

**Current**: Green dot with `box-shadow` glow + pulse animation.

**Proposed**: Smaller dot, no shadow, gentler pulse:

```css
.pulse {
  width: 5px;
  height: 5px;
  border-radius: 50%;
  background: var(--green);
  box-shadow: none;
  animation: pulse 3s ease-in-out infinite;
}
```

### 13. Scrollbar — Near-Invisible

```css
::-webkit-scrollbar { width: 3px; height: 3px; }
::-webkit-scrollbar-thumb { background: #333; border-radius: 2px; }
```

---

## Color Palette (Dark Adaptation)

| Token | Current | Proposed | Notes |
|-------|---------|----------|-------|
| `--bg` | `#0d0c0a` (warm) | `#111111` (neutral) | Cool charcoal |
| `--surface` | `#151310` | `transparent` | Flat, no fills |
| `--text` | `#e8e4da` (warm cream) | `#eaeaea` (Stripe's bg, now our text) | Neutral white |
| `--text-secondary` | `#b0a494` (warm) | `#8d8d8d` (Stripe's inactive) | Neutral gray |
| `--text-tertiary` | `#8a7e70` (warm) | `#555555` | Subtle |
| `--border` | `#2a2419` (warm) | `#1e1e1e` (Stripe's font → our border) | Clean dark |
| `--green` | `#7fb858` | `#c4e817` (Stripe highlight!) | Electric lime |
| `--red` | `#d05858` | `#ff4444` | Brighter red |
| `--amber` | `#d4a84a` | keep | Warm accent for warnings |
| `--blue` | `#5a93c0` | `#6b9fff` | Slightly brighter |

### Accent Color Decision
Stripe uses `#c4e817` (electric lime) as their highlight. This would be a strong choice for Atlas's "profit green" — it's modern, distinctive, and high-contrast on dark. Use it for:
- Positive P&L values
- Active/healthy indicators
- Chart equity line
- The accent bar

---

## Summary of Removals

| Remove | Why |
|--------|-----|
| `#phosphor-bg` canvas | Too noisy, conflicts with clean grid |
| `.crt-vignette` | Same |
| `body::after` accent gradient bar | Replace with single-color or remove |
| All `box-shadow` on cards | Stripe uses zero shadows |
| All gradient backgrounds on cards | Go flat |
| `Instrument Serif` display font | Switch to thin sans-serif |
| Warm amber color tinting throughout | Go neutral/cool |
| `border-radius: 10px` | Reduce to 2-4px everywhere |
| Hover glow effects (`.border-glow`) | Replace with simple border-color shift |

## Summary of Additions

| Add | What |
|-----|------|
| Grid overlay + `+` markers | Fixed, subtle, decorative structure |
| `/SECTION` header pattern | Mono uppercase with slash prefix |
| Dotted separators | Between table rows, metadata items |
| Pill buttons (`border-radius: 99px`) | For action buttons |
| Scroll-triggered animations | Intersection Observer reveals |
| Keyboard shortcut hints in tabs | `[T]` TRADING pattern |
| Inverted active state | Active tab/button fills solid |
| Lighter font weights (300-400) | Throughout |

---

## Implementation Phases

### Phase 1: Foundation (CSS-only, no JS changes)
- New color palette (CSS variables)
- Typography weight/tracking changes
- Remove warm tinting, go neutral
- Flatten cards and sections (remove shadows, gradients)
- Reduce border-radius to 2px
- Dotted borders

### Phase 2: Structure (HTML + CSS)
- Grid overlay with `+` markers
- `/SECTION` header pattern
- Keyboard shortcut hints in tab nav
- Pill button styling

### Phase 3: Animation (JS)
- Intersection Observer scroll reveals
- Staggered grid cross-marker entrance
- Smoother cubic-bezier easing
- Remove phosphor canvas + vignette

---

*Design sketch by Atlas agent — based on inspection of stripe.dev/blog, March 2026*
