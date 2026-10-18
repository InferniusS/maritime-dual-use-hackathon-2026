# Source-only sharing snapshot

This collaboration branch starts a fresh Git history from the complete implementation at `a267421136c32effe103f8d96e2706d632c24126`, including the team’s main code at `227a61351f9d5a37a00c15ce457329709a01def9`. It does not include the original Git object database, commit ancestry, source drawings, drawing-derived examples, screenshots, or prebuilt binaries. The original feature branch and history are preserved separately.

The [manifest](../SHARING_MANIFEST.json) records source revisions, excluded paths, and the three omitted drawing-dependent test functions. All 79 application runtime source files were checked byte-for-byte against the source implementation and remain unchanged. Documentation was rewritten where necessary to remove drawing-specific identities, results, private folder links, obsolete binary instructions, and references to excluded artifacts. Synthetic JSON examples, application code, dependencies, installation instructions, and demo instructions remain included.

## Run and verify

Use [the root installation instructions](../README.md) and [the synthetic demo](edge/DEMO_4.md). The supplied demo requires no drawing or external service. The sanitized snapshot’s Python suite passed **971 tests without deselections** in the prepared Linux environment. This result was obtained after omitting only the three functions requiring excluded drawing artifacts. Those functions remain preserved in the original repository. A clone verification should rerun `python -m pytest -q` and `python -m pip check` in its own environment; optional STEP/browser dependencies are separate.

The [4.0 implementation record](edge/V4_0.md) retains earlier synthetic engineering, browser, benchmark, and compile evidence. Historical test counts and workstation paths in that record describe the development environment; they are not additional assets required to run this snapshot. Native AutoCAD execution, actual laptop/hardware performance, original official standards PDFs, and approved manufacturer catalogs still require independent verification.

## Data and publication boundaries

No CAD or image file is required or distributed. Plugin binaries must be built from source. Generated XLSX/DXF/CSV/report outputs remain local. The `.gitignore` adds source-only CAD, image, binary, generated-output, and secret-file guards; these guards are assistance, not a substitute for reviewing staged changes.

Source/configuration/documentation checks found no actual credentials; recognized URL-userinfo and token-literal matches were synthetic test inputs. No known drawing-derived metric or identifying-handle signature remained in the sanitized source scan. Pattern scanning cannot prove absence of all confidential information or establish data ownership. The standards configuration is a user-supplied transcription with explicit unresolved authority, not an independently verified official document or certification.

Repository visibility must be checked before publication, and publication must use the separately authorized branch without modifying `main`. A private repository limits visibility but does not confer authorization to add or process confidential engineering data.
