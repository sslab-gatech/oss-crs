"""Coverage-aware seed filtering for multi-CRS ensemble fuzzing.

Extracts core libfuzzer merge/test/parse logic for use as an oss-crs
infrastructure sidecar.  Filters fuzzing seeds by coverage contribution
and detects crash-triggering inputs.
"""

__version__ = "0.1.0"
