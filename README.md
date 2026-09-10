# quadroctl

`quadroctl` is a command line tool for reading and changing Aquacomputer Quadro fan controller settings.

## Warning

**This program is experimental, reverse engineered, and provided without any
guarantee of safety or correctness. Running it can change cooling-controller
settings on real hardware. Incorrect settings can overheat or damage your PC,
pump, fans, coolant loop, or other equipment.**

Use `quadroctl` only if you understand and accept the risk. You are solely
responsible for verifying backups, commands, fan behavior, temperatures, and
safe fallback settings before and after every write.

`quadroctl` is a Python command line tool for Aquacomputer Quadro fan
controllers using the reverse-engineered HID report layout documented in 
`quadro-interface-v2.md`.

Every write reads the current report first, validates its CRC, patches only
known offsets, recalculates the CRC, writes a timestamped raw backup, writes the
feature report, sends the known commit output report, and verifies by reading
the report back.

## Platform Support

`quadroctl` supports:

- Windows through the Python `hidapi` package.
- Linux through either `hidapi` or the built-in `/dev/hidraw` backend.

The default `--backend auto` tries `hidapi` first, then falls back to hidraw on
Linux. Use `--backend hidapi` or `--backend hidraw` to force one backend.

## Install

From this checkout:

```sh
python3 -m pip install -e .
```

Windows requires hidapi, and Linux can use it too:

```sh
python3 -m pip install -e '.[hidapi]'
```

## Device Permissions On Linux

Find the device:

```sh
quadroctl list
```

If `/dev/hidrawN` is not readable and writable by your user, grant temporary
access:

```sh
sudo setfacl -m u:$USER:rw /dev/hidrawN
```

or add a persistent udev rule:

```text
SUBSYSTEM=="hidraw", ATTRS{idVendor}=="0c70", ATTRS{idProduct}=="f00d", MODE="0660", TAG+="uaccess"
```

## Commands

```sh
quadroctl status
quadroctl status --verbose
quadroctl dump-settings --raw settings.bin
quadroctl dump-names --raw names.bin
quadroctl restore-settings settings.bin

quadroctl fan 1 fixed 30
quadroctl fan 1 target --source temp1 45
quadroctl fan 1 curve --source temp1 \
  --point 25:20 --point 27:25 --point 29:30 --point 31:35 \
  --point 33:40 --point 35:45 --point 37:50 --point 39:55 \
  --point 41:60 --point 43:65 --point 45:70 --point 47:75 \
  --point 49:80 --point 51:85 --point 53:90 --point 55:100
quadroctl fan 1 start-boost off
quadroctl fan 1 min-power 20
quadroctl fan 1 max-power 80
quadroctl fan 1 fallback 100

quadroctl sensor 1 offset 1.5
quadroctl name fan1 "Radiator"
```

`fan target` and `fan target-temp` are aliases. They set target-temperature
mode, update the source sensor and setpoint, and preserve the existing PID
gain, hysteresis, and flag fields.

Use `--dry-run` before write commands to print the changed byte offsets without
writing to the device.

```sh
quadroctl --dry-run fan 1 fixed 30
```

Use `--device /dev/hidrawN` to select a specific Linux device.
On Windows, use the HID path printed by `quadroctl --backend hidapi list`.
