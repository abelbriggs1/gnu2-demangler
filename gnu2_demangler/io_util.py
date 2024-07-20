"""
Utility functions for working with text streams.
"""

from contextlib import contextmanager
from io import StringIO, TextIOBase
from typing import Iterator, Optional


def read_exact(src: TextIOBase, size: int) -> str:
    """
    Read exactly `n` bytes from `src`, or raise a ValueError
    """
    value = src.read(size)
    if len(value) != size:
        raise ValueError(f"Unable to read {size} bytes; got {value!r}")
    return value


@contextmanager
def peeking(src: TextIOBase, offset: int = 0) -> Iterator[None]:
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


def peek(src: TextIOBase, n: int = 1, offset: int = 0) -> Optional[str]:
    """
    Read up to `n` bytes from `src` without advancing the offset.
    An optional offset can be added to peek starting further ahead of
    the current location.
    """
    with peeking(src, offset=offset):
        return src.read(n)


def peek_exact(src: TextIOBase, n: int = 1, offset: int = 0) -> Optional[str]:
    """
    Try to read exactly `n` bytes from `src` without advancing the offset.
    If there are not enough bytes in the buffer, return "".
    """
    string = peek(src, n, offset=offset)
    if len(string) != n:
        string = ""
    return string


def bytes_left(src: TextIOBase, offset: int = 0) -> int:
    """
    Retrieve the number of bytes left in `src`.
    An optional offset can be added.
    """
    start: int = src.tell() + offset
    with peeking(src):
        src.seek(0, 2)
        end: int = src.tell()

    return end - start


def lookahead_for(src: TextIOBase, chars: list[str]) -> Optional[int]:
    """
    Look ahead in the buffer for a character in the given list.

    If one is found, return the number of chars that need to be read from the current
    offset in order to reach the character.

    If none of the given chars are found and the end of the buffer is found,
    returns None.
    """
    offset: int = 0
    with peeking(src):
        char = src.read(1)
        while char:
            if char in chars:
                return offset
            offset += 1
            char = src.read(1)

    return None


def lookahead_for_substring(src: TextIOBase, string: str, base_offset: int = 0) -> Optional[int]:
    """
    Look ahead in the buffer for a given substring. An optional "base_offset" can be
    provided to start from a later point in the buffer.

    If one is found, return the number of chars that need to be read in order
    to reach the start of the substring (starting from [current location + base offset]).

    If the substring is not found in the buffer, returns None.
    """

    offset: int = 0

    substr = peek(src, n=len(string), offset=base_offset)
    while substr:
        if substr == string:
            return offset
        offset += 1
        substr = peek(src, n=len(string), offset=base_offset + offset)

    return None


def lookahead_while(src: TextIOBase, chars: list[str], base_offset: int = 0) -> int:
    """
    Look ahead in the buffer as long as the buffer contains characters in the given list.
    Return the number of subsequent characters found.
    An optional offset can be passed to start from a later point in the buffer.
    """
    num_chars: int = 0
    with peeking(src, offset=base_offset):
        char = src.read(1)
        while char in chars:
            num_chars += 1
            char = src.read(1)

    return num_chars


@contextmanager
def as_stringio(src: str) -> Iterator[StringIO]:
    """Wrap `src` in a `StringIO`, and assert it was fully consumed at the end of the context"""
    buf = StringIO(src)
    yield buf
    leftover = buf.read()
    if leftover:
        raise ValueError(f"Unable to parse full input, leftover chars: {leftover!r}")


def peek_number(src: TextIOBase) -> Optional[tuple[int, int]]:
    """
    Peek subsequent numeric characters from the source and return them as a positive
    base-10 integer.

    The first element of the tuple contains the read count.
    The second element of the tuple contains the offset from the current base which
    points to the first character after the sequence of digits.

    If a number cannot be read, `None` will be returned.
    """

    # Read each digit.
    offset = 0
    number_str = ""

    with peeking(src):
        while peek(src).isdecimal():
            number_str += read_exact(src, 1)
            offset += 1

    if number_str == "":
        return None
    return (int(number_str), offset)


def read_number(src: TextIOBase, allow_zero: bool = False) -> int:
    """
    Read subsequent numeric characters from the source and return them as a positive
    base-10 integer.

    If a number cannot be read, an error will be thrown.
    If the read number is zero and `allow_zero` is False, an error will be thrown.
    """

    result = peek_number(src)

    if not result:
        raise ValueError("Unable to parse expected number from string.")

    number, next_offset = result

    if not allow_zero:
        if number == 0:
            raise ValueError("length must be positive")

    read_exact(src, next_offset)
    return number


def read_number_with_underscores(src: TextIOBase) -> int:
    """
    Given a buffer which matches one of the following cases, read the number as a
    base-10 decimal and return it.
    - A count surrounded by single underscores (example: `_21_`)
    - A single digit (example: `0`)

    In the first case, the surrounding `_` chars will also be consumed from the buffer.
    Note that this function can return `0` as a valid value.
    """
    number: int = 0

    if peek(src) == "_":
        # Consume the underscore prefix.
        read_exact(src, 1)
        number = read_number(src, allow_zero=True)

        if not peek(src) == "_":
            raise ValueError(f"Expected trailing `_` character after number {number}!")

        # Consume the underscore suffix.
        read_exact(src, 1)
    else:
        if not peek(src).isdecimal():
            raise ValueError(f"Expected to read single decimal digit, got `{peek(src)}`!")
        number = int(read_exact(src, 1))

    return number
