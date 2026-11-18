"""Price Chat Terminal: the newsroom's view of every story the News Desk and Trending Topics see (owner, 2026-11-18).

Nothing here runs a second pipeline. Discovery adds free, official or documented sources to Trending's signal store (app/terminal/discovery);
the scoring reads Trending's topics (app/terminal/intel: viral potential and evidence kept apart, rumour watch, the fast lane, hooks);
the dashboard reads both desks (app/terminal/feed). Publishing goes through Trending's own approve / edit / publish and every gate.
"""
