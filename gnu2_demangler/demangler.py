"""
Demangler for GNU v2 C++ symbols.

This implementation is based on the C implementation of the original GNU v2 demangler
from upstream GCC 13.2.0, before its removal.
"""

import copy
from io import TextIOBase
from typing import Optional, Union

from gnu2_demangler.cxx import CxxName, CxxSymbol, CxxTerm, CxxType
from gnu2_demangler.io_util import (
    as_stringio,
    bytes_left,
    lookahead_for_substring,
    lookahead_while,
    peek,
    peek_number,
    peeking,
    read_exact,
    read_number,
    read_number_with_underscores,
)
from gnu2_demangler.token import Operator, Special, Token


def _read_odd_count(src: TextIOBase) -> int:
    """
    Read the given buffer expecting a count in a mangled name. If the buffer
    does not currently point to a count, raises an error.

    This function handles several special cases:
    - If the buffer points to a string of digits followed by an underscore,
      the returned count will contain the whole number, and the buffer will point
      to the first character after the underscore.
    - If the buffer points to a string of digits *not* followed by an underscore,
      only the first digit will be consumed and returned.

    These special cases are to handle the 'N' type code correctly.
    """
    # Start by naively reading a number.
    result = peek_number(src)
    if not result:
        return None
    number, next_offset = result

    if Token.peek(src, offset=next_offset).is_underscore():
        # Move the offset to after the underscore and accept the read number as-is.
        next_offset += 1
    else:
        # Toss the original result (which read all subsequent digits). Instead,
        # only read the first digit in the sequence.
        number = int(peek(src))
        next_offset = 1

    read_exact(src, next_offset)
    return number


class GNU2Demangler:
    """
    Demangler object.
    """

    def __init__(self):
        self._reset()

    def parse(self, symbol: str):
        self._reset()

        with as_stringio(symbol) as buf:
            return self._parse(buf)

    def _reset(self):
        """
        Reset the parser state.
        """
        # Memory for previous parsed types.
        self._btypes: list[CxxTerm] = []
        self._ktypes: list[CxxName] = []
        self._typevec: list[CxxType] = []
        # Flags that are maintained for the top-level output symbol.
        self._vtable: bool = False
        self._static_type: bool = False
        self._dll_imported: bool = False
        # Nesting level for certain fields.
        # These are used inside of the parser.
        self._ctor: int = 0
        self._dtor: int = 0

    def _parse(self, src: TextIOBase) -> CxxSymbol:
        """
        Parse the given buffer.
        """
        # This function may be called recursively in some special cases. Save and
        # clear certain variables for recursive calls.
        old_ctor = self._ctor
        old_dtor = self._dtor
        old_static = self._static_type
        self._dll_imported = False
        self._ctor = 0
        self._dtor = 0

        base_name: Optional[CxxTerm] = None
        base: int = src.tell()
        try:
            # Try to demangle special cases.
            result = self._gnu_special(src)
        except Exception as e:  # noqa
            # Either demangling a special case failed, or we don't have any
            # special cases.
            # Reset the buffer and parser state to remove bogus work.
            src.seek(base)
            self._reset()
            # Try demangling a normal case.
            result = self._demangle_prefix(src)

        if isinstance(result, CxxSymbol):
            # If we get a full symbol, return it (we're done)
            return result
        else:
            # If we get a qualified name, save it.
            # If we get nothing, just try to parse the signature as normal - our
            # buffer/parser state was modified so we should be good to continue.
            base_name = result

        # No matter how the prefixes were parsed, the buffer should always point to
        # the beginning of the signature here.
        # (or the end of the buffer, if no signature exists)
        sym = self._demangle_signature(src, base_name=base_name)

        # Restore the state of the parser before this call (in case this was called
        # recursively).
        self._ctor = old_ctor
        self._dtor = old_dtor
        self._static_type = old_static

        return sym

    def _remember_type(self, typ: CxxType):
        """
        Remember a demangled type.
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

    def _gnu_special(self, src: TextIOBase) -> Optional[Union[CxxTerm, CxxSymbol]]:
        """
        Process special GNU style mangling forms that don't fit the normal pattern.

        - If a special case is recognized and successfully demangled:
            - The return value may be:
                - `None` if the case was recognized but only modifies the parser/buffer's state.
                - A `QUALIFIED` `CxxTerm` holding the qualified base name of the symbol.
                - The fully demangled `CxxSymbol`.
            - The state of the parser object may be updated.
            - The state of the buffer pointer may be updated.
        - If no special cases are recognized, or if a special case is recognized
          but demangling fails:
            - The function will throw an error.

        Examples:
        _$_3foo                 (destructor for class foo)
        _vt$foo                 (foo virtual table)
        _vt$foo$bar             (foo::bar virtual table)
        __vt_foo                (foo virtual table, new style with thunks)
        _3foo$varname           (static data member)
        _Q22rs2tu$vw            (static data member)
        __t6vector1Zii          (constructor with template)
        __thunk_4__$_7ostream   (virtual function thunk)
        """

        prefix: Special = Special.peek(src)
        if prefix.kind == Special.Kind.DTOR:
            # GNU-style destructor. Get past the `_[MARKER]_`.
            read_exact(src, len(prefix.content))
            self._dtor += 1
            return None

        elif prefix.kind == Special.Kind.VTABLE:
            # GNU-style virtual table.
            # Get past the the prefix.
            read_exact(src, len(prefix.content))
            self._vtable = True

            # Read the vtable qualified name.
            next = Token.peek(src)
            name = CxxTerm(kind=CxxTerm.Kind.QUALIFIED, qualified_name=[])
            while next:
                if next.is_qualified():
                    name.base_on(self._demangle_qualified(src, is_funcname=False))

                elif next.kind == Token.Kind.TEMPLATE:
                    name.add_base_name(self._demangle_template(src, is_type=True, remember=True))

                elif next.is_digit():
                    length = read_number(src)
                    # GNU does not throw an error if the length is too big here,
                    # since we could be seeing a `.(digits)` static local symbol.
                    if length <= bytes_left(src):
                        name.add_base_name(CxxName(src.read(length)))

                else:
                    # Read up to the next marker token, or to the end of the buffer.
                    to_read = Token.scan_for_marker(src)
                    if to_read is None:
                        to_read = -1
                    name.add_base_name(CxxName(src.read(to_read)))

                # We should now be pointing either to a marker or to the end of the
                # buffer.
                next_char = peek(src)
                next = Token.from_char(next_char)
                assert (
                    not next_char or next.is_marker()
                ), f"Expected end of buffer or marker token after demangling part of qualified vtable name, got {next}!"

                if next.is_marker():
                    # Move past the marker to reach the next name (or the end of the buffer)
                    read_exact(src, 1)
                    next = Token.peek(src)

            return name

        elif prefix.kind == Special.Kind.STATIC_DATA:
            # Static data. Get past the underscore prefix.
            read_exact(src, len(prefix.content))
            self._static_type = True

            # Read the next name.
            next = Token.peek(src)
            name = CxxTerm(kind=CxxTerm.Kind.QUALIFIED, qualified_name=[])

            if next.is_qualified():
                name.base_on(self._demangle_qualified(src, is_funcname=False))
            elif next.kind == Token.Kind.TEMPLATE:
                name.add_base_name(self._demangle_template(src, is_type=True, remember=True))
            else:
                # Assume this is a normal class name (possibly with a `_GLOBAL_$N$` anonymous
                # prefix).
                name.add_base_name(self._demangle_class_name(src))

            # We should be pointing at the marker before the variable name.
            assert Token.peek(
                src
            ).is_marker(), "Expected marker before variable name in static data symbol!"
            # Consume the marker and append the rest of the buffer as the variable name.
            read_exact(src, 1)
            name.add_base_name(CxxName(src.read()))

            return name

        elif prefix.is_type_info():
            # Consume the prefix.
            read_exact(src, len(prefix.content))

            # Read the type.
            next = Token.peek(src)
            typ = None

            if next.is_qualified():
                typ = CxxType(terms=[self._demangle_qualified(src, is_funcname=False)])
            elif next.kind == Token.Kind.TEMPLATE:
                typ = CxxType(
                    terms=[
                        CxxTerm(
                            kind=CxxTerm.Kind.QUALIFIED,
                            qualified_name=[
                                self._demangle_template(src, is_type=True, remember=True)
                            ],
                        )
                    ]
                )
            else:
                # This could be any fundamental type.
                typ = self._do_type(src)

            # The buffer should be empty at this point.
            assert not peek(src), "Expected empty buffer after demangling type info symbol!"

            # Use a placeholder for the symbol name.
            name_str = (
                "type_info node" if prefix.kind == Special.Kind.TINFO_NODE else "type_info function"
            )
            name = CxxTerm(kind=CxxTerm.Kind.QUALIFIED, qualified_name=[CxxName(name=name_str)])
            return CxxSymbol(
                name=name,
                type=typ,
                is_type_info_func=prefix.kind == Special.Kind.TINFO_FUNC,
                is_type_info_node=prefix.kind == Special.Kind.TINFO_NODE,
            )

        elif prefix.kind == Special.Kind.VTHUNK:
            # Virtual function thunk.
            # Consume the prefix.
            read_exact(src, len(prefix.content))
            # Read the delta value.
            delta = read_number(src, allow_zero=True)

            # We should be on an underscore. Consume it.
            assert Token.peek(
                src
            ).is_underscore(), "Expected underscore after reading delta for virtual function thunk!"
            read_exact(src, 1)
            # Recursively run the demangler on the rest of the symbol.
            sym = self._parse(src)
            sym.is_virtual_thunk = True
            sym.vthunk_delta = -delta
            return sym

        raise AssertionError("Unknown GNU special prefix.")

    def _demangle_prefix(self, src: TextIOBase) -> Optional[Union[CxxTerm, CxxSymbol]]:
        """
        Consume and demangle the prefix of the mangled name. There are several possible
        return values:
        - the root function name
        - the demangled operator name (if this is an operator overload)
        - the fully demangled symbol
        - `None` in certain special cases.

        In the general case, the buffer should point to the start of the mangled signature
        if this function does not throw an error.
        """

        special = Special.peek_for_dllimport(src)
        if special is not None:
            # This is a symbol from a PE dynamic library.
            read_exact(src, len(special.content))
            self._dll_imported = True

        else:
            special = Special.peek_for_global(src)
            if special is not None:
                if special.kind == Special.Kind.GLOBAL_CTOR:
                    self._ctor = 2
                elif special.kind == Special.Kind.GLOBAL_DTOR:
                    self._dtor = 2

                # This may be a global constructor/destructor.
                if self._ctor == 2 or self._dtor == 2:
                    read_exact(src, len(special.content))
                    # Try to invoke the GNU special case demangler.
                    try:
                        return self._gnu_special(src)
                    except:  # noqa
                        # This could be a global xtor keyed to a unqualified, non-static
                        # global variable. Keep going.
                        pass

        # Move forward to find a combination of two underscores (`__`).
        dunder_offset: int = lookahead_for_substring(src, "__")
        if dunder_offset is not None:
            # We found a sequence of two or more `_` - ensure we start at the last
            # pair in the sequence.
            seq_length = lookahead_while(src, ["_"], base_offset=dunder_offset)
            if seq_length > 2:
                dunder_offset += seq_length - 2

            # Read the character after the found pair.
            after_dunder = Token.peek(src, offset=dunder_offset + 2)
            skipped_chars: bool = dunder_offset != 0

            if self._static_type:
                next = Token.peek(src)
                if not (next.is_digit() or next.kind == Token.Kind.TEMPLATE):
                    raise ValueError(
                        f"Expected digit or template specifier for static data member, got {next}!"
                    )

            elif not skipped_chars and (
                after_dunder.is_digit()
                or after_dunder.is_qualified()
                or after_dunder.is_template_start()
            ):
                # This is a GNU-style constructor.
                self._ctor += 1
                # Consume the two underscores.
                read_exact(src, 2)

            elif not (
                skipped_chars or after_dunder.is_digit() or after_dunder.kind == Token.Kind.TEMPLATE
            ):
                # The mangled name starts with `__`. Skip over any leading `_` characters,
                #  then find the next `__` that separates the prefix from the signature.
                rightmost_guess: Optional[int] = lookahead_for_substring(
                    src, "__", dunder_offset + 2
                )
                if rightmost_guess is None:
                    raise ValueError(
                        "Expected a `__` substring further right in symbol prefix. "
                        "This symbol probably isn't GNUv2 mangled."
                    )

                # Since we looked ahead from `dunder_offset + 2`, we need to add that to get the
                # final guess offset from the current base.
                return CxxTerm(
                    kind=CxxTerm.Kind.QUALIFIED,
                    qualified_name=[
                        self._iterate_demangle_function(src, dunder_offset + 2 + rightmost_guess)
                    ],
                )

            elif bytes_left(src, offset=dunder_offset + 2) > 0:
                # Mangled name does not start with `__`, but does have one somewhere
                # in there with non-empty stuff after it. Possibly a global function name.
                # Iterate over `__`s until the correct one is found.
                return CxxTerm(
                    kind=CxxTerm.Kind.QUALIFIED,
                    qualified_name=[self._iterate_demangle_function(src, dunder_offset)],
                )

        if self._ctor == 2 or self._dtor == 2:
            # If we haven't hit any of the other cases and this is a global x-tor,
            # just add the rest of the buffer as the global constructor/destructor name.
            return CxxTerm(kind=CxxTerm.Kind.QUALIFIED, qualified_name=[CxxName(src.read())])

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
        final_type: Optional[CxxType] = None

        if base_name:
            name_term.add_base_name(base_name)

        if peek(src):
            # Parse each type code and stack onto the CxxTerms.
            func_args: list[CxxType] = []
            func_ret: CxxType = None
            qualis: list[CxxTerm] = []
            func_done: bool = False
            expect_func: bool = False
            expect_return_type: bool = False

            next: Token = Token.peek(src)
            while next:
                if next.is_qualified():
                    # Qualified name.
                    name_term.qualify_with(self._demangle_qualified(src, is_funcname=True))
                    if next.kind == Token.Kind.QUALIFIED:
                        # Remember the mangled type we just parsed.
                        self._remember_type(CxxType(terms=[name_term]))
                    expect_func = True

                elif next == Token.Kind.STATIC:
                    # Static member function.
                    read_exact(src, 1)
                    self._static_type = True

                elif next.is_cv_quali():
                    # Qualified member function.
                    qualis.append(next.get_quali_term())
                    read_exact(src, 1)

                elif next.is_digit():
                    # Class name.
                    name = self._demangle_class(src)
                    # Remember the mangled type we just parsed.
                    self._remember_type(CxxType(terms=[copy.deepcopy(name)]))

                    # Consume constructor/destructor flags if needed.
                    if self._ctor & 1:
                        name.add_base_name(CxxTerm.make_name([name.get_base_name()]))
                        self._ctor -= 1
                    elif self._dtor & 1:
                        name.add_base_name(
                            CxxTerm.make_name([CxxName(name=f"~{name.get_base_name().name}")])
                        )
                        self._dtor -= 1
                    name_term.qualify_with(name)

                    if not Token.peek(src).is_function():
                        expect_func = True

                elif next.kind == Token.Kind.BACKREF:
                    # TODO: Call `do_type()`
                    expect_func = True

                elif next.is_function():
                    # Function
                    func_done = True
                    read_exact(src, 1)

                    func_args = self._demangle_args(src)

                elif next.kind == Token.Kind.TEMPLATE:
                    # G++ template
                    templ_name = self._demangle_template(is_type=True, remember=True)
                    name_term.add_qualifying_name(templ_name)

                    # Remember the mangled type we just parsed.
                    self._remember_type(
                        CxxType(
                            terms=[
                                CxxTerm(kind=CxxTerm.Kind.QUALIFIED, qualified_name=[templ_name])
                            ]
                        )
                    )
                    # TODO: Upstream consumes constructor/destructor flags here... do we need to?
                    expect_func = True

                elif next.kind == Token.Kind.UNDERSCORE:
                    # Function return type.
                    if not expect_return_type:
                        raise ValueError("Unexpected `_` character in function signature!")
                    read_exact(src, 1)
                    func_ret = self._do_type(src)

                elif next.kind == Token.Kind.TEMPLATE_GPP:
                    # G++ template function.
                    name_term.add_qualifying_name(
                        self._demangle_template(src, is_type=False, remember=False)
                    )
                    if not (self._ctor & 1):
                        expect_return_type = True
                    if not peek(src):
                        raise ValueError("Expected a return type for template function!")
                    read_exact(src, 1)

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

                next = Token.peek(src)

            if not func_done:
                # With GNU style demangling, `bar__3foo` is `foo::bar(void)`, and
                # `bar__3fooi` is `foo::bar(int)`. We get here when we find the first case,
                # and need to ensure that the `(void)` gets added to the args.
                func_args = self._demangle_args(src)

            if not func_args:
                # Upstream demangler inserts "void" into all zero-length function param lists.
                # To improve compatibility all-around, we'll do this too.
                func_args = [CxxTerm(kind=CxxTerm.Kind.VOID)]

            final_type = CxxType(
                terms=[
                    CxxTerm(
                        kind=CxxTerm.Kind.FUNCTION,
                        function_params=func_args,
                        function_return=func_ret,
                    )
                ]
            )
            final_type.terms.extend(qualis)

        else:
            # `demangle_signature` was called with an empty buffer, which means
            # - We should already have a base name
            # - We don't know this symbol's type
            assert (
                base_name is not None
            ), "Empty buffer for signature, but base symbol name is unknown!"

        return CxxSymbol(
            name=name_term,
            type=final_type,
            is_vtable=self._vtable,
            is_static=self._static_type,
            is_global_constructor=self._ctor == 2,
            is_global_destructor=self._dtor == 2,
            is_dll_imported=self._dll_imported,
        )

    def _demangle_args(self, src: TextIOBase) -> list[CxxType]:
        """
        Process the argument list of the signature after any class spec has been
        consumed, as well as the first "F" character if it exists. Examples:

        "__als__3fooRT0"            =>  process "RT0"
        "complexfunc5__FPFPc_PFl_i" =>  process "PFPc_PFl_i"
        """
        args: list[CxxType] = []

        next = Token.peek(src)
        while next and next.kind not in [Token.Kind.UNDERSCORE, Token.Kind.ELIPSES]:
            if next.kind in [Token.Kind.REPEAT, Token.Kind.BACKREF_TYPE]:
                read_exact(src, 1)

                # If we're repeating a backreferenced type, read the number of repeats.
                num_repeats: int = 1
                if next.kind == Token.Kind.REPEAT:
                    num_repeats = _read_odd_count(src)
                    assert num_repeats > 0, f"Number of repeats `{num_repeats}` is invalid!"
                # Add the backreferenced type (repeated if necessary) into the argument list.
                repeated_type = self._demangle_backref_type(src)
                args.extend([copy.deepcopy(repeated_type) for _ in range(num_repeats)])

            else:
                args.append(self._do_arg(src))

            next = Token.peek(src)

        if Token.peek(src).kind == Token.Kind.ELIPSES:
            read_exact(src, 1)
            assert False, "Elipses not supported yet"

        return args

    def _demangle_template(self, src: TextIOBase, is_type: bool, remember: bool) -> CxxName:
        assert False, "Templates not yet implemented"

    def _do_arg(self, src: TextIOBase) -> CxxType:
        """
        Demangle an argument type.
        """
        # TODO: support squangled repeated args
        assert (
            Token.peek(src).kind != Token.Kind.SQUANGLE_REPEAT
        ), "Squangling repeat not supported yet"

        typ = self._do_type(src)
        # Remember the demangled type.
        self._remember_type(typ)
        return typ

    def _do_type(self, src: TextIOBase) -> CxxType:
        """
        Demangle a base type.
        """
        done: bool = False
        typ: CxxType = CxxType()

        while not done:
            next = Token.peek(src)

            if next.is_ptr_or_ref():
                # Pointer or lvalue/rvalue reference
                read_exact(src, 1)
                typ.terms.append(next.get_ptr_ref_term())

            elif next.is_array():
                assert False, "Arrays not supported yet"

            elif next.is_function():
                assert False, "Nested functions not supported yet"

            elif next.kind == Token.Kind.UNK_M:
                # Dunno what this is
                assert False, "Dunno what 'M' is but it's not supported yet"

            elif next.kind == Token.Kind.UNK_G:
                # Dunno what this is
                read_exact(src, 1)

            elif next.is_cv_quali():
                read_exact(src, 1)
                typ.terms.append(next.get_quali_term())
            else:
                done = True

        # The next character should give us the underlying type
        next = Token.peek(src)
        if next.is_qualified():
            typ.terms.append(self._demangle_qualified(src, is_funcname=False))
        elif next.kind == Token.Kind.BACKREF_TYPE:
            read_exact(src, 1)
            typ.terms.extend(self._demangle_backref_type(src).terms)
        elif next.kind == Token.Kind.BACKREF:
            assert False, "Back reference 'B' not supported yet"
        elif next.is_template_value_parm():
            assert False, f"Template parameter '{next}' not supported yet"
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
        while guess_offset is not None and peek(src, offset=guess_offset + 2):
            # Attempt to demangle everything up to the current separator offset.
            maybe_name = self._demangle_function_name(src, separator_offset=guess_offset)

            if maybe_name is not None:
                # We got a valid function name. `src` currently points to what may be
                # the function signature - see if it's possible for us to demangle it.
                with peeking(src):
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
            guess_offset = lookahead_for_substring(src, "__", base_offset=guess_offset + 2)

            # If we found another dunder, find the last pair of `_` in this sequence.
            if guess_offset is not None:
                guess_offset = lookahead_while(src, ["_"], base_offset=guess_offset) - 2

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

        with peeking(src):
            # Read everything up to the separator as the prospective function name,
            # then consume the separator itself.
            func_name: str = read_exact(src, separator_offset)
            read_exact(src, 2)

            operator: str = self._demangle_func_name_as_operator(func_name)
            if operator:
                # This is an operator overload function.
                name = CxxName(operator)
                consume = separator_offset + 2
            elif not func_name == ".":
                # This is a valid function name.
                name = CxxName(func_name)
                consume = separator_offset + 2

        read_exact(src, consume)
        return name

    def _demangle_qualified(self, src: TextIOBase, is_funcname: bool) -> CxxTerm:
        """
        Demangle a qualified name, such as "Q25Outer5Inner" which is the mangled
        form of `Outer::Inner`.

        If `is_funcname` is `True` and we are currently demangling a constructor or
        destructor function, an appropriate constructor/destructor name will be
        appended.
        """
        is_xtor_function: bool = is_funcname and ((self._ctor & 1) or (self._dtor & 1))
        name_term: CxxTerm = CxxTerm(kind=CxxTerm.Kind.QUALIFIED)
        num_quali_names: int = 0

        # Read the prefix and find the number of qualified names in this string.
        if Token.read(src).kind == Token.Kind.QUALIFIED_NOREM:
            # A previous qualified name is being reused. Read the index and grab it.
            # We don't want to modify the original in the array, so copy it.
            name_term.add_base_name(copy.deepcopy(self._demangle_ktype(src)))

        else:
            next = Token.peek(src)
            if next.is_underscore():
                # GNU mangled name with more than 9 classes. The count is preceded
                # by an underscore (to distinguish it from the `<= 9` case) and followed
                # by an underscore.
                num_quali_names = read_number_with_underscores(src)
            elif next.is_digit() and next.content != "0":
                # The count is a single digit.
                num_quali_names = int(read_exact(src, 1))
                # If there is an underscore after the digit, skip it.
                # This might be for cfront names.
                if Token.peek(src).is_underscore():
                    read_exact(src, 1)
            else:
                raise ValueError(f"Invalid character {next} for number of name qualifiers!")

        # Pick off the names from outer to inner.
        for _ in range(num_quali_names):
            remember_k: bool = True
            name: CxxName = None

            if Token.peek(src).is_underscore():
                read_exact(src, 1)

            next = Token.peek(src)
            if next.kind == Token.Kind.TEMPLATE:
                # We do not remember the template type here, in order to match the
                # G++ mangling algorithm.
                name = self._demangle_template(src, is_type=True, remember=False)

            elif next.kind == Token.Kind.QUALIFIED_NOREM:
                # Backreferenced qualified name.
                read_exact(src, 1)
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
        # TODO: Upstream doesn't consume ctor/dtor flags here for some reason?
        if is_xtor_function:
            if self._ctor & 1:
                extra = ""
                self._ctor -= 1
            else:
                extra = "~"
                self._dtor -= 1
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
        next = Token.peek(src)
        if next.is_primitive():
            read_exact(src, 1)
            terms.append(next.get_primitive_term())
        elif next.kind == Token.Kind.UNK_G:
            # Unknown (?). Falls through to the "I" case in upstream.
            assert False, "`G` type not implemented yet"
        elif next.kind == Token.Kind.FIXED_WIDTH_INT:
            # C standard fixed-width integer type (?).
            assert False, "`I` type not implemented yet"
        elif next.is_digit():
            # Explicit type, such as "6mytype" or "7integer".
            name = self._demangle_class_name(src)
            self._remember_btype(name)
            terms.append(CxxTerm(kind=CxxTerm.Kind.QUALIFIED, qualified_name=[name]))
        elif next.kind == Token.Kind.TEMPLATE:
            # Templated type.
            name = self._demangle_template(src, is_type=True, remember=True)
            terms.append(CxxTerm(kind=CxxTerm.Kind.QUALIFIED, qualified_name=[name]))
        else:
            raise ValueError(f"Unknown fundamental type specifier `{next}`")

        return CxxType(terms)

    def _demangle_class(self, src: TextIOBase) -> CxxTerm:
        """
        Demangle a class name and save it as a remembered k/btype.
        """
        name: CxxName = self._demangle_class_name(src)
        term = CxxTerm.make_name([name])

        self._remember_ktype(name)
        self._remember_btype(name)
        return term

    def _demangle_quali_spec_terms(self, src: TextIOBase) -> list[CxxTerm]:
        """
        Attempt to parse a list of ANSI C++ type qualifiers or arithmetic type specifiers
        from a buffer which points into a GNUv2 C++ mangled symbol.
        The parser will run until an unknown (non-qualifier/specifier) character is hit.

        If none are found, an empty list will be returned.
        """
        qualis: list[CxxTerm] = []

        next = Token.peek(src)
        while next.is_cv_quali() or next.is_type_spec():
            qualis.append(next.get_quali_spec_term())
            read_exact(src, 1)
            next = Token.peek(src)

        return qualis

    def _demangle_class_name(self, src: TextIOBase) -> CxxName:
        """
        Try to extract a class name from the buffer formatted as `[n][name]`, where:
        - `n` is the length of the name string, in bytes/chars
        - `name` is the name of the type
        """
        name_len = read_number(src)
        name: str = read_exact(src, name_len)

        if Special.peek_for_global(src) == Special.Kind.GLOBAL_ANONYMOUS:
            name = "{anonymous}"

        return CxxName(name)

    def _demangle_backref_type(self, src: TextIOBase) -> CxxType:
        """
        Demangle a backreferencing "T" type and return the referenced type. If the
        index is out of bounds, return an error.
        """
        idx = read_number(src, allow_zero=True)

        if idx < 0 or idx >= len(self._typevec):
            raise ValueError(f"Invalid index {idx} for backreferenced `T` type code!")

        return self._typevec[idx]

    def _demangle_ktype(self, src: TextIOBase) -> CxxName:
        """
        Given a buffer which contains a backreferencing K type index, get the corresponding
        backreferenced name. If the index is out of bounds, return an error.
        """
        k_idx: int = read_number_with_underscores(src)
        if k_idx >= len(self._ktypes):
            raise ValueError(f"Invalid index {k_idx} for backreferenced `K`-type qualified name!")
        return self._ktypes[k_idx]

    def _demangle_func_name_as_operator(self, func_name: str) -> Optional[str]:
        """
        Determine if the given function name corresponds to an operator overload of some kind.
        If it does, demangle and return the proper function name for the operator overload.
        Otherwise, return `None`.
        """
        operator: Operator = Operator.from_func_name(func_name)

        if operator.has_known_name():
            return str(operator)
        elif operator.is_type_conv():
            start: int = 5 if operator.kind == Operator.Kind.TYPE_CONV else 4
            try:
                with as_stringio(func_name[start:]) as src:
                    return f"operator {self._do_type(src)}"
            except:  # noqa
                return None
        else:
            return None


def parse(mangled: str) -> CxxSymbol:
    p = GNU2Demangler()
    result = p.parse(mangled)
    return result


# def demangle(mangled: str) -> str:
#     try:
#         return str(parse(mangled))
#     except ValueError:
#         return mangled
