"""HID transport backends for Aquacomputer Quadro devices."""

from __future__ import annotations

import os
import platform
import select
from dataclasses import dataclass
from typing import Any
from pathlib import Path

from .reports import PID, VID


class TransportError(RuntimeError):
    """Raised when the Quadro HID device cannot be accessed."""


@dataclass(frozen=True)
class DeviceInfo:
    path: str
    backend: str
    vendor_id: int = VID
    product_id: int = PID
    serial: str | None = None
    product: str | None = None
    manufacturer: str | None = None


class QuadroTransport:
    def get_feature_report(self, report_id: int, length: int) -> bytes:
        raise NotImplementedError

    def set_feature_report(self, report: bytes | bytearray) -> None:
        raise NotImplementedError

    def write_output_report(self, report: bytes | bytearray) -> None:
        raise NotImplementedError

    def read_input_report(self, length: int, timeout_ms: int) -> bytes:
        raise NotImplementedError

    def close(self) -> None:
        raise NotImplementedError

    def __enter__(self) -> "QuadroTransport":
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()


class HidApiTransport(QuadroTransport):
    def __init__(self, path: str | bytes | None = None) -> None:
        hid = _load_hid()
        self._hid = hid
        self._device = hid.device()
        try:
            if path:
                self._device.open_path(_hidapi_path(path))
            else:
                self._device.open(VID, PID)
        except Exception as exc:
            raise TransportError(f"hidapi open failed: {exc}") from exc

    def get_feature_report(self, report_id: int, length: int) -> bytes:
        return bytes(self._device.get_feature_report(report_id, length))

    def set_feature_report(self, report: bytes | bytearray) -> None:
        written = self._device.send_feature_report(bytes(report))
        if written <= 0:
            raise TransportError("hidapi send_feature_report returned no bytes written")

    def write_output_report(self, report: bytes | bytearray) -> None:
        written = self._device.write(bytes(report))
        if written <= 0:
            raise TransportError("hidapi write returned no bytes written")

    def read_input_report(self, length: int, timeout_ms: int) -> bytes:
        return bytes(self._device.read(length, timeout_ms))

    def close(self) -> None:
        self._device.close()


class HidRawTransport(QuadroTransport):
    def __init__(self, path: str, writable: bool = False) -> None:
        flags = os.O_RDWR if writable else os.O_RDONLY
        try:
            self._fd = os.open(path, flags | os.O_CLOEXEC)
        except PermissionError as exc:
            raise TransportError(f"permission denied opening {path}") from exc
        except OSError as exc:
            raise TransportError(f"failed to open {path}: {exc}") from exc
        self.path = path

    def get_feature_report(self, report_id: int, length: int) -> bytes:
        import fcntl

        buf = bytearray(length)
        buf[0] = report_id
        try:
            fcntl.ioctl(self._fd, _hid_ioc_get_feature(length), buf, True)
        except OSError as exc:
            raise TransportError(f"failed to read feature report 0x{report_id:02x}: {exc}") from exc
        return bytes(buf)

    def set_feature_report(self, report: bytes | bytearray) -> None:
        import fcntl

        buf = bytearray(report)
        try:
            fcntl.ioctl(self._fd, _hid_ioc_set_feature(len(buf)), buf, True)
        except OSError as exc:
            raise TransportError(f"failed to write feature report 0x{buf[0]:02x}: {exc}") from exc

    def write_output_report(self, report: bytes | bytearray) -> None:
        try:
            os.write(self._fd, bytes(report))
        except OSError as exc:
            raise TransportError(f"failed to write output report 0x{report[0]:02x}: {exc}") from exc

    def read_input_report(self, length: int, timeout_ms: int) -> bytes:
        ready, _, _ = select.select([self._fd], [], [], timeout_ms / 1000)
        if not ready:
            raise TransportError("timed out waiting for input report")
        try:
            return os.read(self._fd, length)
        except OSError as exc:
            raise TransportError(f"failed to read input report: {exc}") from exc

    def close(self) -> None:
        os.close(self._fd)


def list_devices(backend: str = "auto") -> list[DeviceInfo]:
    if backend not in {"auto", "hidapi", "hidraw"}:
        raise ValueError("backend must be auto, hidapi, or hidraw")

    devices: list[DeviceInfo] = []
    if backend in {"auto", "hidapi"}:
        try:
            hid = _load_hid()
        except TransportError:
            if backend == "hidapi":
                raise
            hid = None
        if hid is not None:
            for info in hid.enumerate(VID, PID):
                devices.append(_hidapi_device_info(info))
            if backend == "hidapi":
                return devices
    if backend in {"auto", "hidraw"} and platform.system() == "Linux":
        devices.extend(_list_hidraw_devices())
    elif backend == "hidraw":
        raise TransportError("hidraw backend is only available on Linux")
    return devices


def list_unavailable_hidraw_paths() -> list[str]:
    """Return matching Linux hidraw paths seen in sysfs but missing from /dev."""
    if platform.system() != "Linux":
        return []
    available = {device.path for device in _list_hidraw_devices()}
    return [path for path in _hidraw_candidates_seen_in_sysfs() if path not in available]


def open_quadro(path: str | None = None, writable: bool = False, backend: str = "auto") -> QuadroTransport:
    if backend not in {"auto", "hidapi", "hidraw"}:
        raise ValueError("backend must be auto, hidapi, or hidraw")

    if backend in {"auto", "hidapi"}:
        try:
            return HidApiTransport(path)
        except TransportError:
            if backend == "hidapi":
                raise

    if platform.system() == "Linux" and backend in {"auto", "hidraw"}:
        hidraw_path = path
        if hidraw_path is None:
            devices = _list_hidraw_devices()
            if not devices:
                candidates = _hidraw_candidates_seen_in_sysfs()
                if candidates:
                    paths = ", ".join(candidates)
                    raise TransportError(f"Quadro hidraw device exists in sysfs but no /dev node is available: {paths}")
                raise TransportError("no Quadro hidraw device found")
            hidraw_path = devices[0].path
        return HidRawTransport(hidraw_path, writable=writable)

    if backend == "hidraw":
        raise TransportError("hidraw backend is only available on Linux")
    raise TransportError(
        "no usable HID backend found; install with the hidapi extra on Windows, "
        "or use hidapi/hidraw on Linux"
    )


def _load_hid():
    try:
        import hid  # type: ignore
    except ImportError as exc:
        raise TransportError("Python hid/hidapi module is not installed") from exc
    return hid


def _hidapi_path(path: str | bytes) -> str | bytes:
    if isinstance(path, bytes):
        return path
    if platform.system() == "Windows":
        return path.encode("utf-8", errors="surrogateescape")
    return path.encode(errors="surrogateescape")


def _hidapi_device_info(info: dict[str, Any]) -> DeviceInfo:
    return DeviceInfo(
        path=_stringify_path(info.get("path", "")),
        backend="hidapi",
        serial=_stringify_optional(info.get("serial_number")),
        product=_stringify_optional(info.get("product_string")),
        manufacturer=_stringify_optional(info.get("manufacturer_string")),
    )


def _stringify_path(value: object) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="surrogateescape")
    return str(value)


def _stringify_optional(value: object) -> str | None:
    if value is None:
        return None
    return _stringify_path(value)


def _list_hidraw_devices() -> list[DeviceInfo]:
    devices = []
    for node in sorted(Path("/sys/class/hidraw").glob("hidraw*")):
        vendor = _read_first_ancestor_hex(node / "device", "idVendor")
        product = _read_first_ancestor_hex(node / "device", "idProduct")
        dev_path = Path("/dev") / node.name
        if vendor == VID and product == PID and dev_path.exists():
            devices.append(DeviceInfo(path=str(dev_path), backend="hidraw"))
    return devices


def _hidraw_candidates_seen_in_sysfs() -> list[str]:
    candidates = []
    for node in sorted(Path("/sys/class/hidraw").glob("hidraw*")):
        vendor = _read_first_ancestor_hex(node / "device", "idVendor")
        product = _read_first_ancestor_hex(node / "device", "idProduct")
        if vendor == VID and product == PID:
            candidates.append(f"/dev/{node.name}")
    return candidates


def _read_first_ancestor_hex(start: Path, filename: str) -> int | None:
    path = start.resolve()
    for candidate in (path, *path.parents):
        value_path = candidate / filename
        if value_path.exists():
            try:
                return int(value_path.read_text(encoding="ascii").strip(), 16)
            except (OSError, ValueError):
                return None
    return None


def _ioc(direction: int, type_: str, number: int, size: int) -> int:
    return (direction << 30) | (ord(type_) << 8) | number | (size << 16)


def _hid_ioc_get_feature(length: int) -> int:
    return _ioc(3, "H", 0x07, length)


def _hid_ioc_set_feature(length: int) -> int:
    return _ioc(3, "H", 0x06, length)
