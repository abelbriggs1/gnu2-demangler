"""
Module implementing variant types for GNU demangler type codes and prefixes.

These variants are mostly used to improve the readability of the parser.
"""

from dataclasses import dataclass
from io import TextIOBase
from typing import ClassVar, Optional

from gnu2_demangler.cxx import CxxTerm
from gnu2_demangler.io_util import peek, peek_exact, read_exact
from gnu2_demangler.strenum import StrEnum


@dataclass(frozen=True)
class Token:
    """
    Variant type for individual GNUv2 type codes/tokens.
    """

    class Kind(StrEnum):
        # Abnormal types
        UNKNOWN = "unknown"
        DIGIT = "digit"
        MARKER = "marker"

        # CV qualifiers
        CONST = "C"
        VOLATILE = "V"
        RESTRICT = "u"
        # Type specifiers
        UNSIGNED = "U"
        SIGNED = "S"
        COMPLEX = "J"
        # Fundamental types
        VOID = "v"
        LONG_LONG = "x"
        LONG = "l"
        INT = "i"
        SHORT = "s"
        BOOL = "b"
        CHAR = "c"
        LONG_DOUBLE = "w"
        DOUBLE = "d"
        FLOAT = "f"
        # Memory types
        POINTER = "p"  # Also "P"
        LVALUE_REFERENCE = "R"
        RVALUE_REFERENCE = "O"
        ARRAY = "A"
        # Complex types
        FUNCTION = "F"
        QUALIFIED = "Q"
        QUALIFIED_NOREM = "K"
        # Back references
        BACKREF = "B"
        BACKREF_TYPE = "T"
        # Templates
        TEMPLATE = "t"
        TEMPLATE_GPP = "H"
        TEMPLATE_TYPPARM = "Z"  # Template type parameter
        TEMPLATE_TEMPARM = "z"  # Template template parameter
        TEMPLATE_ARG_BACKREF1 = "X"  # Back reference to a type from the template params.
        TEMPLATE_ARG_BACKREF2 = "Y"  # ??? - maybe backref to a literal from the template params?

        # Other types
        SQUANGLE_REPEAT = "n"
        ELIPSES = "e"
        EXPRESSION = "E"
        REPEAT = "N"
        UNDERSCORE = "_"
        STATIC = "S"
        FIXED_WIDTH_INT = "I"
        # Unknown types
        UNK_G = "G"
        UNK_M = "M"
        NEGATE = "m"
        UNK_W = "W"

    _QUALI_MAP: ClassVar[dict[Kind, CxxTerm.Kind]] = {
        Kind.CONST: CxxTerm.Kind.CONST,
        Kind.VOLATILE: CxxTerm.Kind.VOLATILE,
        Kind.RESTRICT: CxxTerm.Kind.RESTRICT,
    }
    _SPEC_MAP: ClassVar[dict[Kind, CxxTerm.Kind]] = {
        Kind.UNSIGNED: CxxTerm.Kind.UNSIGNED,
        Kind.SIGNED: CxxTerm.Kind.SIGNED,
        Kind.COMPLEX: CxxTerm.Kind.COMPLEX,
    }
    _PRIM_MAP: ClassVar[dict[Kind, CxxTerm.Kind]] = {
        Kind.VOID: CxxTerm.Kind.VOID,
        Kind.LONG_LONG: CxxTerm.Kind.LONG_LONG,
        Kind.LONG: CxxTerm.Kind.LONG,
        Kind.INT: CxxTerm.Kind.INT,
        Kind.SHORT: CxxTerm.Kind.SHORT,
        Kind.BOOL: CxxTerm.Kind.BOOL,
        Kind.CHAR: CxxTerm.Kind.CHAR,
        Kind.LONG_DOUBLE: CxxTerm.Kind.LONG_DOUBLE,
        Kind.DOUBLE: CxxTerm.Kind.DOUBLE,
        Kind.FLOAT: CxxTerm.Kind.FLOAT,
    }
    _PTR_REF_MAP: ClassVar[dict[Kind, CxxTerm.Kind]] = {
        Kind.POINTER: CxxTerm.Kind.POINTER,
        Kind.LVALUE_REFERENCE: CxxTerm.Kind.LVALUE_REFERENCE,
        Kind.RVALUE_REFERENCE: CxxTerm.Kind.RVALUE_REFERENCE,
    }

    kind: Kind
    content: str

    def is_cv_quali(self) -> bool:
        """
        Determine if this is a CV qualifier code.
        """
        return self.kind in self._QUALI_MAP

    def is_type_spec(self) -> bool:
        """
        Determine if this is a type specifier.
        """
        return self.kind in self._SPEC_MAP

    def is_primitive(self) -> bool:
        """
        Determine if this is a fundamental primitive type.
        """
        return self.kind in self._PRIM_MAP

    def is_function(self) -> bool:
        """
        Determine if this is a function token.
        """
        return self.kind == Token.Kind.FUNCTION

    def is_pointer(self) -> bool:
        """
        Determine if this is a pointer token.
        """
        return self.kind == Token.Kind.POINTER

    def is_reference(self) -> bool:
        """
        Determine if this is a reference.
        """
        return self.kind in [Token.Kind.LVALUE_REFERENCE, Token.Kind.RVALUE_REFERENCE]

    def is_ptr_or_ref(self) -> bool:
        """
        Determine if this is a pointer or reference type.
        """
        return self.kind in self._PTR_REF_MAP

    def is_array(self) -> bool:
        """
        Determine if this is an array token.
        """
        return self.kind == Token.Kind.ARRAY

    def is_marker(self) -> bool:
        """
        Determine if this is a C++ marker symbol.
        """
        return self.kind == Token.Kind.MARKER

    def is_digit(self) -> bool:
        """
        Determine if this is a digit.
        """
        return self.kind == Token.Kind.DIGIT

    def is_qualified(self) -> bool:
        """
        Determine if this is a qualified type.
        """
        return self.kind in [Token.Kind.QUALIFIED, Token.Kind.QUALIFIED_NOREM]

    def is_template_start(self) -> bool:
        """
        Determine if this code signifies the start of a template.
        """
        return self.kind in [Token.Kind.TEMPLATE, Token.Kind.TEMPLATE_GPP]

    def is_template_backref_parm(self) -> bool:
        """
        Determine if this code is some kind of template backref parameter.
        """
        return self.kind in [
            Token.Kind.TEMPLATE_ARG_BACKREF1,
            Token.Kind.TEMPLATE_ARG_BACKREF2,
        ]

    def is_underscore(self) -> bool:
        """
        Determine if this code is an underscore.
        """
        return self.kind == Token.Kind.UNDERSCORE

    def get_quali_term(self) -> CxxTerm:
        """
        If this is a CV qualifier type, return an equivalent CV qualifier `CxxTerm`.
        Otherwise, throw an error.
        """
        return CxxTerm(kind=self._QUALI_MAP[self.kind])

    def get_spec_term(self) -> CxxTerm:
        """
        If this is a type specifier, return an equivalent type specifier `CxxTerm`.
        Otherwise, throw an error.
        """
        return CxxTerm(kind=self._SPEC_MAP[self.kind])

    def get_quali_spec_term(self) -> CxxTerm:
        """
        If this is a type specifier or CV qualifier, return an equivalent `CxxTerm`.
        Otherwise, throw an error.
        """
        if self.is_cv_quali():
            return self.get_quali_term()

        assert self.is_type_spec()
        return self.get_spec_term()

    def get_primitive_term(self) -> CxxTerm:
        """
        If this is a primitive type, return an equivalent `CxxTerm`.
        Otherwise, throw an error.
        """
        return CxxTerm(kind=self._PRIM_MAP[self.kind])

    def get_ptr_ref_term(self) -> CxxTerm:
        """
        If this is a pointer or reference type, return an equivalent `CxxTerm`.
        Otherwise, throw an error.
        """
        return CxxTerm(kind=self._PTR_REF_MAP[self.kind])

    def __bool__(self) -> bool:
        return self.kind != Token.Kind.UNKNOWN

    @staticmethod
    def from_char(char: str) -> "Token":
        """
        Construct this variant with the given character and determine its type code.
        """
        try:
            kind = Token.Kind(char)
        except:  # noqa
            if char == "P":
                kind = Token.Kind.POINTER  # Pointer can be upper or lowercase
            elif char in set("$.\0"):
                kind = Token.Kind.MARKER
            elif char.isdecimal():
                kind = Token.Kind.DIGIT
            else:
                kind = Token.Kind.UNKNOWN

        return Token(kind=kind, content=char)

    @staticmethod
    def peek(src: TextIOBase, offset: int = 0) -> "Token":
        """
        Construct this variant by peeking the next character in the given buffer.
        The buffer is not modified.
        """
        return Token.from_char(peek(src, 1, offset=offset))

    @staticmethod
    def read(src: TextIOBase) -> "Token":
        """
        Construct this variant by reading the next character in the given buffer.
        An error will be thrown if there are no characters remaining in the buffer.
        """
        return Token.from_char(read_exact(src, 1))

    @staticmethod
    def scan_for_marker(src: TextIOBase) -> Optional[int]:
        """
        Look ahead in the buffer and scan for the next token of type `MARKER`.
        If a marker is found, return its offset from the current buffer location.
        Otherwise, return `None`.
        """
        offset: int = 0

        next_char = peek(src, offset=offset)
        next_tok = Token.from_char(next_char)
        while next_char and not next_tok.is_marker():
            offset += 1
            next_char = peek(src, offset=offset)
            next_tok = Token.from_char(next_char)

        if not next_char:
            return None
        else:
            return offset

    def __str__(self) -> str:
        return self.content


@dataclass
class Operator:
    """
    Variant type for GNUv2 operator overload prefixes.
    """

    class Kind(StrEnum):
        UNKNOWN = "unknown"
        TYPE_CONV = "type_conv"
        ANSI_TYPE_CONV = "ansi_type_conv"

        NEW = "nw"
        DELETE = "dl"
        NEW_ARR = "vn"
        DELETE_ARR = "vd"
        ASSIGN = "as"
        NOT_EQUAL = "ne"
        EQUAL = "eq"
        GREATER_EQUAL = "ge"
        GREATER = "gt"
        LESS_EQUAL = "le"
        LESS = "lt"
        PLUS = "pl"
        PLUS_ASSIGN = "apl"
        MINUS = "mi"
        MINUS_ASSIGN = "ami"
        MUL = "ml"
        MUL_ASSIGN = "aml"
        PLUS_UNARY = "convert"
        MINUS_UNARY = "negate"
        MOD = "md"
        MOD_ASSIGN = "amd"
        DIV = "dv"
        DIV_ASSIGN = "adv"
        LOG_AND = "aa"
        LOG_OR = "oo"
        LOG_NOT = "nt"
        INC = "pp"
        DEC = "mm"
        BW_OR = "or"
        BW_OR_ASSIGN = "aor"
        BW_XOR = "er"
        BW_XOR_ASSIGN = "aer"
        BW_AND = "ad"
        BW_AND_ASSIGN = "aad"
        CO = "co"
        CALL = "cl"
        SHIFT_LEFT = "ls"
        SHIFT_LEFT_ASSIGN = "als"
        SHIFT_RIGHT = "rs"
        SHIFT_RIGHT_ASSIGN = "ars"
        REFERENCE = "rf"
        ELEM = "vc"
        COMMA = "cm"
        TERNARY = "cn"
        MAX = "mx"
        MIN = "mn"
        RM = "rm"
        SIZEOF = "sz"

    _OPERATORS: ClassVar[dict[Kind, str]] = {
        Kind.NEW: " new",
        Kind.DELETE: " delete",
        Kind.NEW_ARR: " new[]",
        Kind.DELETE_ARR: " delete[]",
        Kind.ASSIGN: "=",
        Kind.NOT_EQUAL: "!=",
        Kind.EQUAL: "==",
        Kind.GREATER_EQUAL: ">=",
        Kind.GREATER: ">",
        Kind.LESS_EQUAL: "<=",
        Kind.LESS: "<",
        Kind.PLUS: "+",
        Kind.PLUS_ASSIGN: "+=",
        Kind.MINUS: "-",
        Kind.MINUS_ASSIGN: "-=",
        Kind.MUL: "*",
        Kind.MUL_ASSIGN: "*=",
        Kind.PLUS_UNARY: "+",
        Kind.MINUS_UNARY: "-",
        Kind.MOD: "%",
        Kind.MOD_ASSIGN: "%=",
        Kind.DIV: "/",
        Kind.DIV_ASSIGN: "/=",
        Kind.LOG_AND: "&&",
        Kind.LOG_OR: "||",
        Kind.LOG_NOT: "!",
        Kind.INC: "++",
        Kind.DEC: "--",
        Kind.BW_OR: "|",
        Kind.BW_OR_ASSIGN: "|=",
        Kind.BW_XOR: "^",
        Kind.BW_XOR_ASSIGN: "^=",
        Kind.BW_AND: "&",
        Kind.BW_AND_ASSIGN: "&=",
        Kind.CO: "~",
        Kind.CALL: "()",
        Kind.SHIFT_LEFT: "<<",
        Kind.SHIFT_LEFT_ASSIGN: "<<=",
        Kind.SHIFT_RIGHT: ">>",
        Kind.SHIFT_RIGHT_ASSIGN: ">>=",
        Kind.REFERENCE: "->",
        Kind.ELEM: "[]",
        Kind.COMMA: ", ",
        Kind.TERNARY: "?:",
        Kind.MAX: ">?",
        Kind.MIN: "<?",
        Kind.RM: "->*",
        Kind.SIZEOF: "sizeof ",
    }
    _OP_ASSIGNS: ClassVar[dict[Kind, Kind]] = {
        Kind.PLUS: Kind.PLUS_ASSIGN,
        Kind.MINUS: Kind.MINUS_ASSIGN,
        Kind.MUL: Kind.MUL_ASSIGN,
        Kind.DIV: Kind.DIV_ASSIGN,
        Kind.MOD: Kind.MOD_ASSIGN,
        Kind.BW_OR: Kind.BW_OR_ASSIGN,
        Kind.BW_AND: Kind.BW_AND_ASSIGN,
        Kind.BW_XOR: Kind.BW_XOR_ASSIGN,
        Kind.SHIFT_LEFT: Kind.SHIFT_LEFT_ASSIGN,
        Kind.SHIFT_RIGHT: Kind.SHIFT_RIGHT_ASSIGN,
    }

    kind: Kind

    def is_unknown(self) -> bool:
        return self.kind == Operator.Kind.UNKNOWN

    def is_type_conv(self) -> bool:
        return self.kind in [Operator.Kind.TYPE_CONV, Operator.Kind.ANSI_TYPE_CONV]

    def has_known_name(self) -> bool:
        """
        Determine if this operator has a known name.
        """
        return not (self.is_unknown() or self.is_type_conv())

    def get_name(self) -> str:
        """
        If this is an operator overload with a known static name, return the full
        method name for this operator. Otherwise, an error will be thrown.
        """
        assert self.has_known_name()
        return f"operator{self._OPERATORS[self.kind]}"

    def __str__(self) -> str:
        return self.get_name()

    @staticmethod
    def from_func_name(func_name: str) -> "Operator":
        """
        Given a function name, attempt to determine if it is an operator overload
        of some kind.
        """

        is_marked_op: bool = (
            func_name.startswith("op") and Token.from_char(func_name[2:3]).is_marker()
        )
        is_type_conv: bool = (
            func_name.startswith("type") and Token.from_char(func_name[4:5]).is_marker()
        )
        is_ansi_type_conv: bool = func_name.startswith("__op")
        is_unmarked_op: bool = (
            func_name.startswith("__") and func_name[2:4].isalpha() and func_name[2:4].islower()
        )

        kind = Operator.Kind.UNKNOWN

        if is_marked_op:
            # See if this is an assignment expression.
            is_assignment: bool = func_name[3:10] == "assign_"
            remaining: str = func_name[10:] if is_assignment else func_name[3:]

            # See if the rest of the string is an operator shorthand.
            try:
                kind = Operator.Kind(remaining)
                if is_assignment:
                    # Convert to assignment operator.
                    kind = Operator._OP_ASSIGNS[kind]
            except:  # noqa
                kind = Operator.Kind.UNKNOWN

        elif is_type_conv:
            kind = Operator.Kind.TYPE_CONV
        elif is_ansi_type_conv:
            kind = Operator.Kind.ANSI_TYPE_CONV

        elif is_unmarked_op:
            # Some other operator format.
            maybe_op: str = func_name[2:]
            try:
                kind = Operator.Kind(maybe_op)
            except:  # noqa
                pass

        return Operator(kind=kind)


@dataclass
class Special:
    """
    Variant type for special GNUv2 mangled prefixes.
    """

    class Kind(StrEnum):
        UNKNOWN = "unknown"
        DLL_IMPORT = "dll_imported"
        DTOR = "destructor"
        VTABLE = "vtable"
        STATIC_DATA = "static_data"
        GLOBAL_ANONYMOUS = "anonymous"
        GLOBAL_CTOR = "global_ctor"
        GLOBAL_DTOR = "global_dtor"
        VTHUNK = "virtual_thunk"
        TINFO_NODE = "typeinfo_node"
        TINFO_FUNC = "typeinfo_func"

    _DATA_CHARS: ClassVar[set[str]] = set("0123456789Qt")
    _GLOBAL_MAP: ClassVar[dict[str, Kind]] = {
        "I": Kind.GLOBAL_CTOR,
        "D": Kind.GLOBAL_DTOR,
        "N": Kind.GLOBAL_ANONYMOUS,
    }

    kind: Kind
    content: str

    def is_type_info(self) -> bool:
        return self.kind in [Special.Kind.TINFO_NODE, Special.Kind.TINFO_FUNC]

    def is_global(self) -> bool:
        return self.kind in [
            Special.Kind.GLOBAL_CTOR,
            Special.Kind.GLOBAL_DTOR,
            Special.Kind.GLOBAL_ANONYMOUS,
        ]

    @staticmethod
    def peek(src: TextIOBase) -> "Special":
        """
        Try to peek into the given buffer to read a GNUv2 special prefix.

        If no special prefix can be parsed, the returned `Special` object will have
        `Kind == UNKNOWN`, and `content` will be empty.
        """

        content: str = peek_exact(src, 2)
        if content:
            if (
                content.startswith("_")
                and content[1] in Special._DATA_CHARS
                and Token.scan_for_marker(src) is not None
            ):
                # Static data member.
                # The demangler needs to read the second character itself, so we don't
                # include that in "content".
                return Special(kind=Special.Kind.STATIC_DATA, content="_")

        content = peek_exact(src, 3)
        if content:
            second_is_marker: bool = Token.from_char(content[1]).is_marker()
            if content.startswith("_") and content.endswith("_") and second_is_marker:
                # Destructor.
                return Special(kind=Special.Kind.DTOR, content=content)

        content = peek_exact(src, 4)
        if content == "__ti":
            # Type info node.
            return Special(kind=Special.Kind.TINFO_NODE, content=content)
        elif content == "__tf":
            # Type info function.
            return Special(kind=Special.Kind.TINFO_FUNC, content=content)
        elif content.startswith("_vt") and Token.from_char(content[3]).is_marker():
            # Old-style virtual table, no thunks
            return Special(kind=Special.Kind.VTABLE, content=content)

        content = peek_exact(src, 5)
        if content == "__vt_":
            # Virtual table (new style, with thunks)
            return Special(kind=Special.Kind.VTABLE, content=content)

        maybe_import = Special.peek_for_dllimport(src)
        if maybe_import:
            # Imported symbol
            return maybe_import

        content = peek_exact(src, 8)
        if content == "__thunk_":
            # Virtual table thunk function
            return Special(kind=Special.Kind.VTHUNK, content=content)

        maybe_global = Special.peek_for_global(src)
        if maybe_global:
            # Found a _GLOBAL_ token (CTOR, DTOR, ANONYMOUS)
            return maybe_global

        # Couldn't parse any tokens.
        return Special(kind=Special.Kind.UNKNOWN, content="")

    @staticmethod
    def peek_for_dllimport(src: TextIOBase) -> Optional["Special"]:
        """
        Try to peek into the given buffer, looking specifically for a DLL import
        prefix.

        If a DLL import token is found, returns the token. Otherwise, returns `None`.
        """
        content = peek_exact(src, 6)
        if content in ["_imp__", "__imp_"]:
            return Special(kind=Special.Kind.DLL_IMPORT, content=content)

        return None

    @staticmethod
    def peek_for_global(src: TextIOBase) -> Optional["Special"]:
        """
        Try to peek into the given buffer, looking specifically for a `_GLOBAL_`-prefixed
        special token (CTOR, DTOR, ANONYMOUS).

        If a global-prefixed token is not found, returns `None`.
        Otherwise, returns a `Special` token whose `kind` is guaranteed to be global
        and whose `content` contains the characters peeked for the token.
        """
        content = peek_exact(src, 11)
        if content.startswith("_GLOBAL_"):
            marked_chunk = content[8:]
            if (
                Token.from_char(marked_chunk[0]).is_marker()
                and Token.from_char(marked_chunk[2]).is_marker()
            ):
                kind = Special._GLOBAL_MAP.get(marked_chunk[1])
                if kind:
                    # Global ctor/dtor/anonymous field.
                    return Special(kind=kind, content=content)

        return None
