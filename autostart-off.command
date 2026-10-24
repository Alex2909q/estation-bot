#!/usr/bin/env bash
# Зупиняє фонового бота й вимикає його автозапуск.
LABEL="ua.estation.monitor"
launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null
rm -f "$HOME/Library/LaunchAgents/$LABEL.plist"
echo "Фоновий бот зупинено, автозапуск вимкнено."
read -n 1 -s -r -p "Натисніть будь-яку клавішу…"
