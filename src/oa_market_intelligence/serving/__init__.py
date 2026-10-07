"""The read-only layer behind the analytics website and the Claude question box.

Everything here reads the published Gold tables and returns aggregates. Nothing writes to
the warehouse, nothing exposes raw rows, and no function accepts SQL.
"""
