from jitsdp import metrics
import numpy as np
import pandas as pd
from pandas.testing import assert_frame_equal

from pytest import approx


def test_prequential_recalls():
    fading_factor = .9
    results = {
        'timestep': [0, 1, 2, 3, 4, 5],
        'target': [0, 1, 0, 0, 1, 1],
        'prediction': [None, 0, 0, 0, 1, 1],
    }
    expected = results.copy()
    expected.update({
        'r0': [0, 0, .526315789, .701107011, .701107011, .701107011],
        'r1': [0, 0,          0,          0, .526315789, .701107011],
    })
    results = pd.DataFrame(results)
    expected = pd.DataFrame(expected)
    actual = metrics.prequential_recalls(results, fading_factor)
    assert_frame_equal(expected, actual)


def test_prequential_gmean():
    recalls = {
        'r0': [0, 0, .526315789, .701107011, .701107011, .701107011],
        'r1': [0, 0,          0,          0, .526315789, .701107011],
    }
    expected = recalls.copy()
    expected.update({
        'gmean': [0, 0,       0,          0, .607456739, .701107011],
    })
    recalls = pd.DataFrame(recalls)
    expected = pd.DataFrame(expected)
    actual = metrics.prequential_gmean(recalls)
    assert_frame_equal(expected, actual)


def test_prequential_proportions():
    fading_factor = .9
    results = {
        'timestep': [0, 1, 2, 3, 4, 5],
        'prediction': [1, 0, 0, 0, 1, 1],
    }
    expected = results.copy()
    expected.update({
        'p0': [0, .5263157895, .7011070111, .7880197732, .5955898513, .4684788895],
        'p1': [1, .4736842105, .2988929889, .2119802268, .4044101487, .5315211105],
    })
    results = pd.DataFrame(results)
    expected = pd.DataFrame(expected)
    actual = metrics.prequential_proportions(results, fading_factor)
    assert_frame_equal(expected, actual)
