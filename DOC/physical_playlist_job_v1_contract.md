# physical_playlist_job.v1 — Canonical Playlist Job Contract

Версия документа: 1.0  
Назначение: единый нейтральный JSON-формат стыковки между программами, которые формируют задание плейлиста, и программами, которые физически подготавливают плейлист на диске.

---

## 1. Назначение формата

`physical_playlist_job.v1` — это внешний нейтральный контракт задания на физическую подготовку плейлиста.

Формат отвечает на вопрос:

```text
Какие исходные аудиофайлы нужно включить в физический плейлист и с какими намерениями обработки?
```

Формат не должен зависеть от конкретного приложения-производителя.

Допустимые производители:

- MusicLib Web;
- TXT / CSV / M3U / M3U8 importer;
- folder scanner;
- ручной генератор JSON;
- другой каталогизатор музыки.

Допустимые потребители:

- Physical Playlist Builder;
- другая CLI / desktop / server-утилита физической сборки плейлистов.

---

## 2. Основные правила независимости

Потребитель `physical_playlist_job.v1` не должен требовать:

- Flask;
- SQLite;
- MusicLib Web;
- запущенный Web-сервер;
- таблицы `audio_files`, `audio_metadata`, `playlist_items`, `duplicate_items`;
- импорт Python-модулей из `backend/` MusicLib;
- внутренние ID MusicLib;
- `musiclib.export_plan.v1`.

`musiclib.export_plan.v1`, если существует, является только внутренней read-only preview-моделью Web-приложения и не является внешним контрактом.

---

## 3. Имя файла

Рекомендуемое каноническое имя:

```text
playlist_job.json
```

Допустимое имя с отметкой времени:

```text
playlist_job_<playlist-name>_<YYYYMMDD_HHMMSS>.json
```

Потребитель не должен полагаться на имя файла. Источником истины является содержимое JSON, прежде всего поле:

```json
"format": "physical_playlist_job.v1"
```

---

## 4. Каноническая верхнеуровневая структура

```json
{
  "format": "physical_playlist_job.v1",
  "generated_at": "2026-05-01T00:00:00+00:00",
  "playlist": {
    "name": "Road Mix",
    "description": "Optional playlist notes",
    "track_count": 2
  },
  "settings": {
    "output_format": "source",
    "copy_mode": "copy_if_compatible",
    "normalize_loudness": false,
    "target_lufs": -14.0,
    "true_peak_db": -1.0,
    "write_tags": false,
    "generate_m3u8": true,
    "filename_template": "{position:02d} - {artist} - {title}"
  },
  "tracks": [],
  "summary": {
    "status": "ready",
    "can_run": true
  },
  "producer_meta": {
    "producer": "MusicLib Web",
    "will_write_files": false
  }
}
```

---

## 5. Верхнеуровневые поля

| Поле | Тип | Обязательность | Описание |
|---|---:|---:|---|
| `format` | string | Да | Всегда строго `"physical_playlist_job.v1"`. |
| `generated_at` | string/null | Рекомендуется | ISO-8601 timestamp формирования файла. |
| `playlist` | object | Да | Метаданные плейлиста. |
| `settings` | object | Нет | Намерения физической подготовки. Если отсутствует, потребитель применяет безопасные defaults. |
| `tracks` | array | Да | Упорядоченный список треков. Может быть пустым, но это warning / not runnable. |
| `summary` | object | Нет | Предварительная оценка producer-side. Потребитель может игнорировать. |
| `producer_meta` | object | Нет | Необязательная трассировка источника. Потребитель обязан безопасно игнорировать. |

Запрещено считать обязательными старые верхнеуровневые поля:

```json
"schema"
"playlist_name"
"output_format"
"normalize_loudness"
"write_tags"
```

Они допускаются только в legacy-normalizer для старых файлов.

---

## 6. Объект `playlist`

```json
{
  "name": "Road Mix",
  "description": "Optional notes",
  "track_count": 12
}
```

| Поле | Тип | Обязательность | Описание |
|---|---:|---:|---|
| `name` | string | Да | Человекочитаемое имя плейлиста. Не должно быть пустым после trim. |
| `description` | string/null | Нет | Описание или комментарий. |
| `track_count` | int | Нет | Информационное число треков. Потребитель может пересчитать по `tracks`. |

Правило: если `playlist.track_count` не совпадает с длиной `tracks`, потребитель должен считать длину `tracks` фактической и добавить warning.

---

## 7. Объект `settings`

```json
{
  "output_format": "source",
  "copy_mode": "copy_if_compatible",
  "normalize_loudness": false,
  "target_lufs": -14.0,
  "true_peak_db": -1.0,
  "write_tags": false,
  "generate_m3u8": true,
  "filename_template": "{position:02d} - {artist} - {title}"
}
```

| Поле | Тип | Обязательность | Default | Описание |
|---|---:|---:|---:|---|
| `output_format` | string/null | Нет | `"source"` | Целевой формат: `"source"`, `"mp3"`, `"flac"`, `"wav"`, `"m4a"` и т.п. `null` legacy-нормализуется в `"source"`. |
| `copy_mode` | string | Нет | `"copy_if_compatible"` | Намерение копирования/конвертации. |
| `normalize_loudness` | boolean | Нет | `false` | Нормализовать громкость только у экспортируемых копий. |
| `target_lufs` | number/null | Нет | `-14.0` | Целевой integrated loudness. Используется только если `normalize_loudness=true`. |
| `true_peak_db` | number/null | Нет | `-1.0` | Ограничение true peak. Используется только если `normalize_loudness=true`. |
| `write_tags` | boolean | Нет | `false` | Записывать теги только в экспортированные копии. |
| `generate_m3u8` | boolean | Нет | `true` | Создать итоговый `playlist.m3u8` в папке экспорта. |
| `filename_template` | string/null | Нет | `"{position:02d} - {artist} - {title}"` | Шаблон имени выходного файла, если у трека нет `output_filename`. |

Рекомендуемые значения `copy_mode`:

| Значение | Смысл |
|---|---|
| `copy_if_compatible` | Копировать исходный файл, если формат совместим с настройками; иначе планировать конвертацию. |
| `copy_only` | Только копировать, не конвертировать. Несовместимые треки становятся warning/blocker по политике потребителя. |
| `convert_if_needed` | Конвертировать только когда целевой формат отличается от исходного или требуется обработка. |
| `force_convert` | Конвертировать все runnable-треки в `output_format`. |

---

## 8. Массив `tracks`

Каждый элемент `tracks` описывает один исходный аудиофайл в порядке плейлиста.

Минимальный трек:

```json
{
  "position": 1,
  "source_path": "D:\\Music\\Artist\\Track.flac"
}
```

Полный пример:

```json
{
  "position": 1,
  "source_path": "D:\\Music\\Artist\\Album\\01 - Track.flac",
  "output_filename": "01 - Artist - Track.flac",
  "filename_hint": "Artist - Track",
  "title": "Track",
  "artist": "Artist",
  "album": "Album",
  "albumartist": "Artist",
  "tracknumber": "1",
  "date": "1991",
  "genre": "Rock",
  "duration_sec": 245.3,
  "codec": "flac",
  "bitrate_kbps": 900,
  "sample_rate_hz": 44100,
  "channels": 2,
  "bit_depth": 16,
  "tag_format": "VorbisComment",
  "availability": "available",
  "warnings": [],
  "blockers": [],
  "duplicate_advice": null
}
```

---

## 9. Поля трека

| Поле | Тип | Обязательность | Описание |
|---|---:|---:|---|
| `source_path` | string | Да для runnable-трека | Абсолютный путь к исходному аудиофайлу. Если отсутствует — track blocker. |
| `position` | int | Рекомендуется | 1-based позиция. Если отсутствует, потребитель использует порядок массива и добавляет warning. |
| `output_filename` | string/null | Нет | Явное имя выходного файла с расширением. Имеет приоритет над `filename_template`. |
| `filename_hint` | string/null | Нет | Подсказка для построения имени, если `output_filename` отсутствует. |
| `title` | string/null | Нет | Название трека для тегов и M3U8. |
| `artist` | string/null | Нет | Исполнитель. |
| `album` | string/null | Нет | Альбом. |
| `albumartist` | string/null | Нет | Album Artist. |
| `tracknumber` | string/null | Нет | Номер трека. Лучше строкой, чтобы сохранить `1/12`. |
| `date` | string/null | Нет | Год или дата. |
| `genre` | string/null | Нет | Жанр. |
| `duration_sec` | number/null | Нет | Длительность в секундах, информационно. |
| `codec` | string/null | Нет | Codec / container hint: `mp3`, `flac`, `aac`, `wav` и т.п. |
| `bitrate_kbps` | int/null | Нет | Bitrate, информационно. |
| `sample_rate_hz` | int/null | Нет | Sample rate. |
| `channels` | int/null | Нет | Количество каналов. |
| `bit_depth` | int/null | Нет | Bit depth для lossless/PCM. |
| `tag_format` | string/null | Нет | Например `ID3v2.3`, `ID3v2.4`, `VorbisComment`. |
| `availability` | string/null | Нет | Producer-side hint: `available`, `missing`, `unknown`. Потребитель обязан сам перепроверить файл. |
| `warnings` | array | Нет | Предупреждения, пришедшие от производителя. |
| `blockers` | array | Нет | Блокирующие проблемы. Если непустой — трек считается blocked. |
| `duplicate_advice` | object/null | Нет | Необязательная рекомендация по дубликатам. Только advisory. |

Запрещено использовать `display_title` как каноническое поле. Старое `display_title` legacy-нормализуется в `title`.

---

## 10. Формат `warnings` и `blockers`

Канонический элемент issue:

```json
{
  "code": "missing_required_tag",
  "message": "Required tag is missing: artist",
  "field": "artist",
  "source": "producer"
}
```

| Поле | Тип | Обязательность | Описание |
|---|---:|---:|---|
| `code` | string | Рекомендуется | Машиночитаемый код. |
| `message` | string | Да | Человекочитаемое сообщение. |
| `field` | string/null | Нет | Поле, к которому относится проблема. |
| `source` | string/null | Нет | Источник: `producer`, `validator`, `filesystem`, `ffmpeg`, `tags`, `loudness`. |

Для совместимости потребитель может принимать строки:

```json
"Missing artist tag"
```

и нормализовать их в:

```json
{
  "code": "legacy_issue",
  "message": "Missing artist tag",
  "source": "producer"
}
```

---

## 11. Объект `summary`

`summary` — необязательная предварительная оценка производителя.

```json
{
  "status": "ready",
  "can_run": true,
  "track_count": 12,
  "blocked_count": 0,
  "warning_count": 1
}
```

Потребитель не должен доверять `summary` как окончательному результату. Он обязан выполнить собственную валидацию.

Рекомендуемые значения `summary.status`:

| Значение | Смысл |
|---|---|
| `ready` | Производитель считает задание готовым. |
| `warnings` | Есть предупреждения, но можно запускать. |
| `blocked` | Есть блокирующие проблемы. |
| `unknown` | Производитель не выполнял readiness-анализ. |

---

## 12. Объект `producer_meta`

`producer_meta` — необязательная трассировка производителя.

```json
{
  "producer": "MusicLib Web",
  "producer_version": "0.12.0",
  "will_write_files": false,
  "source_playlist_id": "optional-internal-id"
}
```

Правила:

1. `producer_meta` не должен быть обязательным.
2. Потребитель обязан безопасно игнорировать неизвестные поля.
3. Внутренние ID допускаются только здесь и не должны требоваться для запуска.
4. Для Web-производителя рекомендуется указывать:

```json
"will_write_files": false
```

---

## 13. Необязательное `duplicate_advice`

`duplicate_advice` используется только как advisory metadata.

```json
{
  "has_preferred_duplicate": true,
  "preferred_source_path": "D:\\Music\\Artist\\Track.flac",
  "current_score": 89,
  "preferred_score": 144,
  "score_delta": 55,
  "reason": [
    "Lossless format (FLAC)",
    "Complete tags",
    "File is available on disk"
  ]
}
```

Правила:

1. Потребитель не должен автоматически заменять `source_path` на `preferred_source_path`.
2. Замена возможна только отдельным явным действием пользователя.
3. Отсутствие `duplicate_advice` не является ошибкой.

---

## 14. Валидационные правила потребителя

Fatal validation errors для всего файла:

- JSON не читается;
- корень не object;
- `format` отсутствует и невозможно legacy-нормализовать;
- `format != "physical_playlist_job.v1"`;
- `playlist.name` отсутствует или пустой после legacy-normalization;
- `tracks` отсутствует или не является массивом.

Track blockers:

- отсутствует `source_path`;
- `source_path` не строка;
- `blockers` уже непустой от производителя;
- потенциально опасный `output_filename` после нормализации;
- дублирующийся `output_filename`, если невозможно безопасно разрешить конфликт.

Warnings:

- отсутствует `position`;
- позиции не уникальны;
- `playlist.track_count` не совпадает с длиной `tracks`;
- отсутствует `settings`, применены defaults;
- `availability=missing`, но фактическая проверка ещё не выполнялась;
- неизвестные необязательные поля;
- legacy-формат был преобразован в canonical.

---

## 15. Legacy-normalization

Для переходного периода потребитель может принимать старый формат.

### Старое поле → каноническое поле

| Legacy | Canonical |
|---|---|
| `schema` | `format` |
| `playlist_name` | `playlist.name` |
| top-level `output_format` | `settings.output_format` |
| top-level `normalize_loudness` | `settings.normalize_loudness` |
| top-level `write_tags` | `settings.write_tags` |
| `tracks[].display_title` | `tracks[].title` |

Если обнаружен legacy-формат, потребитель должен вывести warning:

```text
Legacy playlist_job format detected. Please regenerate the job file using the canonical physical_playlist_job.v1 contract.
```

Web-производители не должны генерировать legacy-формат.

---

## 16. Канонический минимальный пример

```json
{
  "format": "physical_playlist_job.v1",
  "generated_at": "2026-05-01T00:00:00+00:00",
  "playlist": {
    "name": "Minimal Test Playlist",
    "description": null,
    "track_count": 1
  },
  "settings": {
    "output_format": "source",
    "copy_mode": "copy_if_compatible",
    "normalize_loudness": false,
    "target_lufs": -14.0,
    "true_peak_db": -1.0,
    "write_tags": false,
    "generate_m3u8": true,
    "filename_template": "{position:02d} - {artist} - {title}"
  },
  "tracks": [
    {
      "position": 1,
      "source_path": "D:\\Music\\Artist\\Track.flac",
      "output_filename": "01 - Artist - Track.flac",
      "title": "Track",
      "artist": "Artist",
      "album": "Album",
      "albumartist": "Artist",
      "tracknumber": "1",
      "date": "1991",
      "genre": "Rock",
      "duration_sec": 245.3,
      "codec": "flac",
      "bitrate_kbps": 900,
      "sample_rate_hz": 44100,
      "channels": 2,
      "bit_depth": 16,
      "tag_format": "VorbisComment",
      "availability": "available",
      "warnings": [],
      "blockers": []
    }
  ],
  "summary": {
    "status": "ready",
    "can_run": true
  },
  "producer_meta": {
    "producer": "Example Producer",
    "will_write_files": false
  }
}
```

---

## 17. Canonical producer checklist

Производитель `playlist_job.json` должен:

- записывать `format: "physical_playlist_job.v1"`;
- не записывать `schema` как основное поле;
- помещать имя плейлиста в `playlist.name`;
- помещать настройки в `settings`;
- помещать список треков в `tracks`;
- использовать `title`, а не `display_title`;
- не требовать от потребителя Flask, SQLite, таблицы или внутренние ID;
- помещать внутреннюю трассировку только в `producer_meta`;
- не выполнять физические файловые операции, если это Web/read-only producer.

---

## 18. Canonical consumer checklist

Потребитель `playlist_job.json` должен:

- валидировать `format`;
- читать `playlist.name`;
- читать `settings` с безопасными defaults;
- читать `tracks` в порядке `position`, а при отсутствии `position` — в порядке массива;
- считать `source_path` обязательным для runnable-трека;
- считать непустой `blockers` причиной блокировки трека;
- безопасно игнорировать неизвестные поля;
- самостоятельно проверять существование файлов на диске;
- никогда не изменять исходные файлы;
- писать любые результаты только в выбранную output-папку;
- поддерживать legacy-normalization временно, но не документировать legacy как основной формат.

---

## 19. Рекомендуемые тесты совместимости

### Для producer-проекта

- generated job has `format == "physical_playlist_job.v1"`;
- generated job has no top-level `schema`;
- generated job has `playlist.name`;
- generated job has `settings` object;
- generated job has `tracks` array;
- every track has `source_path` or a blocker explaining why it is not runnable;
- optional `producer_meta` does not contain required consumer-only data.

### Для consumer-проекта

- accepts canonical example;
- rejects unsupported `format`;
- rejects missing/empty `playlist.name` after normalization;
- rejects missing `tracks` array;
- reports missing `source_path` as track blocker;
- accepts `producer_meta` with unknown fields;
- accepts legacy example with warning;
- normalizes `display_title` to `title`;
- normalizes top-level `output_format` into `settings.output_format`.

---

## 20. Итоговое правило

Канонический контракт — это:

```text
format + playlist + settings + tracks
```

а не:

```text
schema + playlist_name + top-level processing fields
```

Старый формат допускается только как временный legacy input для мягкой миграции.
