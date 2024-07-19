"""
Demangler for GNU v2 C++ symbols.

This implementation is based on the C implementation of the original GNU v2 demangler
from upstream GCC 13.2.0, before its removal.
"""

import copy
from contextlib import contextmanager
from io import StringIO, TextIOBase
from typing import Iterator, Optional

from gnu2_demangler.cxx import CxxName, CxxSymbol, CxxTerm, CxxType

DATA_CHARS: set[str] = set("0123456789Qt")
CPLUS_MARKERS: set[str] = set("$.\0")
SCOPE_STRING: str = "::"
QUALI_MAP: dict[str, CxxTerm.Kind] = {
    "C": CxxTerm.Kind.CONST,
    "V": CxxTerm.Kind.VOLATILE,
    "u": CxxTerm.Kind.RESTRICT,
}
QUALI_SPEC_MAP: dict[str, CxxTerm.Kind] = {
    **QUALI_MAP,
    "U": CxxTerm.Kind.UNSIGNED,
    "S": CxxTerm.Kind.SIGNED,
    "J": CxxTerm.Kind.COMPLEX,
}
FUND_KIND_MAP: dict[str, CxxTerm.Kind] = {
    "v": CxxTerm.Kind.VOID,
    "x": CxxTerm.Kind.LONG_LONG,
    "l": CxxTerm.Kind.LONG,
    "i": CxxTerm.Kind.INT,
    "s": CxxTerm.Kind.SHORT,
    "b": CxxTerm.Kind.BOOL,
    "c": CxxTerm.Kind.CHAR,
    "w": CxxTerm.Kind.LONG_DOUBLE,
    "d": CxxTerm.Kind.DOUBLE,
    "f": CxxTerm.Kind.FLOAT,
}
OPERATORS: dict[str, str] = {
    "nw": " new",
    "dl": " delete",
    "vn": " new[]",
    "vd": " delete[]",
    "as": "=",
    "ne": "!=",
    "eq": "==",
    "ge": ">=",
    "gt": ">",
    "le": "<=",
    "lt": "<",
    "pl": "+",
    "apl": "+=",
    "mi": "-",
    "ami": "-=",
    "ml": "*",
    "aml": "*=",
    "convert": "+",
    "negate": "-",
    "md": "%",
    "amd": "%=",
    "dv": "/",
    "adv": "/=",
    "aa": "&&",
    "oo": "||",
    "nt": "!",
    "pp": "++",
    "mm": "--",
    "or": "|",
    "aor": "|=",
    "er": "^",
    "aer": "^=",
    "ad": "&",
    "aad": "&=",
    "co": "~",
    "cl": "()",
    "ls": "<<",
    "als": "<<=",
    "rs": ">>",
    "ars": ">>=",
    "rf": "->",
    "vc": "[]",
    "cm": ",",
    "cn": "?:",
    "mx": ">?",
    "mn": "<?",
    "rm": "->*",
    "sz": "sizeof ",
}

DTOR_PREFIXES = [f"_{marker}_" for marker in CPLUS_MARKERS]
NEW_VT_PREFIX = "__vt_"
OLD_VT_PREFIXES = [f"_vt{marker}" for marker in CPLUS_MARKERS]
STATIC_DATA_PREFIXES = [f"_{data_char}" for data_char in DATA_CHARS]
THUNK_PREFIX = "__thunk_"
TYPE_INFO_NODE_PREFIX = "__ti"
TYPE_INFO_FUNC_PREFIX = "__tf"


def _read_exact(src: TextIOBase, size: int) -> str:
    """Read exactly `n` bytes from `src`, or raise a ValueError"""
    value = src.read(size)
    if len(value) != size:
        raise ValueError(f"Unable to read {size} bytes; got {value!r}")
    return value


@contextmanager
def _peeking(src: TextIOBase, offset: int = 0) -> Iterator[None]:
    """
    Store the current offset in `src`,
    and restore it at the end of the context.
    An optional offset can be added to start peeking further ahead from the current
    location.
    """
    ptr = src.tell()
    if offset:
        src.seek(ptr + offset)

    try:
        yield
    finally:
        src.seek(ptr)


def _peek(src: TextIOBase, n: int = 1, offset: int = 0) -> Optional[str]:
    """
    Read up to `n` bytes from `src` without advancing the offset.
    An optional offset can be added to peek starting further ahead of
    the current location.
    """
    with _peeking(src, offset=offset):
        return src.read(n)


def _bytes_left(src: TextIOBase, offset: int = 0) -> int:
    """
    Retrieve the number of bytes left in `src`.
    An optional offset can be added.
    """
    start: int = src.tell() + offset
    with _peeking(src):
        src.seek(0, 2)
        end: int = src.tell()

    return end - start


def _lookahead_for(src: TextIOBase, chars: list[str]) -> Optional[int]:
    """
    Look ahead in the buffer for a character in the given list.

    If one is found, return the number of chars that need to be read from the current
    offset in order to reach the character.

    If none of the given chars are found and the end of the buffer is found,
    returns None.
    """
    offset: int = 0
    with _peeking(src):
        char = src.read(1)
        while char:
            if char in chars:
                return offset
            offset += 1
            char = src.read(1)

    return None


def _lookahead_for_substring(src: TextIOBase, string: str, base_offset: int = 0) -> Optional[int]:
    """
    Look ahead in the buffer for a given substring. An optional "base_offset" can be
    provided to start from a later point in the buffer.

    If one is found, return the number of chars that need to be read in order
    to reach the start of the substring (starting from [current location + base offset]).

    If the substring is not found in the buffer, returns None.
    """

    offset: int = 0

    substr = _peek(src, n=len(string), offset=base_offset)
    while substr:
        if substr == string:
            return offset
        offset += 1
        substr = _peek(src, n=len(string), offset=base_offset + offset)

    return None


def _lookahead_while(src: TextIOBase, chars: list[str], base_offset: int = 0) -> int:
    """
    Look ahead in the buffer as long as the buffer contains characters in the given list.
    Return the number of subsequent characters found.
    An optional offset can be passed to start from a later point in the buffer.
    """
    num_chars: int = 0
    with _peeking(src, offset=base_offset):
        char = src.read(1)
        while char in chars:
            num_chars += 1
            char = src.read(1)

    return num_chars


@contextmanager
def _as_stringio(src: str) -> Iterator[StringIO]:
    """Wrap `src` in a `StringIO`, and assert it was fully consumed at the end of the context"""
    buf = StringIO(src)
    yield buf
    leftover = buf.read()
    if leftover:
        raise ValueError(f"Unable to parse full input, leftover chars: {leftover!r}")


def _read_number(src: TextIOBase) -> int:
    """
    Read subsequent numeric characters from the source and return them as a positive
    base-10 integer.
    """

    # Read each digit.
    number_str = ""
    while _peek(src).isdecimal():
        number_str += _read_exact(src, 1)

    if number_str == "":
        raise ValueError("Unable to parse expected number from string.")
    number = int(number_str)

    if number <= 0:
        raise ValueError("length must be positive")

    return number


def _read_number_with_underscores(src: TextIOBase) -> int:
    """
    Given a buffer which matches one of the following cases, read the number as a
    base-10 decimal and return it.
    - A count surrounded by single underscores (example: `_21_`)
    - A single digit (example: `0`)

    In the first case, the surrounding `_` chars will also be consumed from the buffer.
    Note that this function can return `0` as a valid value.
    """
    number: int = 0

    if _peek(src) == "_":
        # Consume the underscore prefix.
        _read_exact(src, 1)
        number = _read_number(src)

        if not _peek(src) == "_":
            raise ValueError(f"Expected trailing `_` character after number {number}!")

        # Consume the underscore suffix.
        _read_exact(src, 1)
    else:
        if not _peek(src).isdecimal():
            raise ValueError(f"Expected to read single decimal digit, got `{_peek(src)}`!")
        number = int(_read_exact(src, 1))

    return number


class GNU2Demangler:
    """
    Demangler object.
    """

    def __init__(self):
        self._reset()

    def parse(self, symbol: str):
        self._reset()

        with _as_stringio(symbol) as buf:
            return self._parse(buf)

    def _reset(self):
        """
        Reset the parser state.
        """
        self._btypes: list[CxxTerm] = []
        self._ktypes: list[CxxName] = []
        self._typevec: list[str] = []
        self._constructor: int = 0
        self._destructor: int = 0
        self._static_type: bool = False
        self._temp_start: int = 0
        self._type_quals: int = 0
        self._dll_imported: bool = False

    def _parse(self, src: TextIOBase):
        name = self._demangle_prefix(src)
        sym = self._demangle_signature(src, base_name=name)
        return sym

    def _remember_type(self, typ: str):
        """
        Remember a mangled type.
        """
        self._typevec.append(typ)

    def _remember_btype(self, typ: CxxTerm) -> int:
        """
        Remember a B type code and get an index for it.
        """
        self._btypes.append(typ)
        return len(self._btypes) - 1

    def _remember_ktype(self, typ: CxxName) -> int:
        """
        Remember a K type code and get an index for it.
        """
        self._ktypes.append(typ)
        return len(self._ktypes) - 1

    def _forget_types(self):
        """
        Lose all remembered mangled types.
        """
        self._typevec.clear()

    def _forget_B_and_K_types(self):
        """
        Lose all info related to B and K type codes.
        """
        self._btypes.clear()
        self._ktypes.clear()

    # def _gnu_special(self, src: TextIOBase) -> Optional[str]:
    #     """
    #     Process special GNU style mangling forms that don't fit the normal pattern.

    #     Examples:
    #         _$_3foo                 (destructor for class foo)
    #         _vt$foo                 (foo virtual table)
    #         _vt$foo$bar             (foo::bar virtual table)
    #         __vt_foo                (foo virtual table, new style with thunks)
    #         _3foo$varname           (static data member)
    #         _Q22rs2tu$vw            (static data member)
    #         __t6vector1Zii          (constructor with template)
    #         __thunk_4__$_7ostream   (virtual function thunk)
    #     """
    #     result: str = ""

    #     is_dtor: bool = _peek(src, 3) in DTOR_PREFIXES
    #     is_new_vt: bool = _peek(src, 5) == NEW_VT_PREFIX
    #     is_old_vt: bool = _peek(src, 4) in OLD_VT_PREFIXES
    #     is_static_member: bool = (
    #         _peek(src, 2) in STATIC_DATA_PREFIXES
    #         and _lookahead_for(src, CPLUS_MARKERS) is not None
    #     )
    #     is_thunk: bool = _peek(src, 8) == THUNK_PREFIX
    #     is_type_info_node = _peek(src, 4) == TYPE_INFO_NODE_PREFIX
    #     is_type_info_func = _peek(src, 4) == TYPE_INFO_FUNC_PREFIX

    #     if is_dtor:
    #         # This is a GNU destructor, get past "_<CPLUS_MARKER>_".
    #         _read_exact(src, 3)
    #         self._destructor += 1
    #     elif is_new_vt or is_old_vt:
    #         # Found a GNU style virtual table - get past the prefix and begin demangling.
    #         _read_exact(src, 5 if is_new_vt else 4)

    #         char = _peek(src)
    #         while char is not None:
    #             append: str = ""
    #             if char in ["Q", "K"]:
    #                 # Demangle a qualified name.
    #                 append = self._demangle_qualified(src, is_funcname=False)
    #             elif char == "t":
    #                 # Demangle a template.
    #                 append = self._demangle_template(src, is_type=True)
    #             else:
    #                 if char.isdecimal():
    #                     read_len: int = _read_number()
    #                     # If the size is too large, the upstream GNU demangler still
    #                     # treats the result as acceptable, and reads everything left
    #                     # in the buffer. This is why we don't use
    #                     append = src.read(read_len)
    #                 else:
    #                     # Read up to the next CPLUS_MARKER if one exists.
    #                     read_len: Optional[int] = _lookahead_for(src, CPLUS_MARKERS)
    #                     if read_len is None:
    #                         # Read to the end of the buffer.
    #                         read_len = -1
    #                     append += src.read(read_len)

    #             if append:
    #                 result += append

    #             # If the next character is a marker, there's another name after this.
    #             if _peek(src) in CPLUS_MARKERS:
    #                 _read_exact(src, 1)
    #                 result += SCOPE_STRING

    #             char = _peek(src)

    #     elif is_static_member:
    #         # Found a static data member, get past the underscore and start demangling.
    #         _read_exact(src, 1)

    #         append: str = ""
    #         char = _peek(src)
    #         if char in ["Q", "K"]:
    #             # Demangle a qualified name.
    #             append = self._demangle_qualified(src, is_funcname=False)
    #         elif char == "t":
    #             # Demangle a template.
    #             append = self._demangle_template(src, is_type=True)
    #         else:
    #             read_len: int = _read_number()
    #             if read_len > 10:
    #                 prefix = _peek(src, 11)
    #                 is_global = prefix.startswith("_GLOBAL_")
    #                 is_marker = prefix[8] in CPLUS_MARKERS and prefix[8] == prefix[10]
    #                 is_anonymous = prefix[9] == "N"

    #                 if is_global and is_marker and is_anonymous:
    #                     # A member of the anonymous namespace. There's information about
    #                     # the identifier/filename it was keyed to, but the upstream
    #                     # demangler just steps over it.
    #                     append = "{anonymous}"
    #                     _read_exact(src, read_len)

    #         result += append
    #         # We should be on a CPLUS_MARKER which signals the start of the variable name.
    #         if _peek(src) in CPLUS_MARKERS:
    #             # Consume the marker, then read the rest of the buffer as a variable name
    #             # and qualify it with the previous string.
    #             _read_exact(src, 1)
    #             result += SCOPE_STRING
    #             result += src.read()
    #         else:
    #             raise ValueError("In static data symbol, expected CPLUS_MARKER before variable name!")

    #     elif is_thunk:
    #         # Consume the `__thunk_` prefix.
    #         _read_exact(src, 8)

    #         # Read the delta value.
    #         delta: int = _read_number()
    #         if _peek(src) != "_":
    #             raise ValueError("In thunked virtual function, expected `_` after delta!")
    #         # Consume the `_`.
    #         _read_exact(src, 1)

    #         # Recursively demangle the remaining text.
    #         method = self._parse(src)
    #         if not method:
    #             raise ValueError("In thunked virtual function, couldn't recursively demangle `method`!")

    #         result += f"virtual function thunk (delta:{-delta}) for {method}"
    #         # Consume any remaining characters.
    #         src.read()

    #     elif is_type_info_node or is_type_info_func:
    #         # Consume the prefixes.
    #         _read_exact(src, 4)

    #         append: str = ""
    #         char = _peek(src)
    #         if char in ["Q", "K"]:
    #             # Demangle a qualified name.
    #             append = self._demangle_qualified(src, is_funcname=False)
    #         elif char == "t":
    #             # Demangle a template.
    #             append = self._demangle_template(src, is_type=True)
    #         else:
    #             append = self._do_type(src)

    #         result += append
    #         result += " type_info node" if is_type_info_node else " type_info function"
    #     else:
    #         raise ValueError("Not a GNU special symbol")

    def _demangle_prefix(self, src: TextIOBase) -> Optional[CxxName]:
        """
        Consume and demangle the prefix of the mangled name. There are several possible
        return values:
        - the root function name
        - the demangled operator name (if this is an operator overload)
        - `None` in certain special cases.

        In the general case, the buffer should point to the start of the mangled signature
        if this function does not throw an error.
        """

        if _peek(src, 6) in ["_imp__", "__imp_"]:
            # This is a symbol from a PE dynamic library.
            _read_exact(src, 6)
            self._dll_imported = True

        elif _peek(src, 8) == "_GLOBAL_":
            # This may be a global constructor/destructor (e.g. `_GLOBAL_$I$`)
            prefix: str = _peek(src, 11)
            has_xtor_markers: bool = prefix[8] in CPLUS_MARKERS and prefix[8] == prefix[10]
            is_ctor: bool = prefix[9] == "I"
            is_dtor: bool = prefix[9] == "D"

            if has_xtor_markers:
                if is_ctor:
                    self._constructor = 2
                elif is_dtor:
                    self._destructor = 2

                if is_ctor or is_dtor:
                    _read_exact(src, 11)
                    # TODO: Invoke the GNU special case demangler.
                    # return self._gnu_special(src)

        # Move forward to find a combination of two underscores (`__`).
        dunder_offset: int = _lookahead_for_substring(src, "__")
        if dunder_offset is not None:
            # We found a sequence of two or more `_` - ensure we start at the last
            # pair in the sequence.
            seq_length = _lookahead_while(src, ["_"], base_offset=dunder_offset)
            if seq_length > 2:
                dunder_offset += seq_length - 2
        # Read the character after the found pair.
        after_dunder: str = _peek(src, 1, offset=dunder_offset + 2)
        skipped_chars: bool = dunder_offset != 0

        if self._static_type:
            next: str = _peek(src)
            if not (next.isdecimal() or next == "t"):
                raise ValueError(
                    f"Expected digit or template specifier for static data member, got {next}!"
                )

        elif not skipped_chars and (
            after_dunder.isdecimal() or after_dunder in ["Q", "t", "K", "H"]
        ):
            # This is a GNU-style constructor.
            self._constructor += 1
            # Consume the two underscores.
            _read_exact(src, 2)

        elif not (skipped_chars or after_dunder.isdecimal() or after_dunder == "t"):
            # The mangled name starts with `__`. Skip over any leading `_` characters,
            #  then find the next `__` that separates the prefix from the signature.
            rightmost_guess: Optional[int] = _lookahead_for_substring(src, "__", dunder_offset + 2)
            if rightmost_guess is None:
                raise ValueError(
                    "Expected a `__` substring further right in symbol prefix. "
                    "This symbol probably isn't GNUv2 mangled."
                )

            # Since we looked ahead from `dunder_offset + 2`, we need to add that to get the
            # final guess offset from the current base.
            return self._iterate_demangle_function(src, dunder_offset + 2 + rightmost_guess)

        elif _bytes_left(src, offset=dunder_offset + 2) > 0:
            # Mangled name does not start with `__`, but does have one somewhere
            # in there with non-empty stuff after it. Possibly a global function name.
            # Iterate over `__`s until the correct one is found.
            return self._iterate_demangle_function(src, dunder_offset)

        if self._constructor == 2 or self._destructor == 2:
            # If we haven't hit any of the other cases and this is a global x-tor,
            # just add the rest of the buffer as the global constructor/destructor name.
            return CxxName(src.read())

        return None

    def _demangle_signature(
        self, src: TextIOBase, base_name: Optional[CxxName] = None
    ) -> CxxSymbol:
        """
        Given a buffer that points to the start of a mangled "signature", and optionally
        the base name of this symbol parsed from the symbol's prefix, demangle the signature
        and return the resulting symbol.
        """
        name_term: CxxTerm = CxxTerm(kind=CxxTerm.Kind.QUALIFIED, qualified_name=[])
        func_args: list[CxxType] = []
        func_ret: CxxType = None
        qualis: list[CxxTerm] = []

        func_done: bool = False
        expect_func: bool = False
        expect_return_type: bool = False
        old_ptr: Optional[int] = None

        if base_name:
            name_term.add_base_name(base_name)

        # Parse each type code and stack onto the CxxTerms.
        next: Optional[str] = _peek(src)
        while next:
            if next in ["Q", "K"]:
                # Qualified name.
                old_ptr = src.tell()
                name_term.qualify_with(self._demangle_qualified(src, is_funcname=True))
                if next == "Q":
                    # Remember the mangled type we just parsed.
                    length = src.tell() - old_ptr
                    self._remember_type(_peek(src, length, offset=-length))
                old_ptr = None
                expect_func = True

            elif next == "Q":
                # Static member function.
                if old_ptr is None:
                    old_ptr = src.tell()
                _read_exact(src, 1)
                self._static_type = True

            elif next in QUALI_MAP:
                # Qualified member function.
                if old_ptr is None:
                    old_ptr = src.tell()
                qualis.append(CxxTerm(kind=QUALI_MAP[next]))
                _read_exact(src, 1)

            elif next.isdecimal():
                # Class name.
                if old_ptr is None:
                    old_ptr = src.tell()

                name_term.add_qualifying_name(self._demangle_class(src))
                # Remember the mangled type we just parsed.
                length = src.tell() - old_ptr
                self._remember_type(_peek(src, length, offset=-length))

                if _peek(src) != "F":
                    expect_func = True
                old_ptr = None

            elif next == "B":
                # TODO: Call `do_type()`
                old_ptr = None
                expect_func = True

            elif next == "F":
                # Function
                old_ptr = None
                func_done = True
                _read_exact(src, 1)

                func_args = self._demangle_args(src)

            elif next == "t":
                # G++ template
                if old_ptr is None:
                    old_ptr = src.tell()
                name_term.add_qualifying_name(self._demangle_template(is_type=True, remember=True))

                # Remember the mangled type we just parsed.
                length = src.tell() - old_ptr
                self._remember_type(_peek(src, length, offset=-length))
                # TODO: Upstream consumes constructor/destructor flags here... do we need to?
                old_ptr = None
                expect_func = True

            elif next == "_":
                # Function return type.
                if not expect_return_type:
                    raise ValueError("Unexpected `_` character in function signature!")
                _read_exact(src, 1)
                func_ret = self._do_type(src)

            elif next == "H":
                # G++ template function.
                name_term.add_qualifying_name(
                    self._demangle_template(src, is_type=False, remember=False)
                )
                if self._constructor != 1:
                    expect_return_type = True
                if not _peek(src):
                    raise ValueError("Expected a return type for template function!")
                _read_exact(src, 1)

            else:
                # Assume we have stumbled onto the first outermost function
                # argument token, and start processing args.
                func_done = True
                func_args = self._demangle_args(src)

            if expect_func:
                # TODO: Not sure why this is here in upstream
                func_done = True
                func_args = self._demangle_args(src)
                expect_func = False

            next = _peek(src)

        if not func_done:
            # With GNU style demangling, `bar__3foo` is `foo::bar(void)`, and
            # `bar__3fooi` is `foo::bar(int)`. We get here when we find the first case,
            # and need to ensure that the `(void)` gets added to the args.
            func_args = self._demangle_args(src)

        if not func_args:
            # Upstream demangler inserts "void" into all zero-length function param lists.
            # To improve compatibility all-around, we'll do this too.
            func_args = [CxxTerm(kind=CxxTerm.Kind.VOID)]

        func_type = CxxType(
            terms=[
                CxxTerm(
                    kind=CxxTerm.Kind.FUNCTION, function_params=func_args, function_return=func_ret
                )
            ]
        )
        func_type.terms.extend(qualis)
        return CxxSymbol(name=name_term, type=func_type, is_static=self._static_type)

    def _demangle_args(self, src: TextIOBase) -> list[CxxType]:
        """
        Process the argument list of the signature after any class spec has been
        consumed, as well as the first "F" character if it exists. Examples:

        "__als__3fooRT0"            =>  process "RT0"
        "complexfunc5__FPFPc_PFl_i" =>  process "PFPc_PFl_i"
        """
        next: Optional[str] = _peek(src)
        args: list[CxxType] = []

        while next and next not in ["_", "e"]:
            if next in ["N", "T"]:
                raise AssertionError(f"Type code `{next}` in args list not supported yet")
            else:
                args.append(self._do_arg(src))

            next = _peek(src)

        if _peek(src) == "e":
            _read_exact(src, 1)
            raise AssertionError("Elipses not supported yet")

        return args

    def _demangle_template(self, src: TextIOBase, is_type: bool, remember: bool) -> CxxName:
        raise AssertionError("Not implemented yet")

    def _do_arg(self, src: TextIOBase) -> CxxType:
        """
        Demangle an argument type.
        """
        # TODO: support squangled repeated args
        if _peek(src) == "n":
            raise AssertionError("Squangling repeat not supported yet")

        # TODO: remember the mangled type with `_remember_type`
        typ = self._do_type(src)
        return typ

    def _do_type(self, src: TextIOBase) -> CxxType:
        """
        Demangle a base type.
        """
        done: bool = False
        typ: CxxType = CxxType()

        while not done:
            next = _peek(src)

            if next.lower() == "p":
                # Pointer type
                _read_exact(src, 1)
                typ.terms.append(CxxTerm(kind=CxxTerm.Kind.POINTER))

            elif next == "R":
                # L-value reference
                _read_exact(src, 1)
                typ.terms.append(CxxTerm(kind=CxxTerm.Kind.LVALUE_REFERENCE))

            elif next == "O":
                # R-value reference
                _read_exact(src, 1)
                typ.terms.append(CxxTerm(kind=CxxTerm.Kind.RVALUE_REFERENCE))

            elif next == "A":
                raise AssertionError("Arrays not supported yet")
            elif next == "T":
                raise AssertionError("Type back references not supported yet")
            elif next == "F":
                raise AssertionError("Nested functions not supported yet")
            elif next == "M":
                # Dunno what this is
                raise AssertionError("Dunno what 'M' is but it's not supported yet")
            elif next == "G":
                # Dunno what this is
                _read_exact(src, 1)
            elif next in QUALI_MAP:
                _read_exact(src, 1)
                typ.terms.append(CxxTerm(kind=QUALI_MAP[next]))
            else:
                done = True

        # The next character should give us the underlying type
        next = _peek(src)
        if next in ["Q", "K"]:
            typ.terms.append(self._demangle_qualified(src, is_funcname=False))
        elif next == "B":
            raise AssertionError("Back reference 'B' not supported yet")
        elif next in ["X", "Y"]:
            raise AssertionError(f"Template parameter '{next}' not supported yet")
        else:
            typ.terms.extend(self._demangle_fund_type(src).terms)

        return typ

    def _iterate_demangle_function(self, src: TextIOBase, guess_offset: int) -> CxxName:
        """
        Given:
        - a buffer pointing to the first character of what may be a function name
        - an offset from the current buffer pointer to a `__` separator string which is
          the rightmost guess of where the function name ends

        Find the correct `__` sequence where the function name ends and the signature
        starts (which is ambiguous with GNU mangling) and return a demangled function name.

        On success, the function name will be returned, and the buffer will point to the
        start of the signature.
        If a function + signature combination couldn't be demangled, an error will be thrown.
        """
        # Manually save the current state of the buffer so we can restore it if
        # function name demangling fails.
        ptr = src.tell()

        # Iterate over occurrences of `__`, allowing names and types to have a
        # `__` sequence in them. We must start with the first occurrence (not the last),
        # since `__` most often occur between independent mangled parts. Starting at
        # the last occurrence might get us a "successful" demangling of the signature.
        #
        # This is effectively a sliding window. Consider the following input:
        #
        # `foo__bar__i`
        #
        # - In the first iteration, we try to demangle `foo` as the function name
        #   and `bar__i` as a signature. `bar__i` isn't a valid signature, so we
        #   move our `__` guess forward.
        # - In the second iteration, we try to demangle `foo__bar` as the function name
        #   and `i` as a signature, which is valid. `foo__bar` is returned.
        while guess_offset is not None and _peek(src, offset=guess_offset + 2):
            # Attempt to demangle everything up to the current separator offset.
            maybe_name = self._demangle_function_name(src, separator_offset=guess_offset)

            if maybe_name is not None:
                # We got a valid function name. `src` currently points to what may be
                # the function signature - see if it's possible for us to demangle it.
                with _peeking(src):
                    try:
                        # NOTE: Since upstream is just working in strings and not objects, they just
                        # return the demangled signature if this is successful.
                        self._demangle_signature(src)
                        # We successfully demangled a signature after the function name.
                        return maybe_name
                    except Exception as e:
                        # Continue iterating, this wasn't a function signature.
                        print(f"Signature was invalid for name: {maybe_name}")
                        print(e)
                        pass

            # Reset the base pointer to cover the case where we succesfully demangled a
            # function name, but not a signature.
            src.seek(ptr)

            # Consume the current `__` sequence and find the next `__` sequence.
            guess_offset = _lookahead_for_substring(src, "__", base_offset=guess_offset + 2)

            # If we found another dunder, find the last pair of `_` in this sequence.
            if guess_offset is not None:
                guess_offset = _lookahead_while(src, ["_"], base_offset=guess_offset) - 2

        # We never found a function with a signature.
        raise ValueError(
            "Read rest of buffer, but failed to find valid `funcname__signature` combo! "
            "This symbol probably isn't GNUv2 mangled."
        )

    def _demangle_function_name(self, src: TextIOBase, separator_offset: int) -> Optional[CxxName]:
        """
        Given:
        - a buffer pointing to the first character of what may be a function name
        - an offset from the current buffer pointer which points to a `__` separator string

        Attempt to read and demangle a valid function name from the buffer.

        On success, a `CxxName` will be returned, and the buffer will point to
        after the separator.
        On failure, `None` will be returned, and the buffer will not be modified.
        """
        name: CxxName = None
        consume: int = 0

        with _peeking(src):
            # Read everything up to the separator as the prospective function name,
            # then consume the separator itself.
            func_name: str = _read_exact(src, separator_offset)
            _read_exact(src, 2)

            operator: str = self._demangle_func_name_as_operator(func_name)
            if operator:
                # This is an operator overload function.
                name = CxxName(operator)
                consume = separator_offset + 2
            elif not func_name == ".":
                # This is a valid function name.
                name = CxxName(func_name)
                consume = separator_offset + 2

        _read_exact(src, consume)
        return name

    def _demangle_qualified(self, src: TextIOBase, is_funcname: bool) -> CxxTerm:
        """
        Demangle a qualified name, such as "Q25Outer5Inner" which is the mangled
        form of `Outer::Inner`.

        If `is_funcname` is `True` and we are currently demangling a constructor or
        destructor function, an appropriate constructor/destructor name will be
        appended.
        """
        is_xtor_function: bool = is_funcname and (self._constructor == 1 or self._destructor == 1)
        name_term: CxxTerm = CxxTerm(kind=CxxTerm.Kind.QUALIFIED)
        num_quali_names: int = 0

        # Read the prefix and find the number of qualified names in this string.
        if _read_exact(src, 1) == "K":
            # A previous qualified name is being reused. Read the index and grab it.
            # We don't want to modify the original in the array, so copy it.
            name_term.add_base_name(copy.deepcopy(self._demangle_ktype(src)))

        else:
            next: str = _peek(src)
            if next == "_":
                # GNU mangled name with more than 9 classes. The count is preceded
                # by an underscore (to distinguish it from the `<= 9` case) and followed
                # by an underscore.
                num_quali_names = _read_number_with_underscores(src)
            elif next.isdecimal() and next != "0":
                # The count is a single digit.
                num_quali_names = int(_read_exact(src, 1))
                # If there is an underscore after the digit, skip it.
                # This might be for cfront names.
                if _peek(src) == "_":
                    _read_exact(src, 1)
            else:
                raise ValueError(f"Invalid character {next} for number of name qualifiers!")

        # Pick off the names from outer to inner.
        for _ in range(num_quali_names):
            remember_k: bool = True
            name: CxxName = None

            if _peek(src) == "_":
                _read_exact(src, 1)

            if _peek(src) == "t":
                # We do not remember the template type here, in order to match the
                # G++ mangling algorithm.
                name = self._demangle_template(src, is_type=True, remember=False)

            elif _peek(src) == "K":
                # Backreferenced qualified name.
                _read_exact(src, 1)
                remember_k = False
                name = copy.deepcopy(self._demangle_ktype(src))

            else:
                # TODO: Upstream demangler calls `do_type` here. Instead we'll just
                # assume it's a class name for now.
                name = self._demangle_class_name(src)

            if remember_k:
                self._remember_ktype(name)

            name_term.add_base_name(name)

        self._remember_btype(name_term)

        # If the result is a *tor, we need to append the name of the innermost class
        # as the function name.
        if is_xtor_function:
            extra = "~" if self._destructor else ""
            name_term.add_base_name(CxxName(f"{extra}{name.name}"))

        return name_term

    def _demangle_fund_type(self, src: TextIOBase) -> CxxType:
        """
        Given a buffer that represents a type argument, try to decode the type.
        Examples include:
        - "Ci" => "const int"
        - "Sl" => "signed long"
        - "CUs" => "const unsigned short"
        """
        # First, pick off any CV qualifiers and arithmetic type specifiers.
        terms: list[CxxTerm] = self._demangle_quali_spec_terms(src)

        # Next, find the underlying type.
        char: str = _peek(src)
        if char in FUND_KIND_MAP:
            _read_exact(src, 1)
            terms.append(CxxTerm(kind=FUND_KIND_MAP[char]))
        elif char == "G":
            # Unknown (?). Falls through to the "I" case in upstream.
            raise AssertionError("`G` type not implemented yet")
        elif char == "I":
            # C standard fixed-width integer type (?).
            raise AssertionError("`I` type not implemented yet")
        elif char.isdecimal():
            # Explicit type, such as "6mytype" or "7integer".
            name = self._demangle_class_name(src)
            self._remember_btype(name)
            terms.append(CxxTerm(kind=CxxTerm.Kind.QUALIFIED, qualified_name=[name]))
        elif char == "t":
            # Templated type.
            name = self._demangle_template(src, is_type=True, remember=True)
            terms.append(CxxTerm(kind=CxxTerm.Kind.QUALIFIED, qualified_name=[name]))
        else:
            raise ValueError(f"Unknown fundamental type specifier `{char}`")

        return CxxType(terms)

    def _demangle_class(self, src: TextIOBase) -> CxxName:
        """
        Demangle a class name and save it as a remembered k/btype.

        If constructor/destructor flags are set, they will be consumed here.
        """
        name: CxxName = self._demangle_class_name(src)
        if self._constructor == 1 or self._destructor == 1:
            if self._destructor == 1:
                name.name = f"~{name.name}"
                self._destructor -= 1
            else:
                self._constructor -= 1

        self._remember_ktype(name)
        self._remember_btype(name)
        return name

    def _demangle_quali_spec_terms(self, src: TextIOBase) -> list[CxxTerm]:
        """
        Attempt to parse a list of ANSI C++ type qualifiers or arithmetic type specifiers
        from a buffer which points into a GNUv2 C++ mangled symbol.
        The parser will run until an unknown (non-qualifier/specifier) character is hit.

        If none are found, an empty list will be returned.
        """
        num_qualis: int = _lookahead_while(src, QUALI_SPEC_MAP.keys())
        qualis = src.read(num_qualis)
        return [CxxTerm(kind=QUALI_SPEC_MAP[char]) for char in qualis]

    def _demangle_class_name(self, src: TextIOBase) -> CxxName:
        """
        Try to extract a class name from the buffer formatted as `[n][name]`, where:
        - `n` is the length of the name string, in bytes/chars
        - `name` is the name of the type
        """
        name_len = _read_number(src)
        name: str = _read_exact(src, name_len)
        if self._is_anonymous(name):
            name = "{anonymous}"

        return CxxName(name)

    def _demangle_ktype(self, src: TextIOBase) -> CxxName:
        """
        Given a buffer which contains a backreferencing K type index, get the corresponding
        backreferenced name. If the index is out of bounds, return an error.
        """
        k_idx: int = _read_number_with_underscores(src)
        if k_idx >= len(self._ktypes):
            raise ValueError(f"Invalid index {k_idx} for backreferenced `K`-type qualified name!")
        return self._ktypes[k_idx]

    def _demangle_func_name_as_operator(self, func_name: str) -> Optional[str]:
        """
        Determine if the given function name corresponds to an operator overload of some kind.
        If it does, demangle and return the proper function name for the operator overload.
        Otherwise, return `None`.
        """
        is_marked_op: bool = func_name.startswith("op") and func_name[2:3] in CPLUS_MARKERS
        is_type_conv: bool = func_name.startswith("type") and func_name[4:5] in CPLUS_MARKERS
        is_ansi_type_conv: bool = func_name.startswith("__op")
        is_unmarked_op: bool = (
            func_name.startswith("__") and func_name[2:4].isalpha() and func_name[2:4].islower()
        )

        if is_marked_op:
            # See if this is an assignment expression.
            is_assignment: bool = func_name[3:10] == "assign_"
            remaining: str = func_name[10:] if is_assignment else func_name[3:]

            # See if the rest of the string is an operator shorthand.
            for op_name, op_sym in OPERATORS.items():
                if remaining == op_name:
                    name = f"operator{op_sym}"
                    if is_assignment:
                        name += "="
                    return name

        elif is_type_conv or is_ansi_type_conv:
            # (ANSI) Type conversion operator
            start: int = 5 if is_type_conv else 4
            try:
                with _as_stringio(func_name[start:]) as src:
                    return f"operator {self._do_type(src)}"
            except:  # noqa
                return None

        elif is_unmarked_op:
            # Some other operator format.
            maybe_op: str = func_name[2:]
            for op_name, op_sym in OPERATORS.items():
                if maybe_op == op_name:
                    return f"operator{op_sym}"

        else:
            return None

    def _is_anonymous(self, prefix: str) -> bool:
        """
        Determine if the given string signifies that the symbol or class name is
        in the anonymous namespace.
        """
        return self._is_global_with_type(prefix) == "N"

    def _is_global_with_type(self, prefix: str) -> Optional[str]:
        """
        If this the given prefix signifies a global symbol of format `_GLOBAL_$[X]$`,
        where `[X]` is a type code, return the type code `[X]`.
        Otherwise, return `None`.
        """
        if (
            len(prefix) >= 10
            and prefix.startswith("_GLOBAL_")
            and prefix[8] in CPLUS_MARKERS
            and prefix[8] == prefix[10]
        ):
            return prefix[9]

        return None


def parse(mangled: str) -> str:
    return GNU2Demangler().parse(mangled)


# def demangle(mangled: str) -> str:
#     try:
#         return str(parse(mangled))
#     except ValueError:
#         return mangled
