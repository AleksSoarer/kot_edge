# Kot Edge

Текущая версия: **0.13.0**.

Локальный интерфейс и voice-worker Кота для Khadas VIM3.

Версия 0.13.0 подготовлена для проверки всего микрофонного тракта:

- wake-word по умолчанию распознаётся с ненаправленного сырого `CH7`, а после
  активации команда — с аппаратно сформированного `CH6`;
- `kot-mic-calibrate circle` управляет фиксированным лучом и записывает круговой
  тест по 12 направлениям, а `summarize` сравнивает все каналы и aligned/opposite
  варианты `CH6`;
- многоканальный WAV можно разложить на восемь mono-файлов командой `split`;
- hotmap сообщает отсутствие кадров, применяет устойчивое голосование по направлению
  и периодически пишет фактическое состояние beamforming в журнал;
- `kot-recognition-report` строит сводку распознанных фраз, timeout, направлений и
  использованных каналов.

Результаты распознавания сохраняются в ротационный JSONL-журнал:

```text
/var/log/kot-edge/recognition.jsonl
```

Каждая строка — отдельное JSON-событие. Для ASR сохраняются стадия `wake` или
`command`, исходный и нормализованный текст, входной канал, RMS/peak, длительность
аудио, время декодирования, RTF, результат проверки wake-фразы и текущий beam-сектор.
Также записываются начало и завершение сессии, принятая команда, timeout и состояние
hotmap. Посмотреть журнал в реальном времени:

```bash
sudo tail -f /var/log/kot-edge/recognition.jsonl
```

Краткая сводка текущего файла и всех его ротаций:

```bash
cd ~/kot_edge
.venv/bin/kot-recognition-report /var/log/kot-edge/recognition.jsonl
.venv/bin/kot-recognition-report /var/log/kot-edge/recognition.jsonl --json
```

По умолчанию текущий файл ограничен 10 MiB и хранится пять предыдущих файлов
`recognition.jsonl.1` … `recognition.jsonl.5`:

```bash
KOT_RECOGNITION_LOG=/var/log/kot-edge/recognition.jsonl
KOT_RECOGNITION_LOG_MAX_BYTES=10485760
KOT_RECOGNITION_LOG_BACKUPS=5
KOT_RECOGNITION_LOG_MODE=all
```

Режим `all` сохраняет все непустые результаты перекрывающихся wake-окон, не удаляя
повторы: границы окна, номер события и идентификатор сессии позволяют отличить
повторное декодирование одной фразы от нового обращения. Режим `accepted` оставляет
только принятые wake и команды, а `off` отключает журнал. Пустое значение
`KOT_RECOGNITION_LOG=` также полностью отключает запись. Журнал содержит фрагменты
распознанной речи из комнаты даже до wake-word, поэтому его не следует публиковать
без просмотра.

В версии 0.9.0 в верхнюю строку интерфейса добавлены индикаторы загрузки CPU и
оперативной памяти. Каждая шкала содержит ровно 10 символов, а подпись, шкала и
процент занимают фиксированные колонки, поэтому соседние элементы не двигаются:

```text
CPU |||_______  30%    RAM |||||||___  70%
```

Данные читаются непосредственно из Linux `/proc/stat` и `/proc/meminfo`, без
дополнительных Python-зависимостей, и передаются браузеру через существующий
WebSocket. Интервал обновления задаётся в секундах:

```bash
KOT_SYSTEM_MONITOR_INTERVAL=2.0
```

В версии 0.10.0 под мордочкой, рядом с панелью трека, добавлены восемь
фиксированных индикаторов `M0…M7`. Voice-worker получает от SoX все каналы
массива после ресемплинга и вычисляет их RMS с учётом текущего `KOT_MIC_GAIN`.
В ожидании wake ASR получает `KOT_WAKE_CHANNEL`, а после активации VAD/ASR команды
получает `KOT_MIC_BEAM_CHANNEL`:

```text
M0:|||||___
M1:|||_____
M2:||||||__
M3:||______
M4:||||____
M5:|_______
M6:|||||||_
M7:|||_____
```

Шкала логарифмическая: 0 символов соответствует примерно −60 dBFS, 8 символов
— 0 dBFS после программного gain. Частота публикации задаётся отдельно:

```bash
KOT_MIC_LEVEL_UPDATE_SECONDS=0.25
```

В штатной прошивке MA-USB8 каналы `M0…M5` соответствуют шести внешним
микрофонам, `M6` содержит сформированный направленный beam, а `M7` — ещё один
сырой PCM-канал. Близкие уровни `M0…M5` нормальны: направление определяется
не только амплитудой, но прежде всего разницей времени прихода сигнала. Поэтому
светодиоды могут уверенно показывать направление даже при почти одинаковых
шкалах физических микрофонов.

В версии 0.11.0 карта направления 16×16 читается непосредственно из CDC ACM.
Когда один сектор устойчив несколько кадров подряд, voice-worker отправляет
MA-USB8 соответствующую команду `0…9/A/B`; аппаратно сформированный сигнал
появляется в `M6`. После wake направление фиксируется до выполнения команды или
timeout, чтобы луч не перескочил на музыку либо отражение во время распознавания.

Автонаведение включено по умолчанию. Ориентацию можно подстроить без изменения
кода:

```bash
KOT_AUTO_BEAM=1
KOT_AUTO_BEAM_OFFSET=0
KOT_AUTO_BEAM_CLOCKWISE=1
KOT_AUTO_BEAM_STABLE_FRAMES=3
KOT_AUTO_BEAM_MIN_CONTRAST=12
KOT_AUTO_BEAM_NO_FRAME_WARNING_SECONDS=5.0
KOT_AUTO_BEAM_DIAGNOSTIC_SECONDS=15.0
```

`KOT_AUTO_BEAM_OFFSET` поворачивает соответствие секторам с шагом 30°.
Если направление изменяется зеркально, задайте `KOT_AUTO_BEAM_CLOCKWISE=0`.
В журнале каждое реальное переключение выглядит как `[MIC-BEAM] сектор 3`.
Голосование допускает один выброс в окне из трёх кадров. Если CDC-порт открыт,
но hotmap не приходит 5 секунд, появляется явное предупреждение. Строка
`[MIC-DIAG]` и JSON-событие `beam_diagnostic` раз в 15 секунд показывают число
кадров, возраст последнего кадра, contrast, candidate, voted и реально отправленный
сектор. Диагностический интервал `0` отключает периодическую запись.

В экспериментальном mono-AEC доступен только его выбранный Pulse-канал, поэтому
остальные семь шкал будут пустыми. Полный набор работает в штатном `ALSA + SoX`.

## Структура

```text
/home/khadas/kot_edge/
├── kot/
│   ├── main.py          # FastAPI + состояние + WebSocket
│   ├── ui.py            # единый источник динамического UI-текста и мордочек
│   ├── voice.py         # MA-USB8 -> wake word -> VAD -> ASR
│   ├── mic_led.py       # CDC hotmap, LED-индикация и управление CH6
│   ├── calibrate.py     # запись, split и круговой анализ каналов
│   ├── recognition_log.py
│   ├── recognition_report.py
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

Voice-worker использует два входа одного синхронного 8-канального потока:

- MA-USB8: 8 каналов / 48 kHz;
- дополнительный сырой `CH7` для постоянного поиска wake-word без ещё не
  настроенного CH6;
- аппаратно сформированный `CH6` для команды после того, как hotmap выбрал и
  зафиксировал направление;
- mono / 16 kHz;
- Small Zipformer RU;
- Silero VAD;
- wake phrase `Эй, Кот` плюс фонетические варианты `эй код`, `ей кот`, `ей код`.

Переменные каналов используют нумерацию SoX от 1 до 8: значение
`KOT_WAKE_CHANNEL=8` означает физический поток `CH7`, а
`KOT_MIC_BEAM_CHANNEL=7` — `CH6`. Это тестовая конфигурация. Для прямого A/B с
прежним трактом задайте `KOT_WAKE_CHANNEL=7`, тогда и wake, и команда используют
CH6; JSON-журнал явно сохраняет фактический маршрут.

После wake-фразы распознанный текст показывается под мордочкой. После успешной
команды сервер показывает справа `Мяу, щас!` примерно 2.3 секунды. Время жизни и
реплики, и распознанного текста контролирует сервер, а не браузер.

Начиная с версии 0.7.3 wake-фраза ищется по границам слов: например, результат
ASR `бэй кот` больше не совпадает с `эй кот`. Слитные варианты вроде `эйкот`
удаляются из начала уже активной команды, а короткие остатки wake-фразы
(`рим кот`, `день кот`) не отправляются музыкальному контроллеру как команды.

В версии 0.9.1 слитные варианты `эйкот`, `эйкод`, `ейкот`, `ейкод` также могут
сами активировать Кота. Small Zipformer часто распознаёт именно слитное слово;
границы слова по-прежнему защищают от совпадений внутри `бэйкот`.

Настройки:

```bash
KOT_REPLY_SECONDS=2.3
KOT_HEARD_SECONDS=12
KOT_MIC_DEVICE=hw:MicArray,0
KOT_MIC_BEAM_CHANNEL=7
KOT_WAKE_CHANNEL=8
KOT_MIC_GAIN=16.0
KOT_ASR_THREADS=2
KOT_DEBUG_WAKE_ASR=0
KOT_WAKE_BACKEND=asr
KOT_MIC_LED_DEVICE=/dev/ttyACM0
KOT_MIC_LED_ON_WAKE=1
KOT_AUTO_BEAM=1
KOT_AUTO_BEAM_OFFSET=0
KOT_AUTO_BEAM_CLOCKWISE=1
KOT_AUTO_BEAM_STABLE_FRAMES=3
KOT_AUTO_BEAM_MIN_CONTRAST=12
KOT_AUTO_BEAM_NO_FRAME_WARNING_SECONDS=5.0
KOT_AUTO_BEAM_DIAGNOSTIC_SECONDS=15.0
KOT_MUSIC_WAKE_MUTE_SECONDS=3.0
KOT_AEC_ENABLED=0
KOT_AEC_MONITOR_SOURCE=alsa_output.platform-auge_sound.stereo-fallback.monitor
KOT_AEC_MIC_SOURCE=alsa_input.usb-SipeedUSB_SipeedUSB_MicArray_2025082211-00.analog-surround-71
KOT_AEC_MIC_CHANNEL=4
KOT_AEC_FILTER_ORDER=2048
KOT_AEC_MU=0.25
KOT_AEC_LEAKAGE=0.0001
KOT_EDGE_URL=http://127.0.0.1:8765
KOT_VOICE_BASE=/home/khadas/voice-assistant
```

### Индикация прослушивания на MA-USB8

Через CDC ACM контроллер массива принимает штатные команды `E` и `e`. В
проверенной прошивке `e` включает красный D1, а `E` возвращает синюю индикацию
направления звука. После обнаружения wake-word Кот отправляет `e`; после
выполнения команды, timeout, ошибки или остановки worker — `E`.

Проверьте serial-устройство:

```bash
ls -l /dev/ttyACM*
```

Если у пользователя `khadas` нет доступа, добавьте его в группу устройства
(обычно `dialout`) и перезагрузите систему:

```bash
sudo usermod -aG dialout khadas
sudo reboot
```

Индикация не является критичной: недоступный serial-порт записывается в журнал,
но не останавливает распознавание. Отключить функцию можно через
`KOT_MIC_LED_ON_WAKE=0`.

Если музыка играла перед wake-word, она остаётся на паузе минимум 3 секунды с
момента обнаружения «Эй, Кот», даже когда короткая команда распознана раньше.
Интервал задаётся через `KOT_MUSIC_WAKE_MUTE_SECONDS`.

## Калибровка MA-USB8

`kot-mic-calibrate` пишет исходный 8-канальный WAV без `KOT_MIC_GAIN` и показывает
RMS, peak, долю клиппинга и грубое отношение участка речи к начальному участку
шума для каждого канала. У MA-USB8 каналы CH0–CH5 сырые, CH6 — результат
аппаратного beamforming, CH7 — дополнительный сырой канал. Это соответствует
[официальной карте каналов MA-USB8](https://wiki.sipeed.com/hardware/en/modules/micarray_usbboard_bl616.html#product-overview).

Остановите voice-worker, чтобы он не удерживал ALSA-устройство:

```bash
sudo systemctl stop kot-edge-voice
cd ~/kot_edge
mkdir -p ~/kot-mic-tests
```

Проверьте имя устройства:

```bash
arecord -l
arecord --dump-hw-params -D hw:MicArray,0 /dev/null
```

Для одного контрольного файла первые 3 секунды молчите, затем несколько раз
обычным голосом произнесите одинаковую фразу, например «Эй, Кот, включи музыку»:

```bash
.venv/bin/kot-mic-calibrate record ~/kot-mic-tests/control.wav \
  --seconds 15 --noise-seconds 3 --distance-m 1.0 --beam 0
```

Команда `--beam` сначала отправляет массиву фиксированный сектор `0…9/A/B` и ждёт
0,3 секунды стабилизации перед запуском `arecord`. Интервал можно изменить через
`--beam-settle-seconds`. Рядом с WAV создаётся `control.wav.json` с временем,
устройством, gain, beam, положением, расстоянием и параметрами записи. Существующий
файл можно анализировать повторно или разложить на mono-файлы для прослушивания:

```bash
.venv/bin/kot-mic-calibrate analyze ~/kot-mic-tests/control.wav
.venv/bin/kot-mic-calibrate split ~/kot-mic-tests/control.wav
```

### Круговой тест raw-каналов и CH6

Не ходите во время самой записи. Если смотреть на лицевую сторону массива и
держать разъём/плоский край снизу, штатные `0°` направлены к MIC0 сверху, а
секторы идут по часовой стрелке. Зафиксируйте эту ориентацию корпуса, вставайте
в указанную точку, нажимайте Enter и оставайтесь неподвижно.
Первые 3 секунды каждой записи молчите, затем одинаково произносите тестовую
фразу. Ориентируйтесь на номер позиции в каждом приглашении: с `--opposite` для
одной точки подряд будут два файла, поэтому между ними перемещаться не нужно.
Второй файл пишет CH6 с лучом, развёрнутым на 180°, и даёт прямую контрольную пару:

```bash
.venv/bin/kot-mic-calibrate circle ~/kot-mic-tests/circle-01 \
  --seconds 12 --noise-seconds 3 --distance-m 1.0 --opposite

.venv/bin/kot-mic-calibrate summarize ~/kot-mic-tests/circle-01
```

Получится 24 восьмиканальных WAV: 12 `aligned` и 12 `opposite`. Положительная
`delta` в итоговой таблице означает, что CH6 с лучом на говорящего лучше
противоположного луча. Если нулевой сектор корпуса не совпадает с командой массива,
используйте `--beam-offset N`; если порядок идёт зеркально — `--counterclockwise`.
Эти параметры должны затем совпасть с `KOT_AUTO_BEAM_OFFSET` и
`KOT_AUTO_BEAM_CLOCKWISE`.

Отчёт показывает сырой сигнал, грубый SNR и результат после текущего программного
`gain x16`. Ориентиры для обработанной речи: peak примерно от -12 до -3 dBFS,
отсутствие клиппинга и заметный рост RMS относительно первых секунд тишины.
Если клиппинг есть только после gain, уменьшите `KOT_MIC_GAIN`; если он уже есть
в сыром сигнале, нужно уменьшить аппаратный capture gain. Сравните прежде всего
CH6 с сырыми CH0–CH5 и дополнительным сырым CH7. Другой gain
можно проверить без новой записи:
`.venv/bin/kot-mic-calibrate analyze FILE --gain 4`.

После записи верните службу:

```bash
sudo systemctl start kot-edge-voice
journalctl -u kot-edge-voice -f
```

WAV-файлы исключены из Git. Для совместного анализа их нужно передавать
отдельно, не добавляя в репозиторий.

## Вычитание играющей музыки (AEC)

В версии 0.7.0 добавлен экспериментальный AEC, который не создаёт виртуальный
sink и не меняет маршрут Chromium. FFmpeg одновременно читает:

- monitor обычного стереовыхода как чистый эталон музыки;
- все 8 каналов MA-USB8 через PulseAudio/PipeWire;
- выбранный `KOT_AEC_MIC_CHANNEL`, нумеруемый от 1 до 8.

После приведения обоих потоков к mono / 16 kHz адаптивный NLMS-фильтр вычитает
из микрофона коррелирующую музыку. Затем применяется обычный `KOT_MIC_GAIN`, VAD
и ASR. По тестовой записи фон стал тише примерно на 5–6 dB, а отношение речи к
фону улучшилось примерно на 2–3 dB.

Включение при ручном запуске:

```bash
KOT_AEC_ENABLED=1 .venv/bin/python -m kot.voice
```

В заголовке worker должно появиться:

```text
Capture:      Pulse monitor + FFmpeg NLMS
```

Если FFmpeg, Pulse-сервер или один из источников недоступен при запуске, worker
печатает причину и автоматически возвращается к `ALSA + SoX`. Если уже
работающий FFmpeg завершится позднее, systemd перезапустит worker.

Актуальные имена источников проверяются так:

```bash
pactl list short sources
```

Имена задаются через `KOT_AEC_MONITOR_SOURCE` и `KOT_AEC_MIC_SOURCE`. Для
системной службы также нужен доступ к пользовательскому Pulse-сокету:

```ini
Environment=PULSE_SERVER=unix:/run/user/1000/pulse/native
```

Карта каналов Pulse 7.1 у MA-USB8 отличается от порядка сырого ALSA. Поэтому
`KOT_MIC_BEAM_CHANNEL=7` продолжает выбирать аппаратный CH6 в старом
`arecord + SoX`, а AEC использует отдельный `KOT_AEC_MIC_CHANNEL=4`, то есть
рабочую позицию FFmpeg `c3`/`lfe`. Попытка использовать FFmpeg `c6` даёт
полностью нулевой поток.

В версии 0.7.1 установленный service-файл оставляет AEC выключенным: live-NLMS
на текущем PipeWire требует дополнительной синхронизации часов monitor и
USB-микрофона. Проверенный тракт `ALSA + SoX` используется по умолчанию. Для
отдельного эксперимента AEC можно включить через override:

```bash
sudo systemctl edit kot-edge-voice
```

```ini
[Service]
Environment=KOT_AEC_ENABLED=1
```

Затем примените его:

```bash
sudo systemctl daemon-reload
sudo systemctl restart kot-edge-voice
```

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
sudo apt install -y alsa-utils sox playerctl ffmpeg pulseaudio-utils
```

Для новой установки:

```bash
cd /home/khadas/kot_edge
python3 -m venv .venv
.venv/bin/python -m pip install -U pip
.venv/bin/python -m pip install -e '.[voice]'
```

При обновлении существующей установки повторите editable install: он создаст
новую команду `kot-recognition-report`. Затем установите обновлённый unit:

```bash
cd /home/khadas/kot_edge
git pull
.venv/bin/python -m pip install -e '.[voice]'
sudo cp systemd/kot-edge-voice.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl restart kot-edge kot-edge-voice
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

## Автозапуск браузера

Версия 0.8.5 открывает после входа в графическую сессию одно обычное окно
Chromium с двумя вкладками в заданном порядке:

1. `https://music.yandex.com/`;
2. `http://127.0.0.1:8765/` — интерфейс Кота.

Используется обычный профиль браузера, поэтому сохраняются авторизация в
Яндекс Музыке и MPRIS-управление через `playerctl`. Сначала музыка открывается
активной вкладкой. Локальное расширение `yandex-music-bootstrap` до 120 секунд
ждёт и один раз нажимает именно кнопку воспроизведения «Моей волны», не пустую
кнопку нижнего плеера. Скрипт до 90 секунд ждёт `Playing` и непустое название
трека, после чего открывает Кота. Музыка продолжает играть в фоновой вкладке:
это предотвращает удаление свежей очереди Яндексом и сразу делает её доступной
для команд Кота.

Для музыкальной вкладки отключается фоновый throttling Chromium и разрешается
autoplay. Скрипт также ждёт локальный порт Кота до 60 секунд, чтобы при загрузке
системы его вкладка не открылась со страницей ошибки.

Установите XDG Autostart для пользователя `khadas`:

```bash
cd /home/khadas/kot_edge
chmod +x scripts/kiosk.sh
mkdir -p /home/khadas/.config/autostart
cp scripts/kot-edge-browser.desktop /home/khadas/.config/autostart/
```

Проверить без перезагрузки можно из терминала, открытого в рабочем столе:

```bash
/home/khadas/kot_edge/scripts/kiosk.sh
```

Настройки можно переопределить переменными окружения:

```bash
KOT_MUSIC_URL=https://music.yandex.com/
KOT_EDGE_URL=http://127.0.0.1:8765/
KOT_EDGE_WAIT_SECONDS=60
KOT_MUSIC_WARMUP_SECONDS=90  # максимальное ожидание готовой очереди MPRIS
KOT_MUSIC_STABILIZE_SECONDS=3
KOT_MUSIC_AUTOPAUSE=0        # 1 — экспериментально поставить на паузу
```

Автозапуск срабатывает после автоматического или ручного входа пользователя в
графическую сессию. Если после включения Khadas остаётся на экране входа, для
полностью автоматического старта нужно отдельно включить autologin пользователя
`khadas` в настройках display manager.

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
