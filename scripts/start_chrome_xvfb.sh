#!/bin/bash
# Запуск Chrome через xvfb с anti-detect параметрами

# Останавливаем старые процессы
pkill -f "remote-debugging-port=9222" 2>/dev/null
sleep 2

# Запускаем xvfb с реалистичными параметрами дисплея
Xvfb :99 -screen 0 1920x1080x24 -ac +extension GLX +render -noreset &
XVFB_PID=$!
export DISPLAY=:99

# Ждём запуска Xvfb
sleep 2

# Запускаем Chrome с anti-detect флагами
nohup chromium \
  --remote-debugging-port=9222 \
  --user-data-dir=/home/ivans/monitor_price_bot/user_data/1189033386 \
  --disable-blink-features=AutomationControlled \
  --disable-features=IsolateOrigins,site-per-process \
  --disable-dev-shm-usage \
  --no-sandbox \
  --disable-setuid-sandbox \
  --window-size=1920,1080 \
  --start-maximized \
  --disable-infobars \
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
  --lang=ru-RU \
  --use-gl=swiftshader \
  --enable-webgl \
  --ignore-gpu-blocklist \
  >/tmp/chromium.log 2>&1 &

CHROME_PID=$!

echo "Xvfb запущен (PID: $XVFB_PID) на дисплее :99"
echo "Chrome запущен (PID: $CHROME_PID) с CDP на порту 9222"
echo "Логи Chrome: /tmp/chromium.log"
echo ""
echo "Для остановки:"
echo "  kill $CHROME_PID $XVFB_PID"
