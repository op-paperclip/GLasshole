# GLasshole

### Stream your desktop to the prism display of the LilyGO T-Glass

**A wireless desktop monitor for the T-Glass --- powered by an
ESP32-S3.**

[Features](#features) · [Hardware](#hardware) ·
[Installation](#firmware-installation) ·
[Configuration](#wifi-configuration) ·
[Transmitter](GLasshole-Transmitter/README.md)
:::

------------------------------------------------------------------------

## Demo

[Open the demo video](Demo.mp4)

## Features

-   Streams a live desktop image to the **126 × 126** display/prism of
    the LilyGO T-Glass.
-   Wireless connection over your local Wi-Fi network.
-   Automatic device discovery using **mDNS** (`glasshole.local`).
-   Configurable TCP ports from **8090 to 8099**.
-   A physical button on the T-Glass cycles through the listening ports.
-   Transmitter applications are available for **Linux, Windows and
    Android**.

## Hardware

-   **LilyGO T-Glass**
-   ESP32-S3 microcontroller
-   Integrated 126 × 126 display and prism optics
-   USB cable for firmware flashing

The T-Glass receives the image stream over Wi-Fi and displays it in its
prism. The desktop capture and transmission are handled by the companion
GLasshole Transmitter application.

## Firmware installation

Connect the T-Glass to your computer using USB and flash
`GLasshole_V1.6.2.bin` with Espressif's `esptool`.

> Replace the serial port in the examples with the port assigned to your
> T-Glass. The examples assume that the firmware binary is in the
> current working directory.

**Linux**

``` bash
esptool.py --chip esp32s3 --port /dev/ttyUSB0 write_flash 0x0 GLasshole_V1.6.2.bin
```

**Windows**

``` powershell
esptool.exe --chip esp32s3 --port COM3 write_flash 0x0 GLasshole_V1.6.2.bin
```

If your system uses a different port, change `/dev/ttyUSB0` or `COM3`
accordingly.

## Wi-Fi configuration

1.  Power on the T-Glass. It starts a Wi-Fi access point named
    **`GLasshole`**.
2.  Connect your computer or smartphone to this network.
    -   **Password:** `IWannaBeAGlasshole`
3.  Open a browser and visit **<http://glasshole.local>**.
    -   If the hostname does not resolve, use **<http://192.168.4.1>**.
4.  Select your Wi-Fi network from the drop-down list and enter its
    password.
5.  If you use a hidden network, enter its SSID manually in the optional
    hidden-network field.
6.  Submit the settings. The T-Glass connects to your Wi-Fi network and
    the setup access point is no longer needed.

Once connected, the T-Glass can be reached on your network. In the
transmitter application, use **mDNS** to discover it automatically, or
enter its IP address manually.

## TCP ports and the hardware button

The T-Glass listens on one TCP port in the range **8090--8099**.

Press the physical button to advance to the next port:

`8090 → 8091 → 8092 → … → 8099 → 8090`

When using several T-Glass devices on the same network, assign each
device a different port. For example:

  Device        TCP port
  ----------- ----------
  T-Glass 1         8090
  T-Glass 2         8091
  T-Glass 3         8092

Select the matching port in the transmitter application.

## Desktop and Android transmitter

The companion application captures the desktop or screen and sends the
image stream to the T-Glass. It is available for Linux, Windows and
Android.

See **[GLasshole Transmitter --- usage and Android
installation](GLasshole-Transmitter/README.md)** for the application
interface, settings, and platform-specific instructions.

## Acknowledgements

This project is based on the work of **tuckershannon**:

[ESP32-Desktop-Monitor](https://github.com/tuckershannon/ESP32-Desktop-Monitor)

Thank you for the original project and inspiration.
