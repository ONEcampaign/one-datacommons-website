"""Cross-check the model Literal enums against eval_corpus.FROZEN_ENUMS.

The FROZEN_ENUMS dict is extracted from eval_corpus.py via ast.parse rather than importing
the module, because eval_corpus.py imports yaml at the top and PyYAML is not a dependency
of the qre package.
"""
import ast
from pathlib import Path
from typing import get_args

from qre.models import Axis, BindingKind, CoverageKind, StatusLiteral


def _extract_frozen_enums_from_source() -> dict:
    """Parse eval_corpus.py with ast and extract the FROZEN_ENUMS literal dict.

    Returns the dict as Python data without importing the module (and thus without
    pulling in the yaml dependency).
    """
    # __file__ is query_engine/tests/test_frozen_enums.py; parents[1] is query_engine/
    corpus_path = Path(__file__).parents[1] / "eval_corpus.py"
    source = corpus_path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "FROZEN_ENUMS":
                    return ast.literal_eval(node.value)
    raise RuntimeError("FROZEN_ENUMS assignment not found in eval_corpus.py")


FROZEN_ENUMS = _extract_frozen_enums_from_source()


class TestLiteralsMatchCorpus:
    def test_status_matches_corpus(self):
        assert get_args(StatusLiteral) == tuple(FROZEN_ENUMS["status"])

    def test_binding_kind_matches_corpus(self):
        assert get_args(BindingKind) == tuple(FROZEN_ENUMS["binding_kind"])

    def test_axis_matches_corpus(self):
        assert get_args(Axis) == tuple(FROZEN_ENUMS["axis"])

    def test_coverage_kind_matches_contract(self):
        # NOTE: eval_corpus.FROZEN_ENUMS deliberately omits coverage_kind (see
        # eval_corpus.py comment "Coverage.kind is frozen too but coverage is not
        # carried per-golden here"). We assert against the contract literal directly.
        # This is the fourth frozen enum per the contract; it is just not in the corpus dict.
        assert get_args(CoverageKind) == ("exact", "breadth", "bare")
