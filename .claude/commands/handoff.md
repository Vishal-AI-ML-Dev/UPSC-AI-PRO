---
description: Write a handoff block into docs/PROJECT_STATE.md so a fresh session can resume
---

Update the **Last handoff** section of `docs/PROJECT_STATE.md` (replace the previous block) with:

```
### Last handoff — <YYYY-MM-DD>
- Active phase: NN <title> — spec <status>, plan <status>
- Done this session: <bullets, with files>
- Verified: <commands run + exact summary lines>   Not verified: <list>
- In progress / next step: <one concrete action>
- Open questions for the user: <list or "none">
- Uncommitted changes: <git status --porcelain summary>
```

Only state facts from this session; mark anything not checked as UNVERIFIED. Change nothing else in the file.
