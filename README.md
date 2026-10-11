# itrampoline

Добавляет в меню **Game Stick Lite** на плате **GBX-066-V02 (RK3032)**
пункт **iboxed trampoline** для запуска [iman](https://github.com/idleboxed/iman)
и [IGUI](https://github.com/idleboxed/igui) с SD. Перепрошивать стик не нужно.

## Установка

Нужны Linux, Python 3.12+, `uv` и Rust с ARMv7 musl.
[Подробнее о требованиях](docs/usage.md#подготовка).

Из каталога исходников:

```bash
uv tool install --force -e .
itrampoline --help
```

## Использование

Выключите стик, подключите SD к ПК и укажите путь к смонтированной карте:

```bash
itrampoline /path/to/mounted-sd
```

Программа предложит выбрать платформу, ядро которой будет подменено.
По умолчанию — **Daphne**. Подмена отключает штатный запуск игр этой платформы.
Опция `--no-ask` выбирает Daphne без вопросов; если нужна замена существующего
ядра или запускателя, установка остановится.

iman и IGUI установите с помощью [iroll](https://github.com/idleboxed/iroll).
Это можно сделать до или после добавления пункта меню.

После установки безопасно размонтируйте SD и выберите **iboxed trampoline**
в меню стика. iman запустит IGUI.

## Документация

- [Подготовка SD, выбор платформы и восстановление](docs/usage.md)
- [Устройство трамплина и ограничения](docs/runtime.md)
- [Разработка и проверки](docs/development.md)

## Лицензия

[BSD-3-Clause](LICENSE). Copyright (c) 2026, Igor Starikov.
