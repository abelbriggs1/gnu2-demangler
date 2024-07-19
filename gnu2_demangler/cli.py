"""
CLI for the demangler.
"""

import argparse
import dataclasses
import pprint

from gnu2_demangler.demangler import parse

parser = argparse.ArgumentParser("gnu2-demangler", description="Demangler for GNU v2 C++ symbols.")
parser.add_argument("symbol", help="Symbol to demangle.", type=str)


def main():
    args = parser.parse_args()  # noqa
    sym = parse(args.symbol)
    print(str(sym))
    pprint.pprint(dataclasses.asdict(sym), indent=4)


if __name__ == "__main__":
    main()
