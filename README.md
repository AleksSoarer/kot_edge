# Kot Edge

Текущая версия: **0.6.1**.

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
KOT_WAKE_BACKEND=asr
KOT_EDGE_URL=http://127.0.0.1:8765
KOT_VOICE_BASE=/home/khadas/voice-assistant
```

## Калибровка MA-USB8

В версии 0.6.0 добавлена команда `kot-mic-calibrate`. Она пишет исходный
8-канальный WAV без `KOT_MIC_GAIN` и показывает RMS, peak и долю клиппинга для
каждого канала. У MA-USB8 каналы CH0–CH5 сырые, CH6 — результат аппаратного
beamforming, CH7 — дополнительный сырой канал.

Остановите voice-worker, чтобы он не удерживал ALSA-устройство:

```bash
sudo systemctl stop kot-edge-voice
mkdir -p ~/kot-mic-tests
```

Проверьте имя устройства:

```bash
arecord -l
arecord --dump-hw-params -D hw:MicArray,0 /dev/null
```

Сделайте четыре записи по 15 секунд. В каждой сначала оставьте 3 секунды
тишины, затем несколько раз обычным голосом произнесите «Эй, Кот, который час»:

```bash
kot-mic-calibrate record ~/kot-mic-tests/01-near.wav --seconds 15
kot-mic-calibrate record ~/kot-mic-tests/02-far.wav --seconds 15
kot-mic-calibrate record ~/kot-mic-tests/03-side.wav --seconds 15
# Перед последней записью включите музыку с обычной громкостью.
kot-mic-calibrate record ~/kot-mic-tests/04-music.wav --seconds 15
```

`near` записывается с 0,5 м, `far` — с обычного места пользователя, `side` —
сбоку от массива. Команда сразу печатает отчёт; существующий файл можно
проанализировать повторно:

```bash
kot-mic-calibrate analyze ~/kot-mic-tests/04-music.wav
```

Отчёт показывает и сырой сигнал, и результат после текущего программного
`gain x16`. Ориентиры для обработанной речи: peak примерно от -12 до -3 dBFS,
отсутствие клиппинга и заметный рост RMS относительно первых секунд тишины.
Если клиппинг есть только после gain, уменьшите `KOT_MIC_GAIN`; если он уже есть
в сыром сигнале, нужно уменьшить аппаратный capture gain. Сравните прежде всего
CH6 с тем сырым каналом CH0–CH5, который обращён к пользователю. Другой gain
можно проверить без новой записи: `kot-mic-calibrate analyze FILE --gain 4`.

После записи верните службу:

```bash
sudo systemctl start kot-edge-voice
journalctl -u kot-edge-voice -f
```

WAV-файлы исключены из Git. Для совместного анализа их нужно передавать
отдельно, не добавляя в репозиторий.

## Экспериментальный wake-word на VIM3 NPU

Обычный режим `KOT_WAKE_BACKEND=asr` оставлен режимом по умолчанию. Вариант
`npu` подключает постоянно работающий нативный runner с моделью, подготовленной
для Amlogic/VeriSilicon NPU:

```bash
KOT_WAKE_BACKEND=npu
KOT_NPU_WAKE_COMMAND=/opt/kot-npu/kot-wake-runner --model /opt/kot-npu/hey-kot.nb
```

Контракт runner намеренно не зависит от vendor SDK:

- stdin: непрерывный mono PCM S16_LE, 16 kHz;
- stdout: JSON Lines;
- отсутствие события не требует вывода;
- детекция: `{"wake":true,"score":0.91}`;
- диагностические сообщения runner должен писать в stderr.

Python-процесс держит runner запущенным, передаёт ему каждый аудиофрейм и после
детекции использует обычный VAD/ASR для команды. Если runner не стартует или
завершается, voice-worker завершается с ошибкой, и systemd перезапускает его —
тихого перехода на CPU нет, поэтому неверная NPU-конфигурация видна сразу.

Файл `.nb` аппаратно и runtime-зависим. Его нужно получать из выбранной
wake-word модели с помощью VIM3 NPU SDK/Acuity Toolkit и проверять на том же
образе Linux и наборе библиотек, где работает Кот. Репозиторий пока задаёт
готовую границу интеграции, но не выдаёт фиктивную универсальную `.nb`-модель.

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
