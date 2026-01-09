"""CRS (Compiler Repair System) package for OSS-Fuzz."""

from bug_finding.src.build import build_crs
from bug_finding.src.run import run_crs

__all__ = ["build_crs", "run_crs"]
