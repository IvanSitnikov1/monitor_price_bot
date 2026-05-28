#!/bin/bash
chromium --remote-debugging-port=9222 --user-data-dir=/home/ivans/.config/chromium >/tmp/chromium.log 2>&1 &
