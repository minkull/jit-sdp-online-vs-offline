from jitsdp import metrics
from jitsdp.mlp import MLP
from jitsdp.data import FEATURES
from jitsdp.utils import mkdir

from abc import ABCMeta, abstractmethod
import joblib
import logging
import numpy as np
import pandas as pd
import pathlib
import torch
import torch.nn as nn
import torch.optim as optim
import torch.utils.data as data
from sklearn.base import clone
from sklearn.linear_model import SGDClassifier
from sklearn.naive_bayes import GaussianNB
from sklearn.neighbors import KNeighborsClassifier
from sklearn.ensemble import RandomForestClassifier
from sklearn.model_selection import train_test_split
from sklearn.preprocessing import StandardScaler
from sklearn.exceptions import NotFittedError

logger = logging.getLogger(__name__)


def set_seed(config):
    seed = config['seed']
    torch.manual_seed(seed)
    np.random.seed(seed)


def create_pipeline(config):
    map_fn = {
        'mlp': create_mlp_model,
        'nb': create_nb_model,
        'rf': create_rf_model,
        'lr': create_lr_model,
    }
    fn_create_model = map_fn[config['model']]
    if config['ensemble_size'] > 1:
        models = [fn_create_model(config)
                  for i in range(config['ensemble_size'])]
        model = Ensemble(models=models)
    else:
        model = fn_create_model(config)
    if config['threshold'] == 1:
        classifier = RateFixed(
            model=model, normal_proportion=config['normal_proportion'])
    elif config['threshold'] == 2:
        classifier = RateFixedTrain(
            model=model)
    else:
        classifier = ScoreFixed(model=model)
    if config['orb']:
        classifier = ORB(classifier=classifier,
                         normal_proportion=config['normal_proportion'])
    return classifier


def create_mlp_model(config):
    scaler = StandardScaler()
    criterion = nn.BCELoss()
    classifier = MLP(input_size=len(FEATURES),
                     hidden_size=len(FEATURES) // 2, drop_prob=0.2)
    optimizer = optim.Adam(params=classifier.parameters(), lr=0.003)
    return PyTorch(steps=[scaler], classifier=classifier, optimizer=optimizer, criterion=criterion,
                   features=FEATURES, target='target', soft_target='soft_target',
                   max_epochs=config['n_epochs'], batch_size=512, fading_factor=1)


def create_nb_model(config):
    classifier = GaussianNB()
    return NaiveBayes(steps=[], classifier=classifier,
                      features=FEATURES, target='target', soft_target='soft_target',
                      n_updates=config['n_epochs'], fading_factor=1)


def create_rf_model(config):
    classifier = RandomForestClassifier(
        n_estimators=0, criterion='entropy', max_depth=5, warm_start=True, bootstrap=False, ccp_alpha=0.05)
    return RandomForest(steps=[], classifier=classifier,
                        features=FEATURES, target='target', soft_target='soft_target',
                        n_trees=config['n_trees'], fading_factor=1)


def create_lr_model(config):
    scaler = StandardScaler()
    classifier = SGDClassifier(loss='log', penalty='l1', alpha=.01)
    return LogisticRegression(n_epochs=config['n_epochs'], steps=[scaler], classifier=classifier,
                              features=FEATURES, target='target', soft_target='soft_target',
                              batch_size=512, fading_factor=1)


class Model(metaclass=ABCMeta):
    @abstractmethod
    def train(self, df_train, **kwargs):
        pass

    @abstractmethod
    def predict_proba(self, df_features):
        pass

    @abstractmethod
    def save(self):
        pass

    @abstractmethod
    def load(self):
        pass

    @property
    @abstractmethod
    def n_iterations(self):
        pass


class Classifier(Model):
    @abstractmethod
    def predict(self, df_features, **kwargs):
        pass


class Threshold(Classifier):
    def __init__(self, model):
        self.model = model

    def train(self, df_train, **kwargs):
        self.model.train(df_train, **kwargs)

    def predict_proba(self, df_features):
        return self.model.predict_proba(df_features)

    def save(self):
        self.model.save()

    def load(self):
        self.model.load()

    @property
    def n_iterations(self):
        return self.model.n_iterations


class ScoreFixed(Threshold):
    def __init__(self, model, score=.5):
        super().__init__(model=model)
        self.score = score

    def predict(self, df_features, **kwargs):
        prediction = self.predict_proba(df_features=df_features)
        prediction['prediction'] = (
            prediction['probability'] >= self.score).round().astype('int')
        return prediction


class RateFixed(Threshold):
    def __init__(self, model, normal_proportion):
        super().__init__(model=model)
        self.normal_proportion = normal_proportion

    def predict(self, df_features, **kwargs):
        df_threshold = kwargs.pop('df_threshold', None)
        val_probabilities = self.predict_proba(
            df_threshold)['probability'] if df_threshold is not None else None
        prediction = self.predict_proba(df_features=df_features)
        threshold = _tune_threshold(val_probabilities=val_probabilities,
                                    test_probabilities=prediction['probability'], normal_proportion=self.normal_proportion)
        threshold = threshold.values
        prediction['prediction'] = (
            prediction['probability'] >= threshold).round().astype('int')
        return prediction


def _tune_threshold(val_probabilities, test_probabilities, normal_proportion):
    if val_probabilities is None:
        # fixed threshold
        return pd.Series([.5] * len(test_probabilities), name='threshold', index=test_probabilities.index)

    # rolling threshold
    probabilities = pd.concat([val_probabilities, test_probabilities[:-1]])
    threshold = probabilities.rolling(len(val_probabilities)).quantile(
        quantile=normal_proportion)
    threshold = threshold.rename('threshold')
    threshold = threshold.dropna()
    threshold.index = test_probabilities.index
    return threshold


class RateFixedTrain(Threshold):
    def __init__(self, model):
        super().__init__(model=model)

    def predict(self, df_features, **kwargs):
        df_proportion = kwargs.pop('df_proportion', None)
        normal_proportion = 1 - df_proportion['soft_target'].mean()
        normal_proportion = (normal_proportion + .5) / 2
        df_threshold = kwargs.pop('df_threshold', None)
        threshold_probabilities = self.predict_proba(df_threshold)[
            'probability']
        prediction = self.predict_proba(df_features=df_features)
        threshold = _tune_threshold(val_probabilities=threshold_probabilities,
                                    test_probabilities=prediction['probability'], normal_proportion=normal_proportion)
        threshold = threshold.values
        prediction['prediction'] = (
            prediction['probability'] >= threshold).round().astype('int')
        return prediction


class ORB(Classifier):
    def __init__(self, classifier, normal_proportion):
        self.classifier = classifier
        self.th = 1 - normal_proportion
        self.m = 1.5
        self.l0 = 10
        self.l1 = 12

    def train(self, df_train, **kwargs):
        df_ma = kwargs.pop('df_ma', None)
        ma = .4
        for i in range(self.classifier.n_iterations):
            obf0 = 1
            obf1 = 1
            if ma > self.th:
                obf0 = ((self.m ** ma - self.m ** self.th) *
                        self.l0) / (self.m - self.m ** self.th) + 1
            elif ma < self.th:
                obf1 = (((self.m ** (self.th - ma) - 1) * self.l1) /
                        (self.m ** self.th - 1)) + 1
            new_kwargs = dict(kwargs)
            new_kwargs['weights'] = [obf0, obf1]
            new_kwargs['n_iterations'] = 1
            self.classifier.train(df_train, **new_kwargs)
            df_output = self.classifier.predict(df_ma)
            ma = df_output['prediction'].mean()

    def predict(self, df_features, **kwargs):
        return self.classifier.predict(df_features, **kwargs)

    def predict_proba(self, df_features):
        return self.classifier.predict_proba(df_features)

    def save(self):
        self.classifier.save()

    def load(self):
        self.classifier.load()

    @property
    def n_iterations(self):
        return self.classifier.n_iterations


class PyTorch(Model):
    DIR = pathlib.Path('models')
    FILENAME = DIR / 'steps.cpt'

    def __init__(self, steps, classifier, optimizer, criterion, features, target, soft_target, max_epochs, batch_size, fading_factor, val_size=0.0):
        super().__init__()
        self.steps = steps
        self.classifier = classifier
        self.optimizer = optimizer
        self.criterion = criterion
        self.features = features
        self.target = target
        self.soft_target = soft_target
        self.max_epochs = max_epochs
        self.batch_size = batch_size
        self.fading_factor = fading_factor
        self.val_size = val_size
        self.trained = False

    @property
    def n_iterations(self):
        return self.max_epochs

    def train(self, df_train, **kwargs):
        try:
            sampled_train_dataloader, train_dataloader, val_dataloader = _prepare_dataloaders(
                df_train, self.features, self.target, self.soft_target, self.val_size, self.batch_size, self.fading_factor, self.steps, **kwargs)
        except ValueError as e:
            logger.warning(e)
            return

        if torch.cuda.is_available():
            self.classifier = self.classifier.cuda()

        self.max_epochs = kwargs.pop('max_epochs', self.max_epochs)
        train_loss = 0
        for epoch in range(self.max_epochs):
            self.classifier.train()
            for inputs, targets in sampled_train_dataloader:
                if torch.cuda.is_available():
                    inputs, targets = inputs.cuda(), targets.cuda()

                outputs = self.classifier(inputs.float())
                loss = self.criterion(outputs.view(
                    outputs.shape[0]), targets.float())
                train_loss += loss.item()

                self.optimizer.zero_grad()
                loss.backward()
                self.optimizer.step()

            train_loss = train_loss / len(sampled_train_dataloader)
            val_loss = None
            if self.has_validation():
                val_loss = metrics.loss(
                    self.classifier, val_dataloader, criterion=self.criterion)

            logger.debug(
                'Epoch: {}, Train loss: {}, Val loss: {}'.format(epoch, train_loss, val_loss))
        self.trained = True

    def predict_proba(self, df_features):
        if self.trained:
            X = df_features[self.features].values
            X = _steps_transform(self.steps, X)
            y = np.zeros(len(X))
            dataloader = _dataloader(X, y)

            if torch.cuda.is_available():
                self.classifier = self.classifier.cuda()

            probabilities = []
            with torch.no_grad():
                self.classifier.eval()
                for inputs, targets in dataloader:
                    if torch.cuda.is_available():
                        inputs, targets = inputs.cuda(), targets.cuda()

                    outputs = self.classifier(inputs.float())
                    probabilities.append(outputs.detach().cpu().numpy())
            probabilities = np.concatenate(probabilities)
        else:
            probabilities = np.zeros(len(df_features))

        probability = df_features.copy()
        probability['probability'] = probabilities
        return probability

    def has_validation(self):
        return self.val_size > 0

    def load(self):
        state = joblib.load(PyTorch.FILENAME)
        self.steps = state['steps']
        self.trained = state['trained']
        self.classifier.load()

    def save(self):
        mkdir(PyTorch.DIR)
        state = {
            'steps': self.steps,
            'trained': self.trained,
        }
        joblib.dump(state, PyTorch.FILENAME)
        self.classifier.save()


def _prepare_dataloaders(df_train, features, target, soft_target, val_size, batch_size, fading_factor, steps, **kwargs):
    X = df_train[features].values
    y = df_train[target].values
    soft_y = df_train[soft_target].values
    classes = np.unique(y)
    if len(classes) != 2:
        raise ValueError('It is expected two classes to train.')

    val_dataloader = None
    if val_size > 0:
        X_train, X_val, y_train, y_val, soft_y_train, soft_y_val = train_test_split(
            X, y, soft_y, test_size=val_size, shuffle=False)
        val_dataloader = _dataloader(X_val, y_val)
    else:
        X_train, y_train, soft_y_train = X, y, soft_y

    X_train = _steps_fit_transform(steps, X_train, y_train)

    weights = kwargs.pop('weights', [1, 1])
    sampled_train_dataloader = _dataloader(
        X_train, soft_y_train, batch_size=batch_size, sampler=_sampler(y_train, weights, fading_factor))
    train_dataloader = _dataloader(X_train, y_train)

    return sampled_train_dataloader, train_dataloader, val_dataloader


def _tensor(X, y):
    return torch.from_numpy(X), torch.from_numpy(y)


def _dataloader(X, y, batch_size=32, sampler=None):
    X, y = _tensor(X, y)
    dataset = data.TensorDataset(X, y)
    return data.DataLoader(dataset, batch_size=batch_size, sampler=sampler)


def _sampler(y, weights, fading_factor):
    normal_indices = np.flatnonzero(y == 0)
    bug_indices = np.flatnonzero(y == 1)
    age_weights = np.zeros(len(y))
    # normal commit ages
    age_weights[normal_indices] = _fading_weights(
        size=len(normal_indices), fading_factor=fading_factor, total=weights[0])
    # bug commit doesn't age
    age_weights[bug_indices] = _fading_weights(
        size=len(bug_indices), fading_factor=fading_factor, total=weights[1])
    return data.WeightedRandomSampler(weights=age_weights, num_samples=len(y), replacement=True)


def _fading_weights(size, fading_factor, total):
    fading_weights = reversed(range(size))
    fading_weights = [fading_factor**x for x in fading_weights]
    fading_weights = np.array(fading_weights)
    return (total * fading_weights) / np.sum(fading_weights)


def _steps_fit_transform(steps, X, y):
    for step in steps:
        X = step.fit_transform(X, y)
    return X


def _steps_transform(steps, X):
    for step in steps:
        try:
            X = step.transform(X)
        except NotFittedError:
            logger.warning('Step {} not fitted.'.format(step))
    return X


class Scikit(Model):

    def __init__(self, steps, classifier, features, target, soft_target, fading_factor, batch_size, val_size=0.0):
        super().__init__()
        self.steps = steps
        self.classifier = classifier
        self.features = features
        self.target = target
        self.soft_target = soft_target
        self.batch_size = batch_size
        self.fading_factor = fading_factor
        self.val_size = val_size
        self.trained = False

    def train(self, df_train, **kwargs):
        batch_size = self.batch_size if self.batch_size is not None else len(
            df_train)
        try:
            sampled_train_dataloader, train_dataloader, val_dataloader = _prepare_dataloaders(
                df_train, self.features, self.target, self.soft_target, self.val_size, batch_size, self.fading_factor, self.steps, **kwargs)
        except ValueError as e:
            logger.warning(e)
            return

        n_iterations = kwargs.pop('n_iterations', self.n_iterations)
        sampled_classes = set()
        for i in range(n_iterations):
            for inputs, targets in sampled_train_dataloader:
                inputs, targets = inputs.numpy(), targets.numpy()
                sampled_classes.update(targets)
                self.train_iteration(inputs=inputs, targets=targets)

            if self.has_validation():
                train_loss = 0
                for inputs, targets in train_dataloader:
                    inputs, targets = inputs.numpy(), targets.numpy()
                    train_loss += self.classifier.score(inputs, targets)
                train_loss = train_loss / len(val_dataloader)
                val_loss = 0
                for inputs, targets in val_dataloader:
                    inputs, targets = inputs.numpy(), targets.numpy()
                    val_loss += self.classifier.score(inputs, targets)
                val_loss = val_loss / len(val_dataloader)
                logger.debug(
                    'Iteration: {}, Train loss: {}, Val loss: {}'.format(i, train_loss, val_loss))

        if len(sampled_classes) == 2:
            self.trained = True

    @abstractmethod
    def train_iteration(self, inputs, targets):
        pass

    def predict_proba(self, df_features):
        X = df_features[self.features].values
        X = _steps_transform(self.steps, X)

        try:
            try:
                probabilities = self.classifier.predict_proba(X)
                probabilities = probabilities[:, 1]
            except AttributeError:
                probabilities = self.classifier.predict(X)
        except NotFittedError:
            probabilities = np.zeros(len(df_features))

        probability = df_features.copy()
        probability['probability'] = probabilities
        return probability

    def has_validation(self):
        return self.val_size > 0

    def load(self):
        state = joblib.load(PyTorch.FILENAME)
        self.steps = state['steps']
        self.classifier = state['classifier']
        self.val_loss = state['val_loss']

    def save(self):
        mkdir(PyTorch.DIR)
        state = {'steps': self.steps,
                 'classifier': self.classifier,
                 'val_loss': self.val_loss, }
        joblib.dump(state, PyTorch.FILENAME)


class NaiveBayes(Scikit):

    def __init__(self, steps, classifier, features, target, soft_target, fading_factor, n_updates, val_size=0.0):
        super().__init__(steps=steps, classifier=classifier, features=features, target=target,
                         soft_target=soft_target, fading_factor=fading_factor, batch_size=None, val_size=val_size)
        self.n_updates = n_updates

    @property
    def n_iterations(self):
        return self.n_updates

    def train_iteration(self, inputs, targets):
        self.classifier.partial_fit(
            inputs, targets, classes=[0, 1])


class RandomForest(Scikit):

    def __init__(self, steps, classifier, features, target, soft_target, fading_factor, n_trees, val_size=0.0):
        super().__init__(steps=steps, classifier=classifier, features=features, target=target,
                         soft_target=soft_target, fading_factor=fading_factor, batch_size=None, val_size=val_size)
        self.n_trees = n_trees

    @property
    def n_iterations(self):
        return self.n_trees

    def train_iteration(self, inputs, targets):
        self.classifier.n_estimators += 1
        self.classifier.fit(
            inputs, targets)


class LogisticRegression(Scikit):

    def __init__(self, steps, classifier, features, target, soft_target, fading_factor, n_epochs, batch_size, val_size=0.0):
        super().__init__(steps=steps, classifier=classifier, features=features, target=target,
                         soft_target=soft_target, fading_factor=fading_factor, batch_size=batch_size, val_size=val_size)
        self.n_epochs = n_epochs

    @property
    def n_iterations(self):
        return self.n_epochs

    def train_iteration(self, inputs, targets):
        self.classifier.partial_fit(
            inputs, targets, classes=[0, 1])


class Ensemble(Model):
    def __init__(self, models):
        super().__init__()
        self.models = models

    def n_iterations(self):
        return self.models[0].n_iterations

    def train(self, df_train, **kwargs):
        for model in self.models:
            model.train(df_train, **kwargs)

    def predict_proba(self, df_features):
        probability = df_features
        for index, model in enumerate(self.models):
            probability = model.predict_proba(probability)
            probability = probability.rename({
                'probability': 'probability{}'.format(index),
            },
                axis='columns')
        return _combine(probability)

    def save(self):
        for model in self.models:
            model.save()

    def load(self):
        for model in self.models:
            model.load()


def _combine(prediction):
    prediction = prediction.copy()
    probability_cols = [
        col for col in prediction.columns if 'probability' in col]
    prediction['probability'] = prediction[probability_cols].mean(
        axis='columns')
    return prediction
