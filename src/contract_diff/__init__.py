"""Compare OpenAPI contracts from an existing client's point of view."""

from .compare import compare
from .loader import SpecError, load
from .model import Finding, Report

__all__ = ["Finding", "Report", "SpecError", "compare", "load"]
__version__ = "0.2.0"
