# Kot Edge

Локальный интерфейс и voice-worker Кота для Khadas VIM3.

## Структура

```text
/home/khadas/kot_edge/
├── kot/
│   ├── main.py          # FastAPI + состояние + WebSocket
│   ├── ui.py            # единый источник динамического UI-текста и мордочек
│   ├── voice.py         # MA-USB8 -> wake word -> VAD -> ASR
│   ├── music.py         # playerctl/MPRIS: playback, metadata, volume
│   └── static/
│       ├── index.html
│       ├── app.js       # только отображает состояние сервера
│       └── style.css
├── scripts/
├── systemd/
├── tests/
└── pyproject.toml
```

Корневая папка проекта теперь `kot_edge`, а Python-пакет внутри неё — `kot`.

## Где менять надписи и мордочки

Все зависящие от режима данные находятся в одном файле:

```text
kot/ui.py
```

Там задаются для `idle`, `listening`, `music`, `thinking`, `speaking`, `offline`,
`error`:

- ASCII-мордочка;
- заголовок (`IDLE`, `LISTEN`, ...);
- значок;
- строка состояния;
- ответ после команды `Мяу, щас!`.

`index.html` больше не содержит запасных `IDLE` / `Жду обращения`, а `app.js` не
имеет собственных `FACES`, `TITLES` и `ICONS`. Поэтому после загрузки JS не может
вернуть старый текст поверх изменённого.

Статика отдаётся с `Cache-Control: no-store`, поэтому при редактировании HTML/JS/CSS
Chromium не должен поднимать старую копию из HTTP-кэша.

## Wake word

Voice-worker использует текущий рабочий тракт:

- MA-USB8: 8 каналов / 48 kHz;
- beamformed CH6 через SoX `remix 7`;
- mono / 16 kHz;
- Small Zipformer RU;
- Silero VAD;
- wake phrase `Эй, Кот` плюс фонетические варианты `эй код`, `ей кот`, `ей код`.

После wake-фразы распознанный текст показывается под мордочкой. После успешной
команды сервер показывает справа `Мяу, щас!` примерно 2.3 секунды. Время жизни и
реплики, и распознанного текста контролирует сервер, а не браузер.

Настройки:

```bash
KOT_REPLY_SECONDS=2.3
KOT_HEARD_SECONDS=12
KOT_MIC_DEVICE=hw:MicArray,0
KOT_MIC_BEAM_CHANNEL=7
KOT_MIC_GAIN=16.0
KOT_ASR_THREADS=2
KOT_DEBUG_WAKE_ASR=0
KOT_EDGE_URL=http://127.0.0.1:8765
KOT_VOICE_BASE=/home/khadas/voice-assistant
```

## Установка

Системные зависимости:

```bash
sudo apt update
sudo apt install -y alsa-utils sox playerctl
```

Для новой установки:

```bash
cd /home/khadas/kot_edge
python3 -m venv .venv
.venv/bin/python -m pip install -U pip
.venv/bin/python -m pip install -e '.[voice]'
```

Ручной запуск web UI:

```bash
.venv/bin/python -m uvicorn kot.main:app --host 127.0.0.1 --port 8765
```

Voice-worker:

```bash
.venv/bin/python -m kot.voice
```

## systemd

Unit-файлы уже используют `/home/khadas/kot_edge` и пакет `kot`:

```bash
sudo cp systemd/kot-edge.service /etc/systemd/system/
sudo cp systemd/kot-edge-voice.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now kot-edge kot-edge-voice
```

Проверка:

```bash
systemctl status kot-edge --no-pager
systemctl status kot-edge-voice --no-pager
journalctl -u kot-edge-voice -f
```

## API

- `GET /health`
- `GET /api/status`
- `PATCH /api/status`
- `POST /api/mode/{mode}`
- `WS /ws`

`GET /api/status` теперь возвращает уже готовое представление режима:

```json
{
  "mode": "idle",
  "face": " /\\_/\\\n( -.- )\n > ^ <",
  "mode_title": "IDLE",
  "mode_icon": "·",
  "message": "Жду обращения"
}
```

Браузер только отображает эти поля и не содержит второй копии текста.

## Управление Яндекс Музыкой через playerctl

Кот управляет текущим MPRIS-плеером Chromium через `playerctl`.

Установка системной зависимости:

```bash
sudo apt install -y playerctl
```

Логика wake/ASR:

1. Если музыка играла до `Эй, Кот`, Кот сразу делает `playerctl pause`.
2. Команда распознаётся уже без играющей музыки.
3. Для обычной команды музыка автоматически продолжается.
4. При timeout музыка также возвращается в исходное состояние.
5. Музыкальные команды выполняются напрямую:
   - `пауза`, `останови музыку` -> `playerctl pause`;
   - `продолжи музыку`, `включи музыку` -> `playerctl play`;
   - `следующий трек`, `следующий` -> `playerctl next`;
   - `предыдущий трек`, `предыдущий` -> `playerctl previous`;
   - `громче`, `сделай громче` -> +5%;
   - `тише`, `сделай тише` -> -5%;
   - `что играет?`, `какой трек?` -> название трека в реплике Кота.

Web-сервер раз в 2 секунды читает MPRIS-состояние и автоматически обновляет
музыкальный блок: название трека, исполнителя, `Playing/Paused` и громкость.
Этот монитор не меняет активные режимы `LISTEN/THINK/SPEAK`, поэтому фоновое
обновление музыки не может сбить распознавание команды.

Если `kot-edge-voice` запущен как systemd system service, код сам восстанавливает
`XDG_RUNTIME_DIR=/run/user/<uid>` и `DBUS_SESSION_BUS_ADDRESS`, чтобы `playerctl`
мог видеть MPRIS Chromium в пользовательской D-Bus сессии.

Дополнительные настройки:

```bash
KOT_PLAYERCTL_BIN=playerctl
KOT_PLAYERCTL_PLAYER=
KOT_PLAYERCTL_TIMEOUT=2.0
KOT_VOLUME_STEP=0.05
KOT_MUSIC_MONITOR_INTERVAL=2.0
```

`KOT_PLAYERCTL_PLAYER` можно задать, если на машине несколько MPRIS-плееров и
нужно явно выбрать один из вывода `playerctl -l`.
