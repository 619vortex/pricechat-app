"""The News Desk: Discover → Verify → Score → Write → Deduplicate → Publish → Measure, for Price Chat's X account.

sources.py  reads news feeds and prices
checks.py   what a post must pass before it can go out; the only rules that decide that
writer.py   builds the text from verified facts, with a language model only if a key is set
engine.py   one tick of the pipeline, run by the scheduler (the browser doesn't need to be open)
"""
