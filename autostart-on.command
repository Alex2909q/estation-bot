#!/usr/bin/env bash
# Вмикає роботу бота у фоні: стартує сам після перезавантаження Mac, вікно Терміналу не потрібне.
# Запускайте ПІСЛЯ того, як хоча б раз запустили start.command і увійшли в акаунт.
cd "$(dirname "$0")"
DIR="$(pwd)"
LABEL="ua.estation.monitor"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"

if [ ! -f user.session ] || [ ! -x .venv/bin/python ]; then
  echo "Спершу запустіть start.command і увійдіть у свій акаунт (номер + код)."
  read -n 1 -s -r -p "Натисніть будь-яку клавішу…"; exit 1
fi
if pgrep -f "$DIR/.venv/bin/python bot.py" >/dev/null || pgrep -f ".venv/bin/python bot.py" >/dev/null; then
  echo "Зараз бот уже запущено у вікні Терміналу. Закрийте те вікно (Ctrl+C) і запустіть цей файл ще раз."
  read -n 1 -s -r -p "Натисніть будь-яку клавішу…"; exit 1
fi

# Фонові процеси macOS не мають доступу до Завантажень/Робочого столу/Документів,
# тому для фонової роботи копіюємо бота в ~/EStation-bot.
case "$DIR" in
  "$HOME/Downloads"*|"$HOME/Desktop"*|"$HOME/Documents"*|"$HOME/Library/Mobile Documents"*)
    TARGET="$HOME/EStation-bot"
    echo "Копіюю бота в $TARGET (звідти він працюватиме у фоні)…"
    mkdir -p "$TARGET"
    ditto "$DIR" "$TARGET"
    DIR="$TARGET"
    MOVED=1
    ;;
esac

mkdir -p "$HOME/Library/LaunchAgents"
cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key>
  <array>
    <string>/usr/bin/caffeinate</string><string>-i</string>
    <string>$DIR/.venv/bin/python</string><string>$DIR/bot.py</string>
  </array>
  <key>WorkingDirectory</key><string>$DIR</string>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>ThrottleInterval</key><integer>30</integer>
  <key>StandardOutPath</key><string>$DIR/background.log</string>
  <key>StandardErrorPath</key><string>$DIR/background.log</string>
  <key>EnvironmentVariables</key><dict><key>PYTHONUNBUFFERED</key><string>1</string></dict>
</dict>
</plist>
EOF

launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null
launchctl bootstrap "gui/$(id -u)" "$PLIST" && echo "✅ Готово: бот працює у фоні й запускатиметься сам. Журнал: monitor.log" \
  || echo "❌ Не вдалося ввімкнути автозапуск."
if [ -n "$MOVED" ]; then
  echo "Тепер бот живе в папці EStation-bot у вашій домашній папці — ключові слова змінюйте там."
  open "$DIR"
fi
read -n 1 -s -r -p "Натисніть будь-яку клавішу…"
