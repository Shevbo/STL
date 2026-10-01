---
name: project_mail_check_session_hook
description: Ежечасная проверка почты real-trade переживает перезапуск сеанса через SessionStart-хук в settings.local.json, без фоновых циклов
metadata:
  type: project
---

17.09.2026: ежечасная проверка почты окна real-trade (Haiku, 06:57-21:57) живёт как CronCreate, а он только сессионный. Чтобы она переживала перезапуск, в `.claude/settings.local.json` этой папки стоит SessionStart-хук: `cat "$CLAUDE_PROJECT_DIR/.claude/mail_cron_arm.json"` печатает в контекст инструкцию «проверь CronList, нет задачи - создай одну, есть - вторую не создавай». Файл инструкции исключён из git через `.git/info/exclude` - хук только у этого окна.

**Why:** прежнюю фоновую машинерию почты (devmail_sync/hook/autopilot, задания планировщика) удалили в 27d7950; в ночь 17-18.08 два автоответчика подтверждали друг другу подтверждения и сожгли лимит подписки (792 письма, модель раз в 2.9 мин). Хук не ходит в сеть и ни на что не отвечает сам.

**How to apply:** не возвращать фоновый опрос почты с моделью и автоответы. Меняешь текст проверки - правь `.claude/mail_cron_arm.json` (валидный JSON, hookEventName SessionStart). См. [[project_dev_msg_middleware]].
