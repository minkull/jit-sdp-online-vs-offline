from jitsdp import evaluation
from jitsdp.data import FEATURES
from jitsdp import metrics


import numpy as np
import pandas as pd
import torch.nn as nn
import torch.optim as optim

from pytest import approx
from numpy.testing import assert_array_equal


def create_pipeline():
    pipeline = evaluation.create_pipeline({'epochs': 100})
    pipeline.zero_fraction = .5
    return pipeline


def create_data():
    n_samples = 100
    half_samples = n_samples // 2
    features = np.random.rand(n_samples, len(FEATURES))
    features[half_samples:, :] = features[half_samples:, :] + 1
    data = pd.DataFrame(features, columns=FEATURES)
    targets = [0] * half_samples + [1] * half_samples
    data['target'] = np.array(targets, dtype=np.int64)
    return data


def test_train_predict():
    pipeline = create_pipeline()
    data = create_data()
    pipeline.train(data, data)
    target_prediction = pipeline.predict(data)

    # metrics
    expected_gmean = 1.
    expected_recalls = np.array([1., 1.])
    gmean, recalls = metrics.gmean_recalls(target_prediction)
    assert expected_gmean == gmean
    assert_array_equal(expected_recalls, recalls)

    # probability
    assert 0.5 == target_prediction['probability'].round().mean()
