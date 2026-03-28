"""Shim: re-exports from tessera_eval for backward compatibility.

All ML evaluation now runs on the compute server (tee-compute).
This file is kept for any code that still imports from lib.evaluation_engine.
"""

from tessera_eval.evaluate import detect_field_type
