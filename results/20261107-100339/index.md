# Отчёт чтения страницы 20261107-100339

- web_reader 0.1.0 · browser: playwright-chromium
- 2026-11-07T10:03:39+00:00 → 2026-11-07T10:06:34+00:00 · pages: 20/40

## Непрочитанное и прочитанное не полностью

- https://careers.playstation.com/jobs: прочитано частично; число страниц списка (пагинация) (value=5); Страница заявляет 393 вакансий, собрано 353: возможна неполнота.
- https://careers.playstation.com/jobs/page/2: прочитано частично; Страница заявляет 393 вакансий, собрано 59: возможна неполнота.
- https://careers.playstation.com/jobs/page/3: прочитано частично; Страница заявляет 393 вакансий, собрано 59: возможна неполнота.
- https://careers.playstation.com/jobs/page/4: прочитано частично; Страница заявляет 393 вакансий, собрано 59: возможна неполнота.
- https://careers.playstation.com/jobs/page/5: прочитано частично; Страница заявляет 393 вакансий, собрано 59: возможна неполнота.
- https://careers.playstation.com/jobs/page/6: прочитано частично; Страница заявляет 393 вакансий, собрано 59: возможна неполнота.
- https://varjo.com/jobs: прочитано частично; Осталось окно, закрывающее страницу: This website uses cookies We use cookies to personalise content and ads, to provide social media fea
- https://varjo.com/hubfs/Company/Varjo-Culture-Handbook.pdf?hsLang=en: ошибка открытия; file too large (14856037 bytes)

## Сайты, где не хватило лимита (можно поднять для конкретного сайта)

- careers.playstation.com: число страниц списка (пагинация) (value=5)
  `"site_overrides": {"careers.playstation.com": {"run": {"max_pagination_pages": 5}}}`  (чтобы поднять, добавьте в config/settings.json раздел site_overrides)

## Все адреса

| Адрес | Статус | Тип страницы | Найденные вакансии | Открыто по ссылкам | file |
|---|---|---|---|---|---|
| https://careers.playstation.com/jobs | прочитано частично | список вакансий | 353 | 9/14 | 01-careers-playstation-com-jobs.md |
| https://room8group.com/join-the-team/#/?tcoop=full-time | прочитано полностью | список вакансий | 26 | 0/0 | 02-room8group-com-join-the-team.md |
| https://varjo.com/jobs | прочитано частично | обычная страница | 2 | 3/4 | 03-varjo-com-jobs.md |

## Не обработано

нет
