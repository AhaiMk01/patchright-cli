#!/bin/sh
# Start one virtual X display for the container's lifetime so Chrome can stay
# headed, then run patchright-cli. `docker exec ... patchright-cli <command>`
# reuses the same display through the image's DISPLAY=:99.
# Set XVFB=0 to skip Xvfb (e.g. when only using --headless or attaching to a remote browser).
set -e

if [ "${XVFB:-1}" = "1" ]; then
    display_num="${DISPLAY#:}"
    display_num="${display_num%%.*}"
    Xvfb ":$display_num" -screen 0 "${XVFB_SCREEN:-1920x1080x24}" -nolisten tcp >/dev/null 2>&1 &
    # Wait up to 5s for the X socket before Chrome tries to connect.
    i=0
    while [ ! -S "/tmp/.X11-unix/X$display_num" ] && [ "$i" -lt 50 ]; do
        sleep 0.1
        i=$((i + 1))
    done
fi

exec patchright-cli "$@"
