# Publishing URST to `micropython-lib`

This guide describes how to publish URST to the official MicroPython package
index so device users can install its pre-compiled bytecode with:

```bash
mpremote mip install urst
```

This is separate from the existing PyPI package:

```bash
pip install urst-mpy
```

PyPI serves desktop Python users. `mip` does not use PyPI; it uses the
`micropython-lib` index by default.

## Before starting: licensing

URST is currently licensed under the Sustainable Use License (SUL). That
license restricts commercial use, so it is not suitable for contribution to
the freely redistributable `micropython-lib` ecosystem.

Before contributing, the copyright holder must choose one of these options:

1. Relicense URST under an MIT-compatible licence.
2. Dual-license the code contributed to `micropython-lib` under MIT.

The latter permits unrestricted use of the copy held in
`micropython-lib`; it is not compatible with retaining commercial-use
restrictions for that copy.

## Package name

Use **`urst`**, not `micropython-urst`.

`micropython-foo` was an older PyPI/upip distribution naming convention. In
the current `micropython-lib` index, the package directory name is the name
used by `mip`. A package directory named `urst` therefore gives:

```bash
mpremote mip install urst
```

It also matches the existing import:

```python
from urst import Urst
```

## 1. Fork and branch `micropython-lib`

Fork [micropython/micropython-lib](https://github.com/micropython/micropython-lib)
to the `simonl65` GitHub account, then create a branch:

```bash
git clone git@github.com:simonl65/micropython-lib.git
cd micropython-lib
git switch -c add-urst
```

## 2. Add the package

URST is a MicroPython-specific transport library, so add it under the
`micropython/` category:

```text
micropython/
  urst/
    manifest.py
    urst/
      __init__.py
      codec_layer.py
      constants.py
      core_handler.py
      protocol_layer.py
    test_*.py
```

Copy only the device library source into `micropython/urst/urst/`. Do not copy
the PyPI configuration, `package.json`, or locally-built `.mpy` files.

Keep this repository as URST's primary development and PyPI project. The
`micropython-lib` copy is a maintained distribution mirror; releases from
this repository are not imported there automatically.

## 3. Add the manifest

Create `micropython/urst/manifest.py`:

```python
metadata(
    description="Universal Reliable Serial Transport protocol implementation.",
    version="1.0.2",
    license="MIT",
    author="Simon R. Lincoln",
)

package("urst")
```

Every `micropython-lib` package must provide a `manifest.py` with at least a
description and version. The manifest version is published by `mip`; bump it
manually for functional or API changes.

## 4. Add MicroPython-compatible tests

The existing pytest suite remains valuable in this repository. Add a compact
`unittest`-style `test_*.py` suite suitable for `micropython-lib` CI. At a
minimum cover:

- `import urst`;
- frame build/parse round trips;
- COBS and CRC fixtures;
- fragmentation arithmetic; and
- a fake-serial send/receive flow.

## 5. Format, commit, and push

`micropython-lib` uses Ruff and requires signed-off commits:

```bash
python3 tools/codeformat.py
pre-commit run --all-files
git add micropython/urst
git commit -s -m "urst: Add Universal Reliable Serial Transport package."
git push -u origin add-urst
```

## 6. Test the fork's `mip` index

In the fork's GitHub repository settings, create this Actions variable:

```text
MICROPY_PUBLISH_MIP_INDEX=true
```

After pushing the branch, the **Build All Packages** GitHub Action publishes a
branch-local `mip` index. Its log displays the exact install command. It will
look like:

```bash
mpremote mip install \
  --index https://simonl65.github.io/micropython-lib/mip/add-urst \
  urst
```

Test it against every supported MicroPython firmware and board. By default it
installs the index's compiled `.mpy` package. Users can explicitly request
source with `--no-mpy`.

## 7. Open the upstream pull request

Open a pull request from `simonl65:add-urst` to `micropython:master`. Include:

- the package name and import name (`urst`);
- the licence under which the contribution is offered;
- supported MicroPython versions and boards; and
- the fork-index installation command for reviewers.

After the pull request is merged and the official index is published, users
can install URST with:

```bash
mpremote mip install urst
```

`mip` installs compiled bytecode by default. The bytecode must be compatible
with the target firmware's supported `.mpy` format.

## Ongoing releases

There are two independent release tracks:

| Audience | Distribution | Release action |
| --- | --- | --- |
| Desktop Python | PyPI `urst-mpy` | Publish the existing PyPI release. |
| MicroPython devices | `mip install urst` | Submit a `micropython-lib` PR which updates the source and `manifest.py` version. |

