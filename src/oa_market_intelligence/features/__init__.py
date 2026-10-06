"""Model-ready feature matrices built from the Gold tables (PROPOSAL.md §9).

The Gold tables keep only the lag, rolling-average and FDA-derived columns; the leak-safe
features the classifiers train on are computed here, on demand, from those tables.
"""
