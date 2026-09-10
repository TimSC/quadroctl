"""CRC helpers for Quadro HID feature reports."""


def crc16_usb(data: bytes | bytearray | memoryview) -> int:
    """Return the CRC-16/USB checksum used by Quadro settings reports."""
    crc = 0xFFFF
    for byte in data:
        crc ^= byte
        for _ in range(8):
            if crc & 1:
                crc = (crc >> 1) ^ 0xA001
            else:
                crc >>= 1
            crc &= 0xFFFF
    return crc ^ 0xFFFF
