--------------------------------------------------

GLOBAL EXECUTION MODE: QUEUE

You must execute tasks strictly sequentially.

Core rules:
1. Never interrupt current task
2. Ignore any new instructions until current task is fully complete
3. Do not switch context mid-execution
4. Complete ALL steps before responding
5. After finishing, respond ONLY with:

TASK COMPLETE

6. If interrupted, resume from last step instead of restarting

7. Do not partially apply changes — ensure all steps are applied and verified

8. Always validate results (API, deploy, logs) before declaring completion

9. For multi-step tasks:
   - Execute step-by-step
   - Do not skip steps
   - Do not reorder steps

10. This file must be treated as a global system rule for all future tasks

--------------------------------------------------
