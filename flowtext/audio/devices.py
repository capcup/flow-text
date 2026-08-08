"""Abfrage der Audio-Eingabegeräte (nur für Diagnose und Logging)."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class InputDevice:
    index: int
    name: str
    channels: int
    default_samplerate: float
    is_default: bool


def list_input_devices() -> list[InputDevice]:
    import sounddevice as sd

    try:
        default_index = sd.default.device[0]
    except (TypeError, IndexError):
        default_index = None

    devices: list[InputDevice] = []
    for index, dev in enumerate(sd.query_devices()):
        if dev.get("max_input_channels", 0) <= 0:
            continue
        devices.append(
            InputDevice(
                index=index,
                name=str(dev.get("name", "?")),
                channels=int(dev["max_input_channels"]),
                default_samplerate=float(dev.get("default_samplerate", 0.0)),
                is_default=index == default_index,
            )
        )
    return devices


def default_input_name() -> str:
    import sounddevice as sd

    try:
        return str(sd.query_devices(kind="input")["name"])
    except Exception:  # noqa: BLE001
        return "unbekannt"
