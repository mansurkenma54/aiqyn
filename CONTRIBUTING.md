# Contributing / Үлес қосу

**[Қазақша](#қазақша) · [English](#english)**

---

## Қазақша

AIQYN жобасына үлес қосқыңыз келсе — рақмет. Төменде қысқа тәртіп.

### Мәселе туралы хабарлау

Bug тапсаңыз немесе идея болса — [issue ашыңыз](https://github.com/mansurkenma54/aiqyn/issues/new/choose).
Дайын шаблон бар: bug report және feature request.

### Код жіберу

1. Репоны fork жасаңыз, `main` бұтағынан жаңа branch ашыңыз:
   ```bash
   git checkout -b feat/қысқа-сипаттама
   ```
2. Өзгерісті енгізіңіз. Жоба стилін сақтаңыз — жаңа стиль ойлап таппаңыз.
3. Тексеру командаларын жүргізіңіз:
   ```bash
   python -m unittest discover -s tests -v
   ```
4. Коммит хабарламасын **не өзгергенін түсіндіретін** етіп жазыңыз.
   Жақсы: `Fix session cache leaking between accounts`.
   Жаман: `update`, `fix`, `asdf`.
5. Pull request ашыңыз және шаблонды толтырыңыз.

### Не қабылданбайды

- Тексеруден өтпейтін код (CI қызыл болса, PR қаралмайды).
- Себебі түсіндірілмеген үлкен рефакторинг.
- Құпия кілт, токен, пароль немесе жеке дерек бар өзгеріс.

### Тіл

Issue мен PR-ды **қазақша немесе ағылшынша** жазуға болады. Кодтағы
идентификаторлар ағылшынша, ал пайдаланушыға көрінетін мәтін қазақша.

---

## English

Thanks for considering a contribution to AIQYN.

### Reporting problems

Found a bug or have an idea? [Open an issue](https://github.com/mansurkenma54/aiqyn/issues/new/choose) —
templates for bug reports and feature requests are provided.

### Submitting code

1. Fork the repository and branch off `main`:
   ```bash
   git checkout -b feat/short-description
   ```
2. Make your change. Match the surrounding style rather than introducing a new one.
3. Run the checks:
   ```bash
   python -m unittest discover -s tests -v
   ```
4. Write a commit message that explains **what changed and why**.
5. Open a pull request and fill in the template.

### What will not be merged

- Code that fails CI.
- Large refactors with no stated reason.
- Anything containing secrets, tokens, passwords or personal data.

### Language

Issues and pull requests may be written in **Kazakh or English**. Identifiers in
code are English; user-facing strings are Kazakh.

---

**Байланыс / Contact:** Telegram [@abilmansurk](https://t.me/abilmansurk) · kenmamansur@gmail.com
