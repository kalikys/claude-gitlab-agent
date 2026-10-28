Задача {project}#{iid}: результат одобрил @{approver} (метка approved, {approved_at}). Запиши итог в базу знаний.
Одобрено то, что было в задаче на момент {approved_at}: правки и комментарии позже этого времени не переносишь.

Порядок:
1. Выбери из задачи то, что стоит помнить дольше её жизни: решения с причиной, проверенные факты, инструкции,
   итоговые результаты. Переписку, черновики и ход мысли не переносишь. Если нечего — напиши в issue, почему, и закончи.
2. Для каждого знания поищи похожее в `~/gitlab-agent/work/kb` (`kb.py search`). Есть — обнови, нет — `kb.py new`.
   `owner` = @{approver}, `sources` = ссылка на задачу, `approved_by: {approver}`.
3. Персональные данные — не в базу, а ветка `claude/{iid}-...` в `~/gitlab-agent/work/team`, `gl.py push` и `gl.py new-mr acme/team`.
4. `python3 scripts/kb.py lint` без ошибок, затем коммит в `main` базы и отправка: `python3 ~/gitlab-agent/app/gl.py push ~/gitlab-agent/work/kb --project acme/kb --branch main`. Сообщение:

   kb: <что изменилось>

   Source: <ссылка на задачу>
   Approved-by: @{approver}

5. Напиши в issue, какие документы созданы или изменены (ссылки на GitLab), и обнови «Состояние».

{context}
