# rph examples

Configuration templates and task-reference samples for the experience client.

Rules:

- examples contain **no credentials** and no real person's data;
- platform references use public JSON shapes only (`job_id`, `attempt`,
  `resource`, `generation` where applicable);
- examples never embed a Controller launch command, a scientific software
  invocation, or an SSH target — those stay under the platform's controlled
  submit path.

No runnable examples are added by the scaffold task (T-05); the CLI only prints
help until the DSH launch task lands.
