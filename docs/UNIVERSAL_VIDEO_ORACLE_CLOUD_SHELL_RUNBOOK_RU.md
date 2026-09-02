# Oracle Cloud Shell: активация Universal Video

## Назначение

Этот runbook активирует универсальный анализатор учебных видео на **существующем** Oracle-инстансе во Frankfurt как отдельный `universal-video.service`. Он не создаёт новую VM, не меняет production routing, не останавливает и не перезапускает DDS3 или существующие Assistant Lab services.

Контрольный launcher закрепляет:

- сервер `158.180.47.161` и пользователя `ubuntu`;
- ключ Cloud Shell `~/.ssh/bridge_school_dds3_oracle`;
- ED25519 fingerprint сервера;
- control commit `775dd6a88ede5672c3df5f42589e71a16146e2f4`;
- launcher blob `96f5d0245c85865f20de715d783034a369912623`;
- runtime commit `59377de601c1586ae9914a51a340dc72ac2007ce`;
- единственный разрешённый payload `ops/oracle_universal_video_run_command.sh`.

Launcher не принимает произвольный host, user или remote command. OAuth Google Drive этим шагом не передаётся. До отдельного разрешения реальное видео не запускается.

## Важное правило для интерактивного OCI Cloud Shell

**Не включайте `set -u` / `set -o nounset` непосредственно в интерактивной оболочке Cloud Shell.** Oracle PS1 в некоторых сессиях обращается к переменной `USER`; если она не экспортирована, `nounset` вызывает `bash: USER: unbound variable` при отрисовке приглашения. Это ошибка интерактивного wrapper, а не Oracle-сервера и не Universal Video. Сам launcher запускается отдельным `bash` и внутри использует строгий `set -Eeuo pipefail` безопасно.

Если `nounset` уже был включён, достаточно выполнить `set +u` или открыть новую Cloud Shell session. До запуска launcher сервер при такой ошибке не изменяется.

## Предварительные условия

Работа выполняется в OCI Cloud Shell того же аккаунта, где ранее был создан ключ `~/.ssh/bridge_school_dds3_oracle`. Приватный ключ не копируется в чат, GitHub, Google Drive или журнал выполнения.

Перед установкой launcher сам проверяет:

- закреплённый SSH fingerprint и passwordless `sudo`;
- `assistant-lab.service` и реальный локальный DDS3 без fallback;
- состояния `assistant-lab-observer.service` и `assistant-lab-control.service`, если они существуют, и сохраняет эти состояния неизменными;
- не менее 4 GiB доступной RAM;
- не менее 6 GiB свободного диска;
- отсутствие чрезмерной текущей CPU load относительно числа CPU.

## Рекомендуемый режим: одна вставка

В интерактивную Cloud Shell вставляется **один блок**, без `set -u`:

```bash
set +u
export USER="$(id -un)"
CONTROL_COMMIT='775dd6a88ede5672c3df5f42589e71a16146e2f4'
LAUNCHER_BLOB='96f5d0245c85865f20de715d783034a369912623'
F='/tmp/cloud_shell_activate_universal_video.sh'
curl -fsSL "https://raw.githubusercontent.com/olegmed1-art/bridge-video-free/${CONTROL_COMMIT}/ops/cloud_shell_activate_universal_video.sh" -o "$F" &&
test "$(git hash-object "$F")" = "$LAUNCHER_BLOB" &&
bash -n "$F" &&
env USER="$USER" bash "$F" bootstrap
```

`bootstrap` работает fail-closed и строго в порядке `probe → activate → status → synthetic smoke`. Следующий этап начинается только если предыдущий завершился успешно. Synthetic smoke выполняется отдельно от установки и не переустанавливает sidecar повторно. Реальное видео не запускается.

Успешное завершение подтверждается финальным маркером:

```text
ORACLE_UNIVERSAL_VIDEO_CLOUD_SHELL_BOOTSTRAP_PASS
```

Если любой gate не проходит, script завершается сразу и финальный bootstrap PASS не печатается.

## Что теперь проверяет synthetic smoke

Smoke использует только локальный трёхсекундный чёрный ролик с синусоидальным звуком. Для него ожидается fail-closed результат `REVIEW`, а `transcript.qc_pass` должен быть `false`. Наличие файла результата само по себе больше не считается достаточным доказательством. Дополнительно повторно проверяются Assistant Lab, DDS3 и состояния optional resident services.

Ожидаемые маркеры включают:

```text
UNIVERSAL_VIDEO_SYNTHETIC_RESULT_CONTRACT_PASS
UNIVERSAL_VIDEO_SYNTHETIC_SMOKE_PASS
DDS3_AFTER_SYNTHETIC_SMOKE_PASS
ORACLE_UNIVERSAL_VIDEO_CLOUD_SHELL_SMOKE_PASS
```

## Явные gate-режимы для диагностики

### 1. Безопасный probe

```bash
bash /tmp/cloud_shell_activate_universal_video.sh probe
```

`probe` подтверждает host fingerprint, приватный ключ, passwordless sudo, защищённые сервисы, реальный DDS3 без fallback и достаточные свободные ресурсы. Установки и изменений сервисов нет.

### 2. Активация без задания

```bash
bash /tmp/cloud_shell_activate_universal_video.sh activate
```

`activate` выполняет side-by-side установку с пониженным scheduling priority, включает только `universal-video.service`, предварительно прогревает ASR-модель и не ставит в очередь ни реальное, ни синтетическое видео. Фактический `source_commit` обязан точно совпасть с закреплённым runtime commit.

### 3. Повторная read-only проверка status

```bash
bash /tmp/cloud_shell_activate_universal_video.sh status
```

Ожидаются `assistant_lab=active`, `universal_video_enabled=enabled`, `universal_video_active=active`, локальный и внешний DDS3 `ready`, `engine=DDS3`, `fallback_used=false`.

### 4. Ограниченный synthetic smoke

```bash
bash /tmp/cloud_shell_activate_universal_video.sh smoke
```

Диагностический `smoke` сохраняется для отдельного повторного прогона. Школьные записи и материалы учеников не используются.

## Доказательства и остановка по ошибке

Каждый этап переносится в issue #318 только как технические маркеры и состояния сервисов, без приватного ключа и OAuth. При любом несовпадении fingerprint, source commit, недостатке ресурсов, изменении защищённых resident services, деградации Assistant Lab/DDS3 или неверном synthetic QC launcher завершает работу с ошибкой. Следующий этап в таком случае не выполняется.

После bootstrap реальное видео всё ещё не запускается. Отдельно проверяются защищённая установка Drive OAuth, маршрут входного файла, результат в Drive/Neon и политика хранения артефактов; затем разрешается одно контрольное учебное видео.
