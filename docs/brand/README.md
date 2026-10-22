# Kiwi: the canonical mascot

**There is one Kiwi:** `frontend/public/brand/kiwi.webp` (1000 × 844, transparent).
Every Kiwi on the website renders that file through the `Kiwi` component in
`frontend/src/website/Website.tsx` (`site.kiwi` in `websiteData.ts`): the hero, the logo mark in the
header and footer, and the horizon scene in Our Story. It is only placed, scaled, lit (CSS glow) or
mirrored, never redrawn, recoloured or reshaped.

## Where it comes from

`kiwi-source-youtube-avatar.jpg` (480 × 480) is the original Price Chat YouTube channel avatar,
the Kiwi as Price Chat publishes it. `kiwi.webp` is that same artwork (from YouTube's 2048 px
rendition) with only the green background removed by `scripts/brand/kiwi-cutout.mjs`.

**If a higher-resolution master file exists** (for example the designer's original PNG/SVG),
replace `frontend/public/brand/kiwi.webp` with it. Update `width`/`height` in `site.kiwi` if the
proportions differ, then run `node scripts/brand/make-favicon.mjs`. Nothing else needs to change.

## Derived files

- `frontend/public/favicon.png`: generated from `kiwi.webp` by `scripts/brand/make-favicon.mjs`
  (the Kiwi on a Price Chat green circle). Re-run it after replacing the Kiwi.
