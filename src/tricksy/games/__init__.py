"""Where a game's rules engine lives.

One game ships today, :mod:`tricksy.games.texas42`, and **nothing dispatches on which**: there is
no ``Game`` protocol, no registry, and no per-kind branch anywhere in ``tricksy.storage`` or
``tricksy.api``. This package is a name, not an abstraction. See DESIGN.md §11.1 for why the
directory exists anyway and what a second engine would actually have to supply.
"""
