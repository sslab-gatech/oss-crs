"""Shared constants for the seed ensembler."""

# Most sources indicate the default OSS-Fuzz timeout is 25 seconds, but one
# place in the documentation says 65 seconds.  We use the longer duration to
# be conservative.
# See: https://google.github.io/oss-fuzz/advanced-topics/reproducing/#fuzz-target-bugs
DEFAULT_SCORABLE_TIMEOUT_DURATION = 65
