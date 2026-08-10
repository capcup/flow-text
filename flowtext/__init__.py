"""FlowText — lokale Voice-to-Text-Engine für macOS (Apple Silicon)."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("flowtext")
except PackageNotFoundError:  # pragma: no cover — falls Paket nicht installiert ist
    __version__ = "1.5.6"
