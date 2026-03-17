# Contributing to ha-energiedaten

Thanks for your interest in contributing! This integration is in **beta** and we appreciate all help — whether it's reporting a bug, suggesting an improvement, or submitting code.

## Reporting Bugs

Found something broken? [Open a bug report](https://github.com/energiedaten-at/ha-energiedaten/issues/new?template=bug_report.yml) using our issue template. The more detail you provide, the faster we can fix it.

Before opening a new issue, please check [existing issues](https://github.com/energiedaten-at/ha-energiedaten/issues) to see if it's already been reported.

## Suggesting Features

Have an idea? [Open a feature request](https://github.com/energiedaten-at/ha-energiedaten/issues/new?template=feature_request.yml). Keep in mind that this integration is intentionally minimal in scope — it focuses on importing energy data into the HA Energy Dashboard. Features that belong on the energiedaten.at platform side should be directed to [phillip.fickl@energiedaten.at](mailto:phillip.fickl@energiedaten.at).

## Pull Requests

We welcome PRs! Here's how to get started:

### Development Setup

1. Fork and clone the repository
2. Set up a development environment:
   ```bash
   python -m venv venv
   source venv/bin/activate
   pip install -e ".[dev]"
   ```
3. Run tests:
   ```bash
   pytest
   ```

### Guidelines

- **Keep changes focused.** One fix or feature per PR.
- **Follow existing code style.** The codebase is small — read through it before making changes.
- **Add tests** for new functionality or bug fixes when possible.
- **Update the README** if your change affects user-facing behavior.
- **Write clear commit messages** that explain *why*, not just *what*.

### What We're Looking For

- Bug fixes
- Improved error messages and logging
- Translation improvements (German and English)
- Test coverage
- Documentation improvements

### What's Out of Scope

- Real-time power monitoring (not possible with EDA data)
- Energy community data support (planned for a future version)
- Cost tracking or tariff data
- Features that require changes to the energiedaten.at API

## Questions?

- **Integration questions:** [GitHub Issues](https://github.com/energiedaten-at/ha-energiedaten/issues)
- **energiedaten.at platform questions:** [phillip.fickl@energiedaten.at](mailto:phillip.fickl@energiedaten.at)
