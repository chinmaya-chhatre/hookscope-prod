# Install the command line tool

Use Python 3.11 or later. Run `pip install .` from a source checkout, then `hookscope --help` or `hookscope --version`. To build a distributable wheel, install `build` and run `python -m build --wheel`. Install the resulting file with `pip install dist/*.whl`.

CI builds and installs the wheel in a separate virtual environment, checks the console command outside the checkout and verifies bundled dashboard templates. This does not publish the package to PyPI.
