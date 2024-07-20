"""
CLI for the demangler.
"""

import argparse

from gnu2_demangler.demangler import demangle

parser = argparse.ArgumentParser("gnu2-demangler", description="Demangler for GNU v2 C++ symbols.")
parser.add_argument("symbol", help="Symbol to demangle.", type=str)


def main():
    args = parser.parse_args()  # noqa
    print(demangle(args.symbol))


if __name__ == "__main__":
    main()
