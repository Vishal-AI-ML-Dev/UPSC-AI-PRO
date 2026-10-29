# Topper strategy files -> Mentor KB

Yahan distilled topper strategy `.md` files rakho (YAML frontmatter ke saath).
Frontmatter me kam se kam `topper_name` aur `rank` ho -- wahi citeable source label banta hai.

## Ingest kaise kare
Project root se (venv active):
```
uv run python scripts/ingest_topper_md.py
```
Ye mentor KB ko current embedding model pe rebuild karega + search smoke-test dikhayega.

## Aur toppers add karne ho
Bas nayi `.md` file isi folder me daal (same frontmatter format) aur script dobara chala.
Har baar rebuild=True hota hai, to KB clean + consistent rehta hai.

Abhi included: Anuj Agnihotri (AIR 1), Trilok Singh (AIR 20), Prachi Chauhan (AIR 260).
