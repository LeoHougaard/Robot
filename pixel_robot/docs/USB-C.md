# Direct Pixel-to-ESP32 USB-C

Direct USB-C is the preferred first setup. The Pixel acts as USB host and the
CP210x is the USB device. The app matches the commissioned `10C4:EA60` device
first and can also use the serial library's normal probe table.

Validate the physical link before enabling any motor torque:

1. Power the servo rail independently, with the robot suspended or torque off.
2. Connect Pixel USB-C directly to the board USB-C with a known data cable.
3. Accept the Android USB permission prompt and choose Pixel Robot as the
   default handler if desired.
4. Confirm the app receives `hello`, disconnect/reconnect works, and opening
   the port does not cause unsafe motion.
5. If using the start-pose stand, capture the reference only after the app has
   verified all twelve physical torque registers are off. Empty encoder or
   register replies mean the 7.4 V servo rail is not available; do not save a
   reference from the ESP32's cached target values.
6. Run 30 minutes with motors disabled while camera and policy inference are
   active.

If the board does not enumerate over a C-to-C cable, use a USB-C OTG adapter
and the known-working USB-A-to-C cable. Some boards with USB-C receptacles omit
the device-side CC resistors required for direct C-to-C negotiation.

The Pixel cannot normally charge while its only port is acting as a direct USB
host. Use a powered OTG/PD hub if battery drain prevents the combined 30-minute
test or later long sessions. Before using a powered hub, verify that its VBUS
behavior and the board's separately powered servo rail do not back-feed either
device.
