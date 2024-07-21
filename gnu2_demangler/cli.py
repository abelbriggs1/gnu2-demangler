"""
CLI for the demangler.
"""

import argparse

from gnu2_demangler.demangler import demangle, parse

parser = argparse.ArgumentParser("gnu2-demangler", description="Demangler for GNU v2 C++ symbols.")
parser.add_argument("symbol", help="Symbol to demangle.", type=str)
parser.add_argument(
    "--error-on-failure", "-e", help="Throw an exception if demangling fails", action="store_true"
)


def main():
    args = parser.parse_args()  # noqa
    if args.error_on_failure:
        print(str(parse(args.symbol)))
    else:
        print(demangle(args.symbol))


if __name__ == "__main__":
    main()
