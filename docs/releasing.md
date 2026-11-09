# Building and releasing

## Local release checks

```bash
python -m pip install -e '.[dev]'
python -m unittest discover -s tests -v
python -m build
python -m twine check dist/*
python scripts/verify_distribution.py dist
```

The build creates an installable wheel and source archive in dist/. The source archive
includes fictional examples, documentation, and tests. Runtime graphs, research
results, credentials, and the old project's history are excluded. The verification
script checks metadata-independent required contents, banned private paths, and
license inclusion. Install the wheel in a fresh environment and exercise the offline
example before publishing.

## GitHub release

1. Update the matching version in pyproject.toml and src/x_list_discovery/__init__.py.
2. Update CHANGELOG.md and pass CI.
3. Tag that commit (for example, v0.1.0) and push the tag.
4. The release workflow re-tests, builds, checks distributions and creates a **draft**
   GitHub release with wheel, source archive, and SHA-256 manifest attached.
5. Review the draft before making the release public. The workflow never publishes
   a release automatically.

The repository can also be installed directly from its Git URL once accessible.
This does not imply the package is already listed on PyPI.

## Optional PyPI publication

PyPI publication is deliberately a separate, manually invoked workflow:

1. Confirm the distribution name is available on PyPI and create/register the project
   or configure a pending trusted publisher for its first release.
2. Configure a [PyPI trusted publisher](https://docs.pypi.org/trusted-publishers/)
   for owner xcnecon, repository x-list-discovery, workflow publish-pypi.yml,
   and GitHub environment pypi.
3. Create the matching pypi environment in repository settings. Add required reviewers
   if you want an approval gate.
4. Manually dispatch Publish to PyPI from the exact version tag after reviewing the
   GitHub release. The workflow tests and builds before uploading with OIDC.

No PyPI password or token is embedded in source or workflows. Initial repository
setup and package building do not upload to PyPI. PyPI versions are immutable;
change the version for a later upload.
