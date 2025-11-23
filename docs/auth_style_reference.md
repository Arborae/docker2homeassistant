# Auth experience design reference

## Palette and typography
- **Base fonts:** `"Inter", system-ui, -apple-system, "Segoe UI", sans-serif` with occasional `"JetBrains Mono"` for secrets. Body uses radial overlays on theme background for depth.
- **Dark theme tokens:** `--bg` `#0b1020`, primary panels `--panel` `rgba(17, 22, 36, 0.72)` and `--panel-2` `rgba(26, 34, 52, 0.66)`. Accent pair `--accent` `#31c4ff` and `--accent-2` `#7cffc3` with gradients, glows, and borders (`--accent-border`, `--accent-border-strong`, `--accent-border-soft`). Text uses `--text` `#e7ecf4` and `--muted` `#a7b1c6` against low-opacity `--border` and `--shadow` depth.
- **Light theme overrides (`body.theme-light`):** Warm gradient `--bg` and panels `rgba(255, 255, 255, 0.82/0.72)`. Accent shifts to orange/yellow (`--accent` `#ff8a3d`, `--accent-2` `#ffce73`) with matching glows and borders. Text flips to dark slate values with softer `--border` and shadow. Auth canvas tokens (`--auth-bg-1/2`, `--auth-point`, `--auth-line`, `--auth-glow`) mirror the warm palette.
- **Border radius and shadow:** Cards use `20px` rounding; buttons and notes stay between `12–14px`; chips use `999px`. Shadows rely on `--shadow` (`0 14px 42px rgba(0, 0, 0, 0.45)` dark, lighter in light mode).

## Effects and background rules
- **Glassmorphism:** Cards/panels combine layered gradients, translucent borders, and `backdrop-filter: blur(10px) saturate(115%)` with accent surface washes (`--accent-surface`).
- **Gradients and overlays:** Body background stacks radial glows over `--bg`. Cards layer subtle white + accent gradients over `--panel`; headers use `--header-bg`/`--stack-header-bg`. Accent gradient `--accent-gradient` powers buttons and chips.
- **Auth canvas:** Fullscreen `<canvas>` uses theme-aware tokens (`--auth-bg-*`, `--auth-point/line/glow`) and sits beneath `.auth-overlay` content.

## Theme behavior
- Theme switch uses `.theme-light` on `<body>`; custom properties cascade automatically to all components. Radial backgrounds and canvas colors swap via the token set. Onboarding toolbar includes theme selector in `.onboarding-menu`.

## Component map
- `.auth-page`: Flex container centering auth content with padded viewport and hidden overflow.
- `.auth-overlay`: Relative layer that holds flash messages, toolbar, and the card; capped at `960px` width.
- `.auth-card`: Glass card container with 20px radius, layered gradients, soft radial glow pseudo-element, and internal stack for headings, text, and form fields.
- `.auth-card form` + inputs/selects: Column layout with 12px gaps; inputs use 12px radius, `--panel-2` fills, and `--border` outlines. Checkbox/radio adopt accent color. `.password-input-wrapper` and `.password-toggle` add eye control.
- Buttons: `.btn`, `.auth-card button`, `.btn-primary` share accent gradient fills, 14px radius, bold text, lift-on-hover; `.btn-secondary` swaps to neutral panel with border.
- Helper text: `.hint`/`.password-hint` and `.muted` use muted color; strength badges apply semantic colors.
- Layout utilities: `.layout` grid splits main + aside; `.qr-box`, `.qr-title`, `.qr-image`, `.secret-box`, `.auth-choice`, `.actions`, and `.auth-actions-form` format onboarding/2FA details with consistent rounding and borders.
- Brand framing: `.auth-title` fixed at top with glow lettering; `.auth-logo` fixed at bottom; `.onboarding-toolbar` anchors language/theme menu via `.onboarding-toggle` and `.onboarding-menu`.

## Reuse guidance
- Import the token set to propagate theme-ready styles; avoid redefining raw color hex values. Use the accent gradient for primary calls-to-action and keep 12–20px rounding for auth-related surfaces.
- Maintain glass layering (panel color + accent surface + blur) for any new authentication or onboarding screens to stay consistent with the existing aesthetic.
