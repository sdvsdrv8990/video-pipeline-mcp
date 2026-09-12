"""core/ids — личность узла рабочей области: префикс по объявлению, цепочка предков, реестр связей."""

from .chain_resolver import ChainResolver
from .id_generator import IDGenerator
from .link_registry import LinkRegistry, LinkError
from .taxonomy import Taxonomy, TaxonomyError

__all__ = ["IDGenerator", "LinkRegistry", "LinkError", "Taxonomy", "TaxonomyError", "ChainResolver"]
