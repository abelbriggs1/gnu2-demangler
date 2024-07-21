"""
Demangler for GNU v2 C++ symbols.

This implementation is effectively a Python port of the C implementation of
the original GNU v2 demangler from upstream GCC 13.2.0, before its removal.
"""

import copy
from io import TextIOBase
from typing import Optional, Union

from gnu2_demangler.cxx import (
    CxxName,
    CxxSymbol,
    CxxTemplate,
    CxxTerm,
    CxxType,
    CxxValue,
)
from gnu2_demangler.io_util import (
    as_stringio,
    bytes_left,
    lookahead_for_substring,
    lookahead_while,
    peek,
    peek_exact,
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
        # Reference to a function template, if demangled.
        # This is used for backreferencing function arguments from template parameters.
        self._func_templ: Optional[CxxTemplate] = None
        # Flags that are maintained for the top-level output symbol.
        self._vtable: bool = False
        self._static_type: bool = False
        self._dll_imported: bool = False
        # Instead of flags, constructor/destructor status are maintained as integers
        # to properly handle nesting.
        # Bit 1 represents whether we're demangling a global x-tor.
        # Bit 0 represents whether we're demangling a x-tor.
        self._ctor: int = 0
        self._dtor: int = 0
        # Flags which can recursively increase/decrease.
        self._forgetting_types: int = 0

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
        if self._forgetting_types > 0:
            return

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
                        CxxTerm.make_name(
                            [self._demangle_template(src, is_type=True, remember=True)]
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
                result = self._iterate_demangle_function(src, dunder_offset + 2 + rightmost_guess)
                return result if isinstance(result, CxxSymbol) else CxxTerm.make_name([result])

            elif bytes_left(src, offset=dunder_offset + 2) > 0:
                # Mangled name does not start with `__`, but does have one somewhere
                # in there with non-empty stuff after it. Possibly a global function name.
                # Iterate over `__`s until the correct one is found.
                result = self._iterate_demangle_function(src, dunder_offset)
                return result if isinstance(result, CxxSymbol) else CxxTerm.make_name([result])

        if self._ctor == 2 or self._dtor == 2:
            # If we haven't hit any of the other cases and this is a global x-tor,
            # just add the rest of the buffer as the global constructor/destructor name.
            return CxxTerm.make_name([CxxName(src.read())])

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
                    self._consume_xtor_if_needed(name)
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
                    templ_name = self._demangle_template(src, is_type=True, remember=True)
                    name_term.add_qualifying_name(templ_name)

                    # Remember the mangled type we just parsed.
                    self._remember_type(CxxType(terms=[CxxTerm.make_name([templ_name])]))

                    self._consume_xtor_if_needed(name_term)
                    expect_func = True

                elif next.kind == Token.Kind.UNDERSCORE:
                    # Function return type.
                    if not expect_return_type:
                        raise ValueError("Unexpected `_` character in function signature!")
                    read_exact(src, 1)
                    func_ret = self._do_type(src)

                elif next.kind == Token.Kind.TEMPLATE_GPP:
                    # G++ template function.
                    templ_args: CxxTemplate = self._demangle_template(
                        src, is_type=False, remember=False
                    )
                    name_term.get_base_name().template = templ_args

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

    def _demangle_nested_args(self, src: TextIOBase) -> list[CxxType]:
        """
        Demangle nested arguments. Similar to `demangle_args`, but used for nested
        function/method pointers instead of top-level declarations.
        """
        # The G++ name mangling algorithm does not remember types on nested argument
        # lists. Turn off remembering of types here.
        self._forgetting_types += 1
        args = self._demangle_args(src)
        self._forgetting_types -= 1
        return args

    def _demangle_template(
        self, src: TextIOBase, is_type: bool, remember: bool
    ) -> Union[CxxName, CxxTemplate]:
        """
        Demangle a template.

        If `is_type` is `True`, this function will attempt to read a templated type name,
        and will return a `CxxName`.
        Otherwise, a function template will be assumed, and this function will return
        a `CxxTemplate` which should be attached to the base name of the function by the
        caller.

        If `is_type` and `remember` are both `True`, the templated type will be remembered
        in the parser state.
        """
        # Consume the 't' or 'H' character.
        read_exact(src, 1)

        # Start with an empty CxxName to simplify variable usage for both
        # "type" and "function" cases.
        templ: CxxName = CxxName(name="")

        if is_type:
            # We need to read the name.
            next = Token.peek(src)
            if next.kind == Token.Kind.TEMPLATE_TEMPARM:
                read_exact(src, 2)
                assert False, "Template template parameters not yet supported"
            else:
                # This should be a normal name.
                templ = self._demangle_class_name(src)

        # Get the number of template params.
        num_params = _read_odd_count(src)

        # Demangle each template parameter.
        for _ in range(num_params):
            next = Token.peek(src)
            if next.kind == Token.Kind.TEMPLATE_TYPPARM:
                read_exact(src, 1)
                # Demangle the type.
                templ.add_template_param(self._do_type(src))

            elif next.kind == Token.Kind.TEMPLATE_TEMPARM:
                read_exact(src, 1)
                templ.add_template_param(self._demangle_template_template_parm(src))

            else:
                # Read the type of the literal.
                typ = self._do_type(src)
                # Demangle the literal using the given type, then add it to the
                # template args.
                val = self._demangle_template_value_parm(src, typ)
                templ.add_template_param(val)

        if is_type:
            if remember:
                # Remember this templated name.
                self._remember_btype(CxxTerm.make_name([templ]))
            # Return the whole name.
            return templ
        else:
            # Save the function template params in the parser state. If a `X` or `Y` code
            # appears in the function arguments, they'll reference this template.
            assert not self._func_templ, "Nested template function decls. should not be possible!"
            self._func_templ = templ.template
            # Return just the template params.
            return templ.template

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
                # Array
                read_exact(src, 1)

                size = None
                if not Token.peek(src).is_underscore():
                    # Demangle a literal integer value.
                    val = self._demangle_integral_value(src)
                    assert isinstance(
                        val.value, int
                    ), "Only integer literal for array size are currently supported!"
                    size = val.value

                if Token.peek(src).is_underscore():
                    # Consume any trailing underscore.
                    read_exact(src, 1)
                typ.terms.append(CxxTerm(kind=CxxTerm.Kind.ARRAY, array_dim=size))

            elif next.is_function():
                # Function type.
                read_exact(src, 1)

                # Append the function term. Worry about the function return type later,
                # we don't want to make a recursive call to `do_type` in this loop.
                typ.terms.append(
                    CxxTerm(
                        kind=CxxTerm.Kind.FUNCTION, function_params=self._demangle_nested_args(src)
                    )
                )

                # We should either be pointing to a `_` which precedes the
                # function's return type, or the end of the buffer.
                next_char = peek(src)
                next = Token.from_char(next_char)
                assert (
                    not next_char or next.is_underscore()
                ), "Expected pre-return-type `_` or end of buffer after nested function args!"

                # Consume the underscore if it exists.
                if next.is_underscore():
                    read_exact(src, 1)

                # The buffer should now point to the return type if it exists.
                # Escape this loop.
                done = True

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

        if typ.has_primitive_type():
            # We exited the loop after demangling a set of nested function arguments.
            prim_type = typ.primitive_type()
            assert (
                prim_type.kind.is_function()
            ), f"Expected primitive type to be function, not {prim_type.kind}!"

            # We're either pointing to a return type or to the end of the buffer.
            return_type = CxxType(terms=[CxxTerm(kind=CxxTerm.Kind.VOID)])
            if peek(src):
                # The next sequence should be the function's return type.
                # Recursively call `do_type()`.
                #
                # It's worth noting that, when demangling a nested function, upstream
                # basically flushes the existing terms and continues iterating in the
                # previous loop, effectively gathering the qualifiers of the return type.
                # Then, they fall through and combine the qualifiers with whatever they
                # parse below.
                #
                # This is only viable because upstream works solely with strings,
                # and therefore doesn't differentiate between a function return type
                # and any other plain type.
                return_type = self._do_type(src)

            prim_type.function_return = return_type
        else:
            # The next character/sequence should give us an underlying type
            next = Token.peek(src)
            if next.is_qualified():
                typ.terms.append(self._demangle_qualified(src, is_funcname=False))
            elif next.kind == Token.Kind.BACKREF_TYPE:
                read_exact(src, 1)
                typ.terms.extend(self._demangle_backref_type(src).terms)
            elif next.kind == Token.Kind.BACKREF:
                assert False, "Back reference 'B' not supported yet"
            elif next.is_template_backref_parm():
                # Function template parameter backref.
                assert self._func_templ, "Missing saved function template params for backref!"

                # Consume the 'X' or 'Y' type code.
                read_exact(src, 1)

                # Read the index into the template params.
                arg_idx: int = read_number_with_underscores(src)
                assert arg_idx >= 0 and arg_idx < len(
                    self._func_templ.params
                ), f"Index {arg_idx} for template param backref is out of bounds!"

                # Read another number. This is unused in upstream, so probably just filler?
                read_number_with_underscores(src)

                param = self._func_templ.params[arg_idx]
                # For some reason, backreffing literals for function args is supported in upstream,
                # even though it would never make sense. We don't support it here, it would
                # make things way too complicated.
                assert isinstance(
                    param, CxxType
                ), "Non-type parameter backreferenced in template function params!"
                # Append the template type's terms.
                typ.terms.extend(param.terms)
            else:
                typ.terms.extend(self._demangle_fund_type(src).terms)

        return typ

    def _demangle_template_value_parm(self, src: TextIOBase, typ: CxxType) -> CxxValue:
        """
        Demangle a "template value parameter" or a literal value (for example, an array index).

        If we're currently demangling a template parameter 'Y' code, `typ` is ignored.
        Otherwise, if `typ` is provided, it will be used to try and decode the literal value.
        """
        next = Token.peek(src)
        if next.kind == Token.Kind.TEMPLATE_ARG_BACKREF2:
            assert False, "'Y' template params not supported yet"

        prim = typ.primitive_type()

        # Determine the "type kind" term that upstream GNU uses when guessing
        # for what kind of literal to expect after parsing the type for a
        # template value parameter.
        if typ.is_ptr_or_ref_type():
            return self._demangle_symbol_ref_value(src)
        elif prim.kind.is_integer():
            return self._demangle_integral_value(src)
        elif prim.kind.is_real():
            return self._demangle_real_value(src)
        elif prim.kind.is_character():
            return self._demangle_char_value(src)
        elif prim.kind.is_bool():
            return self._demangle_bool_value(src)
        else:
            # No idea what this will be, just try to demangle an integral value.
            return self._demangle_integral_value(src)

    def _demangle_template_template_parm(self, src: TextIOBase) -> CxxName:
        assert False, "Template template params not supported yet"

    def _iterate_demangle_function(
        self, src: TextIOBase, guess_offset: int
    ) -> Union[CxxName, CxxSymbol]:
        """
        Given:
        - a buffer pointing to the first character of what may be a function name
        - an offset from the current buffer pointer to a `__` separator string which is
          the rightmost guess of where the function name ends

        Find the correct `__` sequence where the function name ends and the signature
        starts (which is ambiguous with GNU mangling). If it's possible to demangle the
        entire symbol with the detected function name, do so.

        On success:
        - If a full symbol was successfully demangled, it will be returned.
        - Otherwise, the best guess of the function name will be returned,
          and the buffer will point to the start of the signature.
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
        maybe_name: Optional[CxxName] = None
        while guess_offset is not None and peek(src, offset=guess_offset + 2):
            # Attempt to demangle everything up to the current separator offset.
            maybe_name = self._demangle_function_name(src, separator_offset=guess_offset)

            if maybe_name is not None:
                # We got a valid function name. `src` currently points to what may be
                # the function signature - see if it's possible for us to demangle it.

                try:
                    # Unfortunately, there isn't any way to determine that
                    # we've found the function name other than to actually try and
                    # demangle a signature.
                    return self._demangle_signature(src, maybe_name)
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

        if maybe_name is None:
            # We never found a function with a signature.
            raise ValueError(
                "Read rest of buffer, but failed to find valid `funcname__signature` combo! "
                "This symbol probably isn't GNUv2 mangled."
            )

        return maybe_name

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
        if is_funcname:
            self._consume_xtor_if_needed(name_term)

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
            term = CxxTerm.make_name([self._demangle_class_name(src)])
            self._remember_btype(term)
            terms.append(term)
        elif next.kind == Token.Kind.TEMPLATE:
            # Templated type.
            name = self._demangle_template(src, is_type=True, remember=True)
            terms.append(CxxTerm.make_name([name]))
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
        self._remember_btype(term)
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

    def _demangle_integral_value(self, src: TextIOBase) -> CxxValue:
        """
        Demangle an integral value.
        """

        next = Token.peek(src)
        if next.kind == Token.Kind.EXPRESSION:
            assert False, "Expressions in integral literals are not supported yet"
        elif next.is_qualified():
            assert False, "Qualified integral literals are not supported yet"
        else:
            negate = False
            multidigit_without_leading_underscore = False
            leave_following_underscore = False

            if next.is_underscore():
                if Token.peek(src, offset=1).kind == Token.Kind.NEGATE:
                    # `read_number_with_underscores()` does not handle the `m` prefix,
                    # so we need to do it here - we have to consume the `_`
                    # matching the prepended one.
                    multidigit_without_leading_underscore = True
                    negate = True
                    # Consume the `_` / `m` prefix.
                    read_exact(src, 2)
                else:
                    # Do not consume the following `_`.
                    # `read_number_with_underscores()` will handle that.
                    leave_following_underscore = True
            else:
                if next.kind == Token.Kind.NEGATE:
                    read_exact(src, 1)
                    negate = True

                # `read_number_with_underscores()` does not handle multi-digit numbers
                # that do not start with `_`, and this number could be a template parameter,
                # so we need to call `read_number`.
                multidigit_without_leading_underscore = True
                # Multidigit numbers never end on a `_`, so don't consume
                # one if it exists.
                leave_following_underscore = True

            # Read the number.
            value = (
                read_number(src, allow_zero=True)
                if multidigit_without_leading_underscore
                else read_number_with_underscores(src)
            )
            # Consume a trailing underscore if needed.
            if (
                (value > 9 or multidigit_without_leading_underscore)
                and not leave_following_underscore
                and Token.peek(src).is_underscore()
            ):
                read_exact(src, 1)
            if negate:
                value = -value
            return CxxValue(value=value)

    def _demangle_real_value(self, src: TextIOBase) -> CxxValue:
        """
        Demangle a real (floating-point) value.
        """
        next = Token.peek(src)
        if next.kind == Token.Kind.EXPRESSION:
            assert False, "Expressions in integral literals are not supported yet"

        fp_str: str = ""
        if next.kind == Token.Kind.NEGATE:
            # Consume the negate token and prepend a `-`.
            read_exact(src, 1)
            fp_str += "-"

        # Read the integer part of the number.
        fp_str += str(read_number(src, allow_zero=True))

        if peek(src) == ".":
            # Append the decimal point and decimal value.
            fp_str += read_exact(src, 1)
            fp_str += str(read_number(src, allow_zero=True))

        if peek(src) == "e":
            # Append the exponent specifier and exponent value.
            fp_str += read_exact(src, 1)
            fp_str += str(read_number(src, allow_zero=True))

        return CxxValue(value=float(fp_str))

    def _demangle_bool_value(self, src: TextIOBase) -> CxxValue:
        """
        Demangle a `bool` literal value.
        """
        value = read_number(src, allow_zero=True)
        assert value >= 0 and value <= 1, f"Value {value} out of bounds for `bool` literal!"

        return CxxValue(value=bool(value))

    def _demangle_char_value(self, src: TextIOBase) -> CxxValue:
        """
        Demangle a `char` value.
        """
        result: str = ""

        next = Token.peek(src)
        if next.kind == Token.Kind.NEGATE:
            # Prepend a `-`.
            # It's weird that a negative char literal is supported at all by upstream...
            read_exact(src, 1)
            result += "-"

        value = read_number(src, allow_zero=True)
        assert value >= 0 and value <= 255, f"Value {value} out of bounds for mangled char literal!"
        result += chr(value)

        return CxxValue(value=result)

    def _demangle_symbol_ref_value(self, src: TextIOBase) -> CxxValue:
        """
        Demangle a literal symbol reference value.
        """
        if Token.peek(src).kind == Token.Kind.QUALIFIED:
            return CxxValue(value=self._demangle_qualified(src, is_funcname=False))

        # Otherwise, this is a nested symbol reference.

        symbol_len = read_number(src, allow_zero=True)
        # Yes, upstream supports `symbol_len == 0` for some reason. Yes, it's silly.
        assert (
            symbol_len > 0
        ), f"Symbol with length {symbol_len} in symbol ref literal not supported yet."

        symbol_str = peek_exact(src, symbol_len)
        assert symbol_str, f"Symbol length {symbol_len} exceeds remaining length of buffer!"

        # The entity being demangled here is independent of our parser state, so
        # create a new parser and run that on the substring.
        sym = GNU2Demangler().parse(symbol_str)
        # Consume the length of the symbol.
        read_exact(src, symbol_len)

        return CxxValue(value=CxxTerm(kind=CxxTerm.Kind.SYMBOL_REF, symbol_ref=sym))

    def _consume_xtor_if_needed(self, name_term: CxxTerm):
        """
        If the parser is currently parsing a constructor or destructor and we need to
        append the x-tor name, do so, and consume the x-tor flags.
        """
        is_ctor = bool(self._ctor & 1)
        is_dtor = bool(self._dtor & 1)
        assert not (
            is_ctor and is_dtor
        ), "Cannot parse both constructor and destructor at the same time!"

        if is_ctor or is_dtor:
            if is_ctor:
                extra = ""
                self._ctor -= 1
            else:
                extra = "~"
                self._dtor -= 1

            name_term.add_base_name(CxxName(f"{extra}{name_term.get_base_name().name}"))


def parse(mangled: str) -> CxxSymbol:
    p = GNU2Demangler()
    result = p.parse(mangled)
    return result


def demangle(mangled: str) -> str:
    try:
        return str(parse(mangled))
    except ValueError:
        return mangled
