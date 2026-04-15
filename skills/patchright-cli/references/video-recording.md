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
```

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
