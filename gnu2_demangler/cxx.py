"""
Module implementing C++ type abstractions.
"""

import copy
from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional, Union

from gnu2_demangler.strenum import StrEnum


@dataclass
class CxxValue:
    """
    Represents a literal value inside of a C++ template or array.

    The contents of `value` may vary wildly:
    - For integral types, `value` should contain an `int`.
    - For real types, `value` should contain a `float`.
    - For bool types, `value` should contain a `bool`.
    - For char types, `value` should contain a `str`.
    - Otherwise, `value` may contain a `CxxTerm` of type `QUALIFIED` or `SYMBOL_REF`.
    """

    value: Union[int, float, bool, str, "CxxTerm"]

    def __str__(self) -> str:
        """
        Print this value as a C literal. The resulting string will depend on the
        type.
        """
        if isinstance(self.value, bool):
            return "true" if self.value else "false"

        if isinstance(self.value, str):
            return f"'{self.value}'"

        return str(self.value)


@dataclass
class CxxTemplate:
    """
    Represents the contents of a C++ template. Templates can have one of three
    possible arguments:
    - type template parameters (CxxType)
    - non-type / value template parameters (CxxValue)
    - template template parameters (CxxName)
    """

    params: list[Union["CxxType", CxxValue, "CxxName"]]

    def __str__(self):
        """
        Return these template parameters as a string.
        """
        param_strs = [
            f"template{str(p)}" if isinstance(p, CxxTemplate) else str(p) for p in self.params
        ]
        return f"<{', '.join(param_strs)}>"


@dataclass
class CxxName:
    """
    Represents the name of some explicit C++ object or function.
    """

    name: str
    template: Optional[CxxTemplate] = None

    def add_template_param(self, param: Union["CxxType", CxxValue, "CxxName"]):
        """
        Convenience method to add a parameter to this name's template params.

        If this name currently isn't a template, its template object will be initialized.
        """
        if not self.template:
            self.template = CxxTemplate(params=[])

        self.template.params.append(param)

    def __str__(self):
        template_str = str(self.template) if self.template else ""
        return f"{self.name}{template_str}"


@dataclass
class CxxTerm:
    """
    Represents one part of a C++ name or type.

    One of these objects *generally* corresponds to a single token
    (C, V, U, i, x, ...) or other well-defined component of a mangled symbol.

    This is a variant type, with each `Kind` having different stored data.
    """

    class Kind(StrEnum):
        # ANSI CV qualifiers
        CONST = "const"
        VOLATILE = "volatile"
        RESTRICT = "__restrict"

        # Arithmetic type specifiers
        SIGNED = "signed"
        UNSIGNED = "unsigned"
        COMPLEX = "__complex"

        VOID = "void"
        # Arithmetic primitive types
        BOOL = "bool"
        CHAR = "char"
        WIDE_CHAR = "wchar_t"
        SHORT = "short"
        INT = "int"
        LONG = "long"
        LONG_LONG = "long long"
        FLOAT = "float"
        DOUBLE = "double"
        LONG_DOUBLE = "long double"

        # Memory type specifiers
        POINTER = "*"
        LVALUE_REFERENCE = "&"
        RVALUE_REFERENCE = "&&"
        ARRAY = "[]"

        # Other types/tokens
        FUNCTION = "function"
        QUALIFIED = "qualified"
        SYMBOL_REF = "symbol_ref"

        def is_const(self) -> bool:
            return self == CxxTerm.Kind.CONST

        def is_volatile(self) -> bool:
            return self == CxxTerm.Kind.VOLATILE

        def is_restrict(self) -> bool:
            return self == CxxTerm.Kind.RESTRICT

        def is_cv_qualifier(self) -> bool:
            return self in [CxxTerm.Kind.CONST, CxxTerm.Kind.VOLATILE, CxxTerm.Kind.RESTRICT]

        def is_sign(self) -> bool:
            return self in [CxxTerm.Kind.SIGNED, CxxTerm.Kind.UNSIGNED]

        def is_complex(self) -> bool:
            return self == CxxTerm.Kind.COMPLEX

        def is_void(self) -> bool:
            return self == CxxTerm.Kind.VOID

        def is_arithmetic_type_specifier(self) -> bool:
            return self in [CxxTerm.Kind.SIGNED, CxxTerm.Kind.UNSIGNED, CxxTerm.Kind.COMPLEX]

        def is_bool(self) -> bool:
            return self == CxxTerm.Kind.BOOL

        def is_character(self) -> bool:
            return self in [CxxTerm.Kind.CHAR, CxxTerm.Kind.WIDE_CHAR]

        def is_integer(self) -> bool:
            return self in [
                CxxTerm.Kind.CHAR,
                CxxTerm.Kind.SHORT,
                CxxTerm.Kind.INT,
                CxxTerm.Kind.LONG,
                CxxTerm.Kind.LONG_LONG,
            ]

        def is_real(self) -> bool:
            return self in [CxxTerm.Kind.FLOAT, CxxTerm.Kind.DOUBLE, CxxTerm.Kind.LONG_DOUBLE]

        def is_integral(self) -> bool:
            return self.is_integer() or self.is_character() or self.is_bool()

        def is_arithmetic_type(self) -> bool:
            return self.is_integral() or self.is_real()

        def can_have_sign(self) -> bool:
            return self.is_integer()

        def can_have_complex(self) -> bool:
            return self.is_real()

        def is_pointer(self) -> bool:
            return self == CxxTerm.Kind.POINTER

        def is_reference(self) -> bool:
            return self in [CxxTerm.Kind.LVALUE_REFERENCE, CxxTerm.Kind.RVALUE_REFERENCE]

        def is_ptr_or_ref(self) -> bool:
            return self.is_pointer() or self.is_reference()

        def is_array(self) -> bool:
            return self == CxxTerm.Kind.ARRAY

        def is_memory_type(self) -> bool:
            return self.is_pointer() or self.is_reference() or self.is_array()

        def is_function(self) -> bool:
            return self == CxxTerm.Kind.FUNCTION

        def is_qualified_name(self) -> bool:
            return self == CxxTerm.Kind.QUALIFIED

        def is_symbol_ref(self) -> bool:
            return self == CxxTerm.Kind.SYMBOL_REF

        def is_fund_type(self) -> bool:
            return (
                self.is_void()
                or self.is_arithmetic_type()
                or self.is_function()
                or self.is_qualified_name()
            )

    kind: Kind

    array_dim: Optional[int] = None
    function_params: Optional[List["CxxType"]] = None
    function_return: Optional["CxxType"] = None
    # List of names which qualify each other. The first element is the outermost
    # qualifier, while the last element is the base/innermost name.
    qualified_name: Optional[List[CxxName]] = None
    symbol_ref: Optional["CxxSymbol"] = None

    def __post_init__(self):
        """
        Validate the term's contents.
        """
        if self.array_dim:
            assert (
                self.kind.is_array()
            ), f"Non-array term {self.kind.name} cannot have array dimension."

        if self.function_params or self.function_return:
            assert (
                self.kind.is_function()
            ), f"Non-function term {self.kind.name} cannot have function params/return type."

        if self.qualified_name:
            assert (
                self.kind.is_qualified_name()
            ), f"Non-qualified-name term {self.kind.name} cannot have qualified name."

        if self.symbol_ref:
            assert (
                self.kind.is_symbol_ref()
            ), f"Non-symbol-ref term {self.kind.name} cannot have symbol ref."
        else:
            assert (
                not self.kind.is_symbol_ref()
            ), "Symbol ref term must have `symbol_ref` populated!"

    def add_base_name(self, name: CxxName):
        """
        If this is a `QUALIFIED` term, use the given name as the new base name by appending
        it to the list of qualified names.
        Otherwise, throw an error.
        """
        assert (
            self.kind.is_qualified_name()
        ), f"Cannot add base name to CxxTerm with type {self.kind.name}!"

        if self.qualified_name is None:
            self.qualified_name = []
        self.qualified_name.append(name)

    def add_qualifying_name(self, name: CxxName):
        """
        If this is a `QUALIFIED` term, add the given name as a new outermost qualifier by
        prepending it to the list of qualified names.
        Otherwise, throw an error.
        """
        assert (
            self.kind.is_qualified_name()
        ), f"Cannot add qualifying name to CxxTerm with type {self.kind.name}!"

        if self.qualified_name is None:
            self.qualified_name = []
        self.qualified_name.insert(0, name)

    def qualify_with(self, other: "CxxTerm"):
        """
        If this and the given CxxTerm are both QUALIFIED, prepend the given term's
        names as qualifiers of this name (effectively adding them as outer qualifiers).
        Otherwise, throw an error.
        """
        assert (
            self.kind.is_qualified_name()
        ), f"Cannot add qualifying name to CxxTerm with type {self.kind.name}!"

        assert (
            other.kind.is_qualified_name()
        ), f"Cannot qualify name with term of type {other.kind.name}!"

        # Iterate over a shallow copy in reversed order.
        for name in other.qualified_name[::-1]:
            self.add_qualifying_name(name)

    def base_on(self, other: "CxxTerm"):
        """
        If this and the given CxxTerm are both QUALIFIED, append the given term's
        names as the base of this name.
        Otherwise, throw an error.
        """
        assert (
            self.kind.is_qualified_name()
        ), f"Cannot add qualifying name to CxxTerm with type {self.kind.name}!"

        assert (
            other.kind.is_qualified_name()
        ), f"Cannot qualify name with term of type {other.kind.name}!"

        for name in other.qualified_name:
            self.add_base_name(name)

    def get_base_name(self) -> CxxName:
        """
        If this is a QUALIFIED CxxTerm, get the current base (innermost/rightmost) name
        of the term.
        Otherwise, throw an error.
        """
        assert (
            self.kind.is_qualified_name()
        ), f"Cannot get base name for CxxTerm with type {self.kind.name}!"
        assert bool(
            self.qualified_name
        ), "Cannot get base name for QUALIFIED CxxTerm with no names!"

        return self.qualified_name[-1]

    def __str__(self) -> str:
        """
        Print this term as a C token or declarator if possible.
        """

        if self.kind.is_array():
            # Format array dimension.
            assert self.array_dim is not None
            return f"[{self.array_dim}]"

        elif self.kind.is_function():
            # Format as a function declarator for the convenience of `CxxType.__str__()`.
            # This means we only print the parameters.
            param_str = (
                ", ".join([str(p) for p in self.function_params])
                if self.function_params
                else "void"
            )
            return f"({param_str})"

        elif self.kind.is_qualified_name():
            # Format qualified name.
            assert self.qualified_name
            return "::".join([str(n) for n in self.qualified_name])

        elif self.kind.is_symbol_ref():
            assert self.symbol_ref
            return f"&{self.symbol_ref.name}"

        else:
            return str(self.kind)

    @staticmethod
    def make_name(qualified_name: list[CxxName]) -> "CxxTerm":
        """
        Convenience method for creating a CxxTerm with a `QUALIFIED` kind.
        """
        return CxxTerm(kind=CxxTerm.Kind.QUALIFIED, qualified_name=qualified_name)


@dataclass(frozen=True)
class CxxDeclComponent:
    """
    Convenience dataclass for grouping terms into C++ "declarator" components.
    This is a minimal API for the declarator rules from the C++ grammar.
    """

    class Kind(Enum):
        # Sequence of specifiers ending in a primitive type.
        SPECIFIER_SEQ = 0
        # Named identifier.
        IDENTIFIER = 1
        # Pointer with optional CV qualifiers.
        POINTER = 2
        # Pointer to member, with optional CV qualifiers.
        POINTER_TO_MEMBER = 3
        # L-value reference.
        LVALUE_REF = 4
        # R-value reference.
        RVALUE_REF = 5
        # Array.
        ARRAY = 6
        # Function with optional trailing CV qualifiers.
        FUNCTION = 7

        def is_pointer(self) -> bool:
            return self in [CxxDeclComponent.Kind.POINTER, CxxDeclComponent.Kind.POINTER_TO_MEMBER]

        def is_ref(self) -> bool:
            return self in [CxxDeclComponent.Kind.LVALUE_REF, CxxDeclComponent.Kind.RVALUE_REF]

        def is_specifier_seq(self) -> bool:
            return self == CxxDeclComponent.Kind.SPECIFIER_SEQ

        def is_ptr_or_ref(self) -> bool:
            return self.is_pointer() or self.is_ref()

        def is_noptr_declarator(self) -> bool:
            return self in [CxxDeclComponent.Kind.ARRAY, CxxDeclComponent.Kind.FUNCTION]

    kind: Kind
    terms: list[CxxTerm]

    def apply(
        self, cur_decl_content: Optional[str] = None, prev_decl: Optional["CxxDeclComponent"] = None
    ) -> str:
        """
        Apply this declarator recursively to the result of a previous declaration.

        If `cur_decl_content` is `None`, the returned result will be the same as
        `str(self)`.
        `prev_decl`, if provided, should be the last `CxxDeclComponent` that was applied
        to the given `cur_decl_content`. This is relevant for several declarators that
        require parentheses based on the result of the previous declarator.
        """
        if cur_decl_content is None:
            cur_decl_content = ""

        # Get the base decl string without any special handling.
        base_decl = str(self)

        if self.kind.is_noptr_declarator():
            if prev_decl is not None and prev_decl.kind.is_ptr_or_ref():
                # This declarator requires the previous pointer/ref declarator
                # to be surrounded in parentheses.
                cur_decl_content = f"({cur_decl_content})"

        result: str = None
        if self.kind == CxxDeclComponent.Kind.IDENTIFIER:
            # An ID can only be the first decl in an `apply()` sequence, because
            # it forces a nested declarator to terminate.
            assert not (
                cur_decl_content or prev_decl
            ), "Identifiers can only be printed as the first decl. in a sequence."
            result = base_decl
        elif self.kind.is_ptr_or_ref() or self.kind.is_specifier_seq():
            # Print on the left side of the old decl.
            # Add whitespace between this and the prev. decl if we're printing
            # a base type.
            extra_space = " " if self.kind.is_specifier_seq() and prev_decl is not None else ""
            result = f"{base_decl}{extra_space}{cur_decl_content}"
        else:
            # Print on the right side of the old decl.
            result = f"{cur_decl_content}{base_decl}"

        return result

    @staticmethod
    def from_type(
        typ: "CxxType", identifier: Optional["CxxTerm"] = None
    ) -> list["CxxDeclComponent"]:
        """
        Given a type and optional identifier which the type applies to, partition
        the type's terms into a list of `CxxDeclComponent`s. The returned list of components can
        be iterated and applied to create a valid C++ declaration.
        """
        decl: list[CxxDeclComponent] = []

        if identifier:
            # Prepend the identifier to the list as a component.
            assert (
                identifier.kind.is_qualified_name()
            ), "Identifier for decl must be qualified name."
            decl.append(CxxDeclComponent(kind=CxxDeclComponent.Kind.IDENTIFIER, terms=[identifier]))

        i: int = 0
        kind: CxxDeclComponent.Kind = None
        terms_queue: list[CxxTerm] = copy.copy(typ.terms)
        decl_queue: list[CxxTerm] = []

        # Iterate through the list of terms, partitioning into decl components.
        while i < len(terms_queue):
            this_term = terms_queue[i]
            decl_queue.append(this_term)

            # If we're on a "terminating" term, map it to the decl we want to create.
            # TODO: Handle pointer to member whenever we actually support that
            # in the demangler...
            if this_term.kind.is_pointer():
                kind = CxxDeclComponent.Kind.POINTER
            elif this_term.kind.is_array():
                kind = CxxDeclComponent.Kind.ARRAY
            elif this_term.kind == CxxTerm.Kind.LVALUE_REFERENCE:
                kind = CxxDeclComponent.Kind.LVALUE_REF
            elif this_term.kind == CxxTerm.Kind.RVALUE_REFERENCE:
                kind = CxxDeclComponent.Kind.RVALUE_REF
            elif (
                this_term.kind.is_arithmetic_type()
                or this_term.kind.is_void()
                or this_term.kind.is_qualified_name()
            ):
                kind = CxxDeclComponent.Kind.SPECIFIER_SEQ
            elif this_term.kind.is_function():
                kind = CxxDeclComponent.Kind.FUNCTION
                # Immediately read any CV qualifiers after the function decl.
                if i + 1 < len(terms_queue) and terms_queue[i + 1].kind.is_cv_qualifier():
                    # Start reading CV qualifiers with the next term.
                    i += 1
                    while i < len(terms_queue) and terms_queue[i].kind.is_cv_qualifier():
                        decl_queue.append(terms_queue[i])
                        i += 1

            if kind is not None:
                # Create the decl from the queue of decl terms, then clear the queue.
                decl.append(CxxDeclComponent(kind=kind, terms=copy.copy(decl_queue)))
                decl_queue.clear()

                # If we just created a function component, process its return type
                # immediately and append its components.
                if this_term.kind.is_function() and this_term.function_return is not None:
                    decl.extend(CxxDeclComponent.from_type(typ=this_term.function_return))

                kind = None

            i += 1

        assert (
            len(decl_queue) == 0
        ), "Decl. queue not empty after loop ended! Is there a misplaced CV qualifier?"

        return decl

    def __str__(self) -> str:
        """
        Print this decl as a string without any context.
        """
        terms_to_print = self.terms

        if self.kind.is_pointer():
            # Print terms in reverse order so CV qualifiers appear on the right side
            # instead of the left, as defined by the C++ grammar.
            # Example: "[CONST, POINTER]" prints as `* const`.
            terms_to_print.reverse()

        return " ".join(str(t) for t in terms_to_print)


@dataclass
class CxxType:
    """
    Represents a complete C++ type.

    The final element in `terms` is (generally) the primitive type, such as CHAR or
    FUNCTION. Working from the end, each previous term modifies the type.
    (This order matches the order in the mangled type)

    Examples:
        `[CONST, POINTER, CHAR]` => "char * const"
                                 => "const pointer to char"

        `[POINTER, CONST, CHAR]` => "char const *"
                                 => "pointer to const char"

        `[POINTER, FUNCTION]`    => "[FUNC_RET_TYPE] (*)([FUNC_PARAMS])"
                                 => "pointer to function(FUNC_PARAMS) returning [FUNC_RET_TYPE]"
    """

    terms: List[CxxTerm] = field(default_factory=list)

    def _primitive_type_index(self) -> int:
        """
        Return the index of the primitive type in the `terms` array. Throws an error
        if no fundamental type is found.
        """
        for i in reversed(range(len(self.terms))):
            if self.terms[i].kind.is_fund_type():
                return i

        raise AssertionError("No primitive type found in CxxType!")

    def _has_primitive_type_index(self) -> Optional[int]:
        """
        Return the index of the primitive type in the `terms` array, if known.
        Otherwise, returns `None`.
        """
        for i in reversed(range(len(self.terms))):
            if self.terms[i].kind.is_fund_type():
                return i

        return None

    def primitive_type(self) -> CxxTerm:
        """
        Retrieve this type's underlying primitive/fundamental type. Raises an error
        if this type has no terms or if the type has no primitive type.
        """
        return self.terms[self._primitive_type_index()]

    def has_primitive_type(self) -> bool:
        """
        Determine if this type currently has an underlying primitive/fundamental type term.
        """
        return self._has_primitive_type_index() is not None

    def is_ptr_or_ref_type(self) -> bool:
        """
        Determine if this is a "pointer or reference to" type.
        """
        return any([t.kind.is_ptr_or_ref() for t in self.terms])

    def format(self, identifier: Optional[CxxTerm] = None) -> str:
        """
        Format this C++ type as a declaration with an optional identifier.
        """
        declarators: list[CxxDeclComponent] = CxxDeclComponent.from_type(self, identifier)

        # Recursively apply declarators to obtain our string.
        result = ""
        prev_decl = None
        for decl in declarators:
            result = decl.apply(cur_decl_content=result, prev_decl=prev_decl)
            prev_decl = decl

        return result

    def __str__(self) -> str:
        """
        Format this C++ type as a declaration.
        """
        return self.format(identifier=None)


@dataclass
class CxxSymbol:
    """
    Represents a C++ symbol and its type (if known).
    """

    name: CxxTerm
    type: Optional[CxxType] = None
    is_type_info_node: bool = False
    is_type_info_func: bool = False
    is_vtable: bool = False
    is_static: bool = False
    is_global_constructor: bool = False
    is_global_destructor: bool = False
    is_dll_imported: bool = False
    is_virtual_thunk: bool = False
    vthunk_delta: Optional[int] = None

    def __post_init__(self):
        """
        Verify certain properties of the new symbol.
        """
        # Verify known mutually exclusive flags.
        xor_flags = [
            self.is_type_info_node,
            self.is_type_info_func,
            self.is_vtable,
            self.is_global_constructor,
            self.is_global_destructor,
            self.is_virtual_thunk,
            self.is_dll_imported,
        ]
        if any(xor_flags):
            assert (
                len([b for b in xor_flags if b]) <= 1
            ), "Multiple mutually exclusive flags are set!"

        assert self.name.kind == CxxTerm.Kind.QUALIFIED, "Symbol name terms must be Kind.QUALIFIED!"

        if self.type is None:
            assert (
                self.is_static or self.is_vtable or self.is_global_xtor()
            ), "Symbols must have a type unless they are static data, vtables, or global x-tors!"

        if self.is_virtual_thunk:
            assert self.vthunk_delta is not None, "Virtual thunks must have `delta`!"
        else:
            assert not self.vthunk_delta, "Non-virtual-thunks should not have `delta`!"

    def is_global_xtor(self) -> bool:
        """
        Determine if this is a global constructor/destructor symbol.
        """
        return self.is_global_constructor or self.is_global_destructor

    def __str__(self) -> str:
        """
        Format this demangled symbol as a string with the goal of matching the output
        of upstream GNU's demangler.
        """
        prefix_str = ""
        if self.is_global_xtor():
            xtor = "constructors" if self.is_global_constructor else "destructors"
            prefix_str = f"global {xtor} keyed to "
        elif self.is_dll_imported:
            prefix_str = "import stub for "

        if self.type is None:
            # Format any suffix strings.
            suffix_str = ""
            if self.is_vtable:
                suffix_str = " virtual table"

            # To match GNU, don't output `static` for static data symbols.
            return f"{prefix_str}{self.name}{suffix_str}"
        else:
            # Format this as a declaration.
            static_str = "static " if self.is_static else ""
            return f"{prefix_str}{static_str}{self.type.format(identifier=self.name)}"
