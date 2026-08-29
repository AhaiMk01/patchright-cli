# Video Recording and Capture

## Video recording

Record the browser screen as video using CDP screencast:

```bash
patchright-cli video-start                             # Start recording
patchright-cli video-stop                              # Stop and save as .webm
patchright-cli video-stop --filename=rec.webm          # Save to custom path
```

Video files are saved to `.patchright-cli/` by default. Requires ffmpeg for `.webm` output; without ffmpeg, individual frames are saved instead.

### Chapter markers

Add named chapter markers during recording for easier navigation:

```bash
patchright-cli video-start
patchright-cli goto https://example.com/login
patchright-cli video-chapter "Login page"
# ... perform login ...
patchright-cli video-chapter "Dashboard"
patchright-cli video-stop
```

Chapters are saved as a JSON file alongside the video.

### Action callouts

Label each action in the recording as it happens: a badge naming the action
and the element, plus a ring around the target.

```bash
patchright-cli video-start
patchright-cli video-show-actions                      # 600ms, top-right
patchright-cli video-show-actions --duration=1200 --position=bottom-left
patchright-cli click e5                                # renders: click button "Sign in"
patchright-cli video-hide-actions                      # stop annotating
patchright-cli video-stop
```

`--position` takes `top-left`, `top-right`, `bottom-left`, or `bottom-right`.

The callout is drawn *before* the action, because a click can navigate away
or detach the element. It is `pointer-events: none` and `aria-hidden`, so it
neither blocks the interaction nor shows up in `snapshot`.

Callouts are only drawn while a recording is running -- the flag persists
across `video-start` / `video-stop`, but outside a recording the page is left
alone.

## Codegen (interaction recording)

Record your browser interactions and save them as a replayable bash script:

```bash
patchright-cli codegen                                 # Start recording
# ... click, fill, goto, etc. -- all interactions are captured ...
patchright-cli codegen-stop                            # Stop and save to .patchright-cli/
patchright-cli codegen-stop script.sh                  # Save to custom path
```

The generated script contains the patchright-cli commands that replay the recorded session.

## Screenshots

```bash
patchright-cli screenshot                              # Page screenshot
patchright-cli screenshot e3                           # Element screenshot by ref
patchright-cli screenshot --filename=page.png          # Custom filename
patchright-cli screenshot --full-page                  # Full scrollable page
patchright-cli screenshot --hires                      # Capture at device pixel ratio
```

Screenshots capture one image pixel per CSS pixel. That matches the desktop
default (device pixel ratio 1), so `--hires` only changes anything under
`open --mobile` or `open --device=...`, where the ratio is 2-3x and `--hires`
produces a correspondingly larger file.


Screenshots save to `.patchright-cli/` in the current directory.

## PDF capture

```bash
patchright-cli pdf                                     # Save page as PDF
patchright-cli pdf --filename=page.pdf                 # Custom filename
```

## File upload

Upload files to file input elements on the page:

```bash
patchright-cli upload ./document.pdf                   # Upload to first file input
patchright-cli upload ./photo.jpg e5                   # Upload to specific input by ref
```

## Viewport resize

Change the browser viewport dimensions:

```bash
patchright-cli resize 1920 1080
```

## Dashboard (live session monitor)

View live screenshots of all active sessions:

```bash
patchright-cli show                                    # Open at http://127.0.0.1:9322
patchright-cli show --show-port=9400                   # Custom port
```
