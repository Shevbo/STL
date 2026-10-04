
## 21:50 | main
Grid v8 sweep завершена на всех 6 инструментах (RI, Si, GZ, GD, SR, BR) за ~7h на i9, никто не прошёл Q1-Q5; отчёт в `grid_v8_report.md`, fable-проверка выявила 4 дефекта (take≠closed-pairs, nd10/nd240 anchoring, SR 3m error, rebalance misalignment).
## 08:18 | main
Grid v8 перегнана после исправлений (тейк парные уровни, nd-якорь next-day+M), fable подтвердила сходимость без утечек; вывод прежний: кандидатов нет (Q1-Q5 ни у кого), отчёт grid_v8_report.md, коммит 9243445f.
## 09:08 | main
Витрина собрана (542 карточки: 35 done/507 no_curve; на хостере, коммиты aa3c2477, 5add96cc), spec обновлена (symbols, unit, sort), ui-ux подтвердил; заявка на интерактивное пополнение версий в STL-монитор через Claude (объём без плеча, нормализация), ждёт impl.