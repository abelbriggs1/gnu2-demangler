"""
CLI for the demangler.
"""

import argparse

parser = argparse.ArgumentParser("gnu2-demangler", description="Demangler for GNU v2 C++ symbols.")
parser.add_argument("symbol", help="Symbol to demangle.", type=str, required=True)


def main():
    args = parser.parse_args()  # noqa
    print("Hello world!")


if __name__ == "__main__":
    main()
