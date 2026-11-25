# Contributing

Use [GitHub Issues](https://github.com/Sixtaupi/zkeyring/issues) for ordinary
bugs and feature requests. Follow the [security policy](SECURITY.md) for
private vulnerability reports.

## Set up and test

Use Python 3.12 or later in a virtual environment. Follow the
[development setup](doc/getting-started.rst) to create a west workspace with
the Zephyr and MCUboot revisions from `west-dev.yml`.

Run the signing tests from the repository root after setting up the
workspace and toolchain. The firmware tests use `qemu-system-arm`.

```sh
python -m pytest -q -ra tests/signing
```

Use Docker to exercise the integration backends. `--infisical` adds the
Infisical integration run.

```sh
scripts/test-signing
scripts/test-signing --infisical
```

For Python and shell changes, follow the [Ruff and ShellCheck commands used
by CI](.github/workflows/lint.yml). Check documentation changes with Sphinx.

```sh
python -m pip install -r doc/requirements.txt
make -C doc html SPHINXOPTS=-W
```

## Open a pull request

Sign off each commit with `git commit -s` for the
[Developer Certificate of Origin](https://developercertificate.org/). In
the pull request, describe the behavior change and list the checks you ran.
Include the Zephyr and MCUboot revisions when the change affects build
integration. Record changes users need to know about in the
[changelog](CHANGELOG.md).
