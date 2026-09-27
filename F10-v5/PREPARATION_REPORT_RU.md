# Доработка F10 по оригинальным трейсам

Новая версия: emse-f10-v5-frozen-package-2026-09-27. Исследование по-прежнему F10-EMSE-CLEAN-V5-2026-09-25. Повторных модельных запросов: 0. Исходный protocol, анализатор и исходы сохранены; гейты не менялись. Предыдущая версия статьи с неполными артефактами сохранена в отдельном каталоге.

## Итог проверки

С SSH petr@192.168.2.143 получен полный оригинальный архив. Проверены все 4 800 окон, 2 400 пар и 6 441 provider request/receipt. Все per-task исходы и суммы совпадают с прежним анализом. Ожидаемый маршрут присутствует во всех запросах; mismatch 0; две точные schema objects стабильны внутри окон. Восстановлены excluded ledger на 2 320 ID и исходный бинарник; SHA совпадают. Выбранные 600 ID не пересекаются с ledger или тремя qualification identities.

Frozen estimators пересчитаны непосредственно по оригинальным result.json: все четыре модельных результата и bootstrap bounds совпали. Qwen сохраняет PASS и 25,76% экономии, DeepSeek — PASS и 21,34%. GLM и GPT-OSS остаются без подтверждения по условию zero integrity. Все модели и семейства представлены в статье.

## Добавленные замеры

- Qwen: экономия 579 447 prompt и 54 783 completion tokens. Prompt share разницы 91,36%.
- DeepSeek: экономия 392 821 prompt и 59 447 completion tokens. Prompt share 86,86%.
- Раздельные компоненты, запросы и transport retries приведены для всех четырёх моделей/двух arms.
- Добавлены agent wall-time mean, median, p95 и средняя paired разница. Средняя экономия времени 0,55 с у Qwen и 7,83 с у DeepSeek — описательно, не новое подтверждение latency superiority.
- Native negative-result recovery отделён от transport retries и host relays. Не используется число запросов минус число окон как оценка retry count.
- В семи provider failures: GLM rich один HTTP 503, GLM selective пять HTTP 400, GPT-OSS rich один HTTP 504. Всего шесть одинаковых повторных payloads после ошибок. Два окна затем дали правильный ответ, но usage остался неполным. Неизвестные токены ошибочных вызовов не выдуманы.

## Инженерный результат

Восстановленные схемы имеют 2 583 и 364 байта compact canonical JSON. Это размер schema objects, не доказанная доля tokenizer overhead. Схемы совпадают с отдельно извлечёнными reference objects.

QA не полностью освобождена от общих executor-validation guards. Qwen и DeepSeek имеют по 86 rich и 74 selective stalls; трейсы показывают file-change guard при QA-only tool surface. Поэтому статья оценивает суммарную политику terminal contract и host processing. Чистой абляции отдельных полей, validators и recovery не было; она не объявлена проведённой.

Gateway сохраняет upstream HTTP status в receipt, но возвращает клиенту generic 502. Поэтому для upstream 400 выполнено пять попыток всего: исходная и четыре повтора. Это описанный механизм ограничения обработки ошибок; в frozen binary и исходах он не исправлялся задним числом.

## Артефакты и пределы воспроизводимости

Архив содержит первичные request/response, transport receipts, execution и session records, manifests, ledger, точный executable и frozen runner. Полный оригинальный архив сохранён приватно. В submission copy исключены ephemeral signing secrets, пустые lock files и Python caches; содержательные записи побайтово сохранены и имеют SHA inventory.

Build metadata: Go 1.26.2, Linux amd64, base commit a8c87a3f2804c9a61e07c0350e2cccb860dabe5c, vcs.modified=true. Сам бинарник точный; исходный modified working tree не восстановлен, поэтому сборка только из base commit не объявлена воспроизводимой.

Также не восстановлены исходные HTTP body bytes и provider error bodies, которые gateway не сохранял. JSON responses и usage имеются. Данные не являются независимой аттестацией provider weights, публичной preregistration или доказательством adversarial safety.

## Статья и подача

IMRAD сохранён. Добавлены три таблицы с дополнительными замерами и полное описание primary route check и all-request audit. Abstract 229 слов, 6 keywords, decimal headings, automatic PAGE fields, author-year references. Авторские сведения и подтверждённые declarations сохранены. Final render и визуальная проверка отмечены в document-build-audit.json.

Cover letter обновлён для Research Papers / Agentic Software Engineering. Публичный депозит и отправка в Editorial Manager не выполнялись. Использовать новый пакет FROZEN_PACKAGE; прежний пакет с недостающими трейcами superseded.

Официальные требования: https://link.springer.com/journal/10664/submission-guidelines ; https://emsejournal.github.io/special_issues/2026_SI_Agentic_SE.html . Последняя проверка требований выполнена 27 сентября 2026; deadline 15 октября 2026. Публичный persistent repository рекомендуется; универсального обязательного DOI в найденных правилах нет.

## Проверка переносимого пакета

Готовый supplementary ZIP распакован в отдельном каталоге Ubuntu. Проверены SHA каждого файла архива и всех содержательных первичных файлов. Все пять offline scripts прошли: unpack_primary, reproduce_selection, reproduce_offline, audit_primary_traces и reproduce_primary. Выборка и targets независимо восстановлены из исходных snapshots и exclusion ledger с точным совпадением frozen bytes; статистика пересчитана из всех 4 800 исходов. Повторно полученные audit JSON, CSV и statistics JSON побайтово совпадают с приложенными. Модельных вызовов: 0. Подробности в portable-reproduction-verification.json.

## Приватный frozen пакет для GitHub

Репозиторий https://github.com/pyatkovpetr/emse подтверждён как private. Подготовлен отдельный snapshot F10-v5 с тегом f10-v5-artifact-r1. Добавлен самостоятельный исходный Linux executable (ELF), SHA соответствует frozen protocol; Windows exe не выдаётся за v5. Новых сборок и модельных запросов нет. В рукописи подробнее описаны Go CLI, цикл агента, конфигурация, native/direct terminal paths, controller, gateway, receipts и external grader. Отдельный раздел описывает private доступ, frozen artifact и границы воспроизводимости; full harness source остаётся закрытым.
