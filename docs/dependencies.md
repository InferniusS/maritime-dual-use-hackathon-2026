# Project software inventory

Verified on October 16, 2026. This records the Python environment prepared for the piping prototype. Versions, project source links, and license labels below come from the installed distributions' metadata. The Windows-only entry comes from the resolved wheel metadata because it is not installed on the Mac.

The six direct dependencies are pinned in `requirements.txt`. Their supporting packages are also pinned in `requirements-lock.txt`. The setup scripts install the complete lock file.

## Runtime and verification

The Mac environment uses CPython 3.14.6 on macOS 26.5.1 arm64. It reuses the existing official Python installation and installs project packages in this repository's isolated `.venv`. The environment was created at its final project path after the same packages passed checks in a separate temporary environment. The setup scripts require standard CPython 3.14.x. The earlier bundled Python 3.12.14 environment was preserved as a backup outside the repository.

The Mac setup passed dependency consistency checks, imports, and the synthetic geometry smoke check. That check writes and reopens a 3D DXF, verifies its units, layer, and endpoints, checks its structure, calculates a graph path, performs a spatial query, and serializes a 3D chart. It checks the development environment, not compliance with shipbuilding specifications.

Native Windows ARM64 package resolution passed for CPython 3.14. The NumPy and SciPy distributions have native Windows ARM64 wheels. ezdxf and fonttools resolve to portable Python wheels on ARM64. The remaining dependencies are portable Python packages. Windows-specific dependency conditions were also checked. Actual setup, application execution, and demonstration on the Qualcomm laptop are still pending.

For the Qualcomm laptop, use a current standard CPython 3.14.x ARM64 runtime from the [official Windows downloads](https://www.python.org/downloads/windows/). At the time of this check, [Python 3.14.8](https://www.python.org/downloads/release/python-3148/) is the current 3.14 maintenance release and provides an ARM64 installer, which Python labels experimental. The dependency checks used the standard `cp314` ABI. Free-threaded builds use a different ABI and were not validated. Run the setup script and project checks on the actual laptop before relying on it for the demonstration. The pinned project dependencies resolve as wheels, so they do not require local source compilation.

## Direct dependencies

These packages are installed and checked on the Mac.

| Package | Version | Purpose | License recorded in distribution | Project source |
| --- | --- | --- | --- | --- |
| NumPy | 2.5.4 | Numerical arrays and geometry calculations | `BSD-3-Clause AND 0BSD AND MIT AND Zlib AND CC0-1.0` | [numpy/numpy](https://github.com/numpy/numpy) |
| SciPy | 1.18.1 | Spatial queries and numerical tools | BSD 3-Clause text, plus bundled component notices | [scipy/scipy](https://github.com/scipy/scipy) |
| NetworkX | 3.7 | Pipe connectivity graphs and path algorithms | `BSD-3-Clause` | [networkx/networkx](https://github.com/networkx/networkx) |
| ezdxf | 1.4.5 | Read and write DXF geometry | MIT classifier and bundled LICENSE | [mozman/ezdxf](https://github.com/mozman/ezdxf) |
| Plotly | 7.1.0 | Interactive charts and 3D geometry views | `MIT` | [plotly/plotly.py](https://github.com/plotly/plotly.py) |
| pytest | 9.1.1 | Run automated project checks | `MIT` | [pytest-dev/pytest](https://github.com/pytest-dev/pytest) |

## Supporting dependencies

These packages were installed because the direct dependencies require them. Their installed versions are checked against the lock file. The smoke check imports the parent libraries and also uses packaging directly to verify the pins.

| Package | Version | Used by or purpose | License recorded in distribution | Project source |
| --- | --- | --- | --- | --- |
| fonttools | 4.67.0 | Font support required by ezdxf | MIT, with additional `LICENSE.external` notices | [fonttools/fonttools](https://github.com/fonttools/fonttools) |
| iniconfig | 2.3.1 | pytest configuration parsing | `MIT` | [pytest-dev/iniconfig](https://github.com/pytest-dev/iniconfig) |
| narwhals | 2.27.1 | Plotly's data interchange support | `MIT` | [narwhals-dev/narwhals](https://github.com/narwhals-dev/narwhals) |
| packaging | 26.3 | Version and package requirement handling for Plotly, pytest, and setup checks | `Apache-2.0 OR BSD-2-Clause` | [pypa/packaging](https://github.com/pypa/packaging) |
| pluggy | 1.7.0 | pytest's plugin support | MIT field and classifier | [pytest-dev/pluggy](https://github.com/pytest-dev/pluggy) |
| Pygments | 2.21.0 | Syntax highlighting used by pytest | `BSD-2-Clause` | [pygments/pygments](https://github.com/pygments/pygments) |
| pyparsing | 3.3.3 | Parsing support required by ezdxf | `MIT` | [pyparsing/pyparsing](https://github.com/pyparsing/pyparsing) |
| typing_extensions | 4.16.0 | Python type compatibility support required by ezdxf | `PSF-2.0` | [python/typing_extensions](https://github.com/python/typing_extensions) |

### Windows-only dependency

| Package | Pinned version | Purpose and verification | License recorded in resolved metadata | Project source |
| --- | --- | --- | --- | --- |
| Colorama | 0.4.6 | pytest terminal colors on Windows. Wheel resolution passed for ARM64 with Python 3.14. Not installed or executed on the Mac. | BSD License classifier. No SPDX expression was present in the resolved metadata. | [tartley/colorama](https://github.com/tartley/colorama) |

## Setup tool and license records

The virtual environment also contains pip 26.1.2, the package installer supplied when the environment was created. Its installed metadata lists MIT and [pypa/pip](https://github.com/pypa/pip) as its source. pip is an environment management tool and is not a pinned application dependency.

License labels are an inventory of what the distributions declare. Full copyright and license notices remain in each installed distribution's `.dist-info` directory. Several packages include additional notices for bundled code. NumPy records the combined expression above. SciPy includes notices for components such as OpenBLAS, LAPACK, the GCC runtime, and libquadmath, with inclusion depending on the binary build. Plotly includes third-party license records, and fonttools includes `LICENSE.external`. Retain the applicable bundled notices when distributing their binaries or generated assets.

This inventory does not claim that optional package extras or every bundled binary component were separately exercised. Update it from the installed metadata whenever the lock file changes, and repeat the setup check on the final demonstration laptop.

## AutoCAD plugin build

Added on October 17. The Mac has a local .NET 10.0.402 SDK for compiling the plugin. It does not install AutoCAD or change the Python environment. The target is `net10.0-windows` with x64 output for AutoCAD 2027 for Windows.

The build uses Autodesk's [AutoCAD.NET 26.0.0 reference package](https://www.nuget.org/packages/AutoCAD.NET/26.0.0). Its Core and Model packages provide the remaining AutoCAD API references. These are compile references only. Autodesk assemblies are not included with our plugin. The installed AutoCAD application supplies them when the plugin loads.

The project also references the Microsoft Windows Desktop framework. Windows targeting is enabled so the Mac can compile the DLL. Compiling it on the Mac does not test AutoCAD loading or drawing interactions. Those checks still need the Windows demo laptop.

The Python service and AutoCAD communicate over local HTTP. They can use different processor architectures because they do not load each other's code. The actual AutoCAD installation on the Snapdragon laptop still needs testing. No IQ9 runtime or neural model dependency has been added.

See the [plugin guide](../autocad-plugin/README.md) for building the plugin from source; no DLL is bundled in this snapshot.
