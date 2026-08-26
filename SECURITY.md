# Security Policy / Қауіпсіздік саясаты

## Осалдық туралы хабарлау / Reporting a vulnerability

**Осалдықты жария issue арқылы хабарламаңыз.**
**Please do not report vulnerabilities through public issues.**

Оның орнына / Instead, contact:

- Email: **kenmamansur@gmail.com**
- Telegram: **[@abilmansurk](https://t.me/abilmansurk)**

Хабарламаңызда мыналарды жазыңыз / Please include:

1. Осалдықтың түрі және қай файл/эндпоинт әсер алады
   *(type of issue, and which file or endpoint is affected)*
2. Қайталау қадамдары *(steps to reproduce)*
3. Ықтимал әсері *(potential impact)*

Жауап беру уақыты: **72 сағат ішінде** / Response time: **within 72 hours**.

## Қолдау көрсетілетін нұсқа / Supported version

Тек `main` бұтағының соңғы күйі қолдауда.
Only the latest state of `main` is supported.

## Құпия деректер / Secrets

Бұл репода ешқандай нақты API кілті, токен немесе пароль сақталмайды.
Барлық құпия мән environment variable арқылы беріледі — үлгісі `.env.example`
файлында.

No real API keys, tokens or passwords are stored in this repository. All secrets
are supplied through environment variables; see `.env.example` for the shape.
