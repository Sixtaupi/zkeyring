# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Romain Pelletant

from collections.abc import Iterator


class ZkeyringError(Exception):
    """An error that can be reported to the caller."""


def error_causes(error: BaseException) -> Iterator[BaseException]:
    seen = {id(error)}
    while cause := error.__cause__ or (None if error.__suppress_context__ else error.__context__):
        if id(cause) in seen:
            return
        seen.add(id(cause))
        yield cause
        error = cause
