/**
 * Official Kiwi rank artwork slots. ONE file per rank, made from the canonical Kiwi by the Price Chat design team
 * (never generated or redrawn in code). Put each file in frontend/public/brand/ranks/ and set its path here, e.g.
 *   warrior: "/brand/ranks/warrior.webp"
 * Recommended: transparent WebP, square, 1000 x 1000, the Kiwi at the same scale and baseline across all ranks.
 *
 * While a slot is null the site shows the development stand-in: the canonical Kiwi with a CSS treatment
 * (and, for some ranks, a marked emoji placeholder for the accessory). Filling a slot replaces it everywhere:
 * header, Kiwi Passport, rank progression, leaderboard, rank-up and the homepage ladder.
 */
export const RANK_ASSETS: Record<string, string | null> = {
  egg: null,
  chick: null,
  kiwi: null,
  scout: null,
  warrior: null,
  knight: null,
  royal: null,
  golden: null,
  legend: null,
  mythic: null,
};

export const RANK_KEYS = Object.keys(RANK_ASSETS);
