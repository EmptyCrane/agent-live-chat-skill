# Contributing

Thank you for improving Agent Live Chat Skill.

1. Open an issue for behavior changes or new host support.
2. Keep runtime code compatible with Python 3.9+ and the standard library only.
3. Keep host-specific instructions in `skill/live-chat/references/hosts.md`; do not make the core workflow depend on one vendor tool name.
4. Add or update tests for every behavior change.
5. Run the Python suite, Skill validation, static release audit, and relevant visual checks before submitting a pull request.

Use role names that describe responsibilities, such as Architect, Critic, or Operator. Do not use model names as fictional participant names in examples.

Never commit runtime state, logs, generated screenshots outside `docs/images`, release archives, credentials, or Python caches.
