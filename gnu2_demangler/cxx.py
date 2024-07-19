"""
Module implementing C++ type abstractions.
"""

import collections
from dataclasses import dataclass, field
from itertools import islice
from typing import Generator, Iterable, List, Optional, TypeVar, Union

from gnu2_demangler.strenum import StrEnum

T = TypeVar("T")


def _sliding_window(iterable: Iterable[T], n) -> Generator[Iterable[T], None, None]:
    """
    Collect data into overlapping fixed-length chunks or blocks.

    sliding_window('ABCDEFG', 4) => ABCD BCDE CDEF DEFG
    """
    iterator = iter(iterable)
    window = collections.deque(islice(iterator, n - 1), maxlen=n)
    for x in iterator:
        window.append(x)
        yield tuple(window)


@dataclass
class CxxTemplateValueParam:
    """
    Represents a value parameter inside of a C++ template.

    The contents and type of "value" will depend on the parameter's `typ`:
    - For integral types, `value` will contain an `int`.
    - For real types, `value` will contain a `float`.
    - For bool types, `value` will contain a `bool`.
    - For char types, `value` will contain a `str`.
    """

    typ: "CxxType"
    value: Union[int, float, bool, str]


@dataclass
class CxxTemplateTypeParam:
    """
    Represents a type parameter inside of a C++ template.
    """

    typ: "CxxType"

    def __str__(self):
        return str(self.typ)


@dataclass
class CxxTemplate:
    """
    Represents the contents of a C++ template. Templates can have one of three
    possible arguments:
    - type template parameters (CxxTemplateTypeParam)
    - non-type / value template parameters (CxxTemplateValueParam)
    - template template parameters (CxxTemplate)
    """

    params: list[Union[CxxTemplateTypeParam, CxxTemplateValueParam, "CxxTemplate"]]

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

        def is_array(self) -> bool:
            return self == CxxTerm.Kind.ARRAY

        def is_memory_type(self) -> bool:
            return self.is_pointer() or self.is_reference() or self.is_array()

        def is_function(self) -> bool:
            return self == CxxTerm.Kind.FUNCTION

        def is_qualified_name(self) -> bool:
            return self == CxxTerm.Kind.QUALIFIED

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

    def __post_init__(self):
        """
        Validate the term's contents.
        """
        if self.array_dim and not self.kind.is_array():
            raise AssertionError(f"Non-array term {self.kind.name} cannot have array dimension.")

        if (self.function_params or self.function_return) and not self.kind.is_function():
            raise AssertionError(
                f"Non-function term {self.kind.name} cannot have function params/return type."
            )

        if self.qualified_name and not self.kind.is_qualified_name():
            raise AssertionError(
                f"Non-qualified-name term {self.kind.name} cannot have qualified name."
            )

    def add_base_name(self, name: CxxName):
        """
        If this is a `QUALIFIED` term, use the given name as the new base name by appending
        it to the list of qualified names.
        Otherwise, throw an error.
        """
        if not self.kind.is_qualified_name():
            raise AssertionError(f"Cannot add base name to CxxTerm with type {self.kind.name}!")

        if self.qualified_name is None:
            self.qualified_name = []
        self.qualified_name.append(name)

    def add_qualifying_name(self, name: CxxName):
        """
        If this is a `QUALIFIED` term, add the given name as a new outermost qualifier by
        prepending it to the list of qualified names.
        Otherwise, throw an error.
        """
        if not self.kind.is_qualified_name():
            raise AssertionError(
                f"Cannot add qualifying name to CxxTerm with type {self.kind.name}!"
            )

        if self.qualified_name is None:
            self.qualified_name = []
        self.qualified_name.insert(0, name)

    def qualify_with(self, other: "CxxTerm"):
        """
        If this and the given CxxTerm are both QUALIFIED, prepend the given term's
        names as qualifiers of this name (effectively adding them as outer qualifiers).
        Otherwise, throw an error.
        """
        if not self.kind.is_qualified_name():
            raise AssertionError(
                f"Cannot add qualifying name to CxxTerm with type {self.kind.name}!"
            )
        if not other.kind.is_qualified_name():
            raise AssertionError(f"Cannot qualify name with term of type {other.kind.name}!")

        # Iterate over a shallow copy in reversed order.
        for name in other.qualified_name[::-1]:
            self.add_qualifying_name(name)

    def __str__(self) -> str:
        """
        Print this term as a C token.
        """
        if self.kind.is_array():
            # Format array dimension.
            assert self.array_dim is not None
            return f"[{self.array_dim}]"

        elif self.kind.is_function():
            # Format as function pointer.
            # Upstream demangler omits `void` return types for function symbols,
            # but since we're treating this as a function pointer, we want to include it.
            ret_str = f"{self.function_return} " if self.function_return else "void "
            param_str = (
                ", ".join([str(p) for p in self.function_params]) if self.function_params else ""
            )
            return f"{ret_str}(*) ({param_str})"

        elif self.kind.is_qualified_name():
            # Format qualified name.
            assert self.qualified_name
            return "::".join([str(n) for n in self.qualified_name])

        else:
            return str(self.kind)


@dataclass
class CxxType:
    """
    Represents a complete C++ type.

    The final element in `terms` is generally the primitive type, such as CHAR or
    FUNCTION. Working from the end, each previous term modifies the type.
    (This order matches the order in the mangled type)

    Example:
        `[CONST, POINTER, CHAR]` => "char * const"
        `[POINTER, CONST, CHAR]` => "char const *"
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

    def get_ordered_declaration(self) -> tuple[list[CxxTerm], CxxTerm, list[CxxTerm]]:
        """
        Get a sequence of tokens/terms which, when printed from left to right, can be
        feasibly converted to a C/C++ type.

        The returned tuple has the following members:
        - The first element is a list of all "modifier" CxxTerms that come before the
          primitive type.
        - The second element is the primitive type CxxTerm.
        - The third element is a list of all "modifier" CxxTerms that come after the
          primitive type.

        If this is a non-function type, the pre-modifier terms will be reordered to
        follow typical C/C++ standards for type declarations.

        Examples:
            `CPCUi` ==> [CONST, POINTER, CONST, UNSIGNED, INT]
                    ==> `int unsigned const * const` (when read normally)

                    ==> ([CONST, UNSIGNED], INT, [POINTER, CONST])
                    ==> `const unsigned int * const` (as a proper C declaration)

            `GetBgColor__C9ivPainter` ==> [FUNCTION, CONST]
                                      ==> ivPainter::GetBgColor(void) const

                                      ==> ([], FUNCTION, [CONST])
                                      ==> ivPainter::GetBgColor(void) const
        """
        prim_idx: int = self._primitive_type_index()
        prim = self.terms[prim_idx]

        if prim.kind.is_function():
            # We can just split the list as-is.
            return (self.terms[:prim_idx], prim, self.terms[prim_idx + 1 :])

        # Otherwise, this is some kind of (qualified) fundamental type whose declaration
        # is in reverse order of the tokens.
        # The primitive type should always be the last element in this case.
        assert prim_idx == len(self.terms) - 1

        # Iterate downward until we encounter a memory type, starting after the
        # primitive type.
        split: int = prim_idx
        while split > 0 and not self.terms[split - 1].kind.is_memory_type():
            split -= 1

        # `split` now points to the first left-side term, and points just past the
        # end of the right-side terms.
        left_terms = self.terms[split:prim_idx]
        right_terms = self.terms[:split]
        # Reverse this for accurate ordering.
        right_terms.reverse()

        return (left_terms, prim, right_terms)

    def pre_specifiers(self) -> list[CxxTerm]:
        """
        Get all specifier (CV qualis, arith. type specifiers, ...) `CxxTerm`s
        which come before the fundamental type, in the order that they would
        normally be printed.
        """
        pre, _, _ = self.get_ordered_declaration()
        return pre

    def primitive_type(self) -> CxxTerm:
        """
        Retrieve this type's underlying primitive/fundamental type. Raises an error
        if this type has no terms or if the type has no primitive type.
        """
        return self.terms[self._primitive_type_index()]

    def post_specifiers(self) -> list[CxxTerm]:
        """
        Get all specifier `CxxTerm`s which come after the fundamental type, in the
        order that they would normally be printed.
        """
        _, _, post = self.get_ordered_declaration()
        return post

    def __str__(self) -> str:
        pre, prim, post = self.get_ordered_declaration()
        inner_str = f"{' '.join(str(t) for t in pre)} " if pre else ""

        if prim.kind.is_function():
            # Nothing interesting, same as the inner string.
            outer_str = f" {' '.join(str(t) for t in post)}" if post else ""
        else:
            # The upstream demangler does not print whitespace between memory
            # specifiers for fundamental types, so we need to build this string
            # manually.
            outer_str = ""
            for cur, next in _sliding_window(post, 2):
                outer_str += str(cur)
                if not (cur.kind.is_memory_type() and next.kind.is_memory_type()):
                    outer_str += " "
            if post:
                # Grab the last entry that wasn't added due to the sliding window.
                outer_str += str(post[-1])
            if outer_str:
                outer_str = f" {outer_str}"

        return f"{inner_str}{prim}{outer_str}"


@dataclass
class CxxSymbol:
    """
    Represents a C++ symbol and its type.
    """

    name: CxxTerm
    type: CxxType
    is_static: bool = False

    def __str__(self) -> str:
        static_str = "static " if self.is_static else ""

        pre, prim, post = self.type.get_ordered_declaration()
        if prim.kind.is_function():
            # Format this symbol as a function declaration.
            pre_mods = f"{' '.join(str(m) for m in pre)} " if pre else ""
            post_mods = f" {' '.join(str(m) for m in post)}" if post else ""

            # If an explicit return type isn't found, the upstream demangler completely
            # omits it for function symbols.
            ret_str = f"{prim.function_return} " if prim.function_return else ""
            param_str = (
                ", ".join([str(p) for p in prim.function_params]) if prim.function_params else ""
            )

            return f"{static_str}{pre_mods}{ret_str}{self.name}({param_str}){post_mods}"

        return f"{static_str}{self.name} {self.type}"
