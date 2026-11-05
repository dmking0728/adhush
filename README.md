# AdHush 🤫

**Point a webcam at your TV. AdHush mutes it when an ad comes on and unmutes it when the show is back.**

It works with any streaming app on any box (Apple TV, Roku, Fire TV…), because it never talks to the
app. It just looks at the screen for the little **"Ad 1 of 3 · 0:45"** badge that Hulu, Max, Peacock
and others show during ad breaks, and mutes your TV over your home network.

```
 webcam ──▶ Raspberry Pi ──(reads the "Ad" badge)──▶ Wi-Fi ──▶ Samsung TV: mute / unmute
```

## What you need

| Part | Notes |
| --- | --- |
| Raspberry Pi | A Pi Zero W works (about 1 check every 1–3 s). A **Pi Zero 2 W** (~$15) is a drop-in upgrade and roughly 5× faster. Any Pi 3/4/5 is great. |
| USB webcam | Any UVC webcam. On a Pi Zero you need a micro-USB OTG adapter. |
| A TV AdHush can mute | Samsung smart TVs (2016+) today. More backends welcome, see below. |

## How it decides

1. Every second it grabs a frame and crops the small boxes where the ad badge appears.
2. It reads each box with Tesseract OCR and looks for words like `Ad`, `Ad 1 of 3`, `Advertisement`.
3. Badge seen **2 checks in a row** → mute. Gone **3 checks in a row** → unmute.
4. It only unmutes if *it* did the muting, so it never undoes a mute you pressed yourself.
5. Safety net: it never stays muted longer than 4 minutes. If that trips it was probably a false
   match, so it stands down until the screen is clear again.

Expect a 2–4 second delay at the start and end of each break (a bit more on a Pi Zero W).

## Set up a Raspberry Pi

1. Flash **Raspberry Pi OS Lite** with [Raspberry Pi Imager](https://www.raspberrypi.com/software/).
   In its settings, set your Wi-Fi, a username, and turn on SSH.
2. SSH in and install:
   ```bash
   sudo apt install -y git
   git clone https://github.com/<your-username>/adhush.git
   cd adhush
   sudo ./scripts/install.sh
   ```
3. On the TV, allow network control: **Settings → General → External Device Manager →
   Device Connection Manager → Access Notification: First Time Only** (menu names vary by year).
   Note the TV's IP under **Settings → General → Network → Network Status**. Giving the TV and the
   Pi fixed IPs in your router keeps things working forever.
4. Edit `/etc/adhush/config.yaml`: set `tv.host` to the TV's IP and `tv.backend: samsung`.
5. Test the connection (press **Allow** on the TV if it asks):
   ```bash
   /opt/adhush/venv/bin/adhush -c /etc/adhush/config.yaml test-tv
   sudo systemctl restart adhush
   ```
6. On your phone, open **http://&lt;pi-ip&gt;:8080**. Wait for an ad, then drag a box around the
   "Ad" badge. Do it once per app if the badge sits in a different spot. Press **Save boxes**.

That's it. It starts on boot and restarts itself if anything goes wrong.
Logs: `journalctl -u adhush -f`.

## Camera tips

* Closer is better. A camera on the coffee table aimed at the TV beats one across the room.
* Fill as much of the frame with the TV as you can, and keep it level.
* Turn off any lamp that reflects on the screen right where the badge appears.
* Use **Download frame** on the setup page and run `adhush detect frame.jpg` on a laptop to tune.

## Try it on a Mac or PC first

```bash
brew install tesseract          # or: sudo apt install tesseract-ocr
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev,samsung]"
cp config.example.yaml config.yaml
adhush --dry-run run            # uses your laptop's webcam, never touches the TV
# open http://localhost:8080
adhush detect some-photo-of-an-ad.jpg
pytest
```

## Config

See [`config.example.yaml`](config.example.yaml); every option and default is in
[`adhush/config.py`](adhush/config.py). Boxes drawn in the web page are saved to `regions.yaml`
next to the config.

## Ideas / help wanted

* More TV backends: LG webOS, Roku TV, Sony Bravia, HDMI-CEC from the Pi, Sonos/soundbar volume.
* Per-app badge templates so no box drawing is needed.
* A tiny image model instead of OCR, for badges without text.

## Honest notes

* AdHush only reacts to what the apps put on screen. If an app changes its badge, update the
  patterns in the config.
* The setup page has no password. Keep it on your home network.

MIT licensed.
