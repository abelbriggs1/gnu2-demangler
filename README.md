# `gnu2-demangler`

This is a Python port of the GNU v2 C++ symbol demangler found in GNU `binutils`.

## Features

Supports most of the typical GNU v2 mangled type codes and formats, including:

- Freestanding functions
- Global symbols
- Vtables and virtual thunks
- Constructors
- Destructors
- Qualified names
- Static data
- Template types and functions
- Operator overloads

The demangler passes all of the original demangler's test suite, although the
original test suite only tested a small subset of possible code paths.

Additionally, instead of outputting a string directly, the demangler outputs a
combination of C/C++ tokens and flags in the form of a `CxxSymbol` Python object.
This `CxxSymbol` can then be printed as a string to format an equivalent
C/C++ declaration of the symbol.

### Limitations

This demangler attempts to ensure that the input demangles to a theoretically valid C/C++ symbol
or type declaration. There are many sanity checks and `assert`s in place.
As a result, this demangler is stricter than the `binutils` version, and may throw
an error in cases where `binutils` would output invalid symbols.

This demangler does *not* support other dialects that the upstream `binutils` demangler
supports, such as `ARM` and `HP` demangling.

Since this is a GNU v2 demangler (and the format was only used in the era of C++98),
the demangler and associated Python objects in this package do not have any support for
modern (C++11 and later) keywords and features.

### Future Additions

Some missing type codes for this demangler:

- Arbitrary expressions (example: `1 + 2`) in template value parameters
- Base type backreferences (`B`)
  - Type backreferences in general have not been fully implemented yet.
- Fixed-width integer symbols (type codes `G` and `I`)
- Elipses (`e`) in template/function params
- Squangled repeated args (type codes `n`, `N`)
- Pointers/references to class members

## Installation

TODO: Write install instructions once I figure out if this is going to be on PyPI or not.

## Usage

This package provides a CLI and a Python API to interact with it.

### Command Line Interface

```
usage: gnu2-demangler [-h] [--error-on-failure] symbol

Demangler for GNU v2 C++ symbols.

positional arguments:
  symbol                Symbol to demangle.

optional arguments:
  -h, --help            show this help message and exit
  --error-on-failure, -e
                        Throw an exception if demangling fails
```

Using it is as simple as running `gnu2-demangler` after package installation:

```bash
$ gnu2-demangler 'BgFilter__9ivTSolverP12ivInteractor'
ivTSolver::BgFilter(ivInteractor *)
$ gnu2-demangler 'GetBarInfo__15iv2_6_VScrollerP13ivPerspectiveOiT2'
iv2_6_VScroller::GetBarInfo(ivPerspective *, int &&, int &&)
$ gnu2-demangler '_GLOBAL_$I$__Q27CsColor4Data'
global constructors keyed to CsColor::Data::Data(void)
```

If demangling fails, the symbol will be echoed back with no change:

```bash
$ gnu2-demangler aa__aa
aa__aa
```

You can pass the `-e` argument to the CLI in order to throw an exception if
demangling fails.

Note that some shells may alter or remove characters in the string you pass to
the demangler, before it even reaches the Python code. For example, in `bash`,
`$` characters might get eaten as environment variable references.
To get around this, you may need to enclose your symbol in single or double quotes.

```bash
$ gnu2-demangler _GLOBAL_$I$__Q27CsColor4Data
_GLOBAL_
$ gnu2-demangler '_GLOBAL_$I$__Q27CsColor4Data'
global constructors keyed to CsColor::Data::Data(void)
```

### Python API

You can import the `gnu2_demangler` package to get access to the Python API.

```pycon
>>> import gnu2_demangler
>>>
>>> gnu2_demangler.demangle("saveOnQuitOverlay__Fv")
'saveOnQuitOverlay(void)'
>>>
>>> # Returned type is `gnu2_demangler.CxxSymbol`
>>> symbol = gnu2_demangler.parse("AddAlignment__9ivTSolverUiP12ivInteractorP7ivTGlue")
>>> print(symbol)
ivTSolver::AddAlignment(unsigned int, ivInteractor *, ivTGlue *)
>>> print(symbol.name)
ivTSolver::AddAlignment
>>> print(symbol.name.get_base_name())
AddAlignment
>>> for param in symbol.type.primitive_type().function_params:
...     print(param)
...
unsigned int
ivInteractor *
ivTGlue *
```

**NOTE**: The "C/C++ token" Python objects which are output from the demangler in
this package have several implementation quirks to work around some ambiguities
and quirks in mangled names. They may not be suitable for general use to manipulate
or format custom C/C++ declarations outside of the context of demangling.

## Contributing

Due to the extreme complexity of the demangling logic (no wonder upstream completely
overhauled their format in GNUv3!), directly contributing fixes to the demangler
may be difficult if you are unfamiliar with the original code.

However, additional unit tests and edge cases are always welcome. If you find a symbol that
demangles to a valid C++ symbol in the original demangler, but fails in this demangler,
please create an issue and let us know, and we'll try to handle it!

## Credits

- The GNU project for the original demangler in `binutils`. The demangler in this
  project is effectively a Python port of the original C code with some minor
  facelifts and external API niceties.
- The [`m2c`](https://github.com/matt-kempster/m2c) project's MechWarrior demangler API,
  which inspired some of the Python API for this project.

## License

Since this project is a port of GPLv2 code, it is licensed under LGPLv3 in order to
be compatible with the original code's license. See [`LICENSE`](LICENSE) for more info.
