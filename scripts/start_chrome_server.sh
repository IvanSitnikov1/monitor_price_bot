#!/bin/bash
# Скрипт для запуска Chrome на сервере с anti-detect параметрами

# Убиваем старые процессы
pkill -f "remote-debugging-port=9222" 2>/dev/null
sleep 2

# Запускаем Chrome через xvfb с anti-detect флагами
xvfb-run -a chromium \
  --remote-debugging-port=9222 \
  --user-data-dir=/home/ivans/.config/chromium \
  --disable-blink-features=AutomationControlled \
  --disable-features=IsolateOrigins,site-per-process \
  --disable-dev-shm-usage \
  --no-sandbox \
  --disable-setuid-sandbox \
  --disable-infobars \
  --window-size=1920,1080 \
  --disable-background-networking \
  --disable-background-timer-throttling \
  --disable-backgrounding-occluded-windows \
  --disable-breakpad \
  --disable-component-extensions-with-background-pages \
  --disable-features=TranslateUI \
  --disable-ipc-flooding-protection \
  --disable-renderer-backgrounding \
  --enable-features=NetworkService,NetworkServiceInProcess \
  --force-color-profile=srgb \
  --metrics-recording-only \
  --mute-audio \
  --no-first-run \
  --password-store=basic \
  --use-mock-keychain \
  --lang=ru-RU \
  --disable-gpu \
  --disable-software-rasterizer \
  --use-gl=swiftshader \
  > /tmp/chrome-cdp.log 2>&1 &

echo "Chrome запущен с CDP на порту 9222"
echo "Логи: /tmp/chrome-cdp.log"
