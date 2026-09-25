"""
research/backtest/cross_validation.py
=====================================
Purged K-Fold and Combinatorial Purged Cross-Validation (Lopez de Prado, AFML ch. 7 and 12).

Samples are observations with a start t0 and an end t1 (for trades: entry and exit time). A training
sample is PURGED when its [t0, t1] overlaps the test span, and EMBARGOED when it starts within
`embargo_pct * n` samples after the test span ends, because its information overlaps the test outcome.

For rule-based strategies there is no fitted model; "training" means choosing a parameter set.
`cpcv_selection_paths` does exactly that: on each split, pick the configuration with the best
in-sample Sharpe, record its out-of-sample returns, and stitch the test groups into CPCV paths.
"""
from __future__ import annotations

import math
from itertools import combinations
from typing import Dict, Iterator, List, Mapping, Sequence, Tuple

import numpy as np


def _validate(t0: Sequence[float], t1: Sequence[float]) -> Tuple[np.ndarray, np.ndarray]:
    a = np.asarray(t0, dtype=float)
    b = np.asarray(t1, dtype=float)
    if a.shape != b.shape or a.ndim != 1:
        raise ValueError("t0 and t1 must be 1-D arrays of equal length")
    if np.any(np.diff(a) < 0):
        raise ValueError("samples must be sorted by t0")
    if np.any(b < a):
        raise ValueError("every sample needs t1 >= t0")
    return a, b


def _purge_and_embargo(mask: np.ndarray, t0: np.ndarray, t1: np.ndarray, test: np.ndarray, h: int) -> None:
    lo, hi = t0[test].min(), t1[test].max()
    mask &= ~((t0 <= hi) & (t1 >= lo))
    if h > 0:
        after = np.where(t0 > hi)[0]
        mask[after[:h]] = False


class PurgedKFold:
    def __init__(self, n_splits: int = 5, embargo_pct: float = 0.0):
        if n_splits < 2 or not 0.0 <= embargo_pct < 1.0:
            raise ValueError("n_splits >= 2 and 0 <= embargo_pct < 1 required")
        self.n_splits = n_splits
        self.embargo_pct = embargo_pct

    def split(self, t0: Sequence[float], t1: Sequence[float]) -> Iterator[Tuple[np.ndarray, np.ndarray]]:
        a, b = _validate(t0, t1)
        n = len(a)
        if n < self.n_splits:
            raise ValueError("fewer samples than folds")
        h = int(math.ceil(self.embargo_pct * n))
        for test in np.array_split(np.arange(n), self.n_splits):
            mask = np.ones(n, dtype=bool)
            mask[test] = False
            _purge_and_embargo(mask, a, b, test, h)
            yield np.where(mask)[0], test


class CombinatorialPurgedKFold:
    def __init__(self, n_groups: int = 6, n_test_groups: int = 2, embargo_pct: float = 0.0):
        if not 1 <= n_test_groups < n_groups:
            raise ValueError("need 1 <= n_test_groups < n_groups")
        self.n_groups = n_groups
        self.n_test_groups = n_test_groups
        self.embargo_pct = embargo_pct

    @property
    def n_splits(self) -> int:
        return math.comb(self.n_groups, self.n_test_groups)

    @property
    def n_paths(self) -> int:
        return math.comb(self.n_groups - 1, self.n_test_groups - 1)

    def groups(self, n: int) -> List[np.ndarray]:
        return np.array_split(np.arange(n), self.n_groups)

    def split(self, t0: Sequence[float], t1: Sequence[float]) -> Iterator[Tuple[np.ndarray, Tuple[int, ...], np.ndarray]]:
        a, b = _validate(t0, t1)
        n = len(a)
        if n < self.n_groups:
            raise ValueError("fewer samples than groups")
        h = int(math.ceil(self.embargo_pct * n))
        groups = self.groups(n)
        for combo in combinations(range(self.n_groups), self.n_test_groups):
            test = np.concatenate([groups[g] for g in combo])
            mask = np.ones(n, dtype=bool)
            mask[test] = False
            for g in combo:                       # purge/embargo around each contiguous test block
                _purge_and_embargo(mask, a, b, groups[g], h)
            yield np.where(mask)[0], combo, test

    def assemble_paths(self, split_outputs: Mapping[int, Mapping[int, object]]) -> List[Dict[int, object]]:
        """
        split_outputs[split_index][group] = out-of-sample result for that group in that split.
        Path j takes, for every group, the j-th split (in split order) that tested it.
        """
        per_group: Dict[int, List[object]] = {g: [] for g in range(self.n_groups)}
        for split_index in sorted(split_outputs):
            for g, value in split_outputs[split_index].items():
                per_group[g].append(value)
        if any(len(v) != self.n_paths for v in per_group.values()):
            raise ValueError("every group must appear in exactly n_paths splits")
        return [{g: per_group[g][j] for g in range(self.n_groups)} for j in range(self.n_paths)]


def _sharpe_cols(block: np.ndarray) -> np.ndarray:
    sd = block.std(axis=0, ddof=1)
    with np.errstate(divide="ignore", invalid="ignore"):
        return np.where(sd > 1e-15, block.mean(axis=0) / sd, -np.inf)


def cpcv_selection_paths(returns: np.ndarray, n_groups: int = 6, n_test_groups: int = 2,
                         embargo_pct: float = 0.0) -> Dict[str, object]:
    """
    returns: T x C matrix of per-period returns for C parameter configurations (one row per session).
    Each split selects the in-sample best configuration; its out-of-sample returns fill the test groups.
    Output: the stitched OOS return series for each path and how often each configuration was chosen.
    """
    m = np.asarray(returns, dtype=float)
    if m.ndim != 2 or m.shape[1] < 1:
        raise ValueError("returns must be T x C")
    n = m.shape[0]
    cv = CombinatorialPurgedKFold(n_groups, n_test_groups, embargo_pct)
    idx = np.arange(n, dtype=float)
    groups = cv.groups(n)
    outputs: Dict[int, Dict[int, np.ndarray]] = {}
    chosen: List[int] = []
    for s, (train, combo, _test) in enumerate(cv.split(idx, idx)):
        best = int(np.argmax(_sharpe_cols(m[train]))) if len(train) > 1 else 0
        chosen.append(best)
        outputs[s] = {g: m[groups[g], best] for g in combo}
    paths = [np.concatenate([p[g] for g in range(n_groups)]) for p in cv.assemble_paths(outputs)]
    counts = {c: chosen.count(c) for c in sorted(set(chosen))}
    return {"paths": paths, "chosen_counts": counts, "n_splits": cv.n_splits, "n_paths": cv.n_paths}
