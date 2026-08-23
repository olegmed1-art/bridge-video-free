# Oracle Cloud Shell: активация Universal Video

## Назначение

Этот runbook активирует универсальный анализатор учебных видео на **существующем** Oracle-инстансе во Frankfurt как отдельный `universal-video.service`. Он не создаёт новую VM, не меняет production routing, не останавливает и не перезапускает `assistant-lab.service` или DDS3.

Контрольный launcher закрепляет:

- сервер `158.180.47.161` и пользователя `ubuntu`;
- ключ Cloud Shell `~/.ssh/bridge_school_dds3_oracle`;
- ED25519 fingerprint сервера;
- control commit `bd7b13ad25238ee736954e892ac58e6e38a0bd26`;
- runtime commit `59377de601c1586ae9914a51a340dc72ac2007ce`;
- единственный разрешённый payload `ops/oracle_universal_video_run_command.sh`.

Launcher не принимает произвольный host, user или remote command. OAuth Google Drive этим шагом не передаётся. До отдельного разрешения реальное видео не запускается.

## Предварительные условия

Работа выполняется в OCI Cloud Shell того же аккаунта, где ранее был создан ключ `~/.ssh/bridge_school_dds3_oracle`. Приватный ключ не копируется в чат, GitHub, Google Drive или журнал выполнения.

Скачать launcher по точному control commit и проверить синтаксис:

```bash
CONTROL_COMMIT='bd7b13ad25238ee736954e892ac58e6e38a0bd26'
curl -fsSL "https://raw.githubusercontent.com/olegmed1-art/bridge-video-free/${CONTROL_COMMIT}/ops/cloud_shell_activate_universal_video.sh" \
  -o /tmp/cloud_shell_activate_universal_video.sh
bash -n /tmp/cloud_shell_activate_universal_video.sh
```

## Рекомендуемый режим: одна ручная команда

После скачивания launcher весь безопасный bootstrap выполняется одной ручной командой:

```bash
bash /tmp/cloud_shell_activate_universal_video.sh bootstrap
```

`bootstrap` работает fail-closed и строго в порядке `probe → activate → status → smoke`. Следующий этап начинается только если предыдущий завершился успешно. Synthetic smoke выполняется отдельно от установки и не переустанавливает sidecar повторно. Реальное видео не запускается.

Успешное завершение подтверждается финальным маркером:

```text
ORACLE_UNIVERSAL_VIDEO_CLOUD_SHELL_BOOTSTRAP_PASS
```

Если любой gate не проходит, script завершается сразу и финальный bootstrap PASS не печатается.

## Явные gate-режимы для диагностики

### 1. Безопасный probe

```bash
bash /tmp/cloud_shell_activate_universal_video.sh probe
```

`probe` только подтверждает host fingerprint, пригодность приватного ключа, passwordless `sudo`, активность Assistant Lab и реальный DDS3 без fallback. Установки и изменений сервисов нет.

Ожидаемый финальный маркер:

```text
ORACLE_UNIVERSAL_VIDEO_CLOUD_SHELL_PROBE_PASS
```

### 2. Активация без задания

Только после успешного `probe`:

```bash
bash /tmp/cloud_shell_activate_universal_video.sh activate
```

`activate` выполняет side-by-side установку с пониженным scheduling priority, включает только `universal-video.service`, предварительно прогревает ASR-модель и не ставит в очередь ни реальное, ни синтетическое видео.

Ожидаемый финальный маркер:

```text
ORACLE_UNIVERSAL_VIDEO_CLOUD_SHELL_ACTIVATION_PASS
```

### 3. Повторная read-only проверка status

```bash
bash /tmp/cloud_shell_activate_universal_video.sh status
```

Ожидаются `assistant_lab=active`, `universal_video_enabled=enabled`, `universal_video_active=active`, локальный и внешний DDS3 `ready`, `engine=DDS3`, `fallback_used=false`.

Ожидаемый финальный маркер:

```text
ORACLE_UNIVERSAL_VIDEO_CLOUD_SHELL_STATUS_PASS
```

### 4. Ограниченный synthetic smoke

Только после успешных `activate` и `status`:

```bash
bash /tmp/cloud_shell_activate_universal_video.sh smoke
```

Диагностический `smoke` сохраняется для отдельного повторного прогона. В режиме `bootstrap` используется более экономный smoke-only путь: он создаёт локальный трёхсекундный чёрный ролик с синусоидальным звуком, прогоняет его через уже активный sidecar и проверяет manifest без повторной установки. Школьные записи и материалы учеников не используются.

Ожидаемый финальный маркер:

```text
ORACLE_UNIVERSAL_VIDEO_CLOUD_SHELL_SMOKE_PASS
```

## Доказательства и остановка по ошибке

Каждый этап переносится в issue #318 только как технические маркеры и состояния сервисов, без приватного ключа и OAuth. При любом несовпадении fingerprint, отказе SSH/sudo, деградации Assistant Lab или DDS3 launcher завершает работу с ошибкой. Следующий этап в таком случае не выполняется.

После bootstrap/smoke реальное видео всё ещё не запускается. Отдельно проверяются защищённая установка Drive OAuth, маршрут входного файла, результат в Drive/Neon и политика хранения артефактов; затем разрешается одно контрольное учебное видео.
