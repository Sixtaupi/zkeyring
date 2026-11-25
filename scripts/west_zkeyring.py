# SPDX-License-Identifier: Apache-2.0

import sys
from pathlib import Path

from west.commands import CommandError, WestCommand

# Use the sources of this module, even when zkeyring is also installed.
sys.path.insert(0, str(Path(__file__).resolve().parent / 'signing'))

try:
    from zkeyring import cli

    missing = None
except ModuleNotFoundError as exc:
    missing = exc.name


class Zkeyring(WestCommand):
    def __init__(self):
        super().__init__(
            'zkeyring',
            'sign MCUboot firmware with an external key',
            'Sign Zephyr MCUboot firmware with the key of a zkeyring profile.',
        )

    def do_add_parser(self, parser_adder):
        parser = parser_adder.add_parser(self.name, help=self.help, description=self.description)
        if missing is None:
            cli.add_commands(parser)
        else:
            parser.add_argument('arguments', nargs='...')
        return parser

    def do_run(self, args, unknown):
        if missing is not None:
            self.die(
                f'missing Python package {missing!r}, run "west packages pip --install" '
                'in the west virtual environment'
            )
        if result := cli.run(args):
            raise CommandError(result)
